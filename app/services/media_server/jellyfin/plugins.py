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


def plugin_instalado(conn, identificador: str, nome: str, chave_cache: str,
                     mensagem_ao_encontrar: str = "") -> bool:
    """O plugin está instalado?

    `identificador` é o GUID do plugin e `nome` o seu nome — procura-se pelos
    dois porque o GUID é estável e o nome é o que se lê no log de quem for
    depurar isto. Basta um deles bater certo.
    """
    if not conn.connected:
        return False

    chave = f"{chave_cache}_{conn.api.base_url}"
    guardado = cache.get(chave)
    if guardado is not None:
        return guardado

    encontrado = _procurar(conn, identificador, nome)
    if encontrado is None:
        # O servidor não respondeu: tenta-se de novo à próxima.
        return False

    cache.set(chave, encontrado, timeout=CACHE_SEGUNDOS)
    if encontrado and mensagem_ao_encontrar:
        logger.info(mensagem_ao_encontrar)
    return encontrado


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
