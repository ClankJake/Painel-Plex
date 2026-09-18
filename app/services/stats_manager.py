# app/services/stats_manager.py

import logging
import threading
from typing import Dict, Any, Optional, Set, Union

from flask_babel import gettext as _
from requests.exceptions import RequestException

from .tautulli.api_client import TautulliApiClient
from .tautulli.recommendations_handler import RecommendationsHandler, DEFAULTS as RECOMMENDATION_DEFAULTS
from .tautulli.stats_handler import StatsHandler
from ..config import load_or_create_config
from ..extensions import cache

logger = logging.getLogger(__name__)

# Quanto tempo vale o índice de recomendações, e quanto tempo a CÓPIA ANTERIOR
# dele continua a servir enquanto um novo está a ser construído (ver
# `_construir_indice`). A cópia vive muito mais: ela não é uma segunda cache, é
# a rede por baixo de quem chega no pior momento possível.
VALIDADE_DO_INDICE = 1800
VALIDADE_DA_COPIA_ANTERIOR = 86400


class StatsManager:
    """
    Fachada das estatísticas: pódio, XP, conquistas, recomendações e Wrapped.

    ⚠️ **A agregação é a mesma para todos os servidores; a FONTE é que muda.**
    Tudo isto sai de uma lista de reproduções, e quem a fornece é o
    `api_client` — o Tautulli num painel Plex, o próprio servidor num painel
    Jellyfin (`media_server/jellyfin/stats_api.py`). Foi assim que as
    estatísticas passaram a existir no Jellyfin sem se reescrever uma linha da
    agregação; escrever uma segunda cópia dela era a alternativa, e teriam
    divergido no primeiro ajuste ao XP.

    Uma fonte tem de saber responder a `get_history`, `get_recently_added`,
    `get_metadata` e `image_payload`, e dizer se está `is_configured`.
    """
    def __init__(self, data_manager, api_client=None):
        self.api_client = api_client if api_client is not None else TautulliApiClient()
        self.stats = StatsHandler(self.api_client, data_manager)
        self.recommendations = RecommendationsHandler(self.api_client, data_manager)
        self.data_manager = data_manager
        # ⚡ Um só de cada vez: ver `_construir_indice`. Sob gevent o
        # `monkey.patch_all()` do `run.py` troca isto por um lock cooperativo,
        # por isso quem espera cede a vez em vez de trancar o worker.
        self._indice_em_construcao = threading.Lock()
        # As chaves de cache que o índice já ocupou, para a invalidação as saber
        # apagar: sem `@cache.memoize` não há `delete_memoized` que as encontre,
        # e o `days` não é sempre o mesmo.
        self._chaves_do_indice: Set[str] = set()

    def _nome_do_utilizador(self, media_user_id: Union[int, str]) -> Optional[str]:
        """O nome de quem se vai calcular as estatísticas.

        🐛 O perfil local era a única fonte, e há duas pessoas que não o têm: o
        ADMINISTRADOR, que nunca tem (por desenho — ver o `_autorizar_e_iniciar_sessao`),
        e quem ainda não entrou no painel. Essas ficavam com as estatísticas e o
        Wrapped vazios, e um WARNING no log a dizer "não encontrei o perfil" —
        que não é uma falha, é o normal para o dono do servidor.

        O nome só serve para as notificações de conquistas e para o XP; quem o
        sabe sempre é o servidor de média.
        """
        perfil = self.data_manager.get_user_profile(media_user_id) if self.data_manager else None
        if perfil and perfil.get('username'):
            return perfil['username']

        try:
            from ..extensions import media_server

            utilizador = media_server.get_user_by_id(media_user_id) if media_server else None
        except Exception as e:
            logger.debug(f"O servidor não soube dizer quem é '{media_user_id}': {e}")
            utilizador = None

        return (utilizador or {}).get('username') or None

    def invalidate_stats_cache(self) -> None:
        """Invalida toda a cache relacionada com o Tautulli."""
        cache.delete_memoized(self.get_watch_stats)
        cache.delete_memoized(self.get_user_watch_details)
        cache.delete_memoized(self.get_recently_added)
        cache.delete_memoized(self.get_user_devices)
        self._apagar_o_indice()
        cache.delete_memoized(self._get_recommendations_cached)
        logger.info("Cache de estatísticas do Tautulli invalidado.")

    def invalidate_recommendations_cache(self) -> None:
        """
        Invalida apenas a cache das recomendações.

        Usada quando o administrador muda os parâmetros do motor: sem isto, a
        alteração só teria efeito visível até 30 minutos depois, o que parece
        um bug para quem está a afinar as definições.
        """
        self._apagar_o_indice()
        cache.delete_memoized(self._get_recommendations_cached)
        logger.info("Cache de recomendações invalidada.")

    def reload_credentials(self) -> None:
        """Recarrega as credenciais da fonte, quando ela as tem.

        A fonte do Jellyfin não tem credenciais próprias: usa a ligação ao
        servidor de média, que é recarregada noutro sítio.
        """
        logger.info("A recarregar as credenciais da fonte de estatísticas...")
        if hasattr(self.api_client, 'reload_config'):
            self.api_client.reload_config()
        self.invalidate_stats_cache()  # Invalida a cache ao recarregar
        # O estado de saúde também fica obsoleto: sem isto, o painel continuaria a
        # mostrar "Offline" até 30s depois de o administrador corrigir o URL/chave.
        cache.delete_memoized(self._check_status_cached)

    @property
    def fonte_externa(self):
        """O cliente do serviço À PARTE (o Tautulli), quando a fonte tem um.

        ⚠️ **Não é o mesmo que `api_client`.** Num painel Plex a fonte é um
        despachante: usa o Tautulli quando ele existe e o próprio servidor
        quando não. Quem pergunta pelo ESTADO, pelo endereço ou pelo teste de
        ligação quer o Tautulli — e `api_client.is_configured` passou a
        responder "há estatísticas", que é outra pergunta. Sem esta distinção,
        o estado do sistema mostrava o Tautulli OFFLINE num painel a funcionar
        perfeitamente sem ele.
        """
        return getattr(self.api_client, 'externa', self.api_client)

    def check_status(self) -> Dict[str, str]:
        """
        Verifica o estado da conexão com o Tautulli de forma segura.

        ⚡ Com cache curta (30s), pelo mesmo motivo do Plex: testar a ligação é uma
        chamada de rede real e o painel de saúde pede-a a cada carregamento da
        Dashboard. 30 segundos é curto o suficiente para o estado continuar útil
        em diagnóstico.
        """
        if not getattr(self.fonte_externa, 'is_configured', False):
            return {"status": "DISABLED", "message": _("Não configurado ou desativado.")}

        return self._check_status_cached()

    @cache.memoize(timeout=30)
    def _check_status_cached(self) -> Dict[str, str]:
        try:
            externa = self.fonte_externa
            test_result = externa.test_connection(externa.base_url, externa.api_key)
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
            
        logger.debug(f"Estatísticas: a buscar o pódio (cache miss) para '{days}' dias.")
        return self.stats.get_watch_stats(days, plex_users_info)

    @cache.memoize(timeout=300)
    def get_user_watch_details(self, media_user_id: Union[int, str], days: int = 7) -> Dict[str, Any]:
        """
        Obtém os detalhes aprofundados de um utilizador específico (Gráficos, Géneros, Conquistas, XP/Nível).

        🐛 CORREÇÃO: esta função NÃO recebe mais 'current_user'. Incluir um objeto
        do Flask-Login na chave do @cache.memoize é uma cilada clássica — cada
        pedido carrega uma instância nova do utilizador a partir da base de dados,
        com um endereço de memória diferente, tornando a chave de cache instável e
        imprevisível (o resultado dependia de forma não-determinística de quem
        tinha visto a página primeiro). Agora a chave de cache é só
        (media_user_id, days), estável e correta — os efeitos que dependiam de
        "quem está a ver" (sincronizar XP, notificar conquistas) deixaram de
        depender do utilizador atual: a sincronização de XP corre sempre (é
        barata e já está protegida pela cache de 5 minutos) e as notificações de
        conquistas são sempre enviadas ao verdadeiro dono, independentemente de
        quem tiver despoletado o cálculo.
        """
        if not self.api_client.is_configured:
            return {"success": True, "details": {}}
            
        logger.debug(f"Estatísticas: a buscar os detalhes (cache miss) do ID '{media_user_id}' para '{days}' dias.")

        username = self._nome_do_utilizador(media_user_id)
        if not username:
            # Nem o painel nem o servidor sabem quem é: aí sim, não há o que mostrar.
            logger.warning(f"Nem o painel nem o servidor conhecem o utilizador '{media_user_id}': detalhes vazios.")
            return {"success": True, "details": {}}

        return self.stats.get_user_watch_details(str(media_user_id), username, days=days)

    def get_user_watch_history(self, user_id: Union[int, str], page: int = 1, length: int = 25, search: str = "") -> Dict[str, Any]:
        """Busca o histórico paginado de um utilizador (Sem Cache para permitir pesquisa real-time)."""
        if not self.api_client.is_configured:
            return {"success": True, "history": [], "pagination": {"current_page": 1, "total_pages": 1, "total_records": 0}}
            
        return self.stats.get_user_watch_history(str(user_id), page, length, search)

    @cache.memoize(timeout=300)
    def get_recently_added(self, days: int = 7) -> Dict[str, Any]:
        """Busca o conteúdo adicionado recentemente ao servidor de média."""
        if not self.api_client.is_configured:
            return {"success": True, "media": []}
            
        logger.debug(f"Estatísticas: a buscar os itens adicionados recentemente (cache miss) para '{days}' dias.")
        return self.stats.get_recently_added(days)

    @cache.memoize(timeout=300)
    def get_user_devices(self, media_user_id: Union[int, str]) -> Dict[str, Any]:
        """Busca os dispositivos (players) que o utilizador já usou."""
        if not self.api_client.is_configured:
            return {"success": True, "devices": []}
            
        logger.debug(f"Estatísticas: a buscar os aparelhos (cache miss) do ID '{media_user_id}'.")
        return self.stats.get_user_devices(str(media_user_id))

    # ==========================================
    # PLEX WRAPPED (RETROSPECTIVA ANUAL)
    # ==========================================

    @cache.memoize(timeout=1800)
    def get_wrapped_data(self, media_user_id: Union[int, str], year: Optional[int] = None) -> Dict[str, Any]:
        """
        Retrospectiva anual de um utilizador. Cache mais longa (30 min) do que as
        estatísticas normais porque agrega um ANO inteiro de histórico — é a
        chamada mais pesada do sistema e os dados praticamente não mudam.
        """
        if not self.api_client.is_configured:
            return {"success": True, "has_data": False, "wrapped": None}

        username = self._nome_do_utilizador(media_user_id)
        if not username:
            return {"success": True, "has_data": False, "wrapped": None}

        return self.stats.get_wrapped_data(str(media_user_id), username, year=year)

    # ==========================================
    # RECOMENDAÇÕES ("PORQUE ASSISTIU X...")
    # ==========================================

    def get_recommendation_index(self, days: Optional[int] = None,
                                 permitir_copia_anterior: bool = True) -> Dict[str, Any]:
        """
        Índice de co-visualização do servidor inteiro.

        É deliberadamente independente do utilizador: construí-lo custa uma
        chamada pesada à fonte (mais os metadados dos títulos mais vistos) e o
        resultado serve toda a gente. Com 30 minutos de cache, um servidor com
        100 utilizadores paga esse custo duas vezes por hora, não 100.

        ⚠️ **Não é um `@cache.memoize`, e a diferença é o que está entre a
        cache fria e a página.** O `memoize` não tem tranca nenhuma: à hora a
        que o índice expirava, TODA a gente que tivesse a página aberta o
        reconstruía ao mesmo tempo — cada um com a sua leitura do histórico
        inteiro do servidor, num painel que corre com um worker de propósito.
        Aqui a construção é uma só, e quem chega a meio dela não fica à espera:
        leva a cópia anterior (ver `_construir_indice`).
        """
        chave = self._chave_do_indice(days)
        indice = cache.get(chave)
        if indice is not None:
            return indice
        return self._construir_indice(chave, days, permitir_copia_anterior)

    @staticmethod
    def _chave_do_indice(days: Optional[int]) -> str:
        # `days` a None não é o mesmo que um número: quer dizer "o que estiver
        # no config", e é assim que a rota e a tarefa de aquecimento o pedem.
        return f"recomendacoes:indice:{'config' if days is None else int(days)}"

    def _construir_indice(self, chave: str, days: Optional[int],
                          permitir_copia_anterior: bool) -> Dict[str, Any]:
        """
        Constrói o índice — uma vez, mesmo com vários pedidos a precisarem dele.

        ⚡ **Quem chega com uma construção já a decorrer leva a cópia ANTERIOR.**
        Esperar seria correto e seria péssimo: o custo é uma leitura do
        histórico do servidor inteiro, e a alternativa a recomendações de há
        meia hora é uma página parada. A cópia é guardada por 24 horas
        precisamente para existir neste momento.
        """
        chave_da_copia = f"{chave}:anterior"

        if permitir_copia_anterior and self._indice_em_construcao.locked():
            anterior = cache.get(chave_da_copia)
            if anterior is not None:
                logger.debug("Recomendações: índice a ser construído, a servir a cópia anterior.")
                return anterior

        with self._indice_em_construcao:
            # Pode ter ficado pronto enquanto esperávamos pela vez.
            pronto = cache.get(chave)
            if pronto is not None:
                return pronto

            logger.debug(f"Recomendações: a construir o índice (cache fria) para '{days or 'config'}' dias.")
            indice = self.recommendations.build_index(days=days)

            cache.set(chave, indice, timeout=VALIDADE_DO_INDICE)
            cache.set(chave_da_copia, indice, timeout=VALIDADE_DA_COPIA_ANTERIOR)
            self._chaves_do_indice.update((chave, chave_da_copia))
            return indice

    def _apagar_o_indice(self) -> None:
        """Esquece o índice, a cópia anterior incluída.

        ⚠️ A cópia TAMBÉM tem de sair. Ela existe para ser servida quando o
        índice está a ser refeito, e foi construída com os parâmetros ANTIGOS:
        deixá-la ficar depois de o administrador os mudar era exatamente o bug
        que `invalidate_recommendations_cache` existe para não haver.

        ⚠️ E a chave por omissão é apagada SEMPRE, esteja ou não no registo. A
        cache vive em disco e sobrevive a um reinício; o registo é da memória
        deste processo e nasce vazio — sem isto, mudar as definições logo a
        seguir a reiniciar o painel não apagava nada, que é precisamente quando
        alguém está a afinar o motor e a perguntar-se porque é que não muda.
        """
        padrao = self._chave_do_indice(None)
        for chave in self._chaves_do_indice | {padrao, f"{padrao}:anterior"}:
            cache.delete(chave)
        self._chaves_do_indice.clear()

    @cache.memoize(timeout=900)
    def _get_recommendations_cached(self, media_user_id: str, days: Optional[int] = None) -> Dict[str, Any]:
        """
        Parte com cache do cálculo por utilizador.

        Está separada de propósito: uma falha do Tautulli SOBE daqui como exceção
        em vez de virar um {"success": False} devolvido — assim o @memoize nunca
        chega a guardar o erro. Se guardasse, uma indisponibilidade de um segundo
        deixava o utilizador sem recomendações durante os 15 minutos seguintes.
        """
        index = self.get_recommendation_index(days=days)
        return self.recommendations.recommend(index, media_user_id)

    def get_recommendations(self, media_user_id: Union[int, str], days: Optional[int] = None) -> Dict[str, Any]:
        """Secções "Porque assistiu X, pode gostar de Y" de um utilizador."""
        config = load_or_create_config()
        if not config.get("RECOMMENDATIONS_ENABLED", RECOMMENDATION_DEFAULTS["RECOMMENDATIONS_ENABLED"]):
            return {"success": True, "sections": [], "reason": "disabled"}

        if not self.api_client.is_configured:
            return {"success": True, "sections": [], "reason": "not_configured"}

        try:
            return self._get_recommendations_cached(str(media_user_id), days)
        except RequestException as e:
            logger.warning(f"Recomendações indisponíveis (falha no Tautulli): {e}")
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"Erro inesperado ao gerar recomendações para o ID '{media_user_id}': {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    # ==========================================
    # TEMPORADAS DE XP
    # ==========================================
    def get_season_info(self) -> Optional[Dict[str, Any]]:
        """Informação sobre a temporada de XP atual (sem cache — é leitura barata do config)."""
        return self.stats.get_season_info()

    def reset_season_if_due(self, force: bool = False) -> Dict[str, Any]:
        """Repõe o XP de todos se a temporada terminou (ou se forçado pelo administrador)."""
        result = self.stats.reset_season_if_due(force=force)
        if result.get("reset"):
            # O XP mudou para toda a gente: invalida a cache de estatísticas para
            # que as barras de nível reflitam o reset imediatamente.
            self.invalidate_stats_cache()
        return result
