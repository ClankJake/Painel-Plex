# app/services/plex/connection.py
import logging
import base64
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse
from flask_babel import gettext as _
from plexapi.server import PlexServer
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError, RequestException
from flask import url_for

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

class PlexConnectionManager:
    """
    Gere a conexão principal com o servidor Plex e a MyPlexAccount.
    """
    def __init__(self):
        self.plex = None
        self.account = None

    def _create_resilient_session(self):
        """Cria uma sessão de requests com uma estratégia de retentativa automática."""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,  # Número total de retentativas
            backoff_factor=1,  # Fator de espera entre tentativas (ex: 1s, 2s, 4s)
            status_forcelist=[429, 500, 502, 503, 504],  # Códigos HTTP que devem acionar uma retentativa
            allowed_methods=["HEAD", "GET", "OPTIONS"] # Métodos HTTP para os quais a retentativa se aplica
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # CORREÇÃO: Força o fecho da conexão após cada pedido.
        # Isto previne o erro 'DECRYPTION_FAILED_OR_BAD_RECORD_MAC' causado por
        # condições de corrida no socket SSL quando usado com Eventlet/Gunicorn.
        session.headers.update({'Connection': 'close'})
        
        return session

    def reload(self, from_job=False):
        """
        Recarrega a configuração e tenta conectar-se ao servidor Plex.
        """
        if not from_job:
            logger.info(_("A recarregar a conexão com o Plex..."))
        else:
            logger.debug("A estabelecer conexão com o Plex para tarefa agendada...")

        config = load_or_create_config()
        
        # Cria a sessão com retentativas
        resilient_session = self._create_resilient_session()

        try:
            if not all(k in config and config[k] for k in ["PLEX_URL", "PLEX_TOKEN"]):
                raise ValueError(_("Configurações do Plex (URL e Token) não encontradas ou estão vazias."))
            
            # Otimização: Aumentado o timeout para 30 segundos para ser mais tolerante com redes lentas.
            self.plex = PlexServer(config["PLEX_URL"], config["PLEX_TOKEN"], session=resilient_session, timeout=30)
            self.account = self.plex.myPlexAccount()
            # Garante que a conta também usa a sessão resiliente
            self.account._session = resilient_session
            
            if not from_job:
                logger.info(_("Conexão com o Plex recarregada com sucesso."))
            else:
                logger.debug("Conexão com o Plex estabelecida para tarefa agendada.")

            return True, _("Configurações aplicadas e conexões testadas com sucesso.")
        
        except (ConnectTimeout, ReadTimeout, ConnectionError) as e:
            error_message = _(
                "Não foi possível conectar ao servidor Plex em '%(url)s'. A conexão expirou. "
                "Verifique se o servidor Plex está online, a URL e a porta estão corretas, e se um firewall não está a bloquear o acesso.",
                url=config.get("PLEX_URL")
            )
            logger.warning(f"{error_message} Erro original: {e}")
            self.plex = None
            self.account = None
            return False, error_message
        
        except Exception as e:
            logger.warning(_("Não foi possível conectar ao Plex: %(error)s. Verifique as configurações.", error=e))
            self.plex = None
            self.account = None
            return False, _("Falha ao aplicar novas configurações: %(error)s", error=e)

    def get_libraries(self):
        if not self.plex: return []
        try:
            return [{'title': s.title, 'key': s.key} for s in self.plex.library.sections()]
        except Exception as e:
            logger.error(_("Não foi possível obter as bibliotecas do servidor Plex: %(error)s", error=e))
            return []
