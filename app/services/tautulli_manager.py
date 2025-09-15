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

    @cache.cached(timeout=300, key_prefix='user_details_%(username)s_%(days)s')
    def get_user_watch_details(self, username, days=7, current_user=None):
        logger.debug(f"Buscando detalhes de visualização (cache miss) para '{username}' e '{days}' dias.")
        return self.stats.get_user_watch_details(username, days, current_user)

    def get_user_watch_history(self, username, page=1, length=25, search=""):
        # O histórico paginado e com pesquisa não é um bom candidato para um cache simples.
        # Mantemos a chamada direta para garantir dados sempre atualizados.
        return self.stats.get_user_watch_history(username, page, length, search)

    @cache.cached(timeout=300, key_prefix='recently_added_%(days)s')
    def get_recently_added(self, days=7):
        logger.debug(f"Buscando itens adicionados recentemente (cache miss) para '{days}' dias.")
        return self.stats.get_recently_added(days)

    @cache.cached(timeout=300, key_prefix='user_devices_%(username)s')
    def get_user_devices(self, username):
        logger.debug(f"Buscando dispositivos do utilizador (cache miss) para '{username}'.")
        return self.stats.get_user_devices(username)
