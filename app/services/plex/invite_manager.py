import logging
import secrets
import time
import json
import requests
from datetime import datetime, timezone, timedelta
from plexapi.myplex import MyPlexAccount
from plexapi.exceptions import BadRequest, NotFound
from flask_babel import gettext as _
from flask import url_for

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

class PlexInviteManager:
    """
    Gere todo o ciclo de vida dos convites de utilizadores.
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

        if custom_code:
            if self.data_manager.get_invitation(custom_code):
                return {"success": False, "message": _("Este código personalizado já está em uso.")}
            code = custom_code
        else:
            code = secrets.token_urlsafe(16)
        
        # Validação de Duplicidade de Telegram ID
        if telegram_id:
            # Verifica se já existe um usuário com este ID
            existing_user = self.data_manager.get_user_profile_by_telegram(telegram_id)
            if existing_user:
                 return {"success": False, "message": _("Este Telegram ID já está vinculado ao usuário '%(username)s'.", username=existing_user['username'])}
            
            # Verifica se já existe um convite pendente com este ID
            if self.data_manager.check_telegram_id_exists_in_invites(telegram_id):
                 return {"success": False, "message": _("Já existe um convite ativo gerado para este Telegram ID.")}

        expires_in_minutes = kwargs.get('expires_in_minutes')
        
        invitation_details = {
            "libraries": kwargs.get('library_titles', []),
            "screen_limit": kwargs.get('screens', 0),
            "allow_downloads": kwargs.get('allow_downloads', False),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=int(expires_in_minutes))).isoformat() if expires_in_minutes else None,
            "trial_duration_minutes": kwargs.get('trial_duration_minutes', 0),
            "overseerr_access": kwargs.get('overseerr_access', False),
            "max_uses": max_uses,
            "use_count": 0,
            "claimed_by_users": [],
            "telegram_id": telegram_id # Salva o Telegram ID no convite
        }

        self.data_manager.add_invitation(code, invitation_details)
        return {"success": True, "code": code, "message": _("Código de convite criado com sucesso.")}

    def get_invitation_by_code(self, code):
        invitation = self.data_manager.get_invitation(code)
        if not invitation: return None, _("Convite não encontrado.")
        
        if invitation.get('use_count', 0) >= invitation.get('max_uses', 1):
            return None, _("Este convite já atingiu o seu limite máximo de utilizações.")

        if invitation.get('expires_at') and datetime.fromisoformat(invitation['expires_at']) < datetime.now(timezone.utc): 
            return None, _("Este convite expirou.")
            
        return invitation, _("Convite válido.")

    def claim_invitation(self, code, plex_user_account):
        from app.extensions import scheduler
        from app.scheduler import end_trial_job

        invitation, message = self.get_invitation_by_code(code)
        if not invitation:
            return {"success": False, "message": message}
        
        claimed_users = invitation.get('claimed_by_users', [])
        if plex_user_account.username in claimed_users:
            return {"success": False, "message": _("Você já resgatou este convite anteriormente.")}
        
        # --- BLOQUEIO DE MÚLTIPLOS TESTES ---
        # Verifica se o convite atual é um teste (tem duração de trial definida e maior que 0)
        is_trial_invite = invitation.get("trial_duration_minutes", 0) > 0

        if is_trial_invite:
            try:
                # Busca todos os convites para verificar o histórico
                all_invites = self.data_manager.get_all_invitations()
                # Verifica se o retorno é dict ou lista e normaliza
                invites_list = all_invites.values() if isinstance(all_invites, dict) else all_invites
                
                for past_invite in invites_list:
                    # Verifica se o convite passado TAMBÉM era um teste
                    if past_invite.get("trial_duration_minutes", 0) > 0:
                        # Verifica se o usuário já está na lista de quem usou aquele teste anterior
                        if plex_user_account.username in past_invite.get('claimed_by_users', []):
                            logger.warning(f"Bloqueio de Abuso: O usuário {plex_user_account.username} tentou resgatar um segundo convite de teste.")
                            return {
                                "success": False, 
                                "message": _("Você já utilizou um período de teste anteriormente. Para continuar utilizando o serviço, por favor adquira um plano.")
                            }
            except Exception as e:
                logger.error(f"Erro ao verificar histórico de testes do usuário: {e}")
                # Em caso de erro na verificação, prossegue com cautela ou bloqueia. Aqui deixamos passar mas logamos o erro.
        # ------------------------------------
        
        # Lógica de Vinculação de Telegram ID
        telegram_id_from_invite = invitation.get('telegram_id')
        if telegram_id_from_invite:
            # Verifica novamente (Double check) se o ID já não foi usado entre a criação e o resgate
            existing_user = self.data_manager.get_user_profile_by_telegram(telegram_id_from_invite)
            if existing_user and existing_user['username'] != plex_user_account.username:
                 # Situação rara: O ID foi vinculado manualmente a outro usuário nesse meio tempo.
                 # Decisão: Prosseguir com o convite mas SEM vincular o ID para evitar erro, ou falhar?
                 # Melhor logar e avisar, mas permitir o acesso se o convite for válido.
                 logger.warning(f"Conflito: O convite tinha Telegram ID {telegram_id_from_invite}, mas ele já está em uso por {existing_user['username']}. O vínculo não será feito.")
                 telegram_id_from_invite = None

        # Passamos o ID do utilizador para garantir que o convite vai para a conta certa
        invite_result = self.send_plex_invite(
            plex_user_account.email, 
            invitation['libraries'], 
            plex_user_id=plex_user_account.id
        )
        
        if not invite_result.get("success"):
            return invite_result
        if invite_result.get("already_exists"):
            return {"success": False, "message": _("Você já tem acesso a este servidor.")}

        time.sleep(3)

        accept_result = self._accept_invite_v2(plex_user_account)
        if not accept_result.get("success"):
            # CORREÇÃO: Invalida a cache e depois busca sem argumentos
            self.user_manager.invalidate_user_cache()
            all_current_users = self.user_manager.get_all_plex_users()
            
            if not any(u['id'] == plex_user_account.id for u in all_current_users):
                return {"success": False, "message": accept_result.get('message')}

        if invitation['screen_limit'] > 0:
            self.plex_manager.update_screen_limit(plex_user_account.id, invitation['screen_limit'])

        self.data_manager.increment_invitation_use(code, plex_user_account.username)
        self.user_manager.invalidate_user_cache()
        self.data_manager.create_notification(message=f"'{plex_user_account.username}' resgatou um convite.", category='success', link=url_for('main.users_page'))

        profile_data = {
            'username': plex_user_account.username,
            'email': plex_user_account.email,
            'screen_limit': invitation['screen_limit'], 
            'allow_downloads': invitation.get('allow_downloads', False), 
            'libraries': json.dumps(invitation.get('libraries', []))
        }
        
        # Adiciona o Telegram ID ao perfil se estiver presente no convite
        if telegram_id_from_invite:
            profile_data['telegram_user'] = telegram_id_from_invite
            logger.info(f"Telegram ID {telegram_id_from_invite} vinculado automaticamente ao usuário {plex_user_account.username}.")

        is_trial = False
        if invitation.get("trial_duration_minutes", 0) > 0:
            is_trial = True
            trial_end_utc = datetime.now(timezone.utc) + timedelta(minutes=invitation["trial_duration_minutes"])
            naive_run_date = trial_end_utc.astimezone(scheduler.timezone).replace(tzinfo=None)
            job_id = f"trial_end_{plex_user_account.id}"
            scheduler.add_job(id=job_id, func=end_trial_job, args=[plex_user_account.id], trigger='date', run_date=naive_run_date, replace_existing=True)
            profile_data.update({"trial_end_date": trial_end_utc.isoformat(), "trial_job_id": job_id})

        if invitation.get('overseerr_access'):
            self.overseerr_manager.import_from_plex({"id": plex_user_account.id, "email": plex_user_account.email, "username": plex_user_account.username})
            profile_data['overseerr_access'] = True

        self.data_manager.set_user_profile(plex_user_account.id, profile_data)
        
        new_profile = self.data_manager.get_user_profile(plex_user_account.id)

        config = load_or_create_config()
        overseerr_url = config.get("OVERSEERR_URL", "").rstrip('/')
        expiration_date = profile_data.get("trial_end_date") or profile_data.get("expiration_date")

        return {
            "success": True, 
            "message": _("Convite resgatado e acesso concedido! Bem-vindo, %(username)s.", username=plex_user_account.username),
            "user_data": {
                "username": plex_user_account.username,
                "expiration_date": expiration_date,
                "is_trial": is_trial,
                "payment_token": new_profile.get('payment_token'),
                "overseerr_access": profile_data.get('overseerr_access', False),
                "overseerr_url": overseerr_url if overseerr_url and profile_data.get('overseerr_access', False) else None
            }
        }

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
        """
        Verifica e atualiza o email e username no banco de dados local
        com base nos dados mais recentes do Plex.
        """
        if not plex_user or not plex_user.id:
            return

        try:
            # Obtém o ID e converte para int
            plex_user_id = int(plex_user.id)
            # Busca o perfil atual
            profile = self.data_manager.get_user_profile(plex_user_id)
            
            if profile:
                updates = {}
                current_email = plex_user.email
                current_username = plex_user.username
                
                # Verifica mudança de email
                if profile.get('email') != current_email:
                    logger.info(f"Sincronização (Reativação): Email do utilizador {plex_user_id} alterado. {profile.get('email')} -> {current_email}")
                    updates['email'] = current_email
                
                # Verifica mudança de username
                if profile.get('username') != current_username:
                    logger.info(f"Sincronização (Reativação): Username do utilizador {plex_user_id} alterado. {profile.get('username')} -> {current_username}")
                    updates['username'] = current_username
                
                # Aplica as atualizações se houver
                if updates:
                    self.data_manager.set_user_profile(plex_user_id, updates)
                    
        except Exception as e:
            logger.error(f"Erro não fatal ao sincronizar dados do utilizador durante o convite: {e}")

    def send_plex_invite(self, identifier, library_titles, plex_user_id=None):
        """
        Método inteligente para convidar ou reativar o acesso de um usuário ao Plex.
        Prioriza o ID do utilizador se fornecido para maior precisão.
        
        :param identifier: Email ou username para fallback.
        :param library_titles: Lista de bibliotecas a partilhar.
        :param plex_user_id: (Opcional) ID numérico do utilizador Plex.
        :return: Dict com status, mensagem e 'email' (o email final usado).
        """
        if not self.conn.plex:
            return {"success": False, "message": _("O Plex não está configurado.")}
        
        user_to_invite = None

        # 1. Tenta encontrar o utilizador diretamente pelo ID na lista de amigos atuais
        if plex_user_id:
            try:
                all_friends = self.conn.account.users()
                user_to_invite = next((u for u in all_friends if str(u.id) == str(plex_user_id)), None)
                if user_to_invite:
                    logger.info(f"Utilizador encontrado por ID ({plex_user_id}) na lista de amigos. A usar este objeto.")
            except Exception as e:
                logger.warning(f"Erro ao tentar encontrar utilizador por ID na lista de amigos: {e}")

        try:
            libraries_to_share = [s for s in self.conn.plex.library.sections() if s.title in library_titles]
            if not libraries_to_share:
                return {"success": False, "message": _("Nenhuma biblioteca válida foi encontrada para compartilhar.")}

            # 2. Se o utilizador foi encontrado pelo ID (ou seja, ainda é amigo), usa-o
            if user_to_invite:
                # *** ATUALIZAÇÃO *** Sincroniza dados locais (email/username) se mudaram
                self._sync_local_user_data(user_to_invite)
                
                # Verifica se já tem acesso a este servidor
                if self.conn.plex.machineIdentifier in [s.machineIdentifier for s in user_to_invite.servers]:
                    logger.info(f"O utilizador '{user_to_invite.username}' (ID: {plex_user_id}) já tem acesso ao servidor. Nenhuma ação necessária.")
                    return {"success": True, "already_exists": True, "message": _("O utilizador já tem acesso."), "email": user_to_invite.email}

                logger.info(f"Utilizador '{user_to_invite.username}' (ID: {plex_user_id}) encontrado na conta Plex, mas sem acesso a este servidor. A atualizar as partilhas.")
                self.conn.account.updateFriend(user=user_to_invite, server=self.conn.plex, sections=libraries_to_share)
                return {"success": True, "message": _("Acesso do utilizador atualizado com sucesso via ID!"), "email": user_to_invite.email}

            # 3. Fallback: Se não encontrou pelo ID, tenta pelo identificador (email/username)
            logger.info(f"Utilizador não encontrado por ID. A tentar convidar por identificador: '{identifier}'")
            user_to_invite = self.conn.account.user(identifier)
            
            if user_to_invite:
                 self._sync_local_user_data(user_to_invite)

            if self.conn.plex.machineIdentifier in [s.machineIdentifier for s in user_to_invite.servers]:
                return {"success": True, "already_exists": True, "message": _("O utilizador já tem acesso."), "email": user_to_invite.email}

            self.conn.account.updateFriend(user=user_to_invite, server=self.conn.plex, sections=libraries_to_share)
            return {"success": True, "message": _("Acesso do utilizador atualizado com sucesso para %(identifier)s!", identifier=identifier), "email": user_to_invite.email}

        except NotFound:
            # Se não encontrado em lado nenhum, envia novo convite
            logger.info(f"Utilizador '{identifier}' não encontrado como amigo. A enviar novo convite.")
            self.conn.account.inviteFriend(user=identifier, server=self.conn.plex, sections=libraries_to_share)
            return {"success": True, "message": _("Convite enviado com sucesso para %(identifier)s!", identifier=identifier), "email": identifier}
        
        except BadRequest as e:
            error_str = str(e).lower()
            if 'user is already a friend' in error_str or "already sharing" in error_str or "invite has already been sent" in error_str:
                return {"success": True, "already_exists": True, "message": _("O utilizador já tem acesso ou um convite pendente.")}
            logger.error(f"Erro 'BadRequest' ao convidar '{identifier}': {e}")
            return {"success": False, "message": str(e)}
        
        except Exception as e:
            logger.error(f"Erro inesperado ao convidar '{identifier}': {e}", exc_info=True)
            return {"success": False, "message": str(e)}


    def _accept_invite_v2(self, user_account: MyPlexAccount):
        owner_identifier = self.conn.account.username
        session = requests.Session()
        config = load_or_create_config()
        params = {
            "X-Plex-Product": config.get("APP_TITLE", "Plex Panel"), "X-Plex-Version": "1.0",
            "X-Plex-Client-Identifier": f"{secrets.token_hex(8)}-plex-panel-accept",
            "X-Plex-Platform": "Python", "X-Plex-Device": "Server",
            "X-Plex-Token": user_account.authToken,
        }
        try:
            resp = session.get("https://clients.plex.tv/api/v2/shared_servers/invites/received/pending", params=params, headers={"Accept": "application/json"}, timeout=20)
            resp.raise_for_status()
            invites = resp.json()
            def _matches(inv):
                o = inv.get("owner", {})
                return owner_identifier in (o.get("username"), o.get("email"), o.get("title"))
            invite = next((i for i in invites if _matches(i)), None)
            if not invite or not invite.get("sharedServers"):
                return {"success": False, "message": _("Nenhum convite pendente deste servidor foi encontrado.")}
            invite_id = invite["sharedServers"][0]["id"]
            resp = session.post(f"https://clients.plex.tv/api/v2/shared_servers/{invite_id}/accept", params=params, headers={"Accept": "application/json"}, timeout=20)
            resp.raise_for_status()
            return {"success": True}
        except Exception as e:
            return {"success": False, "message": _("Ocorreu um erro de rede ao tentar aceitar o convite.")}

    def accept_invite_via_token(self, plex_token):
        """
        Permite aceitar um convite pendente usando apenas o token do usuário.
        Usado na página de pagamento para reativação imediata.
        """
        try:
            user_account = MyPlexAccount(token=plex_token)
            logger.info(f"Tentativa de aceite manual de convite para o utilizador Plex: {user_account.username}")
            
            # 1. Tenta aceitar o convite
            accept_result = self._accept_invite_v2(user_account)
            
            if not accept_result.get('success'):
                # Se falhar, verificamos se o usuário JÁ está ativo (pode ter aceitado por email nesse meio tempo)
                # Isso evita erro falso positivo.
                self.user_manager.invalidate_user_cache()
                all_users = self.user_manager.get_all_plex_users()
                if any(str(u['id']) == str(user_account.id) for u in all_users):
                    return {"success": True, "message": _("Usuário já está ativo no servidor."), "user": user_account}
                
                return accept_result

            return {"success": True, "message": _("Convite aceito com sucesso."), "user": user_account}
            
        except Exception as e:
            logger.error(f"Erro ao processar aceite via token: {e}")
            return {"success": False, "message": str(e)}
