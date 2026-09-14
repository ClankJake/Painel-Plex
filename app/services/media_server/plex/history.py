# app/services/media_server/plex/history.py

"""Histórico de reprodução e aparelhos, lidos do próprio Plex.

Havendo Tautulli, é dele que vêm: ele guarda um registo por reprodução, com a
percentagem vista e o reprodutor, e é esse registo que alimenta também as
estatísticas. Este módulo é quem responde quando o Tautulli **não** está
configurado — o servidor sabe dizer o que foi visto, e uma lista com menos
detalhe é muito melhor do que uma página vazia a dar a entender que a pessoa
nunca viu nada.

O que se perde sem o Tautulli, e porquê (é o que o cartão das Conexões avisa):

- **É mais lento.** O Tautulli responde da base de dados dele, já indexada.
  Aqui cada página é uma pergunta ao servidor de média, que vai ler a
  biblioteca; num servidor grande, sente-se.
- **Não há percentagem.** O Plex só cria uma entrada quando o item é dado por
  VISTO — o que ficou a meio não aparece de todo. Por isso a barra vem a 100%:
  é o que a entrada significa, não uma estimativa.
- **A pesquisa é sobre uma janela.** `/status/sessions/history/all` não aceita
  filtro por título: os únicos parâmetros são `accountID`, `viewedAt`,
  `librarySectionID`, `metadataItemID` e `sort`. Pesquisar obriga a trazer as
  reproduções mais recentes e a filtrar aqui.

⚠️ **O id do DONO não é o mesmo dos dois lados.** Nas contas do servidor
(`/accounts`) o dono é a conta número 1; só as contas partilhadas é que lá
aparecem com o id de plex.tv que o painel guarda. Filtrar o histórico do
administrador pelo id de plex.tv dele devolvia sempre uma lista vazia — sem
erro nenhum, que é o pior dos casos.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask_babel import gettext as _
from plexapi import utils as plexapi_utils

from ....extensions import cache
from ....utils.identity import normalize_user_id, same_user
from ....utils.image_proxy import proxied_image_url
from ....utils.log_formatting import describe

logger = logging.getLogger(__name__)

# Quantas reproduções se trazem para responder a uma pesquisa e para deduzir a
# lista de aparelhos. É o compromisso entre ver longe e não pendurar a página.
JANELA = 500

# Os aparelhos e as contas do servidor mudam pouco, e sem isto seriam duas
# chamadas extra em cada página do histórico.
CACHE_SEGUNDOS = 300


def _data_legivel(viewed_at: Any) -> str:
    """A data de uma entrada no formato que a tabela do histórico já usa."""
    try:
        momento = datetime.fromtimestamp(int(viewed_at), tz=timezone.utc)
    except (TypeError, ValueError):
        return ''
    return momento.strftime('%d/%m/%Y %H:%M')


def _instante(viewed_at: Any) -> float:
    """A mesma data em segundos, que é o que a lista de aparelhos usa."""
    try:
        return float(int(viewed_at))
    except (TypeError, ValueError):
        return 0.0


class PlexHistoryManager:
    """Histórico e aparelhos de um utilizador, direto do servidor."""

    def __init__(self, connection):
        self.conn = connection

    # =========================================================================
    # HISTÓRICO
    # =========================================================================

    def get_watch_history(self, user_id: Any, page: int = 1, length: int = 15,
                          search: str = "") -> Dict[str, Any]:
        """O que este utilizador já viu, do mais recente para o mais antigo."""
        pagina = max(1, int(page or 1))
        tamanho = max(1, int(length or 15))

        conta = self.id_de_conta(user_id)
        if conta is None:
            return {"success": True, "history": [], "pagination": self._paginacao(pagina, 0, tamanho)}

        try:
            aparelhos = self.aparelhos_por_id()
            if search:
                return self._pesquisar(conta, aparelhos, search, pagina, tamanho)

            entradas, total = self.entradas(conta, (pagina - 1) * tamanho, tamanho)
            return {
                "success": True,
                "history": [self._traduzir(entrada, aparelhos) for entrada in entradas],
                "pagination": self._paginacao(pagina, total, tamanho),
            }
        except Exception as e:
            logger.warning(f"O Plex não devolveu o histórico de {user_id}: {describe(e)}")
            return {"success": False, "message": _("Não foi possível obter o histórico.")}

    def _pesquisar(self, conta: str, aparelhos: Dict[str, Dict[str, str]],
                   search: str, pagina: int, tamanho: int) -> Dict[str, Any]:
        """A pesquisa é feita aqui, sobre as reproduções mais recentes.

        O servidor não sabe filtrar o histórico por título (ver o cabeçalho do
        módulo), por isso a alternativa a isto era não ter caixa de pesquisa.
        """
        entradas, _total = self.entradas(conta, 0, JANELA)
        linhas = [self._traduzir(entrada, aparelhos) for entrada in entradas]

        termo = search.strip().casefold()
        encontradas = [
            linha for linha in linhas
            if termo in (linha['title'] or '').casefold()
            or termo in (linha['subtitle'] or '').casefold()
        ]

        inicio = (pagina - 1) * tamanho
        return {
            "success": True,
            "history": encontradas[inicio:inicio + tamanho],
            "pagination": self._paginacao(pagina, len(encontradas), tamanho),
        }

    def entradas(self, conta: Optional[str], inicio: int, quantos: int) -> Tuple[List[Any], int]:
        """Uma página do histórico do servidor, e o total que ele diz existir.

        A paginação vai nos cabeçalhos, que é como o Plex a recebe — é também o
        que a `plexapi` faz por dentro, e o que evita trazer o histórico
        inteiro para mostrar quinze linhas.

        `conta` a None traz o histórico de TODA a gente, que é o que o pódio e
        as recomendações precisam (ver `stats_api.py`). Omitir o `accountID` é
        diferente de mandá-lo vazio: com a chave presente e sem valor, o
        servidor não devolve nada.
        """
        filtros = {'sort': 'viewedAt:desc'}
        if conta is not None:
            filtros['accountID'] = conta

        chave = '/status/sessions/history/all' + plexapi_utils.joinArgs(filtros)
        dados = self.conn.plex.query(chave, headers={
            'X-Plex-Container-Start': str(inicio),
            'X-Plex-Container-Size': str(quantos),
        })
        if dados is None:
            return [], 0

        total = int(dados.attrib.get('totalSize') or dados.attrib.get('size') or 0)
        return list(dados), total

    def _paginacao(self, pagina: int, total: int, tamanho: int) -> Dict[str, int]:
        return {
            "current_page": pagina,
            "total_pages": (total + tamanho - 1) // tamanho if tamanho > 0 else 1,
            "total_records": total,
        }

    def _traduzir(self, entrada: Any, aparelhos: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
        """Uma entrada do histórico na forma que a tabela consome."""
        atributos = entrada.attrib

        if atributos.get('type') == 'episode':
            titulo = atributos.get('grandparentTitle') or atributos.get('title')
            temporada = self._inteiro(atributos.get('parentIndex'))
            episodio = self._inteiro(atributos.get('index'))
            subtitulo = f"S{temporada:02d} · E{episodio:02d} - {atributos.get('title') or ''}"
        else:
            titulo = atributos.get('title')
            subtitulo = str(atributos.get('year') or '')

        aparelho = aparelhos.get(str(atributos.get('deviceID') or ''), {})
        return {
            "title": titulo,
            "subtitle": subtitulo,
            "date": _data_legivel(atributos.get('viewedAt')),
            "player": aparelho.get('name') or '',
            # O Plex só regista o que foi dado por VISTO: a entrada existir já
            # é a informação de que foi visto até ao fim. Um 0 aqui seria uma
            # barra vazia por cima de uma reprodução completa.
            "percent_complete": 100,
            "poster_url": proxied_image_url(self._capa(atributos)),
        }

    @staticmethod
    def _inteiro(valor: Any) -> int:
        try:
            return int(valor)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _capa(atributos: Dict[str, str]) -> Optional[str]:
        """A capa da SÉRIE para um episódio, a do próprio item para um filme.

        É a mesma escolha que o caminho do Tautulli faz (`grandparent_thumb`):
        a miniatura de um episódio é um fotograma, que numa lista não diz nada.
        """
        if atributos.get('type') == 'episode':
            thumb = atributos.get('grandparentThumb') or atributos.get('thumb')
        else:
            thumb = atributos.get('thumb')
        return f"plex:{thumb}" if thumb else None

    # =========================================================================
    # APARELHOS
    # =========================================================================

    def get_user_devices(self, user_id: Any) -> Dict[str, Any]:
        """Os aparelhos que ESTE utilizador usou, deduzidos do histórico.

        É o mesmo caminho que o Tautulli percorre, e pela mesma razão: a API do
        plex.tv só lista os aparelhos do DONO do servidor, por isso os de um
        amigo não estão lá. Quem os sabe ligar a uma pessoa é o histórico.
        """
        conta = self.id_de_conta(user_id)
        if conta is None:
            return {"success": True, "devices": []}

        try:
            entradas, _total = self.entradas(conta, 0, JANELA)
            aparelhos = self.aparelhos_por_id()
        except Exception as e:
            logger.warning(f"O Plex não devolveu os aparelhos de {user_id}: {describe(e)}")
            return {"success": False, "message": _("Não foi possível obter os dispositivos.")}

        vistos: Dict[str, Dict[str, Any]] = {}
        for entrada in entradas:
            identificador = str(entrada.attrib.get('deviceID') or '')
            if not identificador:
                continue

            quando = _instante(entrada.attrib.get('viewedAt'))
            if identificador in vistos and quando <= vistos[identificador]['last_seen']:
                continue

            aparelho = aparelhos.get(identificador, {})
            vistos[identificador] = {
                'player': aparelho.get('name') or _("Desconhecido"),
                'platform': aparelho.get('platform') or '',
                'last_seen': quando,
            }

        return {
            "success": True,
            "devices": sorted(vistos.values(), key=lambda a: a['last_seen'], reverse=True),
        }

    def aparelhos_por_id(self) -> Dict[str, Dict[str, str]]:
        """O nome e a plataforma de cada aparelho que já tocou no servidor.

        O histórico traz só o `deviceID`; é esta lista que lhe dá um nome.
        """
        tabela = self._tabela_em_cache(
            '/devices', 'plex_aparelhos',
            lambda atributos: {
                'name': atributos.get('name') or '',
                'platform': atributos.get('platform') or '',
            },
        )
        # Sem a lista, as linhas ficam sem o nome do aparelho — mas ficam.
        return tabela if tabela is not None else {}

    # =========================================================================
    # CONTAS
    # =========================================================================

    def id_de_conta(self, user_id: Any) -> Optional[str]:
        """O id com que o SERVIDOR marca as reproduções desta pessoa.

        ⚠️ Para as contas partilhadas é o mesmo id de plex.tv que o painel
        guarda; para o DONO não é — no servidor ele é a conta 1. Sem esta
        tradução, o administrador via o seu próprio histórico sempre vazio.
        """
        alvo = normalize_user_id(user_id)
        if alvo is None or not self.conn.plex:
            return None

        contas = self._contas()
        if contas is None:
            # O servidor não soube dizer quais são: usa-se o que o painel tem,
            # que é o que serve para toda a gente menos o dono.
            return alvo

        if any(same_user(identificador, alvo) for identificador in contas):
            return alvo

        dono = getattr(self.conn, 'account', None)
        if dono is not None and same_user(getattr(dono, 'id', None), alvo):
            nome_do_dono = (getattr(dono, 'username', '') or getattr(dono, 'title', '') or '').strip().casefold()
            for identificador, nome in contas.items():
                if nome and nome.strip().casefold() == nome_do_dono:
                    return identificador

        logger.debug(f"O Plex não conhece nenhuma conta com o id {alvo}: histórico vazio.")
        return None

    def ids_do_painel(self) -> Dict[str, str]:
        """`accountID` do servidor → o id que o PAINEL guarda.

        É a tradução de `id_de_conta` ao contrário, e existe pela mesma razão:
        ⚠️ nas contas partilhadas os dois ids coincidem, mas o DONO é a conta
        número 1 no servidor e tem outro id no plex.tv. Sem isto, as
        reproduções do administrador ficavam agrupadas sob o id "1" — e o pódio
        mostrava-o como um estranho, sem nível, sem cara e sem se ligar ao
        perfil dele.
        """
        contas = self._contas()
        if not contas:
            return {}

        tabela = {identificador: identificador for identificador in contas}

        dono = getattr(self.conn, 'account', None)
        id_do_dono = getattr(dono, 'id', None)
        if id_do_dono is None:
            return tabela

        nome_do_dono = (getattr(dono, 'username', '') or getattr(dono, 'title', '') or '').strip().casefold()
        for identificador, nome in contas.items():
            if nome and nome.strip().casefold() == nome_do_dono:
                tabela[identificador] = str(id_do_dono)
                break
        return tabela

    def nomes_das_contas(self) -> Dict[str, str]:
        """`accountID` → nome, para as linhas do histórico terem quem as viu."""
        return self._contas() or {}

    def _contas(self) -> Optional[Dict[str, str]]:
        """As contas do servidor, `id` → nome. None quando não se conseguiu ler."""
        return self._tabela_em_cache(
            '/accounts', 'plex_contas',
            lambda atributos: atributos.get('name') or '',
        )

    def _tabela_em_cache(self, caminho: str, prefixo: str, traduzir) -> Optional[Any]:
        """Lê um índice do servidor indexado pelo `id`, com cache curta.

        ⚠️ **Não saber não é o mesmo que não existir**: uma falha de rede não é
        guardada, ou uma chamada falhada deixava o histórico cinco minutos sem
        nomes de aparelhos (ou sem contas) sem razão nenhuma.
        """
        if not self.conn.plex:
            return None

        chave = f"{prefixo}_{getattr(self.conn.plex, '_baseurl', '')}"
        guardado = cache.get(chave)
        if guardado is not None:
            return guardado

        try:
            dados = self.conn.plex.query(caminho)
        except Exception as e:
            logger.debug(f"Não foi possível ler {caminho} do Plex: {describe(e)}")
            return None

        tabela = {}
        for elemento in dados if dados is not None else []:
            identificador = elemento.attrib.get('id')
            if identificador is None:
                continue
            tabela[str(identificador)] = traduzir(elemento.attrib)

        cache.set(chave, tabela, timeout=CACHE_SEGUNDOS)
        return tabela
