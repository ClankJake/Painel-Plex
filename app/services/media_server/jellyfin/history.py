# app/services/media_server/jellyfin/history.py

"""Histórico de reprodução e aparelhos, lidos do próprio Jellyfin.

No Plex estas duas coisas vêm do Tautulli, que guarda um registo de cada
reprodução. O Jellyfin não expõe nada equivalente no núcleo — o registo por
sessão é um plugin (Playback Reporting) — mas guarda, por utilizador e por
item, o que basta para montar um histórico útil sem depender de nada de fora:
`UserData` traz `LastPlayedDate`, `PlayedPercentage`, `PlayCount` e `Played`.

⚠️ **É um histórico por ITEM, não por reprodução.** Ver o mesmo episódio três
vezes dá uma linha, com a data da última. E o Jellyfin não guarda em que
aparelho cada item foi visto, por isso a coluna do reprodutor vem vazia — a
lista de aparelhos responde a essa pergunta por outro caminho
(`GET /Devices`), e esse é melhor do que o do Plex: são os aparelhos REGISTADOS
na conta, não os que se conseguem adivinhar a partir do histórico.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask_babel import gettext as _

from ....utils.image_proxy import proxied_image_url
from ....utils.log_formatting import describe
from .api_client import JellyfinApiError

logger = logging.getLogger(__name__)

# Os campos extra que o `GET /Items` só devolve se lhos pedirmos.
CAMPOS = 'UserData,ProductionYear,SeriesPrimaryImageTag'
TIPOS = 'Movie,Episode'


def _para_datetime(iso: Optional[str]) -> Optional[datetime]:
    """A data que o Jellyfin manda, como `datetime`. None se for ilegível.

    O formato é ISO com `Z` no fim e frações de segundo com SETE casas
    decimais — a precisão dos ticks do .NET. O `fromisoformat` do Python 3.11+
    já tolera as casas a mais (trunca-as), mas o corte fica explícito porque a
    versão anterior levantava `ValueError` e o sintoma seria a data de cada
    linha do histórico a vir vazia, sem erro nenhum.
    """
    if not iso:
        return None

    texto = str(iso).strip().replace('Z', '+00:00')
    if '.' in texto:
        inicio, resto = texto.split('.', 1)
        fracao, fuso = resto, ''
        for marca in ('+', '-'):
            if marca in resto:
                fracao, fuso = resto[:resto.index(marca)], resto[resto.index(marca):]
                break
        texto = f"{inicio}.{fracao[:6]}{fuso}"

    try:
        return datetime.fromisoformat(texto)
    except (ValueError, TypeError):
        logger.debug(f"Data de reprodução ilegível vinda do Jellyfin: {iso!r}")
        return None


def _data_legivel(iso: Optional[str]) -> str:
    """No formato que a tabela do histórico já usa."""
    momento = _para_datetime(iso)
    if momento is None:
        return ''
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(timezone.utc).strftime('%d/%m/%Y %H:%M')


def _instante(iso: Optional[str]) -> float:
    """A mesma data em segundos, que é o que a lista de aparelhos usa."""
    momento = _para_datetime(iso)
    if momento is None:
        return 0.0
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.timestamp()


class JellyfinHistoryManager:
    """Histórico e aparelhos de um utilizador, direto do servidor."""

    def __init__(self, connection):
        self.conn = connection

    # =========================================================================
    # APARELHOS
    # =========================================================================

    def get_user_devices(self, user_id: Any) -> Dict[str, Any]:
        """Os aparelhos registados na conta.

        Devolve a forma que a interface já consome (`player`, `platform`,
        `last_seen`), para a página da conta não precisar de saber de onde veio.
        """
        if not self.conn.connected:
            return {"success": True, "devices": []}

        try:
            resposta = self.conn.api.get('/Devices', params={'userId': str(user_id)})
        except JellyfinApiError as e:
            logger.warning(f"O Jellyfin não devolveu os aparelhos de {user_id}: {describe(e)}")
            return {"success": False, "message": _("Não foi possível obter os aparelhos.")}
        except Exception as e:
            logger.error(f"Erro ao obter os aparelhos de {user_id}: {describe(e)}", exc_info=True)
            return {"success": False, "message": _("Não foi possível obter os aparelhos.")}

        itens = resposta.get('Items') if isinstance(resposta, dict) else resposta
        aparelhos = []
        for bruto in itens or []:
            # `CustomName` é o nome que o administrador deu ao aparelho na
            # interface do Jellyfin; quando existe, é o que a pessoa reconhece.
            aparelhos.append({
                'player': bruto.get('CustomName') or bruto.get('Name') or _("Desconhecido"),
                'platform': bruto.get('AppName') or '',
                'last_seen': _instante(bruto.get('DateLastActivity')),
            })

        aparelhos.sort(key=lambda a: a['last_seen'], reverse=True)
        return {"success": True, "devices": aparelhos}

    # =========================================================================
    # HISTÓRICO
    # =========================================================================

    def get_watch_history(self, user_id: Any, page: int = 1, length: int = 15,
                          search: str = "") -> Dict[str, Any]:
        """O que este utilizador já viu, do mais recente para o mais antigo."""
        if not self.conn.connected:
            return {"success": True, "history": [], "pagination": self._paginacao(page, 0, length)}

        pagina = max(1, int(page or 1))
        tamanho = max(1, int(length or 15))

        params = {
            'userId': str(user_id),
            'Recursive': 'true',
            'IncludeItemTypes': TIPOS,
            'Filters': 'IsPlayed',
            'SortBy': 'DatePlayed',
            'SortOrder': 'Descending',
            'Fields': CAMPOS,
            'StartIndex': (pagina - 1) * tamanho,
            'Limit': tamanho,
        }
        if search:
            params['SearchTerm'] = search

        try:
            resposta = self.conn.api.get('/Items', params=params) or {}
        except JellyfinApiError as e:
            logger.warning(f"O Jellyfin não devolveu o histórico de {user_id}: {describe(e)}")
            return {"success": False, "message": _("Não foi possível obter o histórico.")}
        except Exception as e:
            logger.error(f"Erro ao obter o histórico de {user_id}: {describe(e)}", exc_info=True)
            return {"success": False, "message": _("Não foi possível obter o histórico.")}

        total = int(resposta.get('TotalRecordCount') or 0)
        return {
            "success": True,
            "history": [self._traduzir(item) for item in (resposta.get('Items') or [])],
            "pagination": self._paginacao(pagina, total, tamanho),
        }

    def _paginacao(self, pagina: int, total: int, tamanho: int) -> Dict[str, int]:
        return {
            "current_page": pagina,
            "total_pages": (total + tamanho - 1) // tamanho if tamanho > 0 else 1,
            "total_records": total,
        }

    def _traduzir(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Um item do Jellyfin na forma que a tabela do histórico consome."""
        dados = item.get('UserData') or {}

        if item.get('Type') == 'Episode':
            titulo = item.get('SeriesName') or item.get('Name')
            temporada = item.get('ParentIndexNumber') or 0
            episodio = item.get('IndexNumber') or 0
            subtitulo = f"S{temporada:02d} · E{episodio:02d} - {item.get('Name') or ''}"
        else:
            titulo = item.get('Name')
            subtitulo = str(item.get('ProductionYear') or '')

        return {
            "title": titulo,
            "subtitle": subtitulo,
            "date": _data_legivel(dados.get('LastPlayedDate')),
            # O Jellyfin não guarda o aparelho por item — só a lista de
            # aparelhos da conta o sabe. Vazio é honesto; inventar não.
            "player": "",
            "percent_complete": self._percentagem(dados),
            "poster_url": proxied_image_url(self._capa(item)),
        }

    def _percentagem(self, dados: Dict[str, Any]) -> int:
        """Quanto do item foi visto.

        `PlayedPercentage` só vem preenchido enquanto a reprodução está a meio.
        Um item marcado como visto já não a traz — e aí são 100%, não 0.
        """
        if dados.get('Played'):
            return 100
        try:
            return int(float(dados.get('PlayedPercentage') or 0))
        except (TypeError, ValueError):
            return 0

    def _capa(self, item: Dict[str, Any]) -> Optional[str]:
        """A capa da SÉRIE para um episódio, a do próprio item para um filme.

        É a mesma escolha que o backend do Plex faz (`grandparent_thumb`): a
        miniatura de um episódio é um fotograma, que numa lista não diz nada.
        """
        if item.get('Type') == 'Episode' and item.get('SeriesId') and item.get('SeriesPrimaryImageTag'):
            return f"jellyfin:/Items/{item['SeriesId']}/Images/Primary?tag={item['SeriesPrimaryImageTag']}"

        etiqueta = (item.get('ImageTags') or {}).get('Primary')
        if item.get('Id') and etiqueta:
            return f"jellyfin:/Items/{item['Id']}/Images/Primary?tag={etiqueta}"
        return None
