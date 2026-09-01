import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Dict, Any, Optional
from requests.exceptions import RequestException, ConnectionError, Timeout

from flask_babel import gettext as _
from app.config import load_or_create_config
from app.utils.log_formatting import describe

logger = logging.getLogger(__name__)

class TautulliApiClient:
    """Cliente robusto para realizar chamadas diretas e otimizadas à API do Tautulli."""

    def __init__(self):
        self.base_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.is_configured: bool = False
        
        # Inicia uma sessão HTTP partilhada para Connection Pooling
        self.session = self._create_resilient_session()
        self.reload_config()

    def _create_resilient_session(self) -> requests.Session:
        """Cria uma sessão HTTP com reaproveitamento de conexões (Pooling) e Retries."""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        # Permite manter até 10 conexões abertas com o Tautulli para respostas instantâneas
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({'User-Agent': 'PainelPlex/1.0'})
        return session

    def reload_config(self) -> None:
        """Recarrega a configuração do Tautulli a partir da base de dados."""
        config = load_or_create_config()
        self.base_url = config.get("TAUTULLI_URL", "").rstrip('/')
        self.api_key = config.get("TAUTULLI_API_KEY")
        self.is_configured = bool(self.base_url and self.api_key)
        
        if self.is_configured:
            logger.info("Configuração do TautulliApiClient carregada com sucesso.")
        else:
            logger.warning("Configuração do TautulliApiClient ausente ou incompleta.")

    def _make_request(self, params: Dict[str, Any], method: str = 'GET', data: Optional[Any] = None, timeout: int = 10) -> Any:
        """
        Executa uma requisição resiliente para a API do Tautulli.
        """
        if not self.is_configured:
            raise ValueError(_("As configurações do Tautulli (URL, Chave de API) estão incompletas."))

        api_url = f"{self.base_url}/api/v2"
        params['apikey'] = self.api_key

        try:
            if method.upper() == 'GET':
                response = self.session.get(api_url, params=params, timeout=timeout)
            elif method.upper() == 'POST':
                response = self.session.post(api_url, params=params, data=data, timeout=timeout)
            else:
                raise ValueError(f"Método HTTP não suportado: {method}")

            response.raise_for_status()
            
            # Validação segura do JSON (caso o Tautulli devolva uma página HTML de erro 500)
            try:
                response_json = response.json()
            except ValueError:
                raise RequestException(_("O servidor do Tautulli não retornou um JSON válido."))

            api_response = response_json.get("response", {})
            if api_response.get("result") != "success":
                error_message = api_response.get("message", _("Erro desconhecido do Tautulli."))
                raise RequestException(error_message)

            return api_response.get("data")

        except (ConnectionError, Timeout) as e:
            logger.warning(f"Falha de rede ao contactar Tautulli ({params.get('cmd')}): {describe(e)}")
            raise RequestException(_("Falha de comunicação com o servidor Tautulli.")) from e

    # --- MÉTODOS DA API PÚBLICA ---

    def get_notifier_config(self, notifier_id: int) -> Any:
        return self._make_request({"cmd": "get_notifier_config", "notifier_id": notifier_id})

    def set_notifier_config(self, notifier_id: int, config_data: Any) -> Any:
        return self._make_request(params={"cmd": "set_notifier_config", "notifier_id": notifier_id}, method='POST', data=config_data)

    def get_history(self, **kwargs) -> Any:
        params = {"cmd": "get_history", "length": 10000}
        params.update(kwargs)
        # Um timeout maior (20s) é necessário aqui porque histórico extenso pode demorar a gerar no Tautulli
        return self._make_request(params, timeout=20)
        
    def get_recently_added(self, **kwargs) -> Any:
        params = {"cmd": "get_recently_added"}
        params.update(kwargs)
        return self._make_request(params)

    def get_metadata(self, rating_key: str) -> Any:
        return self._make_request({"cmd": "get_metadata", "rating_key": rating_key})

    # --- TESTE E VALIDAÇÃO ---

    @staticmethod
    def test_connection(url: str, api_key: str) -> Dict[str, Any]:
        """Testa uma ligação com credenciais temporárias do ecrã de configurações."""
        if not url or not api_key:
            return {'success': False, 'message': _('A URL e a Chave da API são obrigatórias.')}
            
        try:
            api_url = f"{url.rstrip('/')}/api/v2"
            logger.info(_("A testar a conexão de configuração com o Tautulli em: %(url)s", url=api_url))
            
            # Como é um teste isolado e único, usa requests base sem polling para testar a rota "crua"
            response = requests.get(
                api_url, 
                params={"apikey": api_key, "cmd": "status"}, 
                headers={'User-Agent': 'PainelPlex/1.0'}, 
                timeout=10
            )
            response.raise_for_status()
            
            data = response.json()
            if data.get("response", {}).get("result") == "success":
                logger.info("Ligação de teste com Tautulli bem-sucedida.")
                return {'success': True, 'message': _('Conexão com Tautulli bem-sucedida!')}
            else:
                error_message = data.get("response", {}).get("message", _('As credenciais do Tautulli parecem inválidas.'))
                logger.warning(f"Falha na autenticação do Tautulli: {error_message}")
                return {'success': False, 'message': error_message}
                
        except ConnectionError:
            return {'success': False, 'message': _('Falha de conexão. Verifique a URL e se o servidor Tautulli está online e sem bloqueios de rede.')}
        except Timeout:
            return {'success': False, 'message': _('A conexão expirou. O servidor pode estar sobrecarregado ou offline.')}
        except Exception as e:
            return {'success': False, 'message': str(e)}
