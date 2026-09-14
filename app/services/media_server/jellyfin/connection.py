# app/services/media_server/jellyfin/connection.py

"""Ligação ao servidor Jellyfin e leitura do catálogo de bibliotecas."""

import logging
from typing import Dict, List, Optional, Tuple

from flask_babel import gettext as _
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError

from ....extensions import cache
from ....utils.log_formatting import describe
from .api_client import JellyfinApiClient, JellyfinApiError

logger = logging.getLogger(__name__)


class JellyfinConnectionManager:
    """Cumpre o contrato `ConnectionBackend` para o Jellyfin.

    Ao contrário do Plex, não há uma "conta" externa: o servidor é a única
    autoridade, e a ligação resume-se a saber se a chave de API responde.
    """

    def __init__(self):
        self.api = JellyfinApiClient()
        self.server_info: Optional[dict] = None

    @property
    def connected(self) -> bool:
        return self.server_info is not None

    def reload(self, from_job: bool = False) -> Tuple[bool, str]:
        if not from_job:
            logger.info("A iniciar conexão com o servidor Jellyfin...")

        self.api.reload_config()
        cache.delete_memoized(self.get_libraries)

        if not self.api.is_configured:
            self.server_info = None
            return False, _("As configurações do Jellyfin (URL, Chave de API) estão incompletas.")

        try:
            self.server_info = self.api.get('/System/Info')
            nome = (self.server_info or {}).get('ServerName', 'Jellyfin')
            if not from_job:
                logger.info(_("Conexão estabelecida com sucesso com o servidor: %(name)s", name=nome))
            return True, _("Configurações aplicadas e conexão testada com sucesso.")

        except (ConnectTimeout, ReadTimeout, ConnectionError) as e:
            self.server_info = None
            mensagem = _(
                "Falha na rede ao contactar o Jellyfin em '%(url)s'. "
                "Verifique se o servidor está online e se não há bloqueios de firewall.",
                url=self.api.base_url,
            )
            logger.warning(f"{mensagem} (Erro: {type(e).__name__})")
            return False, mensagem

        except JellyfinApiError as e:
            self.server_info = None
            # 401/403 é quase sempre uma chave de API revogada ou mal colada:
            # vale a pena dizê-lo em vez de devolver o erro cru.
            if e.status_code in (401, 403):
                return False, _("O Jellyfin recusou a chave de API. Gere uma nova em Painel de Controlo → Chaves de API.")
            return False, str(e)

        except Exception as e:
            self.server_info = None
            logger.error(f"Erro ao ligar ao Jellyfin: {describe(e)}")
            return False, _("Falha de autenticação ou configuração inválida: %(error)s", error=str(e))

    def get_server_identifier(self) -> Optional[str]:
        return (self.server_info or {}).get('Id')

    @cache.memoize(timeout=3600)
    def get_libraries(self) -> List[Dict[str, str]]:
        """As bibliotecas do servidor.

        ⚡ Cache de 1 hora, pelo mesmo motivo do Plex: as bibliotecas quase
        nunca mudam e esta lista é pedida a cada carregamento do painel.

        A 'key' é o ItemId da biblioteca — é isso que a política de um
        utilizador guarda em `EnabledFolders`, e não o nome.
        """
        if not self.connected:
            return []

        try:
            pastas = self.api.get('/Library/VirtualFolders') or []
            return [
                {'title': pasta.get('Name'), 'key': pasta.get('ItemId')}
                for pasta in pastas
                if pasta.get('Name') and pasta.get('ItemId')
            ]
        except Exception as e:
            logger.error(f"Erro ao obter a lista de bibliotecas do Jellyfin: {describe(e)}")
            return []
