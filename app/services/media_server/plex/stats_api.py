# app/services/media_server/plex/stats_api.py

"""As estatísticas do painel, alimentadas pelo próprio Plex.

O pódio, o XP, as conquistas, as recomendações e o Wrapped saem todos de uma
coisa só: uma lista de REPRODUÇÕES. Quem as agrega (`services/tautulli/
stats_handler.py`) não precisa de saber de onde vieram — só do formato. É a
mesma porta por onde o Jellyfin entrou (`jellyfin/stats_api.py`).

⚠️ **Isto existe porque esconder as estatísticas era a resposta errada.** Num
painel Plex sem Tautulli, o pódio, o XP, as conquistas, as recomendações e o
Wrapped desapareciam do menu — e o histórico e os aparelhos já vinham do
servidor, pela mesma razão pela qual estes podem vir: o Plex SABE o que cada
pessoa viu. O que ele não tem é o detalhe do Tautulli, e isso mede-se; não se
esconde a funcionalidade inteira por causa disso.

O que se perde sem o Tautulli, e que o cartão das Conexões diz:

- **uma reprodução é um item DADO POR VISTO.** O Plex só cria a entrada no fim,
  por isso o que ficou a meio não conta de todo — e o que conta conta inteiro:
  o tempo é a duração do item, não o tempo que a pessoa lá esteve. É a mesma
  aproximação que o Jellyfin sem o plugin faz;
- **é uma janela, não o histórico todo.** Traz-se `LIMITE_DE_LINHAS`
  reproduções, das mais recentes para trás. Num servidor com anos de uso, o
  Wrapped de há três anos não está lá;
- **é mais lento.** Cada pergunta é uma consulta ao servidor, e os géneros, a
  duração e o ano obrigam a uma segunda chamada aos metadados.

⚠️ **O `accountID` do histórico NÃO é o id que o painel guarda.** Nas contas
partilhadas coincidem; o dono do servidor é a conta número 1 e tem outro id no
plex.tv. Sem traduzir, as reproduções do administrador ficavam agrupadas sob o
id "1": no pódio ele aparecia como um estranho, sem nível, sem cara e sem se
ligar ao perfil dele. Quem traduz é `history.ids_do_painel()`.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ....utils.log_formatting import describe

logger = logging.getLogger(__name__)

# O tecto de reproduções que se trazem de uma vez. O pódio e as recomendações
# pedem o histórico do servidor INTEIRO: sem limite, um servidor com anos de uso
# trazia tudo a cada meia hora.
LIMITE_DE_LINHAS = 5000

# Quantos itens se pedem por chamada aos metadados. O Plex aceita vários
# `ratingKey` separados por vírgula, mas um URL não os leva todos.
BLOCO_DE_METADADOS = 100


def _inteiro(valor: Any, omissao: int = 0) -> int:
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return omissao


class PlexStatsApi:
    """Responde como o cliente do Tautulli, com dados do próprio Plex.

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
    def _plex(self):
        return getattr(getattr(self._backend, 'conn', None), 'plex', None)

    @property
    def _history(self):
        return getattr(self._backend, 'history', None)

    @property
    def is_configured(self) -> bool:
        """Há de onde tirar estatísticas?

        Aqui não há nada para o administrador configurar — ou o servidor de
        média está ligado, ou não está.
        """
        return self._plex is not None

    def reload_config(self) -> None:
        """Não há credenciais próprias: a ligação é recarregada noutro sítio."""

    def test_connection(self, url: str = None, api_key: str = None) -> Dict[str, Any]:
        """Não se aplica: a fonte é o próprio servidor, que já foi testado."""
        return {"success": True, "message": ""}

    def image_payload(self, thumb: Optional[str], width: int = 300, height: int = 450) -> Optional[str]:
        """O `<prefixo>:<caminho>` que o proxy de imagens do painel entende.

        A largura e a altura são ignoradas de propósito: o `plex:` entrega o
        caminho ao servidor tal e qual (ver `authorize_image_url`), que é o
        mesmo caminho que o histórico sem Tautulli já usa. Redimensionar
        obrigaria a passar pelo transcodificador de imagem do Plex, e uma capa
        que não carrega é pior do que uma capa maior do que o necessário.
        """
        if not thumb:
            return None
        return f"plex:{thumb}"

    # =========================================================================
    # HISTÓRICO
    # =========================================================================

    def get_history(self, after: Optional[str] = None, user_id: Optional[Any] = None,
                    start: Optional[int] = None, length: Optional[int] = None,
                    search: str = "", **_ignorado) -> Dict[str, Any]:
        """As reproduções, na forma que a agregação consome.

        `after` é uma data (`YYYY-MM-DD`), como no Tautulli — mas o filtro é
        aplicado AQUI: `/status/sessions/history/all` só aceita `accountID`,
        `viewedAt`, `librarySectionID`, `metadataItemID` e `sort`, e uma janela
        ordenada por data serve as duas coisas sem inventar parâmetros.
        """
        if not self.is_configured:
            return {"data": [], "recordsFiltered": 0}

        conta = self._conta_do_servidor(user_id)
        if conta is False:
            # O servidor não conhece esta pessoa: lista vazia, não um erro.
            return {"data": [], "recordsFiltered": 0}

        try:
            entradas, _total = self._history.entradas(conta, 0, LIMITE_DE_LINHAS)
        except Exception as e:
            logger.warning(f"O Plex não devolveu o histórico para as estatísticas: {describe(e)}")
            return {"data": [], "recordsFiltered": 0}

        desde = self._para_instante(after)
        if desde is not None:
            entradas = [e for e in entradas if _inteiro(e.attrib.get('viewedAt')) >= desde]

        linhas = self._traduzir(entradas)

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

    def _conta_do_servidor(self, user_id: Optional[Any]):
        """O `accountID` a pedir ao servidor.

        `None` quer dizer "toda a gente" (é o que o pódio pede); `False` quer
        dizer que o painel conhece esta pessoa e o servidor não — que é
        diferente, e vale uma lista vazia em vez de o histórico de todos.
        """
        if user_id in (None, ''):
            return None
        return self._history.id_de_conta(user_id) or False

    @staticmethod
    def _para_instante(after: Optional[str]) -> Optional[int]:
        if not after:
            return None
        try:
            momento = datetime.strptime(str(after)[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError:
            logger.debug(f"Data de início ilegível no pedido de histórico: {after!r}")
            return None
        return int(momento.timestamp())

    # =========================================================================
    # TRADUÇÃO
    # =========================================================================

    def _traduzir(self, entradas: List[Any]) -> List[Dict[str, Any]]:
        """As entradas do servidor na forma que a agregação do painel consome."""
        if not entradas:
            return []

        aparelhos = self._history.aparelhos_por_id()
        donos = self._history.ids_do_painel()
        nomes = self._history.nomes_das_contas()
        detalhes = self._detalhes([e.attrib.get('ratingKey') for e in entradas])

        linhas = []
        for entrada in entradas:
            atributos = entrada.attrib
            chave = str(atributos.get('ratingKey') or '')
            item = detalhes.get(chave, {})
            episodio = atributos.get('type') == 'episode'

            conta = str(atributos.get('accountID') or '')
            aparelho = aparelhos.get(str(atributos.get('deviceID') or ''), {})

            linhas.append({
                'date': _inteiro(atributos.get('viewedAt')),
                # ⚠️ Segundos do ITEM, não da reprodução: o Plex só regista o
                # que foi dado por visto e não guarda quanto tempo lá esteve.
                'duration': item.get('duracao', 0),
                'media_type': 'episode' if episodio else 'movie',
                'title': atributos.get('title') or '',
                'grandparent_title': atributos.get('grandparentTitle') or '',
                'rating_key': chave,
                'grandparent_rating_key': str(atributos.get('grandparentRatingKey') or ''),
                'year': _inteiro(atributos.get('year') or item.get('ano')),
                'genres': item.get('generos', []),
                'directors': item.get('realizadores', []),
                'platform': aparelho.get('platform') or '',
                'player': aparelho.get('name') or '',
                'thumb': atributos.get('thumb') or '',
                'grandparent_thumb': atributos.get('grandparentThumb') or '',
                'user_id': donos.get(conta, conta),
                'user': nomes.get(conta, ''),
                # A entrada existir já é a informação de que foi visto até ao
                # fim. Um 0 aqui punha uma barra vazia sobre uma reprodução
                # completa — e, pior, o mínimo de percentagem das recomendações
                # deitava fora todas as linhas.
                'percent_complete': 100,
                'media_index': _inteiro(atributos.get('index')),
                'parent_media_index': _inteiro(atributos.get('parentIndex')),
                'parent_title': atributos.get('parentTitle') or '',
                'added_at': item.get('adicionado', 0),
                'watched_status': 1,
            })
        return linhas

    def _detalhes(self, chaves: List[Any]) -> Dict[str, Dict[str, Any]]:
        """A duração, o ano, os géneros e o realizador de cada item.

        Nada disto vem no histórico — ele traz o título e pouco mais. O Plex
        aceita vários `ratingKey` de uma vez, por isso são uma chamada por
        bloco e não uma por linha. Um item apagado da biblioteca não volta
        daqui: a linha conta na mesma, com o que o histórico gravou.
        """
        unicos = [str(c) for c in dict.fromkeys(chaves) if c]
        if not unicos:
            return {}

        detalhes: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(unicos), BLOCO_DE_METADADOS):
            bloco = unicos[i:i + BLOCO_DE_METADADOS]
            try:
                dados = self._plex.query('/library/metadata/' + ','.join(bloco))
            except Exception as e:
                logger.debug(f"Não foi possível obter os metadados de {len(bloco)} itens: {describe(e)}")
                continue

            for elemento in dados if dados is not None else []:
                chave = str(elemento.attrib.get('ratingKey') or '')
                if not chave:
                    continue
                detalhes[chave] = {
                    # O Plex conta em milissegundos; a agregação conta em
                    # segundos, como o Tautulli devolve.
                    'duracao': _inteiro(elemento.attrib.get('duration')) // 1000,
                    'ano': _inteiro(elemento.attrib.get('year')),
                    'adicionado': _inteiro(elemento.attrib.get('addedAt')),
                    'generos': self._etiquetas(elemento, 'Genre'),
                    'realizadores': self._etiquetas(elemento, 'Director')[:3],
                }
        return detalhes

    @staticmethod
    def _etiquetas(elemento: Any, nome: str) -> List[str]:
        return [filho.attrib['tag'] for filho in elemento.findall(nome) if filho.attrib.get('tag')]

    # =========================================================================
    # OUTRAS PERGUNTAS DA AGREGAÇÃO
    # =========================================================================

    def get_recently_added(self, count: int = 50, **_ignorado) -> Dict[str, Any]:
        """O que entrou na biblioteca há pouco tempo."""
        if not self.is_configured:
            return {"recently_added": []}

        try:
            dados = self._plex.query(
                '/library/recentlyAdded',
                headers={'X-Plex-Container-Start': '0',
                         'X-Plex-Container-Size': str(max(1, _inteiro(count, 50)))},
            )
        except Exception as e:
            logger.warning(f"O Plex não devolveu os itens recentes: {describe(e)}")
            return {"recently_added": []}

        recentes = []
        for elemento in dados if dados is not None else []:
            atributos = elemento.attrib
            episodio = atributos.get('type') == 'episode'
            recentes.append({
                'title': atributos.get('title'),
                'year': _inteiro(atributos.get('year')) or None,
                'thumb': atributos.get('thumb') or '',
                'grandparent_thumb': atributos.get('grandparentThumb') or '',
                'added_at': _inteiro(atributos.get('addedAt')),
                'media_type': 'episode' if episodio else 'movie',
                'grandparent_title': atributos.get('grandparentTitle') or '',
                'parent_title': atributos.get('parentTitle') or '',
                'media_index': _inteiro(atributos.get('index')),
                'parent_media_index': _inteiro(atributos.get('parentIndex')),
                'rating_key': str(atributos.get('ratingKey') or ''),
            })
        return {"recently_added": recentes}

    def get_metadata(self, rating_key: Any) -> Dict[str, Any]:
        """Os géneros de um item, que é o que a agregação vem cá buscar.

        É por aqui que um EPISÓDIO ganha os géneros: eles vivem na série, e a
        agregação pede-os pelo `grandparent_rating_key`.
        """
        if not self.is_configured:
            return {}

        chave = str(rating_key or '')
        item = self._detalhes([chave]).get(chave) or {}
        return {
            'genres': item.get('generos', []),
            'year': item.get('ano'),
        }

    def get_metadata_batch(self, rating_keys: List[Any]) -> Dict[str, Dict[str, Any]]:
        """Os mesmos metadados, de vários itens e numa ida só ao servidor.

        ⚡ `_detalhes` sempre soube pedir um bloco inteiro — era o `get_metadata`
        que lhe entregava uma chave de cada vez e deitava fora o resto. As
        recomendações pediam os géneros de quarenta títulos e isso eram quarenta
        `/library/metadata/<k>`, com quem abriu a página à espera da soma de
        todas. Agora são os blocos de `BLOCO_DE_METADADOS` que já existiam.
        """
        if not self.is_configured:
            return {}

        detalhes = self._detalhes([str(chave) for chave in rating_keys if chave])
        return {
            chave: {'genres': item.get('generos', []), 'year': item.get('ano')}
            for chave, item in detalhes.items()
        }


class FonteDeEstatisticasDoPlex:
    """O Tautulli quando está configurado; o próprio Plex quando não está.

    A escolha é feita a CADA pergunta, e não no arranque, porque o Tautulli
    pode ser configurado (ou apagado) na página de Configurações sem reiniciar
    o painel — é a mesma razão pela qual `OverseerrManager.enabled` é uma
    propriedade e não um atributo.

    ⚠️ **O que é do Tautulli continua a ser do Tautulli.** O endereço, a chave
    e o teste de ligação passam sempre para o cliente dele, mesmo quando as
    estatísticas estão a vir do servidor: é deles que vivem o cartão das
    Conexões e o estado do sistema, e sem isso o administrador ficava sem
    maneira nenhuma de o ligar. Quem quiser saber se ELE está ativo pergunta
    `externa_ativa` — `is_configured` responde "há estatísticas", que passou a
    ser outra pergunta.
    """

    def __init__(self, obter_backend, cliente_externo=None):
        from ...tautulli.api_client import TautulliApiClient

        self.externa = cliente_externo if cliente_externo is not None else TautulliApiClient()
        self.servidor = PlexStatsApi(obter_backend)

    @property
    def externa_ativa(self) -> bool:
        return bool(getattr(self.externa, 'is_configured', False))

    @property
    def _fonte(self):
        return self.externa if self.externa_ativa else self.servidor

    @property
    def is_configured(self) -> bool:
        return self.externa_ativa or self.servidor.is_configured

    # --- O que é sempre do Tautulli ---

    @property
    def base_url(self):
        return getattr(self.externa, 'base_url', None)

    @property
    def api_key(self):
        return getattr(self.externa, 'api_key', None)

    def test_connection(self, url, api_key):
        return self.externa.test_connection(url, api_key)

    def reload_config(self) -> None:
        if hasattr(self.externa, 'reload_config'):
            self.externa.reload_config()

    # --- O que muda de fonte ---

    def get_history(self, **kwargs):
        return self._fonte.get_history(**kwargs)

    def get_recently_added(self, **kwargs):
        return self._fonte.get_recently_added(**kwargs)

    def get_metadata(self, rating_key):
        return self._fonte.get_metadata(rating_key)

    def get_metadata_batch(self, rating_keys):
        """Os metadados de vários itens de uma vez, quando a fonte ativa souber.

        ⚠️ O Tautulli não sabe — o `cmd=get_metadata` dele é mesmo por item — e
        por isso a resposta é `None`, que quem chama lê como "pergunta um a um".
        Um dicionário vazio seria dizer que o servidor não conhece nenhum
        daqueles títulos, e as recomendações ficavam caladamente sem géneros.
        """
        em_lote = getattr(self._fonte, 'get_metadata_batch', None)
        return em_lote(rating_keys) if callable(em_lote) else None

    def image_payload(self, thumb, width: int = 300, height: int = 450):
        return self._fonte.image_payload(thumb, width, height)
