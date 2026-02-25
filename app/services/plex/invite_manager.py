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

        # Validação do código personalizado
        if custom_code:
            if self.data_manager.get_invitation(custom_code):
                return {"success": False, "message": _("Este código personalizado já está em uso.")}
            code = custom_code
        else:
            code = secrets.token_urlsafe(16)
        
        # Validação de Duplicidade de Telegram ID
        if telegram_id:
            existing_user = self.data_manager.get_user_profile_by_telegram(telegram_id)
            if existing_user:
                 return {"success": False, "message": _("Este Telegram ID já está vinculado ao utilizador '%(username)s'.", username=existing_user['username'])}
            
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

        if invitation.get('expires_at') and datetime.fromisoformat(invitation['expires_at']) < datetime.now(timezone.utc): 
            return None, _("Este convite expirou.")
            
        return invitation, _("Convite válido.")

    def claim_invitation(self, code, plex_user_account):
        """
        Orquestra o resgate de um convite. (Refatorado para melhor leitura e SRP).
        """
        invitation, message = self.get_invitation_by_code(code)
        if not invitation:
            return {"success": False, "message": message}
        
        username = plex_user_account.username
        
        # 1. Validação básica de resgate duplicado
        if username in invitation.get('claimed_by_users', []):
            return {"success": False, "message": _("Já resgatou este convite anteriormente.")}
        
        # 2. Prevenção de Abuso de Testes (Trials)
        if invitation.get("trial_duration_minutes", 0) > 0:
            if self._check_trial_abuse(username):
                logger.warning(f"Bloqueio de Abuso: O utilizador {username} tentou resgatar um segundo convite de teste.")
                return {
                    "success": False, 
                    "message": _("Já utilizou um período de teste anteriormente. Para continuar a utilizar o serviço, adquira um plano.")
                }
        
        # 3. Tratamento de Telegram ID
        telegram_id_from_invite = self._handle_telegram_linking(invitation, username)

        # 4. Enviar Convite via Plex
        invite_result = self.send_plex_invite(plex_user_account.email, invitation['libraries'], plex_user_id=plex_user_account.id)
        if not invite_result.get("success"):
            return invite_result
        if invite_result.get("already_exists"):
            return {"success": False, "message": _("Já tem acesso a este servidor.")}

        # 5. Aceitar o Convite
        # Nota: O `_accept_invite_v2` agora tem retry automático, não precisamos do `time.sleep(3)` aqui.
        accept_result = self._accept_invite_v2(plex_user_account)
        if not accept_result.get("success"):
            # Fallback de segurança: Verifica se realmente falhou ou se já está aceite na cache
            self.user_manager.invalidate_user_cache()
            all_current_users = self.user_manager.get_all_plex_users()
            if not any(str(u['id']) == str(plex_user_account.id) for u in all_current_users):
                return {"success": False, "message": accept_result.get('message')}

        # 6. Atualizar Limites e Estado Local
        if invitation.get('screen_limit', 0) > 0:
            self.plex_manager.update_screen_limit(plex_user_account.id, invitation['screen_limit'])

        self.data_manager.increment_invitation_use(code, username)
        self.user_manager.invalidate_user_cache()
        self.data_manager.create_notification(message=f"'{username}' resgatou um convite.", category='success', link=url_for('main.users_page'))

        # 7. Configurar Perfil Local, Trial e Overseerr
        user_data_response = self._setup_local_profile_and_integrations(
            plex_user_account, invitation, telegram_id_from_invite
        )

        return {
            "success": True, 
            "message": _("Convite resgatado e acesso concedido! Bem-vindo(a), %(username)s.", username=username),
            "user_data": user_data_response
        }

    # --- MÉTODOS AUXILIARES DE RESGATE (SRP) ---

    def _check_trial_abuse(self, username):
        """Verifica se o utilizador já usou algum convite de teste (trial) no passado."""
        try:
            all_invites = self.data_manager.get_all_invitations()
            invites_list = all_invites.values() if isinstance(all_invites, dict) else all_invites
            
            for past_invite in invites_list:
                if past_invite.get("trial_duration_minutes", 0) > 0:
                    if username in past_invite.get('claimed_by_users', []):
                        return True
            return False
        except Exception as e:
            logger.error(f"Erro ao verificar histórico de testes do utilizador {username}: {e}")
            return False

    def _handle_telegram_linking(self, invitation, username):
        """Valida e retorna o Telegram ID a vincular, evitando conflitos."""
        telegram_id = invitation.get('telegram_id')
        if not telegram_id:
            return None
            
        existing_user = self.data_manager.get_user_profile_by_telegram(telegram_id)
        if existing_user and existing_user['username'] != username:
            logger.warning(f"Conflito: Convite tinha Telegram ID {telegram_id}, mas já está em uso por {existing_user['username']}. O vínculo será ignorado.")
            return None
            
        return telegram_id

    def _setup_local_profile_and_integrations(self, plex_account, invitation, telegram_id):
        """Configura perfil na BD, Overseerr e Job de Expiração."""
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

        # Trial Logic
        is_trial = False
        if invitation.get("trial_duration_minutes", 0) > 0:
            is_trial = True
            trial_end_utc, job_id = self._schedule_trial_end(plex_account.id, invitation["trial_duration_minutes"])
            profile_data.update({"trial_end_date": trial_end_utc.isoformat(), "trial_job_id": job_id})

        # Overseerr Logic
        if invitation.get('overseerr_access'):
            self.overseerr_manager.import_from_plex({"id": plex_account.id, "email": plex_account.email, "username": plex_account.username})
            profile_data['overseerr_access'] = True

        self.data_manager.set_user_profile(plex_account.id, profile_data)
        new_profile = self.data_manager.get_user_profile(plex_account.id)

        config = load_or_create_config()
        overseerr_url = config.get("OVERSEERR_URL", "").rstrip('/')
        expiration_date = profile_data.get("trial_end_date") or profile_data.get("expiration_date")

        return {
            "username": plex_account.username,
            "expiration_date": expiration_date,
            "is_trial": is_trial,
            "payment_token": new_profile.get('payment_token'),
            "overseerr_access": profile_data.get('overseerr_access', False),
            "overseerr_url": overseerr_url if overseerr_url and profile_data.get('overseerr_access', False) else None
        }

    def _schedule_trial_end(self, plex_user_id, duration_minutes):
        """Agenda a remoção do utilizador via APScheduler."""
        from app.extensions import scheduler
        from app.scheduler import end_trial_job
        
        trial_end_utc = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        naive_run_date = trial_end_utc.astimezone(scheduler.timezone).replace(tzinfo=None)
        job_id = f"trial_end_{plex_user_id}"
        
        scheduler.add_job(
            id=job_id, func=end_trial_job, args=[plex_user_id], 
            trigger='date', run_date=naive_run_date, replace_existing=True
        )
        return trial_end_utc, job_id

    # --- RESTANTE GESTÃO DE CONVITES ---

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
                    logger.info(f"Sincronização: Email alterado {profile.get('email')} -> {plex_user.email}")
                    updates['email'] = plex_user.email
                
                if profile.get('username') != plex_user.username:
                    logger.info(f"Sincronização: Username alterado {profile.get('username')} -> {plex_user.username}")
                    updates['username'] = plex_user.username
                
                if updates:
                    self.data_manager.set_user_profile(plex_user_id, updates)
                    
        except Exception as e:
            logger.error(f"Erro não fatal ao sincronizar dados do utilizador: {e}")

    def send_plex_invite(self, identifier, library_titles, plex_user_id=None):
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

            # Se já for amigo
            if user_to_invite:
                self._sync_local_user_data(user_to_invite)
                
                if self.conn.plex.machineIdentifier in [s.machineIdentifier for s in user_to_invite.servers]:
                    return {"success": True, "already_exists": True, "message": _("O utilizador já tem acesso."), "email": user_to_invite.email}

                self.conn.account.updateFriend(user=user_to_invite, server=self.conn.plex, sections=libraries_to_share)
                return {"success": True, "message": _("Acesso do utilizador atualizado com sucesso via ID!"), "email": user_to_invite.email}

            # Fallback (Ainda não é amigo ou ID falhou)
            user_to_invite = self.conn.account.user(identifier)
            if user_to_invite:
                 self._sync_local_user_data(user_to_invite)

            if self.conn.plex.machineIdentifier in [s.machineIdentifier for s in user_to_invite.servers]:
                return {"success": True, "already_exists": True, "message": _("O utilizador já tem acesso."), "email": user_to_invite.email}

            self.conn.account.updateFriend(user=user_to_invite, server=self.conn.plex, sections=libraries_to_share)
            return {"success": True, "message": _("Acesso do utilizador atualizado com sucesso para %(identifier)s!", identifier=identifier), "email": user_to_invite.email}

        except NotFound:
            # Novo amigo
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

    def _accept_invite_v2(self, user_account: MyPlexAccount, max_retries=3, delay=2.0):
        """
        Aceita o convite pendente usando o token da conta Plex do utilizador com mecanismo de Retry.
        PlexAPI as vezes sofre de consistência eventual (eventual consistency), o convite demora 1 a 2 seg a aparecer.
        """
        from app.config import load_or_create_config
        config = load_or_create_config()
        
        owner_identifier = self.conn.account.username
        session = requests.Session()
        params = {
            "X-Plex-Product": config.get("APP_TITLE", "Plex Panel"), 
            "X-Plex-Version": "1.0",
            "X-Plex-Client-Identifier": f"{secrets.token_hex(8)}-plex-panel-accept",
            "X-Plex-Platform": "Python", 
            "X-Plex-Device": "Server",
            "X-Plex-Token": user_account.authToken,
        }
        
        def _matches(inv):
            o = inv.get("owner", {})
            return owner_identifier in (o.get("username"), o.get("email"), o.get("title"))

        for attempt in range(max_retries):
            try:
                resp = session.get("https://clients.plex.tv/api/v2/shared_servers/invites/received/pending", params=params, headers={"Accept": "application/json"}, timeout=15)
                resp.raise_for_status()
                invites = resp.json()
                
                invite = next((i for i in invites if _matches(i)), None)
                if invite and invite.get("sharedServers"):
                    invite_id = invite["sharedServers"][0]["id"]
                    resp_accept = session.post(f"https://clients.plex.tv/api/v2/shared_servers/{invite_id}/accept", params=params, headers={"Accept": "application/json"}, timeout=15)
                    resp_accept.raise_for_status()
                    return {"success": True}
                
                # Se não encontrar o convite, aguarda e tenta novamente (Consistência Eventual do Plex)
                logger.debug(f"Convite não encontrado na tentativa {attempt + 1}/{max_retries}. A aguardar {delay}s...")
                time.sleep(delay)
                
            except Exception as e:
                logger.error(f"Erro na tentativa {attempt + 1} ao aceitar convite na API do Plex: {e}")
                time.sleep(delay)

        return {"success": False, "message": _("Não foi possível encontrar um convite pendente. Tente verificar o seu email.")}

    def accept_invite_via_token(self, plex_token):
        """
        Aceita um convite pendente usando o token do utilizador (usado no ecrã de pagamento para reativação).
        """
        try:
            user_account = MyPlexAccount(token=plex_token)
            
            accept_result = self._accept_invite_v2(user_account)
            
            if not accept_result.get('success'):
                # Verifica se o utilizador JÁ está ativo (pode ter aceite por email entretanto)
                self.user_manager.invalidate_user_cache()
                all_users = self.user_manager.get_all_plex_users()
                if any(str(u['id']) == str(user_account.id) for u in all_users):
                    return {"success": True, "message": _("O utilizador já está ativo no servidor."), "user": user_account}
                
                return accept_result

            return {"success": True, "message": _("Convite aceite com sucesso."), "user": user_account}
            
        except Exception as e:
            logger.error(f"Erro ao processar aceite manual via token: {e}")
            return {"success": False, "message": str(e)}
