# app/services/tautulli/api_client.py
import logging
import requests
from requests.exceptions import RequestException, ConnectionError, Timeout
from flask_babel import gettext as _

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

class TautulliApiClient:
    """Cliente para realizar chamadas diretas à API do Tautulli."""

    def __init__(self):
        self.base_url = None
        self.api_key = None
        self.is_configured = False
        self.reload_config()

    def reload_config(self):
        """Recarrega a configuração do Tautulli a partir do ficheiro."""
        config = load_or_create_config()
        self.base_url = config.get("TAUTULLI_URL", "").rstrip('/')
        self.api_key = config.get("TAUTULLI_API_KEY")
        self.is_configured = bool(self.base_url and self.api_key)
        if self.is_configured:
            logger.info("Configuração do TautulliApiClient (re)carregada com sucesso.")
        else:
            logger.warning("Configuração do TautulliApiClient (re)carregada, mas os dados estão incompletos.")

    def _make_request(self, params, method='GET', data=None, timeout=10):
        """
        Executa uma requisição para a API do Tautulli.

        :param params: Dicionário de parâmetros para a URL da API.
        :param method: Método HTTP (GET ou POST).
        :param data: Dados para o corpo da requisição POST.
        :param timeout: Timeout da requisição em segundos.
        :return: A secção 'data' da resposta da API em caso de sucesso.
        :raises RequestException: Em caso de erro de rede ou HTTP.
        """
        if not self.is_configured:
            raise ValueError(_("As configurações do Tautulli (URL, Chave de API) estão incompletas."))

        api_url = f"{self.base_url}/api/v2"
        params['apikey'] = self.api_key

        if method.upper() == 'GET':
            response = requests.get(api_url, params=params, timeout=timeout)
        elif method.upper() == 'POST':
            response = requests.post(api_url, params=params, data=data, timeout=timeout)
        else:
            raise ValueError(f"Método HTTP não suportado: {method}")

        response.raise_for_status()
        response_json = response.json()

        if response_json.get("response", {}).get("result") != "success":
            error_message = response_json.get("response", {}).get("message", _("Erro desconhecido do Tautulli."))
            raise RequestException(error_message)

        return response_json["response"]["data"]

    def get_notifier_config(self, notifier_id):
        """Busca a configuração de um notificador."""
        params = {"cmd": "get_notifier_config", "notifier_id": notifier_id}
        return self._make_request(params)

    def set_notifier_config(self, notifier_id, config_data):
        """Escreve a configuração de um notificador."""
        params = {"cmd": "set_notifier_config", "notifier_id": notifier_id}
        return self._make_request(params=params, method='POST', data=config_data)

    def get_history(self, **kwargs):
        """Busca o histórico de visualizações."""
        params = {"cmd": "get_history", "length": 10000}
        params.update(kwargs)
        return self._make_request(params, timeout=20)
        
    def get_recently_added(self, **kwargs):
        """Busca os itens adicionados recentemente."""
        params = {"cmd": "get_recently_added"}
        params.update(kwargs)
        return self._make_request(params)

    @staticmethod
    def test_connection(url, api_key):
        """Testa uma conexão com as credenciais fornecidas."""
        if not url or not api_key:
            return {'success': False, 'message': _('URL e Chave da API são obrigatórios.')}
        try:
            api_url = f"{url.rstrip('/')}/api/v2"
            logger.info(_("A testar a conexão com o Tautulli em: %(url)s", url=api_url))
            response = requests.get(api_url, params={"apikey": api_key, "cmd": "status"}, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get("response", {}).get("result") == "success":
                logger.info(_("Conexão com Tautulli bem-sucedida."))
                return {'success': True, 'message': _('Conexão com Tautulli bem-sucedida!')}
            else:
                error_message = data.get("response", {}).get("message", _('Credenciais do Tautulli parecem inválidas.'))
                logger.warning(_("Falha na autenticação com Tautulli: %(error)s", error=error_message))
                return {'success': False, 'message': error_message}
        except ConnectionError:
            return {'success': False, 'message': _('Falha de conexão. Verifique se a URL do Tautulli está correta e se o servidor Tautulli está acessível a partir da rede onde esta aplicação está hospedada.')}
        except Timeout:
            return {'success': False, 'message': _('A conexão com o Tautulli expirou. O servidor pode estar sobrecarregado, offline ou a sua rede pode estar lenta.')}
        except Exception as e:
            return {'success': False, 'message': str(e)}
