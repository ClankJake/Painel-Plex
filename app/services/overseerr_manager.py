# app/services/overseerr_manager.py

import logging
import requests
from flask_babel import gettext as _

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
        
    def get_user_requests(self, email, limit=10, filter='all'):
        """Busca os pedidos mais recentes de um utilizador no Overseerr."""
        if not self.enabled:
            return {"success": False, "message": "Integração com Overseerr desativada."}

        user = self.find_user_by_email(email)
        if not user:
            return {"success": False, "message": "Utilizador não encontrado no Overseerr."}
        
        user_id = user.get("id")
        params = {
            "take": limit,
            "skip": 0,
            "filter": filter,
            "sort": "added",
            "requestedBy": user_id
        }
        result = self._make_request("GET", "/request", params=params)

        if not result.get("success"):
            return result

        requests_data = result.get("data", {}).get("results", [])
        processed_requests = []
        for req in requests_data:
            media = req.get("media", {})
            media_type = media.get("mediaType")
            tmdb_id = media.get("tmdbId")

            # Valores padrão
            title = "Título Desconhecido"
            year_str = "----"
            poster_url = None

            if tmdb_id and media_type in ['movie', 'tv']:
                details_endpoint = f"/{media_type}/{tmdb_id}"
                details_result = self._make_request("GET", details_endpoint)

                if details_result.get("success"):
                    source_data = details_result.get("data", {})
                    poster_path = source_data.get("posterPath")
                    poster_url = f"https://image.tmdb.org/t/p/w200{poster_path}" if poster_path else None
                    title = source_data.get("title") or source_data.get("name") or "Título Desconhecido"
                    release_date = source_data.get("releaseDate") or source_data.get("firstAirDate")
                    year_str = release_date[:4] if release_date else "----"
                else:
                    logger.warning(f"Não foi possível obter detalhes para {media_type} com tmdbId {tmdb_id}. A usar informações básicas.")

            # --- CORREÇÃO FINAL: Lógica de Status hierárquica ---
            request_status_code = req.get("status")
            media_availability_code = media.get("status")

            # Status do Pedido (Prioridade)
            request_status_map = {
                1: {"text": _("Pendente"), "color": "yellow"},
                2: {"text": _("Aprovado"), "color": "blue"},
                3: {"text": _("Recusado"), "color": "red"},
            }
            # Status de Disponibilidade da Mídia (usado se o pedido for aprovado)
            media_availability_map = {
                1: {"text": _("Desconhecido"), "color": "gray"},
                2: {"text": _("Pendente"), "color": "yellow"},
                3: {"text": _("Processando"), "color": "blue"},
                4: {"text": _("Parcialmente Disponível"), "color": "teal"},
                5: {"text": _("Disponível"), "color": "green"},
            }

            status_info = {"text": _("Desconhecido"), "color": "gray"}
            if request_status_code == 3: # Recusado
                status_info = request_status_map[3]
            elif request_status_code == 1: # Pendente
                status_info = request_status_map[1]
            elif request_status_code == 2: # Aprovado, agora verificamos a disponibilidade
                status_info = media_availability_map.get(media_availability_code, request_status_map[2])
            
            processed_requests.append({
                "id": req.get("id"),
                "title": title,
                "year": year_str,
                "type": media_type,
                "status_text": status_info["text"],
                "status_color": status_info["color"],
                "poster_url": poster_url,
                "requested_at": req.get("createdAt")
            })
        
        return {"success": True, "requests": processed_requests}

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

