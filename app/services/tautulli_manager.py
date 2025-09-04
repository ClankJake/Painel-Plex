# app/services/tautulli_manager.py

import logging
from flask_babel import gettext as _
from datetime import datetime, timedelta

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
        # --- MELHORIA: Cache em memória para estatísticas ---
        self._stats_cache = {}
        self._stats_cache_time = None
        self._cache_ttl = timedelta(minutes=5) # Cache válido por 5 minutos

    def _is_cache_valid(self, key):
        """Verifica se a cache para uma chave específica é válida."""
        if not self._stats_cache_time or (datetime.now() - self._stats_cache_time > self._cache_ttl):
            return False
        return key in self._stats_cache

    def invalidate_stats_cache(self):
        """Invalida a cache de estatísticas."""
        self._stats_cache = {}
        self._stats_cache_time = None
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
    def get_watch_stats(self, days=7, plex_users_info=None):
        cache_key = f"watch_stats_{days}"
        if self._is_cache_valid(cache_key):
            logger.debug(f"A devolver estatísticas de visualização da cache para '{days}' dias.")
            # Atualiza os 'thumbs' na cache, pois podem mudar
            cached_data = self._stats_cache[cache_key]
            if plex_users_info and cached_data.get('success'):
                 for stat in cached_data.get('stats', []):
                     stat['thumb'] = plex_users_info.get(stat.get('original_username', stat['username']), None)
            return cached_data

        result = self.stats.get_watch_stats(days, plex_users_info)
        if result.get("success"):
            if not self._stats_cache_time or (datetime.now() - self._stats_cache_time > self._cache_ttl):
                self.invalidate_stats_cache() # Limpa a cache se expirou
            self._stats_cache[cache_key] = result
            self._stats_cache_time = datetime.now()
        return result

    def get_user_watch_details(self, username, days=7, current_user=None):
        cache_key = f"user_details_{username}_{days}"
        if self._is_cache_valid(cache_key):
            logger.debug(f"A devolver detalhes de visualização da cache para '{username}' e '{days}' dias.")
            return self._stats_cache[cache_key]

        result = self.stats.get_user_watch_details(username, days, current_user)
        if result.get("success"):
            if not self._stats_cache_time or (datetime.now() - self._stats_cache_time > self._cache_ttl):
                self.invalidate_stats_cache()
            self._stats_cache[cache_key] = result
            self._stats_cache_time = datetime.now()
        return result

    def get_user_watch_history(self, username, page=1, length=25, search=""):
        # O histórico paginado e com pesquisa não é um bom candidato para um cache simples.
        # Mantemos a chamada direta para garantir dados sempre atualizados.
        return self.stats.get_user_watch_history(username, page, length, search)

    def get_recently_added(self, days=7):
        cache_key = f"recently_added_{days}"
        if self._is_cache_valid(cache_key):
            logger.debug(f"A devolver itens adicionados recentemente da cache para '{days}' dias.")
            return self._stats_cache[cache_key]
        
        result = self.stats.get_recently_added(days)
        if result.get("success"):
            if not self._stats_cache_time or (datetime.now() - self._stats_cache_time > self._cache_ttl):
                self.invalidate_stats_cache()
            self._stats_cache[cache_key] = result
            self._stats_cache_time = datetime.now()
        return result

    def get_user_devices(self, username):
        cache_key = f"user_devices_{username}"
        if self._is_cache_valid(cache_key):
            logger.debug(f"A devolver dispositivos do utilizador da cache para '{username}'.")
            return self._stats_cache[cache_key]

        result = self.stats.get_user_devices(username)
        if result.get("success"):
            if not self._stats_cache_time or (datetime.now() - self._stats_cache_time > self._cache_ttl):
                self.invalidate_stats_cache()
            self._stats_cache[cache_key] = result
            self._stats_cache_time = datetime.now()
        return result
