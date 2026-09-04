# /app/services/plex/invite_manager.py 

import logging
import secrets
import time
import json
import re
import requests
from datetime import datetime, timezone, timedelta

from plexapi.myplex import MyPlexAccount
from plexapi.exceptions import BadRequest, NotFound
from flask_babel import gettext as _
from flask import url_for
from ...utils.log_sanitizer import mask_email, mask_code

logger = logging.getLogger(__name__)

# =========================================================================
# TRADUTOR DE ERROS DO PLEX
# =========================================================================
def extract_plex_error_message(exception) -> str:
    """
    Extrai uma mensagem de erro legível e amigável das exceções brutas da API do Plex.
    """
    error_message = str(exception)

    xml_pattern = r'<Response[^>]+status="([^"]*)"[^>]*/?>'
    xml_match = re.search(xml_pattern, error_message)
    if xml_match:
        return xml_match.group(1)

    json_pattern = r'"message":\s*"([^"]*)"'
    json_match = re.search(json_pattern, error_message)
    if json_match:
        return json_match.group(1)

    status_pattern = r"\(\d+\)\s+([^;]+);"
    status_match = re.search(status_pattern, error_message)
    if status_match:
        error_text = status_match.group(1).strip()
        return error_text.replace("_", " ").title()

    if hasattr(exception, "message"):
        return str(exception.message)

    clean_message = error_message.split(";")[0] if ";" in error_message else error_message
    clean_message = clean_message.replace("plexapi.exceptions.", "").replace("BadRequest: ", "")

    stripped = clean_message.strip().strip("'\"")
    if stripped.isdigit():
        return f"ID de biblioteca inválido: '{stripped}'. Verifique as configurações."

    return clean_message


