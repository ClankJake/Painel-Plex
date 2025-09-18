# app/services/tautulli_manager.py

import logging
from flask_babel import gettext as _
from datetime import datetime, timedelta

from .tautulli.api_client import TautulliApiClient
from .tautulli.stats_handler import StatsHandler
from ..extensions import cache # Importa a instância do cache

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

    def invalidate_stats_cache(self):
        """Invalida toda a cache relacionada com o Tautulli."""
        cache.delete_memoized(self.get_watch_stats)
        cache.delete_memoized(self.get_user_watch_details)
        cache.delete_memoized(self.get_recently_added)
        cache.delete_memoized(self.get_user_devices)
        logger.info("Cache de estatísticas do Tautulli invalidado.")

    def reload_credentials(self):
        """Recarrega as credenciais e configurações para o Tautulli."""
        logger.info("A recarregar as credenciais do Tautulli Manager...")
        self.api_client.reload_config()
        self.invalidate_stats_cache() # Invalida a cache ao recarregar

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

    # --- MÉTODOS DE ESTATÍSTICAS (DELEGADOS) COM CACHE ---
    @cache.cached(timeout=300, key_prefix='watch_stats_%(days)s')
    def get_watch_stats(self, days=7, plex_users_info=None):
        logger.debug(f"Buscando estatísticas de visualização (cache miss) para '{days}' dias.")
        return self.stats.get_watch_stats(days, plex_users_info)

    @cache.memoize(timeout=300)
    def get_user_watch_details(self, plex_user_id, days=7, current_user=None):
        logger.debug(f"Buscando detalhes de visualização (cache miss) para ID '{plex_user_id}' e '{days}' dias.")
        
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile or not profile.get('username'):
            logger.warning(f"Não foi possível encontrar o perfil para '{plex_user_id}' ao buscar detalhes de visualização.")
            return {"success": True, "details": {}}

        username = profile.get('username')
        
        return self.stats.get_user_watch_details(plex_user_id, username, days=days, current_user=current_user)

    def get_user_watch_history(self, user_id, page=1, length=25, search=""):
        return self.stats.get_user_watch_history(user_id, page, length, search)

    @cache.cached(timeout=300, key_prefix='recently_added_%(days)s')
    def get_recently_added(self, days=7):
        logger.debug(f"Buscando itens adicionados recentemente (cache miss) para '{days}' dias.")
        return self.stats.get_recently_added(days)

    @cache.cached(timeout=300, key_prefix='user_devices_%(plex_user_id)s')
    def get_user_devices(self, plex_user_id):
        logger.debug(f"Buscando dispositivos do utilizador (cache miss) para o ID '{plex_user_id}'.")
        return self.stats.get_user_devices(plex_user_id)

