# app/services/plex/invite_manager.py
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
    def __init__(self, connection, user_manager, data_manager, tautulli_manager, overseerr_manager, notifier_manager):
        self.conn = connection
        self.user_manager = user_manager
        self.data_manager = data_manager
        self.tautulli_manager = tautulli_manager
        self.overseerr_manager = overseerr_manager
        self.notifier_manager = notifier_manager

    def create_invitation(self, **kwargs):
        if not kwargs.get('library_titles'):
            return {"success": False, "message": _("Pelo menos uma biblioteca deve ser selecionada para o convite.")}

        code = secrets.token_urlsafe(16)
        expires_in_minutes = kwargs.get('expires_in_minutes')
        
        invitation_details = {
            "libraries": kwargs.get('library_titles', []),
            "screen_limit": kwargs.get('screens', 0),
            "allow_downloads": kwargs.get('allow_downloads', False),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=int(expires_in_minutes))).isoformat() if expires_in_minutes else None,
            "trial_duration_minutes": kwargs.get('trial_duration_minutes', 0),
            "overseerr_access": kwargs.get('overseerr_access', False)
        }

        self.data_manager.add_invitation(code, invitation_details)
        return {"success": True, "code": code, "message": _("Código de convite criado com sucesso.")}

    def get_invitation_by_code(self, code):
        invitation = self.data_manager.get_invitation(code)
        if not invitation: return None, _("Convite não encontrado.")
        if invitation.get('claimed_by'): return None, _("Este convite já foi resgatado por %(username)s.", username=invitation.get('claimed_by'))
        if invitation.get('expires_at') and datetime.fromisoformat(invitation['expires_at']) < datetime.now(timezone.utc): return None, _("Este convite expirou.")
        return invitation, _("Convite válido.")

    def claim_invitation(self, code, plex_user_account):
        from app.extensions import scheduler
        from app.scheduler import end_trial_job

        invitation, message = self.get_invitation_by_code(code)
        if not invitation:
            return {"success": False, "message": message}
        
        invite_result = self._invite_user_to_plex(plex_user_account.email, invitation['libraries'])
        if not invite_result.get("success"):
            return invite_result
        if invite_result.get("already_exists"):
            return {"success": False, "message": _("Você já tem acesso a este servidor.")}

        logger.info(_("Convite para '%(username)s' enviado. A verificar o estado do acesso...", username=plex_user_account.username))
        time.sleep(3)

        accept_result = self._accept_invite_v2(plex_user_account)
        if not accept_result.get("success"):
            all_current_users = self.user_manager.get_all_plex_users(force_refresh=True)
            if not any(u['username'] == plex_user_account.username for u in all_current_users):
                logger.error(_("Falha final na aceitação do convite para %(username)s: %(message)s", username=plex_user_account.username, message=accept_result.get('message')))
                return {"success": False, "message": accept_result.get('message')}
            logger.warning(_("A aceitação formal falhou, mas o acesso foi confirmado para %(username)s.", username=plex_user_account.username))

        if invitation['screen_limit'] > 0:
            self.tautulli_manager.update_screen_limit(plex_user_account.email, plex_user_account.username, invitation['screen_limit'])

        self.data_manager.update_invitation_claim(code, plex_user_account.username, datetime.now(timezone.utc).isoformat())
        self.user_manager.invalidate_user_cache()
        self.data_manager.create_notification(message=f"'{plex_user_account.username}' resgatou um convite.", category='success', link=url_for('main.users_page'))

        profile_data = {'screen_limit': invitation['screen_limit'], 'allow_downloads': invitation.get('allow_downloads', False), 'libraries': json.dumps(invitation.get('libraries', []))}
        
        if invitation.get("trial_duration_minutes", 0) > 0:
            trial_end_utc = datetime.now(timezone.utc) + timedelta(minutes=invitation["trial_duration_minutes"])
            naive_run_date = trial_end_utc.astimezone(scheduler.timezone).replace(tzinfo=None)
            job_id = f"trial_end_{plex_user_account.username}"
            scheduler.add_job(id=job_id, func=end_trial_job, args=[plex_user_account.username], trigger='date', run_date=naive_run_date, replace_existing=True)
            profile_data.update({"trial_end_date": trial_end_utc.isoformat(), "trial_job_id": job_id})

        if invitation.get('overseerr_access'):
            self.overseerr_manager.import_from_plex({"id": plex_user_account.id, "email": plex_user_account.email, "username": plex_user_account.username})
            profile_data['overseerr_access'] = True

        self.data_manager.set_user_profile(plex_user_account.username, profile_data)
        return {"success": True, "message": _("Convite resgatado e acesso concedido! Bem-vindo, %(username)s.", username=plex_user_account.username)}

    def list_invitations(self):
        return self.data_manager.get_all_pending_invitations()

    def delete_invitation(self, code):
        self.data_manager.delete_invitation(code)
        return {"success": True, "message": _("Convite removido com sucesso.")}

    def _invite_user_to_plex(self, identifier, library_titles):
        if not self.conn.plex:
            return {"success": False, "message": _("O Plex não está configurado.")}
        
        try:
            try:
                user_account = self.conn.account.user(identifier)
                if self.conn.plex.machineIdentifier in [s.machineIdentifier for s in user_account.servers]:
                     return {"success": True, "already_exists": True, "message": _("O utilizador já tem acesso.")}
            except NotFound:
                pass 

            libraries_to_share = [s for s in self.conn.plex.library.sections() if s.title in library_titles]
            if not libraries_to_share:
                return {"success": False, "message": _("Nenhuma biblioteca válida encontrada para partilhar.")}

            self.conn.account.inviteFriend(user=identifier, server=self.conn.plex, sections=libraries_to_share)
            return {"success": True, "message": _("Convite enviado com sucesso para %(identifier)s!", identifier=identifier)}
        
        except BadRequest as e:
            if 'user is already a friend' in str(e).lower() or "already sharing" in str(e).lower():
                return {"success": True, "already_exists": True, "message": _("O utilizador já tem acesso.")}
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _accept_invite_v2(self, user_account: MyPlexAccount):
        owner_identifier = self.conn.account.username
        logger.info(_("A iniciar aceitação de convite v2 para '%(user)s' do proprietário do servidor '%(owner)s'.", user=user_account.username, owner=owner_identifier))
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
            logger.error(_("Erro durante a aceitação do convite v2: %(error)s", error=e), exc_info=True)
            return {"success": False, "message": _("Ocorreu um erro de rede ao tentar aceitar o convite.")}
