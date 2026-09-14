# app/services/media_server/jellyfin/plugins.py

"""Descobrir se um plugin está instalado no servidor Jellyfin.

Dois submanagers precisam do mesmo: o histórico por reprodução
(`playback_reporting.py`) e o limite de telas imposto pelo servidor
(`stream_limit.py`). Cada um responde de outra maneira conforme o plugin
exista ou não, e nenhum pode ficar a perguntá-lo a cada pedido.

⚠️ **Não saber não é o mesmo que não existir.** Só se guarda em cache uma
resposta que o servidor deu mesmo — gravar o "não" de uma falha de rede
deixaria a funcionalidade desligada dez minutos sem razão nenhuma.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from ....extensions import cache
from ....utils.log_formatting import describe

logger = logging.getLogger(__name__)

# Instalar ou remover um plugin obriga a reiniciar o Jellyfin, por isso a
# resposta não muda sozinha entre reinícios.
CACHE_SEGUNDOS = 600

# 🔇 O que já foi ANUNCIADO no log, por chave de cache.
#
# A mensagem "o limite de telas passa a ser imposto pelo servidor" descreve um
# ESTADO, não um acontecimento — e estava a ser escrita a cada vez que a cache
# expirava, ou seja de dez em dez minutos, para sempre. Num log que se lê para
# perceber o que correu mal, uma linha que se repete sem nada ter mudado é
# ruído que empurra para fora do ecrã aquilo que interessa.
#
# ⚠️ Isto vive na MEMÓRIA do processo, e não na cache, de propósito: um painel
# que arranca deve dizer uma vez com que plugins está a contar. Guardado na
# cache (que sobrevive em disco), esse arranque ficava mudo.
_ja_anunciado: Dict[str, bool] = {}


def esquecer_o_que_ja_foi_anunciado() -> None:
    """Faz o próximo anúncio voltar a ser o primeiro.

    Serve aos testes, que partilham o processo entre si: sem isto, o primeiro
    a correr ficava com o anúncio e os seguintes não o veriam — uma falha que
    depende da ORDEM dos testes, que é o pior tipo de falha que há.
    """
    _ja_anunciado.clear()


def plugin_instalado(conn, identificador: str, nome: str, chave_cache: str,
                     mensagem_ao_encontrar: str = "",
                     mensagem_ao_perder: str = "") -> bool:
    """O plugin está instalado?

    `identificador` é o GUID do plugin e `nome` o seu nome — procura-se pelos
    dois porque o GUID é estável e o nome é o que se lê no log de quem for
    depurar isto. Basta um deles bater certo.

    As duas mensagens são escritas no log apenas quando o estado MUDA: o painel
    diz uma vez que passou a contar com o plugin, e uma vez que deixou de poder
    contar com ele.
    """
    if not conn.connected:
        return False

    chave = f"{chave_cache}_{conn.api.base_url}"
    guardado = cache.get(chave)
    if guardado is not None:
        return guardado

    encontrado = _procurar(conn, identificador, nome)
    if encontrado is None:
        # ⚠️ O servidor não respondeu, e não saber não é o mesmo que não
        # existir: tenta-se de novo à próxima, e NÃO se toca no que já foi
        # anunciado — uma falha de rede não pode passar por "o plugin
        # desapareceu".
        return False

    cache.set(chave, encontrado, timeout=CACHE_SEGUNDOS)
    _anunciar_se_mudou(chave, encontrado, mensagem_ao_encontrar, mensagem_ao_perder)
    return encontrado


def _anunciar_se_mudou(chave: str, encontrado: bool,
                       mensagem_ao_encontrar: str, mensagem_ao_perder: str) -> None:
    """Escreve no log só o que é novidade."""
    anterior = _ja_anunciado.get(chave)
    _ja_anunciado[chave] = encontrado

    if encontrado and anterior is not True:
        if mensagem_ao_encontrar:
            logger.info(mensagem_ao_encontrar)
        return

    # ⚠️ Só se anuncia a PERDA a quem já tinha o plugin. Num painel que nunca o
    # teve, `anterior` é None, e dizer que ele "já não está instalado" a cada
    # arranque seria trocar um ruído por outro — a ausência de um plugin
    # opcional não é um acontecimento.
    if not encontrado and anterior is True:
        if mensagem_ao_perder:
            logger.warning(mensagem_ao_perder)
        return

    logger.debug(f"Estado do plugin confirmado sem mudanças ({chave}): {encontrado}.")


def _procurar(conn, identificador: str, nome: str) -> Optional[bool]:
    """True/False conforme o servidor respondeu; None se não respondeu."""
    try:
        plugins = conn.api.get('/Plugins')
    except Exception as e:
        logger.debug(f"Não foi possível listar os plugins do Jellyfin: {describe(e)}")
        return None

    if plugins is None:
        return None

    alvo_id = str(identificador or '').replace('-', '').lower()
    alvo_nome = str(nome or '').lower()
    for plugin in plugins:
        se_id = str(plugin.get('Id') or '').replace('-', '').lower()
        se_nome = str(plugin.get('Name') or '').lower()
        if (alvo_id and se_id == alvo_id) or (alvo_nome and alvo_nome in se_nome):
            return True
    return False
