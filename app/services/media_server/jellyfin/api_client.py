# app/services/media_server/jellyfin/api_client.py

"""Cliente HTTP para a API do Jellyfin.

Todas as chamadas ao servidor passam por aqui: é o único sítio que sabe como
se autentica (o cabeçalho `Authorization: MediaBrowser ...`), como se compõe o
URL e o que fazer quando a rede falha.
"""

import logging
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from flask_babel import gettext as _

from ....config import load_or_create_config
from ....utils.log_formatting import describe

logger = logging.getLogger(__name__)

# Identificação do painel perante o Jellyfin. Aparece na lista de dispositivos
# do servidor, por isso convém ser reconhecível por quem o administra.
CLIENT_NAME = "Painel"
DEVICE_NAME = "Painel"
DEVICE_ID = "painel-plex"
CLIENT_VERSION = "1.0.0"


class JellyfinApiError(Exception):
    """Falha devolvida pelo Jellyfin (não é uma falha de rede)."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class JellyfinApiClient:
    """Chamadas à API do Jellyfin, com retentativas e pool de ligações."""

    TIMEOUT_SECONDS = 15

    def __init__(self):
        self.base_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.is_configured: bool = False
        self.session = self._create_resilient_session()
        self.reload_config()

    def _create_resilient_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def reload_config(self) -> None:
        config = load_or_create_config()
        url = config.get("JELLYFIN_URL") or ""
        chave = config.get("JELLYFIN_API_KEY") or ""

        # 🛡️ Blindagem contra configuração corrompida, pelo mesmo motivo que no
        # Plex: um valor gravado como dicionário rebentava a ligação com uma
        # exceção pouco clara em vez de um aviso.
        self.base_url = url.strip().rstrip('/') if isinstance(url, str) else ""
        self.api_key = chave.strip() if isinstance(chave, str) else ""
        self.is_configured = bool(self.base_url and self.api_key)

        if self.is_configured:
            logger.info("Configuração do JellyfinApiClient carregada com sucesso.")
        else:
            logger.warning("Configuração do Jellyfin ausente ou incompleta.")

    def _headers(self) -> Dict[str, str]:
        # O Jellyfin aceita a chave no cabeçalho Authorization, no formato
        # declarado pela própria especificação (securityScheme
        # 'CustomAuthentication'). Os restantes campos identificam o painel na
        # lista de dispositivos do servidor.
        autorizacao = (
            f'MediaBrowser Client="{CLIENT_NAME}", Device="{DEVICE_NAME}", '
            f'DeviceId="{DEVICE_ID}", Version="{CLIENT_VERSION}", Token="{self.api_key}"'
        )
        return {
            'Authorization': autorizacao,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }

    def request(self, method: str, endpoint: str, *, params=None, json=None, timeout=None) -> Any:
        """Executa um pedido e devolve o corpo já descodificado.

        Devolve None quando a resposta não tem corpo (204, ou um 200 vazio —
        vários endpoints do Jellyfin respondem assim a um POST bem-sucedido).
        """
        if not self.is_configured:
            raise JellyfinApiError(_("As configurações do Jellyfin (URL, Chave de API) estão incompletas."))

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        resposta = self.session.request(
            method.upper(), url,
            headers=self._headers(),
            params=params,
            json=json,
            timeout=timeout or self.TIMEOUT_SECONDS,
        )

        if resposta.status_code >= 400:
            raise JellyfinApiError(
                _("O Jellyfin recusou o pedido (%(codigo)s): %(detalhe)s",
                  codigo=resposta.status_code, detalhe=(resposta.text or '')[:200]),
                status_code=resposta.status_code,
            )

        if resposta.status_code == 204 or not resposta.content:
            return None

        try:
            return resposta.json()
        except ValueError:
            logger.debug(f"Resposta do Jellyfin sem JSON em {endpoint}: {describe(resposta)}")
            return None

    def get(self, endpoint, **kwargs):
        return self.request('GET', endpoint, **kwargs)

    def post(self, endpoint, **kwargs):
        return self.request('POST', endpoint, **kwargs)

    def delete(self, endpoint, **kwargs):
        return self.request('DELETE', endpoint, **kwargs)


def test_connection(url: str, api_key: str) -> Dict[str, Any]:
    """Testa um par URL + chave de API sem tocar na configuração guardada.

    Usado pelo assistente de instalação e pelo botão "Testar" das definições,
    que precisam de validar credenciais que ainda não foram gravadas.
    """
    cliente = JellyfinApiClient()
    cliente.base_url = (url or '').strip().rstrip('/')
    cliente.api_key = (api_key or '').strip()
    cliente.is_configured = bool(cliente.base_url and cliente.api_key)

    if not cliente.is_configured:
        return {"success": False, "message": _("URL e chave de API são obrigatórios.")}

    try:
        info = cliente.get('/System/Info') or {}
        return {
            "success": True,
            "message": _("Ligado a '%(nome)s' (versão %(versao)s).",
                         nome=info.get('ServerName', 'Jellyfin'),
                         versao=info.get('Version', '?')),
            "server_name": info.get('ServerName'),
            "version": info.get('Version'),
        }
    except JellyfinApiError as e:
        if e.status_code in (401, 403):
            return {"success": False, "message": _("O Jellyfin recusou a chave de API. Gere uma nova em Painel de Controlo → Chaves de API.")}
        return {"success": False, "message": str(e)}
    except Exception as e:
        return {"success": False, "message": _("Não foi possível contactar o Jellyfin: %(erro)s", erro=describe(e))}


def list_administrators(url: str, api_key: str) -> Dict[str, Any]:
    """Lista as contas do servidor, marcando quais são administradores.

    O assistente usa-a para o administrador se identificar: quem detém a chave
    de API do Jellyfin já tem controlo total do servidor, por isso escolher a
    conta aqui não concede nada que a chave não conceda.
    """
    cliente = JellyfinApiClient()
    cliente.base_url = (url or '').strip().rstrip('/')
    cliente.api_key = (api_key or '').strip()
    cliente.is_configured = bool(cliente.base_url and cliente.api_key)

    if not cliente.is_configured:
        return {"success": False, "message": _("URL e chave de API são obrigatórios."), "users": []}

    try:
        utilizadores = cliente.get('/Users') or []
    except JellyfinApiError as e:
        return {"success": False, "message": str(e), "users": []}
    except Exception as e:
        return {"success": False, "message": _("Não foi possível contactar o Jellyfin: %(erro)s", erro=describe(e)), "users": []}

    return {
        "success": True,
        "users": [
            {
                "id": u.get('Id'),
                "name": u.get('Name'),
                "is_admin": bool((u.get('Policy') or {}).get('IsAdministrator')),
            }
            for u in utilizadores if u.get('Id')
        ],
    }
