# app/services/tautulli_manager.py

import logging
from typing import Dict, Any, Optional, Union

from flask_babel import gettext as _

from .tautulli.api_client import TautulliApiClient
from .tautulli.stats_handler import StatsHandler
from ..extensions import cache

logger = logging.getLogger(__name__)

class TautulliManager:
    """
    Atua como uma fachada (Facade) para os serviços do Tautulli, focado
    exclusivamente em estatísticas e gestão do estado da ligação.
    """
    def __init__(self, data_manager):
        self.api_client = TautulliApiClient()
        self.stats = StatsHandler(self.api_client, data_manager)
        self.data_manager = data_manager

    def invalidate_stats_cache(self) -> None:
        """Invalida toda a cache relacionada com o Tautulli."""
        cache.delete_memoized(self.get_watch_stats)
        cache.delete_memoized(self.get_user_watch_details)
        cache.delete_memoized(self.get_recently_added)
        cache.delete_memoized(self.get_user_devices)
        logger.info("Cache de estatísticas do Tautulli invalidado.")

    def reload_credentials(self) -> None:
        """Recarrega as credenciais e configurações para o Tautulli."""
        logger.info("A recarregar as credenciais do Tautulli Manager...")
        self.api_client.reload_config()
        self.invalidate_stats_cache()  # Invalida a cache ao recarregar

    def check_status(self) -> Dict[str, str]:
        """Verifica o estado da conexão com o Tautulli de forma segura."""
        if not self.api_client.is_configured:
            return {"status": "DISABLED", "message": _("Não configurado ou desativado.")}
        
        try:
            test_result = self.api_client.test_connection(self.api_client.base_url, self.api_client.api_key)
            if test_result.get('success'):
                return {"status": "ONLINE", "message": _("Conectado com sucesso.")}
            else:
                return {"status": "OFFLINE", "message": test_result.get('message', _("Falha desconhecida."))}
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar o status do Tautulli: {e}")
            return {"status": "OFFLINE", "message": _("Erro interno ao testar conexão.")}

    def test_connection(self, url: str, api_key: str) -> Dict[str, Any]:
        """Testa uma ligação manual introduzida na interface de configurações."""
        return self.api_client.test_connection(url, api_key)


    # ==========================================
    # MÉTODOS DE ESTATÍSTICAS (DELEGADOS & CACHED)
    # ==========================================

    @cache.memoize(timeout=300)
    def get_watch_stats(self, days: int = 7, plex_users_info: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Obtém estatísticas globais de visualização (Leaderboard)."""
        if not self.api_client.is_configured:
            return {"success": True, "stats": []}
            
        logger.debug(f"Tautulli: A buscar estatísticas globais de visualização (cache miss) para '{days}' dias.")
        return self.stats.get_watch_stats(days, plex_users_info)

    @cache.memoize(timeout=300)
    def get_user_watch_details(self, plex_user_id: Union[int, str], days: int = 7, current_user=None) -> Dict[str, Any]:
        """Obtém os detalhes aprofundados de um utilizador específico (Gráficos, Géneros, Conquistas)."""
        if not self.api_client.is_configured:
            return {"success": True, "details": {}}
            
        logger.debug(f"Tautulli: A buscar detalhes de visualização (cache miss) para ID '{plex_user_id}' e '{days}' dias.")
        
        profile = self.data_manager.get_user_profile(int(plex_user_id))
        if not profile or not profile.get('username'):
            logger.warning(f"Tautulli: Não foi possível encontrar o perfil para o ID '{plex_user_id}'. Detalhes de visualização vazios.")
            return {"success": True, "details": {}}

        username = profile.get('username')
        return self.stats.get_user_watch_details(str(plex_user_id), username, days=days, current_user=current_user)

    def get_user_watch_history(self, user_id: Union[int, str], page: int = 1, length: int = 25, search: str = "") -> Dict[str, Any]:
        """Busca o histórico paginado de um utilizador (Sem Cache para permitir pesquisa real-time)."""
        if not self.api_client.is_configured:
            return {"success": True, "history": [], "pagination": {"current_page": 1, "total_pages": 1, "total_records": 0}}
            
        return self.stats.get_user_watch_history(str(user_id), page, length, search)

    @cache.memoize(timeout=300)
    def get_recently_added(self, days: int = 7) -> Dict[str, Any]:
        """Busca o conteúdo adicionado recentemente ao servidor Plex através do Tautulli."""
        if not self.api_client.is_configured:
            return {"success": True, "media": []}
            
        logger.debug(f"Tautulli: A buscar itens adicionados recentemente (cache miss) para '{days}' dias.")
        return self.stats.get_recently_added(days)

    @cache.memoize(timeout=300)
    def get_user_devices(self, plex_user_id: Union[int, str]) -> Dict[str, Any]:
        """Busca os dispositivos (players) que o utilizador já usou."""
        if not self.api_client.is_configured:
            return {"success": True, "devices": []}
            
        logger.debug(f"Tautulli: A buscar dispositivos do utilizador (cache miss) para o ID '{plex_user_id}'.")
        return self.stats.get_user_devices(str(plex_user_id))
