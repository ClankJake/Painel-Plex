# app/services/plex/connection.py

import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Tuple, List, Dict, Optional

from flask_babel import gettext as _
from plexapi.server import PlexServer
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError, RequestException

from app.config import load_or_create_config
from ...extensions import cache
from ...utils.log_formatting import describe

logger = logging.getLogger(__name__)

class PlexConnectionManager:
    """
    Gere a conexão principal com o servidor Plex local e a conta Plex.tv (MyPlexAccount).
    """
    def __init__(self):
        self.plex: Optional[PlexServer] = None
        self.account = None

    def _create_resilient_session(self) -> requests.Session:
        """Cria uma sessão HTTP com estratégia de retentativa automática e pool expandido."""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,  # Número total de retentativas
            backoff_factor=1,  # Fator de espera (1s, 2s, 4s)
            status_forcelist=[429, 500, 502, 503, 504],  # Códigos que acionam retentativa
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
        )
        
        # Aumento do pool_connections e pool_maxsize de 10 (padrão) para 50
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=50,
            pool_maxsize=50
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # Previne o erro 'DECRYPTION_FAILED_OR_BAD_RECORD_MAC' causado por 
        # reaproveitamento de sockets SSL em workers assíncronos (Eventlet/Gunicorn).
        session.headers.update({'Connection': 'close'})
        
        return session

    def reload(self, from_job: bool = False) -> Tuple[bool, str]:
        """
        Lê a configuração da BD e tenta estabelecer a conexão com o servidor Plex.
        Retorna um tuplo (sucesso, mensagem_de_status).
        """
        if not from_job:
            logger.info(_("A iniciar conexão com o servidor Plex..."))
        else:
            logger.debug("A verificar conexão com o Plex (Background Job)...")

        config = load_or_create_config()
        
        # 1. Validação prévia de credenciais (Evita exceções desnecessárias)
        plex_url = config.get("PLEX_URL")
        plex_token = config.get("PLEX_TOKEN")
        
        # --- CORREÇÃO: Blindagem contra dados corrompidos (Dicionários ocultos) ---
        if not plex_url or not plex_token or not isinstance(plex_url, str) or not isinstance(plex_token, str):
            logger.warning("Conexão cancelada: URL ou Token do Plex estão vazios ou corrompidos no formato 'dict'.")
            self.plex = None
            self.account = None
            return False, _("As configurações do Plex estão corrompidas. Por favor, cole o seu Plex Token novamente nas configurações e guarde.")
        
        plex_url = plex_url.strip()
        plex_token = plex_token.strip()
        # --- FIM DA CORREÇÃO ---

        resilient_session = self._create_resilient_session()

        try:
            # 2. Conexão ao Servidor Local
            # Timeout de 30s é ideal para lidar com discos em hibernação (spin-up)
            self.plex = PlexServer(plex_url, plex_token, session=resilient_session, timeout=30)
            
            # 3. Conexão à conta Plex.tv (Para gerir convites e amigos)
            self.account = self.plex.myPlexAccount()
            self.account._session = resilient_session  # Injeta a sessão resiliente na lib
            
            # 4. Limpa a cache de bibliotecas se a conexão for bem sucedida
            cache.delete_memoized(self.get_libraries)
            
            if not from_job:
                logger.info(_("Conexão estabelecida com sucesso com o servidor: %(name)s", name=self.plex.friendlyName))
            
            return True, _("Configurações aplicadas e conexão testada com sucesso.")
        
        except (ConnectTimeout, ReadTimeout, ConnectionError) as e:
            error_message = _(
                "Falha na rede ao contactar o Plex em '%(url)s'. "
                "Verifique se o servidor está online e se não há bloqueios de firewall.",
                url=plex_url
            )
            logger.warning(f"{error_message} (Erro: {type(e).__name__})")
            self.plex = None
            self.account = None
            return False, error_message
        
        except Exception as e:
            logger.error(f"Erro ao autenticar no Plex: {describe(e)}")
            self.plex = None
            self.account = None
            return False, _("Falha de autenticação ou configuração inválida: %(error)s", error=str(e))

    @cache.memoize(timeout=3600)  # Cache de 1 hora para evitar chamadas API lentas
    def get_libraries(self) -> List[Dict[str, str]]:
        """
        Obtém a lista de bibliotecas (Secções) do servidor Plex.
        Usa cache de 1 hora, pois as bibliotecas raramente mudam.
        """
        if not self.plex: 
            return []
            
        try:
            # A chamada self.plex.library.sections() faz um pedido HTTP real ao Plex
            libraries = [{'title': s.title, 'key': s.key} for s in self.plex.library.sections()]
            return libraries
        except Exception as e:
            logger.error(f"Erro ao obter a lista de bibliotecas do Plex: {e}")
            return []
