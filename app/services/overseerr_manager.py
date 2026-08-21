# app/services/overseerr_manager.py

import logging
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List

from flask_babel import gettext as _
from ..config import load_or_create_config
from ..utils.log_sanitizer import mask_email

logger = logging.getLogger(__name__)

class OverseerrManager:
    """Gerencia a comunicação com a API do Overseerr/Jellyseerr."""

    def __init__(self):
        self.enabled = False
        self.api_url = None
        self.api_key = None

    def _get_config(self) -> bool:
        """
        Lê as configurações atuais da BD. Retorna True se o serviço estiver ativo e configurado.
        Isto previne o uso de configurações "presas" na memória.
        """
        config = load_or_create_config()
        self.enabled = config.get("OVERSEERR_ENABLED", False)
        self.api_url = config.get("OVERSEERR_URL", "").rstrip('/')
        self.api_key = config.get("OVERSEERR_API_KEY")
        
        return bool(self.enabled and self.api_url and self.api_key)

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Executa uma requisição centralizada e segura para a API do Overseerr/Jellyseerr."""
        if not self._get_config():
            return {"success": False, "message": _("Overseerr/Jellyseerr não está configurado ou ativado.")}
        
        url = f"{self.api_url}/api/v1{endpoint}"
        headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        
        try:
            response = requests.request(method, url, headers=headers, timeout=10, **kwargs)
            response.raise_for_status()
            return {"success": True, "data": response.json() if response.text else {}}
            
        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            try:
                # Tenta extrair a mensagem de erro formatada pelo Overseerr
                error_json = e.response.json()
                error_message = error_json.get("message", error_text)
                logger.error(f"Overseerr API HTTP Error: {error_message}")
                return {"success": False, "message": f"Erro do Servidor Overseerr: {error_message}"}
            except ValueError:
                logger.error(f"Overseerr API HTTP Error (Non-JSON): {error_text}")
                return {"success": False, "message": f"Erro do Servidor Overseerr: {e.response.status_code}"}
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de conexão ao comunicar com o Overseerr: {e}")
            return {"success": False, "message": _("Falha de comunicação com o servidor de pedidos.")}

    # --- MÉTODOS PÚBLICOS ---

    def test_connection(self, url: str, api_key: str) -> Dict[str, Any]:
        """Testa a conexão com credenciais fornecidas na página de configuração."""
        if not url or not api_key:
            return {'success': False, 'message': _('URL e Chave da API são obrigatórios.')}
        
        test_url = f"{url.rstrip('/')}/api/v1/settings/about"
        headers = {"X-Api-Key": api_key}
        
        try:
            response = requests.get(test_url, headers=headers, timeout=10)
            response.raise_for_status()
            return {'success': True, 'message': _('Conexão com Overseerr/Jellyseerr bem-sucedida!')}
        except requests.exceptions.RequestException as e:
            logger.error(f"Falha no teste de conexão com Overseerr: {e}")
            return {'success': False, 'message': _("Falha na conexão. Verifique o URL e a Chave da API.")}

    def import_from_plex(self, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """Importa ou atualiza um utilizador no Overseerr a partir do Plex ID."""
        plex_id = user_info.get('id')
        username = user_info.get('username')
        
        if not plex_id:
            logger.error(f"Falha na importação Overseerr: Plex ID de '{username}' não encontrado.")
            return {"success": False, "message": _("ID do Plex não encontrado.")}
            
        logger.info(f"Overseerr: A tentar importar/sincronizar o utilizador '{username}' (Plex ID: {plex_id}).")
        result = self._make_request("POST", "/user/import-from-plex", json={"plexIds": [str(plex_id)]})
        
        if result.get("success"):
            logger.info(f"Overseerr: Utilizador '{username}' importado com sucesso.")
            return {"success": True, "message": _("Acesso ao Overseerr concedido.")}
        else:
            logger.error(f"Overseerr: Falha ao importar '{username}': {result.get('message')}")
            return {"success": False, "message": result.get('message')}

    def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Procura o ID de um utilizador no Overseerr com base no endereço de e-mail."""
        if not email:
            return None
            
        result = self._make_request("GET", "/user?take=1000")
        if not result.get("success"):
            return None
        
        users = result.get("data", {}).get("results", [])
        for user in users:
            if user.get("email", "").lower() == email.lower():
                return user
                
        return None
        
    def remove_user(self, email: str) -> Dict[str, Any]:
        """Remove o utilizador do sistema de pedidos Overseerr."""
        user = self.find_user_by_email(email)
        if not user:
            logger.warning(f"Overseerr: Utilizador '{mask_email(email)}' não encontrado para remoção. A ignorar.")
            return {"success": True, "message": _("Utilizador não encontrado no Overseerr.")}
        
        user_id = user.get("id")
        logger.info(f"Overseerr: A remover o utilizador '{mask_email(email)}' (ID interno: {user_id}).")
        
        result = self._make_request("DELETE", f"/user/{user_id}")
        if result.get("success"):
            logger.info(f"Overseerr: Utilizador '{mask_email(email)}' removido com sucesso.")
            return {"success": True, "message": _("Acesso removido com sucesso.")}
        else:
            logger.error(f"Overseerr: Falha ao remover utilizador '{mask_email(email)}': {result.get('message')}")
            return {"success": False, "message": result.get('message')}

    # --- LÓGICA DE PEDIDOS (OTIMIZADA) ---

    def get_user_requests(self, email: str, limit: int = 10, filter: str = 'all') -> Dict[str, Any]:
        """
        Busca os pedidos de um utilizador. 
        Otimizado com processamento paralelo para buscar as imagens do TMDB rapidamente.
        """
        if not self._get_config():
            return {"success": False, "message": _("Integração com Overseerr desativada.")}

        user = self.find_user_by_email(email)
        if not user:
            return {"success": False, "message": _("Utilizador não encontrado no Overseerr.")}
        
        params = {
            "take": limit, "skip": 0, "filter": filter,
            "sort": "added", "requestedBy": user.get("id")
        }
        
        result = self._make_request("GET", "/request", params=params)
        if not result.get("success"):
            return result

        requests_data = result.get("data", {}).get("results", [])
        processed_requests = []

        # OTIMIZAÇÃO: Usa 5 threads em paralelo para buscar as informações do TMDB (Posters)
        # em vez de esperar 1 segundo por cada filme individualmente.
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self._process_single_request, req) for req in requests_data]
            for future in as_completed(futures):
                try:
                    res = future.result()
                    if res:
                        processed_requests.append(res)
                except Exception as e:
                    logger.error(f"Erro ao processar item do Overseerr em background: {e}")

        # Como as threads terminam em ordem aleatória, voltamos a ordenar pela data do pedido
        processed_requests.sort(key=lambda x: x.get("requested_at", ""), reverse=True)
        
        return {"success": True, "requests": processed_requests}

    def _process_single_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Processa um pedido individual, buscando os detalhes no TMDB via Overseerr."""
        media = req.get("media", {})
        media_type = media.get("mediaType")
        tmdb_id = media.get("tmdbId")

        # Dados Padrão (Fallback)
        title = _("Título Desconhecido")
        year_str = "----"
        poster_url = None

        # Busca Detalhes (Capa/Título/Ano)
        if tmdb_id and media_type in ['movie', 'tv']:
            details_result = self._make_request("GET", f"/{media_type}/{tmdb_id}")
            if details_result.get("success"):
                source_data = details_result.get("data", {})
                
                if poster_path := source_data.get("posterPath"):
                    poster_url = f"https://image.tmdb.org/t/p/w200{poster_path}"
                    
                title = source_data.get("title") or source_data.get("name") or title
                release_date = source_data.get("releaseDate") or source_data.get("firstAirDate")
                year_str = release_date[:4] if release_date else "----"

        status_info = self._get_status_info(req.get("status"), media.get("status"))

        return {
            "id": req.get("id"),
            "title": title,
            "year": year_str,
            "type": media_type,
            "status_text": status_info["text"],
            "status_color": status_info["color"],
            "poster_url": poster_url,
            "requested_at": req.get("createdAt")
        }

    def _get_status_info(self, request_status_code: Optional[int], media_availability_code: Optional[int]) -> Dict[str, str]:
        """Calcula o estado final baseando-se na hierarquia do Pedido vs Média."""
        # 1 = Pending, 2 = Approved, 3 = Declined
        request_status_map = {
            1: {"text": _("Pendente"), "color": "yellow"},
            2: {"text": _("Aprovado"), "color": "blue"},
            3: {"text": _("Recusado"), "color": "red"},
        }
        
        # 1 = Unknown, 2 = Pending, 3 = Processing, 4 = Partially Available, 5 = Available
        media_availability_map = {
            1: {"text": _("Desconhecido"), "color": "gray"},
            2: {"text": _("Pendente"), "color": "yellow"},
            3: {"text": _("Processando"), "color": "blue"},
            4: {"text": _("Parcialmente Disponível"), "color": "teal"},
            5: {"text": _("Disponível"), "color": "green"},
        }

        # Estado Base vem do Pedido
        status_info = request_status_map.get(request_status_code, {"text": _("Desconhecido"), "color": "gray"})
        
        # Se o pedido foi Aprovado, o que importa é o estado do download (Média)
        if request_status_code == 2:
            status_info = media_availability_map.get(media_availability_code, status_info)
            
        return status_info
