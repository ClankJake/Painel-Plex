# app/services/media_server/jellyfin/stream_limit.py

"""Limite de telas imposto pelo SERVIDOR, via o plugin StreamLimiter.

O núcleo do Jellyfin não sabe limitar reproduções simultâneas (o
`MaxActiveSessions` limita autenticações — ver `user_manager.clear_session_limits`),
e a ordem de parar depende de o cliente obedecer, coisa que o leitor integrado
da aplicação Android não faz.

Este plugin resolve isso onde nenhuma das duas resolvia: intercepta o PEDIDO
HTTP da mídia (um `IAsyncResourceFilter`) e recusa-o antes de servir um único
byte. Não há cliente que possa ignorar um 4xx no pedido do próprio ficheiro.

O painel continua a ser quem decide o limite de cada pessoa; o que muda é que
passa a haver quem o faça cumprir de verdade. `update_screen_limit()` empurra o
valor para cá, e o `cleanup_job` repõe o que divergir.

⚠️ **"0" quer dizer coisas diferentes dos dois lados.** No painel, 0 é
ILIMITADO. No plugin, 0 apaga o limite próprio do utilizador e passa a valer o
`DefaultMaxStreams` do servidor — que, se estiver definido, é um limite e não a
ausência dele. Não há como dizer "sem limite para esta pessoa" enquanto existir
um padrão; por isso, nesse caso, avisa-se em vez de fingir que ficou aplicado.
"""

import logging
from typing import Any, Dict, List, Optional

from flask_babel import gettext as _

from ....utils.identity import normalize_user_id
from ....utils.log_formatting import describe
from .api_client import JellyfinApiError
from .identity import chave_de
from .plugins import plugin_instalado

logger = logging.getLogger(__name__)

# Como se reconhece o plugin em `GET /Plugins`.
ID_DO_PLUGIN = 'd98fbe02-daf3-4c09-a832-4b4e1d07326c'
NOME_DO_PLUGIN = 'streamlimiter'


class JellyfinStreamLimit:
    """Escreve no plugin o limite de telas que o painel decidiu."""

    def __init__(self, connection):
        self.conn = connection

    def esta_disponivel(self) -> bool:
        """O plugin está instalado neste servidor? (com cache — ver `plugins.py`)"""
        return plugin_instalado(
            self.conn, ID_DO_PLUGIN, NOME_DO_PLUGIN, 'jellyfin_stream_limiter',
            "Plugin StreamLimiter encontrado: o limite de telas passa a ser imposto pelo servidor.",
        )

    # =========================================================================
    # ESCRITA
    # =========================================================================

    def definir_limite(self, user_id: Any, telas: int) -> bool:
        """Aplica o limite de UM utilizador. False quando não foi aplicado."""
        if not self.esta_disponivel():
            return False

        identificador = normalize_user_id(user_id)
        if not identificador:
            return False

        telas = max(0, int(telas or 0))
        if telas == 0:
            self._avisar_se_ha_padrao(identificador)

        try:
            self.conn.api.post(
                '/StreamLimit/SetUserStreamLimit',
                params={'userId': identificador, 'streamsAllowed': telas},
            )
        except JellyfinApiError as e:
            # 404 é o utilizador não existir no servidor — acontece com um
            # perfil de alguém já removido, e não é um erro do painel.
            nivel = logger.debug if e.status_code == 404 else logger.warning
            nivel(f"O StreamLimiter não aceitou o limite de {identificador}: {describe(e)}")
            return False
        except Exception as e:
            logger.warning(f"Falha ao escrever o limite de {identificador} no StreamLimiter: {describe(e)}")
            return False

        logger.info(
            "Limite de %s tela(s) aplicado no servidor para o utilizador %s.",
            telas or _("sem limite próprio"), identificador,
        )
        return True

    def sincronizar(self, perfis: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Repõe no plugin os limites que divergirem do painel.

        Corre no `cleanup_job`. Serve para dois casos: o plugin foi instalado
        DEPOIS de os limites já estarem definidos no painel (e ninguém os
        voltaria a escrever sozinho), e alguém mexeu neles na página do plugin.
        O painel é a fonte da verdade — o que estiver diferente é reposto.
        """
        if not self.esta_disponivel():
            return {"success": True, "corrigidos": 0}

        atuais = self._limites_atuais()
        if atuais is None:
            return {"success": False, "message": _("O plugin não devolveu os limites."), "corrigidos": 0}

        corrigidos = 0
        for perfil in perfis or []:
            identificador = normalize_user_id(perfil.get('media_user_id'))
            if not identificador:
                continue

            desejado = max(0, int(perfil.get('screen_limit') or 0))
            if atuais.get(chave_de(identificador), 0) == desejado:
                continue
            if self.definir_limite(identificador, desejado):
                corrigidos += 1

        return {"success": True, "corrigidos": corrigidos}

    # =========================================================================
    # LEITURA
    # =========================================================================

    def _resposta_geral(self) -> Optional[Dict[str, Any]]:
        try:
            return self.conn.api.get('/StreamLimit/GetAllStreamLimits') or {}
        except Exception as e:
            logger.debug(f"O StreamLimiter não devolveu os limites: {describe(e)}")
            return None

    def _limites_atuais(self) -> Optional[Dict[str, int]]:
        """Os limites PRÓPRIOS de cada utilizador, por id sem hífenes."""
        resposta = self._resposta_geral()
        if resposta is None:
            return None

        limites = resposta.get('limits')
        if not isinstance(limites, dict):
            return {}

        traduzidos = {}
        for identificador, valor in limites.items():
            try:
                traduzidos[chave_de(identificador)] = int(valor)
            except (TypeError, ValueError):
                continue
        return traduzidos

    def _avisar_se_ha_padrao(self, identificador: str) -> None:
        """Um aviso quando "ilimitado" no painel não vai ser ilimitado lá.

        Escrever 0 apaga o limite próprio e devolve a pessoa ao
        `DefaultMaxStreams` do servidor. Se esse padrão existir, ela fica
        limitada — e o painel a dizer "Ilimitado". É preciso dizê-lo.
        """
        resposta = self._resposta_geral()
        padrao = (resposta or {}).get('defaultMaxStreams') or 0
        try:
            padrao = int(padrao)
        except (TypeError, ValueError):
            return

        if padrao > 0:
            logger.warning(
                "⚠️ O utilizador %s está como ILIMITADO no painel, mas o StreamLimiter tem um "
                "limite padrão de %s tela(s) e não há como o dispensar para uma pessoa em "
                "concreto. Ele vai continuar limitado pelo padrão. Para o padrão não se "
                "aplicar a ninguém, ponha-o a 0 na página do plugin.",
                identificador, padrao,
            )
