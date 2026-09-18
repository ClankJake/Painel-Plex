# app/services/media_server/jellyfin/stats_api.py

"""As estatísticas do painel, alimentadas pelo Jellyfin.

O pódio, o XP, as conquistas, as recomendações e o Wrapped saem todos de uma
coisa só: uma lista de REPRODUÇÕES. Quem as agrega (`services/tautulli/
stats_handler.py`) não precisa de saber de onde vieram — só do formato. Este
módulo fala essa língua com dados do Jellyfin, e é por isso que as estatísticas
passaram a existir aqui sem se reescrever a agregação.

Há duas fontes, e a diferença entre elas é grande:

- **O plugin Playback Reporting** é o bom caminho: guarda uma linha por
  reprodução, com os SEGUNDOS vistos, o aparelho e o cliente. Ver o mesmo
  episódio três vezes dá três linhas, e o tempo visto é o tempo visto.
- **O núcleo do Jellyfin**, sem o plugin, só sabe o que cada pessoa já VIU:
  uma linha por item, com a data da última vez (`UserData.LastPlayedDate`).
  Não há tempo assistido — usa-se a duração do item, o que assume que quem deu
  o item por visto o viu inteiro. É uma aproximação, e está dito na interface.

⚠️ **O id do utilizador vem em duas grafias.** O `UserId` do plugin é um GUID
com hífenes; o painel guarda-o sem. O pódio junta o histórico aos perfis por
esse id — com as duas grafias misturadas, cada pessoa aparecia duas vezes, ou
nenhuma. Tudo o que sai daqui passa por `chave_de()`.

⚠️ **A data do plugin não traz fuso.** É texto (`2026-09-12 20:23:28`) escrito
pelo SQLite do plugin. Lê-se como UTC, que é o que o resto do painel já faz com
essas linhas (`history.py`) — o importante é não ter duas leituras diferentes
da mesma data no mesmo painel.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ....utils.log_formatting import describe
from .identity import chave_de
from .playback_reporting import (
    TIPOS, JellyfinPlaybackReporting, condicao_de_utilizador,
)

logger = logging.getLogger(__name__)

# Os tipos, já prontos para entrarem num `IN (...)` de SQL.
TIPOS_EM_SQL = ", ".join(f"'{tipo}'" for tipo in TIPOS)

# Os campos que o núcleo só devolve se lhos pedirmos. `People` traz a equipa do
# item (é de lá que sai o realizador favorito).
CAMPOS = 'UserData,Genres,People,ProductionYear,RunTimeTicks,SeriesPrimaryImageTag,DateCreated'

# O tecto de reproduções que se trazem de uma vez. O pódio e as recomendações
# pedem o histórico do servidor inteiro: sem limite, um servidor com anos de
# uso trazia tudo a cada meia hora.
LIMITE_DE_LINHAS = 5000

# O mesmo para o núcleo, mas por utilizador — ali cada linha é um item visto.
LIMITE_POR_UTILIZADOR = 1000


def _epoch(momento: Optional[datetime]) -> int:
    return int(momento.timestamp()) if momento else 0


def _data_do_plugin(texto: Any) -> Optional[datetime]:
    """A data que o SQLite do plugin devolve, em UTC.

    Vem sem fuso (`2026-09-12 20:23:28`). Ver o aviso no topo do módulo.
    """
    limpo = str(texto or '').strip().replace('T', ' ')
    if not limpo:
        return None
    for formato in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(limpo[:26], formato).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.debug(f"Data ilegível vinda do Playback Reporting: {texto!r}")
    return None


class JellyfinStatsApi:
    """Responde como o cliente do Tautulli, com dados do Jellyfin.

    Recebe uma função que devolve o backend em vez do backend em si: quando
    isto é construído (no `create_app`), o servidor de média ainda não existe —
    é a mesma injeção tardia que o resto das dependências circulares usa.
    """

    # A fonte do Tautulli tem endereço e chave próprios; esta não tem — usa a
    # ligação ao servidor de média. Ficam declarados a None de propósito: há
    # quem os leia (o estado da ligação, o proxy de imagens) e um `getattr` em
    # falta dava um AttributeError num sítio que só queria saber se existiam.
    base_url = None
    api_key = None

    def __init__(self, obter_backend):
        self._obter_backend = obter_backend
        self._plugin = None

    # =========================================================================
    # ESTADO
    # =========================================================================

    @property
    def _backend(self):
        try:
            return self._obter_backend()
        except Exception:  # pragma: no cover - defensivo
            return None

    @property
    def _conn(self):
        return getattr(self._backend, 'conn', None)

    @property
    def plugin(self) -> Optional[JellyfinPlaybackReporting]:
        """O leitor do Playback Reporting, criado quando há ligação."""
        ligacao = self._conn
        if ligacao is None:
            return None
        if self._plugin is None or self._plugin.conn is not ligacao:
            self._plugin = JellyfinPlaybackReporting(ligacao)
        return self._plugin

    @property
    def is_configured(self) -> bool:
        """Há de onde tirar estatísticas?

        Aqui não há nada para o administrador configurar — ou o servidor de
        média está ligado, ou não está.
        """
        return bool(getattr(self._conn, 'connected', False))

    def test_connection(self, url: str, api_key: str) -> Dict[str, Any]:
        """Não se aplica: a fonte é o próprio servidor, que já foi testado."""
        return {"success": True, "message": ""}

    def image_payload(self, thumb: Optional[str], width: int = 300, height: int = 450) -> Optional[str]:
        """O `<prefixo>:<caminho>` que o proxy de imagens do painel entende."""
        if not thumb:
            return None
        separador = '&' if '?' in thumb else '?'
        return f"jellyfin:{thumb}{separador}fillWidth={width}&fillHeight={height}"

    # =========================================================================
    # HISTÓRICO
    # =========================================================================

    def get_history(self, after: Optional[str] = None, user_id: Optional[Any] = None,
                    start: Optional[int] = None, length: Optional[int] = None,
                    search: str = "", **_ignorado) -> Dict[str, Any]:
        """As reproduções, na forma que a agregação consome.

        `after` é uma data (`YYYY-MM-DD`), como no Tautulli. Devolve sempre um
        dicionário: quem chama trata a lista vazia como "não viu nada", e um
        erro aqui não pode passar por isso — por isso as falhas sobem.
        """
        if not self.is_configured:
            return {"data": [], "recordsFiltered": 0}

        desde = self._para_data(after)
        reproducoes = self._reproducoes(desde, user_id)
        linhas = self._traduzir(reproducoes)

        if search:
            termo = str(search).strip().casefold()
            linhas = [linha for linha in linhas
                      if termo in (linha.get('title') or '').casefold()
                      or termo in (linha.get('grandparent_title') or '').casefold()]

        total = len(linhas)
        if start is not None or length is not None:
            inicio = int(start or 0)
            fim = inicio + int(length) if length else None
            linhas = linhas[inicio:fim]

        return {"data": linhas, "recordsFiltered": total}

    @staticmethod
    def _para_data(after: Optional[str]) -> Optional[datetime]:
        if not after:
            return None
        try:
            return datetime.strptime(str(after)[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError:
            logger.debug(f"Data de início ilegível no pedido de histórico: {after!r}")
            return None

    def _reproducoes(self, desde: Optional[datetime], user_id: Optional[Any]) -> List[Dict[str, Any]]:
        """As reproduções em bruto, do plugin quando o há e do núcleo quando não."""
        plugin = self.plugin
        if plugin is not None and plugin.esta_disponivel():
            do_plugin = self._do_plugin(desde, user_id)
            if do_plugin is not None:
                return do_plugin
            logger.info("O Playback Reporting não respondeu: as estatísticas voltam ao registo do núcleo.")

        return self._do_nucleo(desde, user_id)

    # --- Fonte 1: o plugin -----------------------------------------------

    def _do_plugin(self, desde: Optional[datetime], user_id: Optional[Any]) -> Optional[List[Dict[str, Any]]]:
        """Uma linha por reprodução, com os segundos que foram mesmo vistos."""
        condicoes = [f"ItemType IN ({TIPOS_EM_SQL})"]

        if desde is not None:
            condicoes.append(f"DateCreated >= '{desde.strftime('%Y-%m-%d')}'")

        if user_id is not None:
            # ⚠️ O plugin pode ter gravado o id com ou sem hífenes, conforme a
            # versão. Comparar só com a forma do painel devolvia zero linhas —
            # e "zero linhas" aqui lê-se como "nunca viu nada".
            condicao = condicao_de_utilizador(user_id)
            if not condicao:
                return []
            condicoes.append(condicao)

        linhas = self.plugin._consultar(
            "SELECT DateCreated, UserId, ItemId, ItemType, ItemName, ClientName, DeviceName, PlayDuration "
            f"FROM PlaybackActivity WHERE {' AND '.join(condicoes)} "
            f"ORDER BY DateCreated DESC LIMIT {LIMITE_DE_LINHAS}"
        )
        if linhas is None:
            return None

        reproducoes = []
        for linha in linhas:
            (data, dono, item_id, tipo, nome, cliente, aparelho, segundos) = (list(linha) + [None] * 8)[:8]
            quando = _data_do_plugin(data)
            if quando is None:
                continue
            reproducoes.append({
                'quando': quando,
                'user_id': chave_de(dono),
                'item_id': str(item_id or ''),
                'tipo': tipo,
                'nome': nome,
                'cliente': cliente or '',
                'aparelho': aparelho or '',
                'segundos': self._inteiro(segundos),
            })
        return reproducoes

    # --- Fonte 2: o núcleo -----------------------------------------------

    def _do_nucleo(self, desde: Optional[datetime], user_id: Optional[Any]) -> List[Dict[str, Any]]:
        """Uma linha por ITEM visto, que é tudo o que o núcleo guarda.

        Sem o plugin não há tempo assistido: conta-se a duração do item, o que
        assume que quem o deu por visto o viu inteiro.
        """
        reproducoes = []
        for identificador, nome in self._utilizadores(user_id).items():
            for item in self._itens_vistos(identificador):
                dados = item.get('UserData') or {}
                quando = self._data_iso(dados.get('LastPlayedDate'))
                if quando is None or (desde is not None and quando < desde):
                    continue

                reproducoes.append({
                    'quando': quando,
                    'user_id': identificador,
                    'item_id': str(item.get('Id') or ''),
                    'tipo': item.get('Type'),
                    'nome': item.get('Name'),
                    'cliente': '',
                    'aparelho': '',
                    # Segundos do ITEM, não da reprodução (ver o docstring).
                    'segundos': self._inteiro(item.get('RunTimeTicks')) // 10_000_000,
                    'item': item,
                    'username': nome,
                })
        reproducoes.sort(key=lambda r: r['quando'], reverse=True)
        return reproducoes[:LIMITE_DE_LINHAS]

    def _itens_vistos(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            resposta = self._conn.api.get('/Items', params={
                'userId': user_id,
                'Recursive': 'true',
                'IncludeItemTypes': ','.join(TIPOS),
                'Filters': 'IsPlayed',
                'SortBy': 'DatePlayed',
                'SortOrder': 'Descending',
                'Fields': CAMPOS,
                'Limit': LIMITE_POR_UTILIZADOR,
            }) or {}
        except Exception as e:
            logger.debug(f"Não foi possível obter os itens vistos por {user_id}: {describe(e)}")
            return []
        return resposta.get('Items') or []

    @staticmethod
    def _data_iso(texto: Any) -> Optional[datetime]:
        from .history import _para_datetime

        momento = _para_datetime(texto)
        if momento is None:
            return None
        return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)

    # =========================================================================
    # TRADUÇÃO
    # =========================================================================

    def _utilizadores(self, user_id: Optional[Any] = None) -> Dict[str, str]:
        """`id` → nome. Um só quando se pede um só."""
        try:
            utilizadores = self._backend.users.list_users() or []
        except Exception as e:
            logger.debug(f"Não foi possível listar os utilizadores do Jellyfin: {describe(e)}")
            return {}

        nomes = {chave_de(u.get('id')): u.get('username') or '' for u in utilizadores if u.get('id')}
        if user_id is None:
            return nomes

        alvo = chave_de(user_id)
        return {alvo: nomes.get(alvo, '')} if alvo else {}

    def _traduzir(self, reproducoes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """As reproduções na forma que a agregação do painel consome."""
        if not reproducoes:
            return []

        nomes = self._utilizadores()
        detalhes = self._detalhes([r['item_id'] for r in reproducoes if not r.get('item')])

        linhas = []
        for reproducao in reproducoes:
            item = reproducao.get('item') or detalhes.get(reproducao['item_id']) or {}
            episodio = (item.get('Type') or reproducao.get('tipo')) == 'Episode'
            duracao_item = self._inteiro(item.get('RunTimeTicks')) // 10_000_000

            linhas.append({
                'date': _epoch(reproducao['quando']),
                'duration': reproducao['segundos'],
                'media_type': 'episode' if episodio else 'movie',
                'title': item.get('Name') or self._titulo_gravado(reproducao),
                'grandparent_title': item.get('SeriesName') or '',
                'rating_key': reproducao['item_id'],
                'grandparent_rating_key': str(item.get('SeriesId') or ''),
                'year': item.get('ProductionYear') or 0,
                'genres': [str(g) for g in (item.get('Genres') or [])],
                'directors': self._realizadores(item),
                'platform': reproducao.get('cliente') or '',
                'player': reproducao.get('aparelho') or '',
                'thumb': self._capa(item),
                'grandparent_thumb': self._capa_da_serie(item),
                'user_id': reproducao['user_id'],
                'user': reproducao.get('username') or nomes.get(reproducao['user_id']) or '',
                'percent_complete': self._percentagem(reproducao['segundos'], duracao_item, item),
                'media_index': item.get('IndexNumber') or 0,
                'parent_media_index': item.get('ParentIndexNumber') or 0,
                'parent_title': item.get('SeasonName') or '',
                'added_at': _epoch(self._data_iso(item.get('DateCreated'))),
                'watched_status': 1,
            })
        return linhas

    @staticmethod
    def _titulo_gravado(reproducao: Dict[str, Any]) -> str:
        """O nome que o plugin guardou, para quando o item já não existe.

        Nos episódios ele grava "Série - S01E02 - Título"; fica só a série, que
        é o que a agregação conta.
        """
        texto = str(reproducao.get('nome') or '').strip()
        if reproducao.get('tipo') == 'Episode' and ' - ' in texto:
            return texto.split(' - ', 1)[0]
        return texto

    def _detalhes(self, ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Os metadados dos itens, numa chamada só.

        O plugin guarda o id e pouco mais: os géneros, o ano e a capa vêm daqui.
        Um item apagado da biblioteca não volta — a linha conta na mesma, com o
        nome que o plugin gravou.
        """
        unicos = [i for i in dict.fromkeys(ids) if i]
        if not unicos:
            return {}

        detalhes = {}
        # Em blocos: um servidor com anos de histórico tem milhares de ids, e
        # um URL não os leva todos.
        for bloco in (unicos[i:i + 200] for i in range(0, len(unicos), 200)):
            try:
                resposta = self._conn.api.get('/Items', params={
                    'ids': bloco, 'Fields': CAMPOS, 'Recursive': 'true',
                }) or {}
            except Exception as e:
                logger.debug(f"Não foi possível obter os metadados de {len(bloco)} itens: {describe(e)}")
                continue
            for item in (resposta.get('Items') or []):
                detalhes[str(item.get('Id'))] = item
        return detalhes

    @staticmethod
    def _realizadores(item: Dict[str, Any]) -> List[str]:
        """Quem realizou, para a conquista do realizador favorito."""
        pessoas = item.get('People') or []
        return [p.get('Name') for p in pessoas if p.get('Type') == 'Director' and p.get('Name')][:3]

    @staticmethod
    def _capa(item: Dict[str, Any]) -> Optional[str]:
        etiqueta = (item.get('ImageTags') or {}).get('Primary')
        if not item.get('Id') or not etiqueta:
            return None
        return f"/Items/{item['Id']}/Images/Primary?tag={etiqueta}"

    @staticmethod
    def _capa_da_serie(item: Dict[str, Any]) -> Optional[str]:
        """A capa da SÉRIE — a miniatura de um episódio é um fotograma."""
        if not item.get('SeriesId') or not item.get('SeriesPrimaryImageTag'):
            return None
        return f"/Items/{item['SeriesId']}/Images/Primary?tag={item['SeriesPrimaryImageTag']}"

    def _percentagem(self, segundos: int, duracao_item: int, item: Dict[str, Any]) -> int:
        """Quanto do item foi visto.

        Com o plugin, é o tempo visto sobre a duração. Sem ele, o item está
        marcado como visto — e isso são 100%, não a percentagem parada no
        `UserData`, que só vem preenchida a meio de uma reprodução.
        """
        if duracao_item > 0 and segundos > 0:
            return max(0, min(100, int(segundos * 100 / duracao_item)))
        if (item.get('UserData') or {}).get('Played'):
            return 100
        return 0

    @staticmethod
    def _inteiro(valor: Any) -> int:
        try:
            return int(float(valor or 0))
        except (TypeError, ValueError):
            return 0

    # =========================================================================
    # OUTRAS PERGUNTAS DA AGREGAÇÃO
    # =========================================================================

    def get_recently_added(self, count: int = 50, **_ignorado) -> Dict[str, Any]:
        """O que entrou na biblioteca há pouco tempo."""
        if not self.is_configured:
            return {"recently_added": []}

        try:
            resposta = self._conn.api.get('/Items', params={
                'Recursive': 'true',
                'IncludeItemTypes': ','.join(TIPOS),
                'SortBy': 'DateCreated',
                'SortOrder': 'Descending',
                'Limit': max(1, int(count or 50)),
                'Fields': CAMPOS,
            }) or {}
        except Exception as e:
            logger.warning(f"O Jellyfin não devolveu os itens recentes: {describe(e)}")
            return {"recently_added": []}

        recentes = []
        for item in (resposta.get('Items') or []):
            episodio = item.get('Type') == 'Episode'
            recentes.append({
                'title': item.get('Name'),
                'year': item.get('ProductionYear'),
                'thumb': self._capa_da_serie(item) if episodio else self._capa(item),
                'added_at': _epoch(self._data_iso(item.get('DateCreated'))),
                'media_type': 'episode' if episodio else 'movie',
                'grandparent_title': item.get('SeriesName') or '',
                'parent_title': item.get('SeasonName') or '',
                'media_index': item.get('IndexNumber') or 0,
                'parent_media_index': item.get('ParentIndexNumber') or 0,
                'rating_key': str(item.get('Id') or ''),
            })
        return {"recently_added": recentes}

    def get_metadata(self, rating_key: Any) -> Dict[str, Any]:
        """Os géneros de um item, que é o que a agregação vem cá buscar."""
        detalhes = self._detalhes([str(rating_key or '')])
        item = detalhes.get(str(rating_key or '')) or {}
        return self._metadados_do_item(item)

    def get_metadata_batch(self, rating_keys: List[Any]) -> Dict[str, Dict[str, Any]]:
        """Os mesmos metadados, de vários itens e numa ida só ao servidor.

        ⚡ `_detalhes` sempre soube pedir até 200 ids de uma vez — era o
        `get_metadata` que lhe entregava um e deitava fora o bloco. As
        recomendações pediam os géneros de quarenta títulos e isso eram quarenta
        `GET /Items`, com quem abriu a página à espera da soma de todos.
        """
        detalhes = self._detalhes([str(chave) for chave in rating_keys if chave])
        return {chave: self._metadados_do_item(item) for chave, item in detalhes.items()}

    @staticmethod
    def _metadados_do_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """O que a agregação vem cá buscar, seja o pedido de um item ou de muitos."""
        return {
            'genres': [str(g) for g in (item.get('Genres') or [])],
            'title': item.get('Name'),
            'year': item.get('ProductionYear'),
        }
