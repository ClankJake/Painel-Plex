# app/services/overseerr_manager.py

import logging
import requests

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

class OverseerrManager:
    """Gerencia a comunicação com a API do Overseerr/Jellyseerr."""

    def __init__(self):
        self.config = {}
        self.api_url = None
        self.api_key = None
        self.enabled = False
        self.reload_config()

    def reload_config(self):
        """Recarrega a configuração a partir do ficheiro."""
        self.config = load_or_create_config()
        self.api_url = self.config.get("OVERSEERR_URL", "").rstrip('/')
        self.api_key = self.config.get("OVERSEERR_API_KEY")
        self.enabled = self.config.get("OVERSEERR_ENABLED", False)

    def _make_request(self, method, endpoint, **kwargs):
        """Executa uma requisição para a API do Overseerr/Jellyseerr."""
        if not self.enabled or not self.api_url or not self.api_key:
            return {"success": False, "message": "Overseerr/Jellyseerr não está configurado ou habilitado."}
        
        url = f"{self.api_url}/api/v1{endpoint}"
        headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        
        try:
            response = requests.request(method, url, headers=headers, timeout=10, **kwargs)
            response.raise_for_status()
            return {"success": True, "data": response.json() if response.text else {}}
        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            logger.error(f"Erro HTTP ao comunicar com o Overseerr: {e}. Resposta do servidor: {error_text}")
            try:
                # Tenta extrair a mensagem de erro específica do JSON de resposta
                error_json = e.response.json()
                error_message = error_json.get("message", error_text)
                return {"success": False, "message": f"Erro do Servidor Overseerr: {error_message}"}
            except:
                return {"success": False, "message": f"Erro do Servidor Overseerr: {error_text}"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de conexão ao comunicar com o Overseerr: {e}")
            return {"success": False, "message": str(e)}

    def test_connection(self, url, api_key):
        """Testa a conexão com as credenciais fornecidas."""
        if not url or not api_key:
            return {'success': False, 'message': 'URL e Chave da API são obrigatórios.'}
        
        test_url = f"{url.rstrip('/')}/api/v1/settings/about"
        headers = {"X-Api-Key": api_key}
        
        try:
            response = requests.get(test_url, headers=headers, timeout=10)
            response.raise_for_status()
            return {'success': True, 'message': 'Conexão com Overseerr/Jellyseerr bem-sucedida!'}
        except requests.exceptions.RequestException as e:
            logger.error(f"Falha no teste de conexão com Overseerr: {e}")
            return {'success': False, 'message': f"Falha na conexão: {e}"}

    def import_from_plex(self, user_info):
        """Importa ou atualiza um utilizador no Overseerr a partir dos seus dados do Plex."""
        plex_id = user_info.get('id')
        username = user_info.get('username')
        if not plex_id:
            logger.error(f"Não foi possível importar o utilizador '{username}' para o Overseerr porque o seu ID do Plex não foi encontrado.")
            return {"success": False, "message": "ID do Plex não encontrado."}
            
        logger.info(f"A tentar importar o utilizador '{username}' (Plex ID: {plex_id}) para o Overseerr.")
        result = self._make_request("POST", "/user/import-from-plex", json={"plexIds": [str(plex_id)]})
        
        if result["success"]:
            logger.info(f"Utilizador '{username}' importado com sucesso para o Overseerr.")
            return {"success": True, "message": "Utilizador criado/atualizado com sucesso no Overseerr."}
        else:
            logger.error(f"Falha ao importar o utilizador '{username}' para o Overseerr: {result.get('message')}")
            return {"success": False, "message": f"Falha ao criar utilizador no Overseerr: {result.get('message')}"}

    def find_user_by_email(self, email):
        """Encontra um utilizador no Overseerr pelo seu endereço de e-mail."""
        logger.debug(f"A procurar utilizador no Overseerr com o email: {email}")
        result = self._make_request("GET", "/user?take=1000")
        if not result["success"]:
            return None
        
        users = result.get("data", {}).get("results", [])
        for user in users:
            if user.get("email", "").lower() == email.lower():
                logger.debug(f"Utilizador encontrado no Overseerr: {user['displayName']} (ID: {user['id']})")
                return user
        logger.debug(f"Nenhum utilizador encontrado no Overseerr com o email: {email}")
        return None

    def remove_user(self, email):
        """Remove um utilizador do Overseerr."""
        user = self.find_user_by_email(email)
        if not user:
            logger.warning(f"Tentativa de remover o utilizador '{email}' do Overseerr, mas ele não foi encontrado.")
            return {"success": True, "message": "Utilizador não encontrado no Overseerr."}
        
        user_id = user.get("id")
        logger.info(f"A remover o utilizador '{email}' (ID: {user_id}) do Overseerr.")
        result = self._make_request("DELETE", f"/user/{user_id}")
        
        if result["success"]:
            logger.info(f"Utilizador '{email}' removido com sucesso do Overseerr.")
            return {"success": True, "message": "Utilizador removido com sucesso do Overseerr."}
        else:
            logger.error(f"Falha ao remover o utilizador '{email}' do Overseerr: {result['message']}")
            return {"success": False, "message": f"Falha ao remover utilizador do Overseerr: {result['message']}"}
