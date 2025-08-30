# app/services/tautulli_manager.py

import logging
from flask_babel import gettext as _

from .tautulli.api_client import TautulliApiClient
from .tautulli.stats_handler import StatsHandler

logger = logging.getLogger(__name__)

class TautulliManager:
    """
    Atua como uma fachada para os serviços do Tautulli, agora focado
    exclusivamente em estatísticas.
    """
    def __init__(self, data_manager):
        self.api_client = TautulliApiClient()
        self.stats = StatsHandler(self.api_client, data_manager)
        self.data_manager = data_manager

    def reload_credentials(self):
        """Recarrega as credenciais e configurações para o Tautulli."""
        logger.info("A recarregar as credenciais do Tautulli Manager...")
        self.api_client.reload_config()

    def check_status(self):
        """Verifica o estado da conexão com o Tautulli."""
        if not self.api_client.is_configured:
            return {"status": "DISABLED", "message": _("Não configurado ou desativado.")}
        
        test_result = self.api_client.test_connection(self.api_client.base_url, self.api_client.api_key)
        if test_result['success']:
            return {"status": "ONLINE", "message": _("Conectado com sucesso.")}
        else:
            return {"status": "OFFLINE", "message": test_result['message']}

    def test_connection(self, url, api_key):
        return self.api_client.test_connection(url, api_key)

    # --- MÉTODOS DE ESTATÍSTICAS (DELEGADOS) ---
    def get_watch_stats(self, days=7, plex_users_info=None):
        return self.stats.get_watch_stats(days, plex_users_info)

    def get_user_watch_details(self, username, days=7, current_user=None):
        return self.stats.get_user_watch_details(username, days, current_user)

    def get_user_watch_history(self, username, page=1, length=25, search=""):
        return self.stats.get_user_watch_history(username, page, length, search)

    def get_recently_added(self, days=7):
        return self.stats.get_recently_added(days)

    def get_user_devices(self, username):
        return self.stats.get_user_devices(username)