class PlexInviteManager:
    """
    Gere todo o ciclo de vida dos convites de utilizadores e reativações.
    """
    def __init__(self, connection, user_manager, data_manager, plex_manager, overseerr_manager, notifier_manager):
        self.conn = connection
        self.user_manager = user_manager
        self.data_manager = data_manager
        self.plex_manager = plex_manager
        self.overseerr_manager = overseerr_manager
        self.notifier_manager = notifier_manager

    def create_invitation(self, **kwargs):
        if not kwargs.get('library_titles'):
            return {"success": False, "message": _("Pelo menos uma biblioteca deve ser selecionada para o convite.")}

        custom_code = kwargs.get('custom_code')
        max_uses = kwargs.get('max_uses', 1)
        telegram_id = kwargs.get('telegram_id')
        # Normaliza logo à entrada: um bot pode enviar o ID como número inteiro e um
        # formulário como texto com espaços — sem isto, '123' e ' 123 ' seriam
        # tratados como IDs diferentes e escapariam à validação de duplicados.
        if telegram_id is not None:
            telegram_id = str(telegram_id).strip() or None

        if custom_code:
            if self.data_manager.get_invitation(custom_code):
                return {"success": False, "message": _("Este código personalizado já está em uso.")}
            code = custom_code
        else:
            code = secrets.token_urlsafe(16)
        
        if telegram_id:
            existing_user = self.data_manager.get_user_profile_by_telegram(telegram_id)
            if existing_user:
                 return {"success": False, "message": _("Este Telegram ID já está vinculado ao usuário '%(username)s'.", username=existing_user['username'])}
            
            if self.data_manager.check_telegram_id_exists_in_invites(telegram_id):
                 return {"success": False, "message": _("Já existe um convite ativo gerado para este Telegram ID.")}

        expires_in_minutes = kwargs.get('expires_in_minutes')
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=int(expires_in_minutes))).isoformat() if expires_in_minutes else None
        
        invitation_details = {
            "libraries": kwargs.get('library_titles', []),
            "screen_limit": kwargs.get('screens', 0),
            "allow_downloads": kwargs.get('allow_downloads', False),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
            "trial_duration_minutes": kwargs.get('trial_duration_minutes', 0),
            "overseerr_access": kwargs.get('overseerr_access', False),
            "max_uses": max_uses,
            "use_count": 0,
            "claimed_by_users": [],
            "telegram_id": telegram_id
        }

        self.data_manager.add_invitation(code, invitation_details)
        return {"success": True, "code": code, "message": _("Código de convite criado com sucesso.")}

    def get_invitation_by_code(self, code):
        invitation = self.data_manager.get_invitation(code)
        if not invitation: 
            return None, _("Convite não encontrado.")
        
        if invitation.get('use_count', 0) >= invitation.get('max_uses', 1):
            return None, _("Este convite já atingiu o seu limite máximo de utilizações.")

        # Esta rota é pública: uma data mal formada na base de dados (edição
        # manual, importação antiga, valor sem fuso horário — comparar um
        # datetime ingénuo com um consciente levanta TypeError) devolvia 500 a
        # quem abrisse o link. Tratamos o convite como expirado, que é o lado
        # seguro do erro.
        expires_at = invitation.get('expires_at')
        if expires_at:
            try:
                expirado = datetime.fromisoformat(expires_at) < datetime.now(timezone.utc)
            except (TypeError, ValueError):
                logger.warning(f"O convite '{mask_code(code)}' tem uma data de expiração inválida ({expires_at!r}). Tratado como expirado.")
                expirado = True
            if expirado:
                return None, _("Este convite expirou.")
            
        return invitation, _("Convite válido.")

    def claim_invitation(self, code, plex_user_account):
        """
        Orquestra o resgate de um convite inicial, com proteção anti-espertos.
        """
        invitation, message = self.get_invitation_by_code(code)
        if not invitation:
            return {"success": False, "message": message}
        
        username = plex_user_account.username
        plex_user_id = plex_user_account.id
        
        # 1. Validação básica de resgate duplicado do mesmo convite.
        # Compara pelo ID do Plex (identidade estável) e, para os convites
        # antigos que ainda não têm IDs registados, pelo username.
        ja_resgatou = (
            str(plex_user_id) in (invitation.get('claimed_by_ids') or [])
            or username in invitation.get('claimed_by_users', [])
        )
        if ja_resgatou:
            return {"success": False, "message": _("Já resgatou este convite anteriormente.")}
            
        # 🛡️ 1.5. SISTEMA ANTI-BURLA (O bloqueio de espertinhos)
        # Verifica se esta conta do Plex já faz parte do nosso sistema
        existing_profile = self.data_manager.get_user_profile(plex_user_id)
        if existing_profile:
            is_blocked = self.data_manager.get_blocked_user(plex_user_id) is not None
            
            # Se o utilizador já foi cliente, mas deixou expirar ou foi bloqueado
            if existing_profile.get('status') == 'inactive' or is_blocked:
                logger.warning(f"O utilizador inativo '{username}' tentou usar o convite '{mask_code(code)}' para contornar o pagamento.")
                return {
                    "success": False,
                    "message": _("A sua conta encontra-se inativa ou expirada. Por favor, acesse à página minha conta para renovar a assinatura em vez de utilizar um novo convite.")
                }
            
            # Se o utilizador já está ativo, não precisa de gastar um convite
            if existing_profile.get('status') == 'active':
                return {
                    "success": False,
                    "message": _("Você já possui acesso ativo a este servidor. Não necessita de resgatar novos convites.")
                }
        
        # 2. Prevenção de Abuso de Testes (Trials)
        if invitation.get("trial_duration_minutes", 0) > 0:
            if self._check_trial_abuse(username, plex_user_id):
                logger.warning(f"Bloqueio de Abuso: O utilizador {username} tentou resgatar um segundo convite de teste.")
                return {
                    "success": False, 
                    "message": _("Já utilizou um período de teste anteriormente. Para continuar a utilizar o serviço, adquira um plano.")
                }
        
        telegram_id_from_invite = self._handle_telegram_linking(invitation, username)

        # 🛡️ RESERVA A VAGA ANTES DE FALAR COM O PLEX.
        # A validação acima (get_invitation_by_code) é só uma leitura, e a partir
        # daqui seguem-se dezenas de chamadas de rede. Com o worker gevent, cada
        # uma delas é um ponto de troca entre greenlets: dois resgates simultâneos
        # do mesmo código passavam ambos na validação e ambos recebiam acesso.
        # A reserva é atómica na base de dados, por isso só um pode ganhar.
        if not self.data_manager.reserve_invitation_use(code, username, plex_user_id):
            logger.warning(f"Resgate do convite '{mask_code(code)}' recusado: as vagas esgotaram-se entretanto.")
            return {"success": False, "message": _("Este convite já atingiu o seu limite máximo de utilizações.")}

        # A partir daqui, QUALQUER saída sem sucesso tem de devolver a vaga —
        # caso contrário uma tentativa falhada queimava uma utilização do convite.
        try:
            invite_result = self.send_plex_invite(
                identifier=plex_user_account.email, 
                library_titles=invitation['libraries'], 
                plex_user_id=plex_user_account.id,
                allow_sync=invitation.get('allow_downloads', False)
            )
            
            if not invite_result.get("success"):
                self.data_manager.release_invitation_use(code, username, plex_user_id)
                return invite_result
            if invite_result.get("already_exists"):
                self.data_manager.release_invitation_use(code, username, plex_user_id)
                return {"success": False, "message": _("Já tem acesso a este servidor.")}

            accept_result = self._accept_invite_v2(plex_user_account)
            if not accept_result.get("success"):
                self.user_manager.invalidate_user_cache()
                all_current_users = self.user_manager.get_all_plex_users()
                if not any(str(u['id']) == str(plex_user_account.id) for u in all_current_users):
                    self.data_manager.release_invitation_use(code, username, plex_user_id)
                    return {"success": False, "message": accept_result.get('message')}

            self._apply_online_media_preferences(plex_user_account)

            if invitation.get('screen_limit', 0) > 0:
                self.plex_manager.update_screen_limit(plex_user_account.id, invitation['screen_limit'])
        except Exception:
            self.data_manager.release_invitation_use(code, username, plex_user_id)
            raise

        # O uso já foi contabilizado pela reserva — não voltar a incrementar aqui.
        self.user_manager.invalidate_user_cache()
        
        try:
            self.data_manager.create_notification(message=_("'%(username)s' resgatou um convite.", username=username), category='success', link=url_for('main.users_page'))
        except RuntimeError:
             self.data_manager.create_notification(message=_("'%(username)s' resgatou um convite.", username=username), category='success')

        user_data_response = self._setup_local_profile_and_integrations(
            plex_user_account, invitation, telegram_id_from_invite
        )

        return {
            "success": True, 
            "message": _("Convite resgatado e acesso concedido! Bem-vindo(a), %(username)s.", username=username),
            "user_data": user_data_response
        }

    def _check_trial_abuse(self, username, plex_user_id=None):
        """
        Já houve um período de teste para esta pessoa?

        🐛 CORREÇÃO: a verificação comparava apenas o USERNAME do Plex, que o
        próprio utilizador pode alterar a qualquer momento em plex.tv — o painel
        até tem sincronização (`_sync_local_user_data`) precisamente porque essa
        mudança é um caso esperado. Bastava mudar de nome para o histórico
        deixar de bater certo e ganhar um segundo teste grátis, tantas vezes
        quantas se quisesse.

        Passa a comparar pelo ID do Plex, que nunca muda. O username continua a
        ser aceite como recurso: os convites resgatados antes desta alteração
        não têm IDs guardados, e ignorá-los abriria exatamente a mesma brecha
        para quem já está no histórico.
        """
        try:
            all_invites = self.data_manager.get_all_invitations()
            invites_list = all_invites.values() if isinstance(all_invites, dict) else all_invites

            id_procurado = str(plex_user_id) if plex_user_id is not None else None

            for past_invite in invites_list:
                if past_invite.get("trial_duration_minutes", 0) <= 0:
                    continue
                if id_procurado and id_procurado in (past_invite.get('claimed_by_ids') or []):
                    return True
                if username in past_invite.get('claimed_by_users', []):
                    return True
            return False
        except Exception as e:
            logger.error(f"Erro ao verificar histórico de testes do utilizador {username}: {e}")
            return False

    def _resolve_pending_referral(self, plex_account):
        """
        Converte o código de indicação guardado na sessão no ID de quem indicou.

        Devolve None (sem nunca lançar) em qualquer situação inválida: fora de um
        contexto HTTP, sistema desativado, código inexistente ou auto-indicação.
        Um problema no programa de indicações nunca pode impedir alguém de resgatar
        um convite legítimo.
        """
        try:
            from flask import session, has_request_context
            from app.config import load_or_create_config
            if not has_request_context():
                return None

            code = session.pop('pending_referral_code', None)
            if not code:
                return None

            config = load_or_create_config()
            if not config.get("REFERRAL_ENABLED", False):
                return None

            referrer = self.data_manager.get_user_profile_by_referral_code(code)
            if not referrer:
                logger.info(f"Código de indicação '{mask_code(code)}' não corresponde a nenhum utilizador. Ignorado.")
                return None

            referrer_id = referrer.get('plex_user_id')
            # 🛡️ Bloqueia a auto-indicação (usar o próprio código numa segunda conta
            # é o abuso mais óbvio deste tipo de sistema).
            if str(referrer_id) == str(plex_account.id):
                logger.warning(f"Auto-indicação bloqueada no resgate do convite (ID {plex_account.id}).")
                return None

            logger.info(f"Indicação registada: '{plex_account.username}' foi indicado por '{referrer.get('username')}'.")
            return referrer_id
        except Exception as e:
            logger.error(f"Erro ao resolver a indicação pendente: {e}", exc_info=True)
            return None

    def _handle_telegram_linking(self, invitation, username):
        telegram_id = invitation.get('telegram_id')
        if telegram_id is None or str(telegram_id).strip() == "":
            return None

        # Normaliza para comparar de forma fiável com o que está guardado.
        telegram_id = str(telegram_id).strip()

        # 🛡️ Revalidação no momento do RESGATE: entre a geração do convite e o seu
        # uso pode ter passado bastante tempo, e nesse intervalo o mesmo Telegram ID
        # pode ter sido vinculado a outra conta. Neste caso, o registo prossegue
        # normalmente — apenas o vínculo do Telegram é ignorado, para nunca deixar
        # dois utilizadores a apontar para o mesmo chat.
        existing_user = self.data_manager.get_user_profile_by_telegram(telegram_id)
        if existing_user and existing_user['username'] != username:
            logger.warning(
                f"Conflito de Telegram ID: o convite tinha o ID {telegram_id}, mas este já está "
                f"vinculado a '{existing_user['username']}'. O registo continua, mas sem o vínculo do Telegram."
            )
            return None

        return telegram_id

    def _setup_local_profile_and_integrations(self, plex_account, invitation, telegram_id):
        from app.config import load_or_create_config
        
        profile_data = {
            'username': plex_account.username,
            'email': plex_account.email,
            'screen_limit': invitation.get('screen_limit', 0), 
            'allow_downloads': invitation.get('allow_downloads', False), 
            'libraries': json.dumps(invitation.get('libraries', []))
        }
        
        if telegram_id:
            profile_data['telegram_user'] = telegram_id
            logger.info(f"Telegram ID {telegram_id} vinculado ao utilizador {plex_account.username}.")

        # 🎁 INDIQUE E GANHE: se o utilizador chegou através de um link de indicação
        # (/r/CODIGO), o código ficou guardado na sessão. É neste momento — quando o
        # perfil é criado de facto — que a indicação é associada.
        # A recompensa NÃO é paga aqui: só quando ele efetuar o primeiro pagamento.
        referred_by = self._resolve_pending_referral(plex_account)
        if referred_by:
            profile_data['referred_by'] = referred_by
            profile_data['referral_rewarded'] = False

        is_trial = False
        if invitation.get("trial_duration_minutes", 0) > 0:
            is_trial = True
            trial_end_utc, job_id = self._schedule_trial_end(plex_account.id, invitation["trial_duration_minutes"])
            profile_data.update({"trial_end_date": trial_end_utc.isoformat(), "trial_job_id": job_id})

        if invitation.get('overseerr_access'):
            self.overseerr_manager.import_from_plex({"id": plex_account.id, "email": plex_account.email, "username": plex_account.username})
            profile_data['overseerr_access'] = True

        self.data_manager.set_user_profile(plex_account.id, profile_data)
        new_profile = self.data_manager.get_user_profile(plex_account.id)

        config = load_or_create_config()
        overseerr_url = config.get("OVERSEERR_URL", "").rstrip('/')
        # 🐛 `profile_data` é o dicionário montado aqui e NUNCA tem a chave
        # 'expiration_date' — só 'trial_end_date', e apenas para testes. O segundo
        # ramo era portanto sempre None e o ecrã de sucesso nunca chegava a mostrar
        # "O seu acesso é válido até ...". A data tem de vir do perfil gravado.
        expiration_date = profile_data.get("trial_end_date") or new_profile.get("expiration_date")

        return {
            "username": plex_account.username,
            "expiration_date": expiration_date,
            "is_trial": is_trial,
            "payment_token": new_profile.get('payment_token'),
            "overseerr_access": profile_data.get('overseerr_access', False),
            "overseerr_url": overseerr_url if overseerr_url and profile_data.get('overseerr_access', False) else None
        }

    def _schedule_trial_end(self, plex_user_id, duration_minutes):
        from app.extensions import scheduler
        from app.scheduler import end_trial_job
        
        trial_end_utc = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        naive_run_date = trial_end_utc.astimezone(scheduler.timezone).replace(tzinfo=None)
        job_id = f"trial_end_{plex_user_id}_{secrets.token_hex(4)}"
        
        scheduler.add_job(
            id=job_id, func=end_trial_job, args=[plex_user_id], 
            trigger='date', run_date=naive_run_date, replace_existing=True
        )
        return trial_end_utc, job_id

    def list_invitations(self):
        return self.data_manager.get_all_invitations()

    def delete_invitation(self, code):
        self.data_manager.delete_invitation(code)
        return {"success": True, "message": _("Convite removido com sucesso.")}

    def reactivate_invitation(self, code):
        if self.data_manager.reset_invitation_usage(code):
             return {"success": True, "message": _("Convite reativado com sucesso (Contador resetado e validade estendida).")}
        return {"success": False, "message": _("Convite não encontrado.")}

    def _sync_local_user_data(self, plex_user):
        """Verifica e atualiza o email e username na BD local."""
        if not plex_user or not getattr(plex_user, 'id', None):
            return

        try:
            plex_user_id = int(plex_user.id)
            profile = self.data_manager.get_user_profile(plex_user_id)
            
            if profile:
                updates = {}
                if profile.get('email') != plex_user.email:
                    logger.info(f"Sincronização: Email alterado {mask_email(profile.get('email'))} -> {mask_email(plex_user.email)}")
                    updates['email'] = plex_user.email
                
                if profile.get('username') != plex_user.username:
                    logger.info(f"Sincronização: Username alterado {profile.get('username')} -> {plex_user.username}")
                    updates['username'] = plex_user.username
                
                if updates:
                    self.data_manager.set_user_profile(plex_user_id, updates)
                    
        except Exception as e:
            logger.error(f"Erro não fatal ao sincronizar dados do utilizador: {e}")

    def _get_invite_token(self, user_account_or_identifier):
        """Tenta extrair o token de convite (inviteToken) diretamente da API do Plex."""
        try:
            identifier = (getattr(user_account_or_identifier, 'email', None) or 
                          getattr(user_account_or_identifier, 'username', None) or 
                          str(user_account_or_identifier)).lower()
            
            for ss in self.conn.account.sharedServers():
                if ss.machineIdentifier == self.conn.plex.machineIdentifier:
                    ss_email = (ss.invitedEmail or "").lower()
                    ss_user = (ss.username or "").lower()
                    
                    if identifier == ss_email or identifier == ss_user:
                        if getattr(ss, 'accepted', False):
                            return "ACCEPTED"
                        return getattr(ss, 'inviteToken', None)
        except Exception as e:
            logger.debug(f"Erro ao buscar inviteToken para {user_account_or_identifier}: {e}")
        return None

    # =========================================================================
    # REATIVAÇÃO E ENVIO DE CONVITES REFORÇADOS
    # =========================================================================
    def send_plex_invite(self, identifier, library_titles, plex_user_id=None, allow_sync=False):
        """
        Envia o convite para a Plex.tv.
        A MELHORIA: Se o utilizador já for amigo, usa o user_manager blindado para atualizar as
        permissões (Downloads e Bibliotecas), restaurando o acesso com perfeição.
        """
        if not self.conn.plex:
            return {"success": False, "message": _("O Plex não está configurado.")}
        
        user_to_invite = None

        if plex_user_id:
            try:
                all_friends = self.conn.account.users()
                user_to_invite = next((u for u in all_friends if str(u.id) == str(plex_user_id)), None)
            except Exception as e:
                logger.warning(f"Erro ao tentar encontrar utilizador por ID na lista de amigos: {e}")

        try:
            libraries_to_share = [s for s in self.conn.plex.library.sections() if s.title in library_titles]
            if not libraries_to_share:
                return {"success": False, "message": _("Nenhuma biblioteca válida foi encontrada para partilhar.")}

            if not user_to_invite:
                try:
                    user_to_invite = self.conn.account.user(identifier)
                except NotFound:
                    pass

            if user_to_invite:
                self._sync_local_user_data(user_to_invite)
                
                if self.conn.plex.machineIdentifier in [s.machineIdentifier for s in user_to_invite.servers]:
                    logger.info(f"O utilizador {user_to_invite.username} já é amigo. Restaurando bibliotecas e permissão de Sync via Atualização de Fundo...")
                    self.user_manager.update_user_libraries(user_to_invite.id, library_titles, allow_sync=allow_sync)
                    return {"success": True, "already_exists": True, "message": _("O usuário já tem acesso. Permissões restauradas."), "email": user_to_invite.email, "invite_token": "ACCEPTED"}

                try:
                    self.conn.account.updateFriend(user=user_to_invite, server=self.conn.plex, sections=libraries_to_share, allowSync=allow_sync)
                    return {"success": True, "message": _("Acesso do usuário atualizado com sucesso!"), "email": user_to_invite.email, "invite_token": self._get_invite_token(user_to_invite)}
                except Exception as update_err:
                    self.user_manager.update_user_libraries(user_to_invite.id, library_titles, allow_sync=allow_sync)
                    return {"success": True, "message": _("Acesso do usuário atualizado com sucesso (via Sistema Seguro)!"), "email": user_to_invite.email, "invite_token": "ACCEPTED"}

            # É um amigo Novo: Usa o convite nativo do PlexAPI que funciona perfeitamente para novos e-mails
            self.conn.account.inviteFriend(user=identifier, server=self.conn.plex, sections=libraries_to_share, allowSync=allow_sync)
            return {"success": True, "message": _("Convite enviado com sucesso para %(identifier)s!", identifier=identifier), "email": identifier, "invite_token": self._get_invite_token(identifier)}
        
        except BadRequest as e:
            error_str = str(e).lower()
            if 'user is already a friend' in error_str or "already sharing" in error_str or "invite has already been sent" in error_str:
                return {"success": True, "already_exists": True, "message": _("O usuário já tem acesso ou um convite pendente.")}
            
            clean_error = extract_plex_error_message(e)
            logger.error(f"Erro 'BadRequest' ao convidar '{identifier}': {clean_error}")
            return {"success": False, "message": clean_error}
        
        except Exception as e:
            clean_error = extract_plex_error_message(e)
            logger.error(f"Erro inesperado ao convidar '{identifier}': {clean_error}")
            return {"success": False, "message": clean_error}

    # =========================================================================
    # FONTES DE MÍDIA ONLINE DO PLEX
    # =========================================================================
    def _apply_online_media_preferences(self, user_account):
        """
        Esconde as fontes de mídia da própria Plex (TV ao Vivo, Filmes e
        Programas de TV, etc.) na conta de quem acabou de entrar.

        Só é possível aqui: estas preferências pertencem à conta do utilizador,
        e é neste instante — logo após o aceite — que o painel tem o token dele.
        Uma falha aqui é apenas cosmética, por isso nunca interrompe o convite.
        """
        online_media = getattr(self.plex_manager, 'online_media', None)
        if not online_media:
            return

        try:
            online_media.apply_to_account(user_account)
        except Exception as e:
            logger.warning(f"Falha ao ajustar as fontes de mídia online do utilizador: {e}")

    # =========================================================================
    # ACEITAÇÃO BLINDADA V2
    # =========================================================================
    def _accept_invite_v2(self, user_account: MyPlexAccount, max_retries=3, delay=2.0):
        owner_identifier = self.conn.account.username
        owner_email = getattr(self.conn.account, 'email', None)
        owner_title = getattr(self.conn.account, 'title', None)

        base = "https://clients.plex.tv"
        
        params = {
            "X-Plex-Product": "PlexPanel", 
            "X-Plex-Version": "1.0",
            "X-Plex-Client-Identifier": getattr(user_account, 'uuid', f"{secrets.token_hex(8)}-plex-panel"),
            "X-Plex-Platform": "Web", 
            "X-Plex-Platform-Version": "1.0",
            "X-Plex-Features": "external-media,indirect-media,hub-style-list",
            "X-Plex-Language": "pt",
            "X-Plex-Token": user_account.authToken,
        }
        headers = {"Accept": "application/json"}

        def _matches(inv):
            owner_data = inv.get("owner", {})
            possible_owner_values = [
                owner_data.get("username"), 
                owner_data.get("email"), 
                owner_data.get("title"), 
                owner_data.get("friendlyName")
            ]
            server_identifiers = [owner_identifier, owner_email, owner_title]
            return any(val and val in possible_owner_values for val in server_identifiers)

        for attempt in range(max_retries):
            try:
                session = getattr(user_account, '_session', requests.Session())
                
                url_list = f"{base}/api/v2/shared_servers/invites/received/pending"
                resp = session.get(url_list, params=params, headers=headers, timeout=15)
                resp.raise_for_status()
                invites = resp.json()
                
                invite = next((i for i in invites if _matches(i)), None)
                if invite and invite.get("sharedServers"):
                    invite_id = invite["sharedServers"][0]["id"]
                    url_accept = f"{base}/api/v2/shared_servers/{invite_id}/accept"
                    
                    resp_accept = session.post(url_accept, params=params, headers=headers, timeout=15)
                    resp_accept.raise_for_status()
                    
                    logger.info(f"Convite ID {invite_id} aceite com sucesso via API V2!")
                    return {"success": True}
                
                logger.debug(f"Convite não encontrado na tentativa {attempt + 1}/{max_retries}. A aguardar {delay}s...")
                time.sleep(delay)
                
            except Exception as e:
                logger.error(f"Erro na tentativa {attempt + 1} ao aceitar convite na API V2: {e}")
                time.sleep(delay)

        return {"success": False, "message": _("O convite não apareceu no sistema a tempo. Por favor, tente aceitá-lo manualmente no seu email.")}

    def accept_invite_via_token(self, plex_token):
        """
        Aceita um convite pendente usando o token do utilizador (usado no ecrã de pagamento para reativação).
        """
        try:
            user_account = MyPlexAccount(token=plex_token)
            accept_result = self._accept_invite_v2(user_account)
            
            if not accept_result.get('success'):
                self.user_manager.invalidate_user_cache()
                all_users = self.user_manager.get_all_plex_users()
                if any(str(u['id']) == str(user_account.id) for u in all_users):
                    self._apply_online_media_preferences(user_account)
                    return {"success": True, "message": _("O usuário já está ativo no servidor."), "user": user_account}
                return accept_result

            self._apply_online_media_preferences(user_account)
            return {"success": True, "message": _("Convite aceite com sucesso."), "user": user_account}
            
        except Exception as e:
            logger.error(f"Erro ao processar aceite manual via token: {e}")
            return {"success": False, "message": str(e)}
