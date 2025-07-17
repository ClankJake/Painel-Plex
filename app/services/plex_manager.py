# app/services/plex_manager.py

import logging
from flask import current_app
from flask_babel import gettext as _

# Imports dos novos gestores modulares
from .plex.connection import PlexConnectionManager
from .plex.user_manager import PlexUserManager
from .plex.invite_manager import PlexInviteManager
from .plex.subscription_manager import PlexSubscriptionManager

logger = logging.getLogger(__name__)

class PlexManager:
    """
    Atua como uma fachada, a coordenar vários serviços relacionados com o Plex.
    Isto mantém a API pública consistente para o resto da aplicação,
    enquanto a lógica é separada em classes de gestores mais pequenas e focadas.
    """
    def __init__(self, data_manager, tautulli_manager, notifier_manager, overseerr_manager):
        # Instancia todos os sub-gestores
        self.conn = PlexConnectionManager()
        self.users = PlexUserManager(self.conn, data_manager, tautulli_manager, overseerr_manager)
        self.invites = PlexInviteManager(self.conn, self.users, data_manager, tautulli_manager, overseerr_manager, notifier_manager)
        self.subscriptions = PlexSubscriptionManager(data_manager, self.users, tautulli_manager)
        
        # Mantém referências a outros gestores para injeção de dependências
        self.data_manager = data_manager
        self.tautulli_manager = tautulli_manager
        self.notifier_manager = notifier_manager
        self.overseerr_manager = overseerr_manager
        
        self.app = None
        self.plex = None # Para retrocompatibilidade
        self.account = None # Para retrocompatibilidade

    def init_app(self, app):
        from app.config import is_configured
        self.app = app
        if is_configured():
            self.reload_connections()

    def reload_connections(self):
        """Recarrega as conexões e atualiza as referências dos objetos principais."""
        success, message = self.conn.reload()
        if success:
            self.plex = self.conn.plex
            self.account = self.conn.account
            self.users.invalidate_user_cache()
            if self.app:
                from app.config import load_or_create_config
                self.app.config.update(load_or_create_config())
        return success, message

    # --- Métodos de Fachada ---
    # Estes métodos delegam as chamadas para o sub-gestor apropriado.
    
    def get_active_sessions(self):
        return self.conn.get_active_sessions()

    def get_libraries(self):
        return self.conn.get_libraries()

    def get_all_plex_users(self, force_refresh=False):
        return self.users.get_all_plex_users(force_refresh)

    def get_user_libraries(self, email):
        return self.users.get_user_libraries(email)

    def update_user_libraries(self, email, library_titles):
        return self.users.update_user_libraries(email, library_titles)

    def remove_user(self, email):
        return self.users.remove_user(email)

    def toggle_overseerr_access(self, email, username, access: bool):
        return self.users.toggle_overseerr_access(email, username, access)

    def create_invitation(self, **kwargs):
        return self.invites.create_invitation(**kwargs)

    def get_invitation_by_code(self, code):
        return self.invites.get_invitation_by_code(code)

    def claim_invitation(self, code, plex_user_account):
        return self.invites.claim_invitation(code, plex_user_account)

    def list_invitations(self):
        return self.invites.list_invitations()

    def delete_invitation(self, code):
        return self.invites.delete_invitation(code)

    def renew_subscription(self, username, months_to_add, base_mode='today', base_date_str=None, expiration_time_str=None):
        return self.subscriptions.renew_subscription(username, months_to_add, base_mode, base_date_str, expiration_time_str)

    # --- Métodos que permanecem no gestor principal para coordenação ---

    def get_users_to_notify(self):
        # Esta lógica poderia ser movida para um task_manager.py no futuro
        from app.config import load_or_create_config
        from datetime import datetime
        from tzlocal import get_localzone

        users_to_notify = []
        config = load_or_create_config()
        days_to_notify = config.get("DAYS_TO_NOTIFY_EXPIRATION", 0)
        if not days_to_notify > 0: return []
            
        user_expirations = self.data_manager.get_all_user_expirations()
        today = datetime.now(get_localzone()).date()
        
        for username, data in user_expirations.items():
            try:
                if data.get('expiration_date'):
                    exp_date = datetime.fromisoformat(data['expiration_date']).date()
                    days_diff = (exp_date - today).days
                    if 0 <= days_diff < days_to_notify:
                        users_to_notify.append(username)
            except (ValueError, TypeError):
                continue
        return users_to_notify
        
    def get_users_to_remove(self):
        # Esta lógica também poderia ser movida para um task_manager.py
        from app.config import load_or_create_config
        from datetime import datetime
        from tzlocal import get_localzone

        users_to_remove = []
        config = load_or_create_config()
        days_to_remove = config.get("DAYS_TO_REMOVE_BLOCKED_USER", 0)
        if not days_to_remove > 0: return []

        blocked_users_data = {u['username']: u for u in self.data_manager.get_blocked_users()}
        if not blocked_users_data: return []
            
        today = datetime.now(get_localzone()).date()
        for username, block_data in blocked_users_data.items():
            try:
                ref_date = datetime.fromisoformat(block_data.get('blocked_at')).date()
                if (today - ref_date).days >= days_to_remove:
                    users_to_remove.append(username)
            except (ValueError, TypeError, AttributeError):
                continue
        return users_to_remove
