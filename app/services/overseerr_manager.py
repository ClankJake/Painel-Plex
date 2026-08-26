# app/services/overseerr_manager.py

import logging
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List

from flask_babel import gettext as _
from ..config import load_or_create_config
from ..utils.log_sanitizer import mask_email

logger = logging.getLogger(__name__)

class OverseerrManager:
    """Gerencia a comunicação com a API do Overseerr/Jellyseerr."""

    # Tempo de vida das caches em memória.
    USER_CACHE_TTL = 600      # 10 min — o ID de um utilizador quase nunca muda
    MEDIA_CACHE_TTL = 86400   # 24 h  — título e ano de um filme NUNCA mudam

    def __init__(self):
        self.enabled = False
        self.api_url = None
        self.api_key = None
        # Caches simples em memória: {chave: (timestamp, valor)}.
        # Protegidas por lock porque o processamento dos pedidos é feito em várias
        # threads (ver ThreadPoolExecutor em get_user_requests).
        self._user_cache = {}
        self._media_cache = {}
        self._cache_lock = threading.Lock()


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
        # Um utilizador acabado de importar não pode ficar preso a uma cache
        # anterior que dizia 'não existe'.
        self.invalidate_user_cache(user_info.get('email'))
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
        """
        Procura um utilizador do Overseerr pelo e-mail.

        ⚡ OTIMIZAÇÃO: antes esta função descarregava a lista COMPLETA de
        utilizadores (`/user?take=1000`) a cada carregamento da página de pedidos,
        só para extrair um único ID. Num servidor com centenas de utilizadores
        isso é uma resposta enorme, repetida sem necessidade.

        Agora tentamos primeiro a pesquisa nativa do Overseerr (`?q=`), que
        devolve poucos registos. A listagem completa fica apenas como recurso de
        último caso, para versões do Overseerr que não suportem o parâmetro.

        O resultado é guardado em cache (10 min): o ID de um utilizador
        praticamente não muda.
        """
        if not email:
            return None

        email_norm = email.lower().strip()

        # 1. Cache em memória — evita repetir a procura em cada F5 da página.
        cached = self._user_cache.get(email_norm)
        if cached and (time.time() - cached[0]) < self.USER_CACHE_TTL:
            return cached[1]

        user = self._search_user(email_norm)
        if user:
            self._user_cache[email_norm] = (time.time(), user)
        return user

    def _search_user(self, email_norm: str) -> Optional[Dict[str, Any]]:
        """
        Procura o utilizador percorrendo a listagem por páginas.

        ⚠️ NOTA: a API do Seerr/Overseerr **não** oferece pesquisa de utilizadores
        por email — o endpoint `/user` aceita apenas 'take', 'skip', 'sort' e
        'sortDirection' (confirmado na especificação oficial do Seerr). Parâmetros
        desconhecidos são simplesmente ignorados, o que tornaria uma tentativa de
        pesquisa uma chamada desperdiçada.
        Por isso paginamos — mas paramos assim que encontramos, em vez de pedir os
        1000 utilizadores de uma vez como acontecia antes.
        """
        skip = 0
        page_size = 100
        while skip < 5000:  # limite de segurança para não iterar indefinidamente
            result = self._make_request("GET", "/user", params={"take": page_size, "skip": skip})
            if not result.get("success"):
                return None

            dados = result.get("data", {}) or {}
            resultados = dados.get("results", []) or []

            for user in resultados:
                if (user.get("email") or "").lower() == email_norm:
                    return user

            # Última página: ou veio menos do que pedimos, ou já cobrimos o total.
            total = (dados.get("pageInfo") or {}).get("results")
            if len(resultados) < page_size or (total is not None and skip + page_size >= total):
                break
            skip += page_size

        return None

    def invalidate_user_cache(self, email: str = None):
        """Limpa a cache de utilizadores (usada ao importar ou remover alguém)."""
        if email:
            self._user_cache.pop(email.lower().strip(), None)
        else:
            self._user_cache.clear()

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
            # O utilizador deixou de existir: a entrada em cache ficaria a apontar
            # para um ID inválido nas próximas consultas.
            self.invalidate_user_cache(email)
            logger.info(f"Overseerr: Utilizador '{mask_email(email)}' removido com sucesso.")
            return {"success": True, "message": _("Acesso removido com sucesso.")}
        else:
            logger.error(f"Overseerr: Falha ao remover utilizador '{mask_email(email)}': {result.get('message')}")
            return {"success": False, "message": result.get('message')}

    # --- LÓGICA DE PEDIDOS (OTIMIZADA) ---

    def get_user_requests(self, email: str, limit: int = 10, filter: str = 'all', skip: int = 0) -> Dict[str, Any]:
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
            "take": limit, "skip": skip, "filter": filter,
            "sort": "added", "requestedBy": user.get("id")
        }
        
        result = self._make_request("GET", "/request", params=params)
        if not result.get("success"):
            return result

        dados = result.get("data", {}) or {}
        requests_data = dados.get("results", []) or []
        page_info = dados.get("pageInfo", {}) or {}
        processed_requests = []

        # OTIMIZAÇÃO: Usa 5 threads em paralelo para buscar as informações do TMDB (Posters)
        # em vez de esperar 1 segundo por cada filme individualmente.
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(self._process_single_request, req): req for req in requests_data}
            for future in as_completed(futures):
                req_original = futures[future]
                try:
                    res = future.result()
                    if res:
                        processed_requests.append(res)
                except Exception as e:
                    # 🐛 Antes, um erro aqui fazia o pedido DESAPARECER da lista: o
                    # utilizador via menos pedidos do que tem, sem qualquer indicação
                    # de que algo falhou. Agora devolvemos uma entrada degradada —
                    # sem capa, mas com o estado correto — para o pedido continuar visível.
                    logger.error(f"Erro ao processar item do Overseerr em background: {e}")
                    media_err = req_original.get("media", {}) or {}
                    estado = self._get_status_info(req_original.get("status"), media_err.get("status"))
                    processed_requests.append({
                        "id": req_original.get("id"),
                        "title": _("Título indisponível"),
                        "year": "----",
                        "type": media_err.get("mediaType"),
                        "status_text": estado["text"],
                        "status_color": estado["color"],
                        "poster_url": None,
                        "requested_at": req_original.get("createdAt"),
                    })

        # Como as threads terminam em ordem aleatória, voltamos a ordenar pela data do pedido
        processed_requests.sort(key=lambda x: x.get("requested_at") or "", reverse=True)

        return {
            "success": True,
            "requests": processed_requests,
            # Informação de paginação, para a interface poder oferecer "ver mais".
            "pagination": {
                "total": page_info.get("results", len(processed_requests)),
                "has_more": (skip + len(requests_data)) < page_info.get("results", 0),
            }
        }

    def _get_media_details(self, media_type: str, tmdb_id: int) -> Dict[str, Any]:
        """
        Obtém título, ano e capa de um item, com cache de 24 horas.

        ⚡ OTIMIZAÇÃO: estes dados eram procurados a CADA carregamento da página —
        uma chamada por pedido listado. Como o título e o ano de um filme nunca
        mudam (e a capa quase nunca), guardá-los em cache elimina a esmagadora
        maioria das chamadas à API sem qualquer perda prática de atualidade.

        O estado do pedido (pendente/aprovado/disponível) NÃO é guardado em cache:
        esse muda com frequência e continua a ser lido a cada pedido.
        """
        chave = f"{media_type}:{tmdb_id}"

        with self._cache_lock:
            em_cache = self._media_cache.get(chave)
            if em_cache and (time.time() - em_cache[0]) < self.MEDIA_CACHE_TTL:
                return em_cache[1]

        detalhes = {"title": None, "year": None, "poster_url": None}

        result = self._make_request("GET", f"/{media_type}/{tmdb_id}")
        if result.get("success"):
            dados = result.get("data", {}) or {}
            if poster_path := dados.get("posterPath"):
                detalhes["poster_url"] = f"https://image.tmdb.org/t/p/w200{poster_path}"
            detalhes["title"] = dados.get("title") or dados.get("name")
            data_lancamento = dados.get("releaseDate") or dados.get("firstAirDate")
            detalhes["year"] = data_lancamento[:4] if data_lancamento else None

            # Só guardamos em cache respostas ÚTEIS. Guardar uma falha faria o item
            # aparecer sem título durante 24 h por causa de um erro momentâneo.
            if detalhes["title"]:
                with self._cache_lock:
                    self._media_cache[chave] = (time.time(), detalhes)

        return detalhes

    def _process_single_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Processa um pedido individual, buscando os detalhes no TMDB via Overseerr."""
        media = req.get("media", {})
        media_type = media.get("mediaType")
        tmdb_id = media.get("tmdbId")

        # Dados Padrão (Fallback)
        title = _("Título Desconhecido")
        year_str = "----"
        poster_url = None

        if tmdb_id and media_type in ['movie', 'tv']:
            detalhes = self._get_media_details(media_type, tmdb_id)
            title = detalhes.get("title") or title
            year_str = detalhes.get("year") or "----"
            poster_url = detalhes.get("poster_url")

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

    def handle_notification_webhook(self, data):
        """
        Traduz uma notificação do Overseerr numa mensagem para o utilizador que
        fez o pedido.

        O payload do agente de Webhook do Overseerr tem esta forma:
            notification_type, subject, message, image,
            media:   {media_type, tmdbId, status, ...}
            request: {request_id, requestedBy_email, requestedBy_username, ...}

        Devolve sempre um dicionário (nunca lança), porque o Overseerr repete
        notificações que não recebem resposta de sucesso.
        """
        from .. import extensions

        pedido = data.get('request') or {}
        media = data.get('media') or {}

        email = (pedido.get('requestedBy_email') or '').strip()
        username_seerr = pedido.get('requestedBy_username') or ''

        if not email:
            logger.warning("Webhook do Overseerr sem email de quem pediu; não é possível identificar o utilizador.")
            return {"success": False, "message": "Pedido sem email do requerente."}

        # Localiza o utilizador NO PAINEL pelo email (o mesmo que ele usa no Plex).
        perfil = extensions.data_manager.get_user_profile_by_email(email)
        if not perfil:
            logger.info(f"Webhook do Overseerr: nenhum utilizador local corresponde a {mask_email(email)}. Ignorado.")
            return {"success": True, "message": "Utilizador não encontrado no painel."}

        # Monta o URL para o item no Overseerr (o mesmo destino que a interface usa).
        #
        # 🐛 'self.api_url' só é preenchido por '_get_config()', que corre quando o
        # serviço é usado. Um webhook pode chegar antes disso (o Overseerr chama-nos
        # de forma independente), e então o link saía VAZIO na mensagem — o
        # utilizador recebia "Acesse o pedido:" sem endereço nenhum.
        # Garantimos a configuração antes de montar o URL.
        if not self.api_url:
            self._get_config()

        media_type = media.get('media_type') or ''
        tmdb_id = media.get('tmdbId')
        base = (self.api_url or '').replace('/api/v1', '').rstrip('/')

        # Último recurso: lê diretamente da configuração (cobre o caso de o módulo
        # estar desativado no painel mas o webhook continuar a ser enviado).
        if not base:
            base = (load_or_create_config().get('OVERSEERR_URL') or '').rstrip('/')

        media_url = f"{base}/{media_type}/{tmdb_id}" if (base and media_type and tmdb_id) else ''
        if not media_url:
            logger.warning("Webhook do Overseerr: não foi possível montar o link do item (URL do Overseerr por configurar?).")

        # 'subject' costuma vir como "Título (Ano)" e 'message' como a sinopse.
        dados = {
            "title": data.get('subject') or '',
            "overview": data.get('message') or '',
            "status": media.get('status') or data.get('notification_type') or '',
            "username": username_seerr or perfil.get('username') or '',
            "media_url": media_url,
            "image_url": data.get('image') or None,
            "event": data.get('event') or '',
            # Tipo do evento (MEDIA_PENDING, MEDIA_APPROVED, MEDIA_AVAILABLE,
            # MEDIA_DECLINED, MEDIA_FAILED...). É o que permite ao notificador
            # escolher a mensagem certa para cada situação.
            "notification_type": (data.get('notification_type') or '').upper(),
        }

        try:
            extensions.notifier_manager.send_media_request_notification(perfil, dados)
            logger.info(
                f"Notificação de pedido reencaminhada para '{perfil.get('username')}' "
                f"({dados['title'][:40]})."
            )
            return {"success": True, "message": "Notificação enviada."}
        except Exception as e:
            logger.error(f"Falha ao reencaminhar a notificação de pedido: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

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
