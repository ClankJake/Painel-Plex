# app/services/media_server/jellyfin/playback_reporting.py

"""Histórico por REPRODUÇÃO, vindo do plugin Playback Reporting.

O núcleo do Jellyfin só guarda, por utilizador e por item, a data da última
vez que o viu (ver `history.py`). Quem instala o plugin Playback Reporting
passa a ter um registo de cada reprodução — e com ele vem o que faltava: a
mesma mídia vista três vezes dá três linhas, e cada uma diz em que APARELHO
foi vista.

A tabela do plugin é esta, e é só esta:

    PlaybackActivity(DateCreated, UserId, ItemId, ItemType, ItemName,
                     PlaybackMethod, ClientName, DeviceName, PlayDuration)

⚠️ **O plugin não expõe uma rota de listagem paginada.** A única forma de ler
esta tabela como o painel precisa é `POST /user_usage_stats/submit_custom_query`,
que corre SQL cru contra o SQLite do plugin e não aceita parâmetros ligados.
Por isso nada que venha de fora entra na consulta sem passar por
`_literal_sql()` — e o id do utilizador é validado à parte, porque um GUID que
não pareça um GUID não é um engano de escrita, é um ataque.

O plugin guarda quanto tempo se viu (`PlayDuration`, em segundos) mas não a
percentagem. Ela é calculada com a duração do item, que se vai buscar numa
segunda chamada ao núcleo — uma só por página, com todos os ids de uma vez.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from flask_babel import gettext as _

from ....utils.log_formatting import describe
from .api_client import JellyfinApiError
from .plugins import plugin_instalado

logger = logging.getLogger(__name__)

# Como se reconhece o plugin em `GET /Plugins`.
ID_DO_PLUGIN = '5c534381-91a3-43cb-907a-35aa02eb9d2c'
NOME_DO_PLUGIN = 'playback reporting'

# Um GUID do Jellyfin, com ou sem hífenes. Tudo o resto é recusado antes de
# chegar perto do SQL.
GUID_VALIDO = re.compile(r'^[0-9a-fA-F-]{8,64}$')

TIPOS = ("Movie", "Episode")


def _literal_sql(valor: str) -> str:
    """Um texto pronto a entrar numa string literal de SQLite.

    Duplicar a plica é o escape do SQLite (não há barra invertida), e é o que
    fecha a porta a fechar a string e acrescentar comandos. O corte no
    comprimento é a segunda tranca: uma pesquisa não precisa de 300 caracteres.
    """
    limpo = re.sub(r'[\x00-\x1f]', '', str(valor or ''))[:100]
    return limpo.replace("'", "''")


class JellyfinPlaybackReporting:
    """Leitura do registo do plugin, quando ele existe."""

    def __init__(self, connection):
        self.conn = connection

    # =========================================================================
    # DISPONIBILIDADE
    # =========================================================================

    def esta_disponivel(self) -> bool:
        """O plugin está instalado neste servidor? (com cache — ver `plugins.py`)"""
        return plugin_instalado(
            self.conn, ID_DO_PLUGIN, NOME_DO_PLUGIN, 'jellyfin_playback_reporting',
            "Plugin Playback Reporting encontrado: o histórico passa a ser por reprodução.",
        )

    # =========================================================================
    # HISTÓRICO
    # =========================================================================

    def get_watch_history(self, user_id: Any, page: int = 1, length: int = 15,
                          search: str = "") -> Optional[Dict[str, Any]]:
        """As reproduções deste utilizador, da mais recente para a mais antiga.

        Devolve None quando o plugin não pode responder — quem chama volta ao
        histórico por item do núcleo, em vez de mostrar uma página vazia.
        """
        if not GUID_VALIDO.match(str(user_id or '')):
            logger.warning("Identificador de utilizador recusado antes da consulta: %r", user_id)
            return None

        pagina = max(1, int(page or 1))
        tamanho = max(1, int(length or 15))
        onde = self._clausula_where(user_id, search)

        total = self._contar(onde)
        if total is None:
            return None

        linhas = self._consultar(
            "SELECT DateCreated, ItemId, ItemType, ItemName, ClientName, DeviceName, PlayDuration "
            f"FROM PlaybackActivity WHERE {onde} "
            f"ORDER BY DateCreated DESC LIMIT {tamanho} OFFSET {(pagina - 1) * tamanho}"
        )
        if linhas is None:
            return None

        return {
            "success": True,
            "history": self._traduzir(linhas),
            "pagination": {
                "current_page": pagina,
                "total_pages": (total + tamanho - 1) // tamanho if tamanho > 0 else 1,
                "total_records": total,
            },
        }

    def _clausula_where(self, user_id: Any, search: str) -> str:
        tipos = ", ".join(f"'{t}'" for t in TIPOS)
        partes = [f"UserId = '{_literal_sql(user_id)}'", f"ItemType IN ({tipos})"]
        if search:
            partes.append(f"ItemName LIKE '%{_literal_sql(search)}%'")
        return " AND ".join(partes)

    def _contar(self, onde: str) -> Optional[int]:
        linhas = self._consultar(f"SELECT COUNT(*) FROM PlaybackActivity WHERE {onde}")
        if linhas is None:
            return None
        try:
            return int(linhas[0][0])
        except (IndexError, TypeError, ValueError):
            # Uma contagem que não se percebe não pode virar "zero registos":
            # a página diria que a pessoa nunca viu nada.
            return None

    def _consultar(self, sql: str) -> Optional[List[List[Any]]]:
        """Corre uma consulta no plugin. None quando não dá para confiar no resultado."""
        try:
            resposta = self.conn.api.post(
                '/user_usage_stats/submit_custom_query',
                json={'CustomQueryString': sql, 'ReplaceUserId': False},
            ) or {}
        except JellyfinApiError as e:
            # 404 é o plugin ausente; o resto é uma falha a comunicar.
            nivel = logger.debug if e.status_code == 404 else logger.warning
            nivel(f"O Playback Reporting não respondeu à consulta: {describe(e)}")
            return None
        except Exception as e:
            logger.warning(f"Falha ao consultar o Playback Reporting: {describe(e)}")
            return None

        # O plugin devolve o erro do SQLite dentro de uma resposta 200, na
        # chave `message`. Sem olhar para ela, uma consulta inválida passava
        # por uma lista vazia — "este utilizador nunca viu nada".
        mensagem = str(resposta.get('message') or '')
        if mensagem and 'error' in mensagem.lower():
            logger.warning("O Playback Reporting recusou a consulta: %s", mensagem)
            return None

        resultados = resposta.get('results')
        return resultados if isinstance(resultados, list) else None

    # =========================================================================
    # TRADUÇÃO
    # =========================================================================

    def _traduzir(self, linhas: List[List[Any]]) -> List[Dict[str, Any]]:
        from .history import _data_legivel

        detalhes = self._detalhes_dos_itens([linha[1] for linha in linhas if len(linha) > 1])

        historico = []
        for linha in linhas:
            (data, item_id, tipo, nome, cliente, aparelho, duracao) = (list(linha) + [None] * 7)[:7]
            item = detalhes.get(str(item_id or ''), {})

            historico.append({
                "title": item.get('title') or self._titulo(nome, tipo),
                "subtitle": item.get('subtitle') or self._subtitulo(nome, tipo),
                "date": _data_legivel(data),
                # O que o núcleo não sabia dizer: em que aparelho foi visto.
                "player": self._reprodutor(cliente, aparelho),
                "percent_complete": self._percentagem(duracao, item.get('duracao_ms')),
                "poster_url": item.get('poster_url'),
            })
        return historico

    def _reprodutor(self, cliente: Optional[str], aparelho: Optional[str]) -> str:
        """"Jellyfin Android (SM-M236B)" — o cliente diz pouco sozinho."""
        cliente, aparelho = (cliente or '').strip(), (aparelho or '').strip()
        if cliente and aparelho and cliente != aparelho:
            return f"{cliente} ({aparelho})"
        return cliente or aparelho

    def _titulo(self, nome: Optional[str], tipo: Optional[str]) -> str:
        """O nome guardado pelo plugin, quando o item já não existe.

        Para episódios o plugin grava "Série - S01E02 - Título"; a série é o
        que interessa na coluna do título, como no resto do painel.
        """
        texto = (nome or '').strip()
        if tipo == 'Episode' and ' - ' in texto:
            return texto.split(' - ', 1)[0]
        return texto

    def _subtitulo(self, nome: Optional[str], tipo: Optional[str]) -> str:
        texto = (nome or '').strip()
        if tipo == 'Episode' and ' - ' in texto:
            return texto.split(' - ', 1)[1]
        return ''

    def _percentagem(self, duracao_s: Any, duracao_total_ms: Optional[int]) -> int:
        """Quanto do item foi visto NESTA reprodução.

        O plugin guarda os segundos vistos, não a percentagem — e é melhor
        assim: a percentagem do núcleo é a do item, e a mesma para as três
        vezes que se viu o mesmo episódio.
        """
        try:
            vistos = float(duracao_s or 0)
        except (TypeError, ValueError):
            return 0
        if not duracao_total_ms or vistos <= 0:
            return 0
        return max(0, min(100, int(vistos * 1000 * 100 / duracao_total_ms)))

    def _detalhes_dos_itens(self, ids: List[Any]) -> Dict[str, Dict[str, Any]]:
        """Capa, duração e nomes dos itens da página, numa chamada só.

        O plugin só guarda o id e o nome em texto. Um item entretanto apagado
        da biblioteca não volta daqui — e a linha fica na mesma, com o nome que
        o plugin gravou e sem capa.
        """
        from .history import CAMPOS, JellyfinHistoryManager

        unicos = [str(i) for i in dict.fromkeys(ids) if i]
        if not unicos:
            return {}

        try:
            resposta = self.conn.api.get('/Items', params={
                # Uma lista, não um texto com vírgulas: o `requests` repete o
                # parâmetro (`ids=a&ids=b`), que é a forma que o Jellyfin liga
                # a um array sem depender de como separa as vírgulas.
                'ids': unicos,
                'Fields': CAMPOS,
                'Recursive': 'true',
            }) or {}
        except Exception as e:
            logger.debug(f"Não foi possível obter os detalhes dos itens do histórico: {describe(e)}")
            return {}

        tradutor = JellyfinHistoryManager(self.conn)
        detalhes = {}
        for item in (resposta.get('Items') or []):
            traduzido = tradutor._traduzir(item)
            detalhes[str(item.get('Id'))] = {
                'title': traduzido['title'],
                'subtitle': traduzido['subtitle'],
                'poster_url': traduzido['poster_url'],
                'duracao_ms': self._duracao_ms(item.get('RunTimeTicks')),
            }
        return detalhes

    def _duracao_ms(self, ticks: Any) -> int:
        from .sessions import ticks_para_ms

        return ticks_para_ms(ticks)
