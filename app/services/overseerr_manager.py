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

    # Utilizadores pedidos por chamada à listagem do Seerr.
    PAGE_SIZE = 100
    # Teto da cache de médias. Sem isto, um painel que corre durante meses
    # acumula uma entrada por cada filme/série alguma vez pedido e nunca liberta
    # nada — as entradas expiram, mas ficam lá.
    MEDIA_CACHE_MAX = 1000

    def __init__(self):
        self._enabled = False
        self.api_url = None
        self.api_key = None
        # Caches simples em memória: {chave: (timestamp, valor)}.
        # Protegidas por lock porque o processamento dos pedidos é feito em várias
        # threads (ver ThreadPoolExecutor em get_user_requests).
        self._user_cache = {}
        self._media_cache = {}
        self._cache_lock = threading.Lock()


    @property
    def enabled(self):
        """
        Indica se o módulo de pedidos está ativo.

        🐛 CORREÇÃO: isto era um atributo simples, preenchido apenas quando
        '_get_config()' corria. Como a recarga de credenciais é seletiva (só
        acontece quando as chaves do Seerr mudam), o valor ficava preso em False
        desde o arranque — e a aba de pedidos do utilizador mostrava
        "Módulo de pedidos desativado no servidor" mesmo com a ligação
        configurada e a funcionar.
        Passa a ser uma propriedade que lê a configuração atual.
        """
        self._get_config()
        return bool(self._enabled)

    def _get_config(self) -> bool:
        """
        Lê as configurações atuais da BD. Retorna True se o serviço estiver ativo e configurado.
        Isto previne o uso de configurações "presas" na memória.
        """
        config = load_or_create_config()
        self._enabled = config.get("OVERSEERR_ENABLED", False)
        self.api_url = config.get("OVERSEERR_URL", "").rstrip('/')
        self.api_key = config.get("OVERSEERR_API_KEY")
        
        return bool(self._enabled and self.api_url and self.api_key)

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

    # ⚠️ Cada servidor de média entra no Seerr pela SUA porta, e o corpo do
    # pedido tem a chave com o nome dele. Não há um endpoint genérico: o
    # `import-from-plex` só existe no Overseerr/Jellyseerr para contas do Plex,
    # e o `import-from-jellyfin` só no Jellyseerr.
    IMPORTACAO_POR_SERVIDOR = {
        'plex': ('/user/import-from-plex', 'plexIds'),
        'jellyfin': ('/user/import-from-jellyfin', 'jellyfinUserIds'),
    }

    def import_user(self, user_info: Dict[str, Any], tipo_servidor: str = 'plex') -> Dict[str, Any]:
        """Importa (ou sincroniza) no Seerr um utilizador do servidor de média.

        ⚠️ **A porta depende do servidor.** Durante muito tempo só havia
        `import-from-plex`, e num painel Jellyfin isso significava mandar GUIDs
        do Jellyfin para o endpoint das contas do Plex: o Seerr respondia com
        erro ou, pior, aceitava e não importava ninguém. Hoje a porta e o nome
        do campo saem de `IMPORTACAO_POR_SERVIDOR`.

        Quem só tem Overseerr (sem Jellyfin) não tem a rota do Jellyfin: o erro
        do servidor volta tal e qual, porque é ele que explica o que falta.
        """
        # Um utilizador acabado de importar não pode ficar preso a uma cache
        # anterior que dizia 'não existe'.
        self._esquecer(user_info.get('email'), user_info.get('username'))

        media_user_id = user_info.get('id')
        username = user_info.get('username')

        if not media_user_id:
            logger.error(f"Falha na importação no Seerr: '{username}' não tem identificador no servidor.")
            return {"success": False, "message": _("Usuário sem identificador no servidor de mídia.")}

        endpoint, campo = self.IMPORTACAO_POR_SERVIDOR.get(
            (tipo_servidor or 'plex').lower(), self.IMPORTACAO_POR_SERVIDOR['plex']
        )

        logger.info(f"Seerr: a importar '{username}' por {endpoint}.")
        result = self._make_request("POST", endpoint, json={campo: [str(media_user_id)]})

        if result.get("success"):
            logger.info(f"Seerr: utilizador '{username}' importado com sucesso.")
            return {"success": True, "message": _("Acesso ao sistema de pedidos concedido.")}

        logger.error(f"Seerr: falha ao importar '{username}' por {endpoint}: {result.get('message')}")
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
        return self.find_user(email=email)

    def find_user(self, email: str = None, username: str = None) -> Optional[Dict[str, Any]]:
        """Encontra o utilizador no Seerr pelo email OU pelo nome de utilizador.

        ⚠️ **O email não é obrigatório em todos os servidores.** Num painel
        Jellyfin, o convite pede o email como OPCIONAL — e quem não o preencheu
        nunca era encontrado aqui: a aba "Meus Pedidos" ficava vazia para
        sempre, sem erro nenhum, como se a pessoa nunca tivesse pedido nada.

        A pesquisa do Seerr (`?q=`) cobre `username`, `email`, `plexUsername` e
        `jellyfinUsername`, por isso o nome serve perfeitamente como segunda
        tentativa. Só a procura por EMAIL confirma o email encontrado; a procura
        por nome aceita o primeiro resultado da pesquisa nativa, que é o que o
        Seerr considera corresponder.
        """
        for chave, por_email in ((email, True), (username, False)):
            chave = (chave or '').lower().strip()
            if not chave:
                continue

            # Cache em memória — evita repetir a procura em cada F5 da página.
            with self._cache_lock:
                cached = self._user_cache.get(chave)
            if cached and (time.time() - cached[0]) < self.USER_CACHE_TTL:
                return cached[1]

            user = self._search_user(chave) if por_email else self._search_user_por_nome(chave)
            if user:
                with self._cache_lock:
                    self._user_cache[chave] = (time.time(), user)
                return user

        return None

    def _search_user_por_nome(self, nome_norm: str) -> Optional[Dict[str, Any]]:
        """O primeiro resultado da pesquisa nativa, confirmando o nome.

        ⚠️ Ao contrário do email, o nome não é único no Seerr — e há três campos
        onde ele pode estar (`username`, `plexUsername`, `jellyfinUsername`).
        Confirma-se contra os três em vez de aceitar o que vier: um utilizador
        errado aqui mostrava os pedidos de OUTRA pessoa na conta desta.
        """
        resultados, _total = self._listar_utilizadores({"take": self.PAGE_SIZE, "q": nome_norm})
        if not resultados:
            return None

        for utilizador in resultados:
            nomes = {
                str(utilizador.get(campo) or '').lower().strip()
                for campo in ('username', 'plexUsername', 'jellyfinUsername', 'displayName')
            }
            if nome_norm in nomes:
                return utilizador
        return None

    def _search_user(self, email_norm: str) -> Optional[Dict[str, Any]]:
        """
        Procura o utilizador na listagem do Seerr.

        ⚡ O endpoint `/user` aceita `q`, e a pesquisa cobre username, email,
        plexUsername e jellyfinUsername (ver 'server/routes/user/index.ts' no
        Seerr). Uma única chamada resolve o caso normal.

        Versões antigas do Overseerr que não conheçam o parâmetro limitam-se a
        ignorá-lo e devolvem a primeira página sem filtro — que percorremos na
        mesma. Se de lá não vier nada e a resposta tiver vindo cheia (ou seja,
        havia mais utilizadores para ver), caímos na paginação completa.
        """
        resultados, _total = self._listar_utilizadores({"take": self.PAGE_SIZE, "q": email_norm})
        if resultados is None:
            return None

        encontrado = self._encontrar_email(resultados, email_norm)
        if encontrado:
            return encontrado

        # Página incompleta = já vimos tudo o que estes parâmetros dão. Isso vale
        # tanto quando o 'q' foi aplicado (não há ninguém com este email) como
        # quando foi ignorado num servidor com poucos utilizadores.
        if len(resultados) < self.PAGE_SIZE:
            return None

        return self._search_user_paginando(email_norm)

    def _search_user_paginando(self, email_norm: str) -> Optional[Dict[str, Any]]:
        """
        Recurso de último caso: percorre a listagem página a página.

        Usado apenas quando o servidor ignora o parâmetro de pesquisa. Paramos
        assim que encontramos, em vez de pedir os 1000 utilizadores de uma vez
        como acontecia antes.
        """
        skip = 0
        while skip < 5000:  # limite de segurança para não iterar indefinidamente
            resultados, total = self._listar_utilizadores({"take": self.PAGE_SIZE, "skip": skip})
            if resultados is None:
                return None

            encontrado = self._encontrar_email(resultados, email_norm)
            if encontrado:
                return encontrado

            # Última página: ou veio menos do que pedimos, ou já cobrimos o total.
            if len(resultados) < self.PAGE_SIZE or (total is not None and skip + self.PAGE_SIZE >= total):
                break
            skip += self.PAGE_SIZE

        return None

    def _listar_utilizadores(self, params: Dict[str, Any]):
        """
        Lê uma página de '/user'. Devolve (resultados, total) ou (None, None) se
        a chamada falhar — 'None' distingue "erro" de "não há ninguém".
        """
        result = self._make_request("GET", "/user", params=params)
        if not result.get("success"):
            return None, None

        dados = result.get("data", {}) or {}
        return (dados.get("results", []) or []), (dados.get("pageInfo") or {}).get("results")

    @staticmethod
    def _encontrar_email(utilizadores: List[Dict[str, Any]], email_norm: str) -> Optional[Dict[str, Any]]:
        """O 'q' do Seerr faz correspondência parcial: o email tem de bater certo."""
        for user in utilizadores:
            if (user.get("email") or "").lower() == email_norm:
                return user
        return None

    def invalidate_user_cache(self, email: str = None):
        """Limpa a cache de utilizadores (usada ao importar ou remover alguém)."""
        with self._cache_lock:
            if email:
                self._user_cache.pop(email.lower().strip(), None)
            else:
                self._user_cache.clear()

    def _esquecer(self, *chaves):
        """Esquece estas identidades — e SÓ estas.

        ⚠️ `invalidate_user_cache(None)` limpa tudo, e chamá-lo com um email em
        falta (o caso normal numa conta local do Jellyfin) deitava fora a cache
        de toda a gente sem que ninguém percebesse porquê.
        """
        for chave in chaves:
            if chave:
                self.invalidate_user_cache(chave)

    def reload_credentials(self):
        """
        Relê a configuração e esvazia as caches.

        🐛 Chamado pelas rotas de definições sempre que o URL, a chave ou o
        estado do Seerr mudam (ver 'app/blueprints/api/system.py'). Até aqui o
        método não existia e a chamada era simplesmente ignorada: ao apontar o
        painel para OUTRO servidor Seerr, os IDs de utilizador ficavam em cache
        até 10 min e os detalhes das médias até 24 h — a apontar para registos
        do servidor antigo.
        """
        with self._cache_lock:
            self._user_cache.clear()
            self._media_cache.clear()
        self._get_config()

    def remove_user(self, email: str, username: str = None) -> Dict[str, Any]:
        """Remove o utilizador do sistema de pedidos.

        ⚠️ O `username` não é um extra: num painel Jellyfin há contas SEM email,
        e sem ele o acesso ficava por retirar — o painel dizia que tinha
        removido e a pessoa continuava a poder pedir.
        """
        etiqueta = mask_email(email) if email else (username or '?')
        user = self.find_user(email=email, username=username)
        if not user:
            logger.warning(f"Seerr: utilizador '{etiqueta}' não encontrado para remoção. A ignorar.")
            return {"success": True, "message": _("Usuário não encontrado no sistema de pedidos.")}

        user_id = user.get("id")
        logger.info(f"Seerr: a remover o utilizador '{etiqueta}' (ID interno: {user_id}).")

        result = self._make_request("DELETE", f"/user/{user_id}")
        if result.get("success"):
            # O utilizador deixou de existir: a entrada em cache ficaria a apontar
            # para um ID inválido nas próximas consultas.
            self._esquecer(email, username)
            logger.info(f"Seerr: utilizador '{etiqueta}' removido com sucesso.")
            return {"success": True, "message": _("Acesso removido com sucesso.")}

        logger.error(f"Seerr: falha ao remover o utilizador '{etiqueta}': {result.get('message')}")
        return {"success": False, "message": result.get('message')}

    # --- LÓGICA DE PEDIDOS (OTIMIZADA) ---

    def get_user_requests(self, email: str, limit: int = 10, filter: str = 'all', skip: int = 0,
                          username: str = None) -> Dict[str, Any]:
        """
        Busca os pedidos de um utilizador. 
        Otimizado com processamento paralelo para buscar as imagens do TMDB rapidamente.

        ⚠️ O `username` é a segunda tentativa, para quem não tem email — o caso
        normal num painel Jellyfin, onde o convite pede o email como opcional.
        """
        if not self._get_config():
            return {"success": False, "message": _("Integração com o sistema de pedidos desativada.")}

        user = self.find_user(email=email, username=username)
        if not user:
            return {"success": False, "message": _("Usuário não encontrado no sistema de pedidos.")}
        
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
                    if len(self._media_cache) >= self.MEDIA_CACHE_MAX:
                        self._descartar_medias_expiradas()
                    self._media_cache[chave] = (time.time(), detalhes)

        return detalhes

    def _descartar_medias_expiradas(self):
        """
        Liberta espaço na cache de médias. Deve ser chamado com o lock tomado.

        Remove primeiro o que já expirou; se mesmo assim continuar cheia (muitos
        títulos diferentes dentro das 24 h), esvazia tudo — perder a cache custa
        algumas chamadas à API, deixá-la crescer sem limite custa memória.
        """
        agora = time.time()
        for chave in [k for k, (ts, _v) in self._media_cache.items()
                      if (agora - ts) >= self.MEDIA_CACHE_TTL]:
            self._media_cache.pop(chave, None)

        if len(self._media_cache) >= self.MEDIA_CACHE_MAX:
            self._media_cache.clear()

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

        # 🧪 NOTIFICAÇÃO DE TESTE: o botão "Testar" do Seerr envia
        # notification_type='TEST_NOTIFICATION' SEM os blocos 'request' e 'media'.
        # Sem este tratamento, o pedido caía no erro "sem email do requerente" e o
        # Seerr mostrava falha — mesmo estando tudo bem configurado.
        # Respondemos com sucesso para que o teste confirme que o endereço está
        # correto e acessível.
        if str(data.get('notification_type') or '').upper() == 'TEST_NOTIFICATION':
            logger.info("Webhook do Seerr: notificação de TESTE recebida com sucesso.")
            return {"success": True, "message": "Notificação de teste recebida com sucesso."}

        email = (pedido.get('requestedBy_email') or '').strip()
        username_seerr = pedido.get('requestedBy_username') or ''

        # Os eventos de problemas reportados (ISSUE_CREATED, ISSUE_COMMENT,
        # ISSUE_RESOLVED, ISSUE_REOPENED) chegam pelo mesmo webhook mas não
        # trazem bloco 'request' — trazem 'issue' e/ou 'comment'. Não são
        # pedidos, por isso são ignorados em silêncio, em vez de ficarem
        # registados como falha "sem email do requerente".
        if not email and (data.get('issue') or data.get('comment')):
            logger.info("Webhook do Seerr: evento de 'issue'/comentário sem pedido associado. Ignorado.")
            return {"success": True, "message": "Evento sem pedido associado."}

        if not email and not username_seerr:
            logger.warning("Webhook do Seerr sem email nem nome de quem pediu; não há como identificar o utilizador.")
            return {"success": False, "message": "Pedido sem identificação do requerente."}

        # ⚠️ **O EMAIL PODE NÃO EXISTIR.** Num painel Jellyfin o convite pede-o
        # como opcional, e o Jellyseerr manda o `requestedBy_email` vazio para
        # quem não o tem. O painel dizia "pedido sem email do requerente" e não
        # notificava ninguém — o pedido era aprovado, ficava disponível, e a
        # pessoa nunca sabia. O nome vinha no mesmo payload, por usar.
        perfil = None
        if email:
            perfil = extensions.data_manager.get_user_profile_by_email(email)
        if not perfil and username_seerr:
            perfil = extensions.data_manager.get_user_profile_by_username(username_seerr)

        # ⚠️ O perfil pode NÃO EXISTIR, e o webhook continua a interessar: quem
        # aprova o pedido é o administrador, e ele quer saber que entrou um
        # pedido novo mesmo que quem o fez não tenha (ainda) perfil no painel.
        # Por isso o aviso dele vem ANTES desta desistência, que era onde o
        # webhook morria em silêncio.

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
            # ⚠️ `perfil` pode ser None: o aviso ao administrador é montado a
            # partir daqui e acontece ANTES de se desistir por falta de perfil.
            "username": username_seerr or (perfil or {}).get('username') or '',
            "media_url": media_url,
            "image_url": data.get('image') or None,
            "event": data.get('event') or '',
            # Tipo do evento (MEDIA_PENDING, MEDIA_APPROVED, MEDIA_AVAILABLE,
            # MEDIA_DECLINED, MEDIA_FAILED...). É o que permite ao notificador
            # escolher a mensagem certa para cada situação.
            "notification_type": (data.get('notification_type') or '').upper(),
        }

        self._avisar_administrador_do_pedido(dados)

        if not perfil:
            etiqueta = mask_email(email) if email else username_seerr
            logger.info(f"Webhook do Seerr: nenhum utilizador local corresponde a {etiqueta}. Ignorado.")
            return {"success": True, "message": "Usuário não encontrado no painel."}

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

    @staticmethod
    def _avisar_administrador_do_pedido(dados):
        """Regista o pedido novo no sino do painel e manda-o ao celular do dono.

        ⚠️ Nada disto pode derrubar o webhook: o Seerr trata um erro como uma
        entrega falhada e volta a tentar, o que daria a mesma notificação várias
        vezes a quem pediu.
        """
        from .. import extensions

        if str(dados.get("notification_type") or "").upper() not in (
                "MEDIA_PENDING", "MEDIA_AUTO_APPROVED"):
            return

        try:
            extensions.data_manager.create_notification(
                message=_("%(username)s pediu %(title)s.",
                          username=dados.get("username") or _("Alguém"),
                          title=dados.get("title") or _("um título")),
                category='info', link=dados.get("media_url") or None,
            )
            extensions.db.session.commit()
            if extensions.socketio:
                extensions.socketio.emit('new_notification', namespace='/')
        except Exception as e:
            logger.warning(f"Não foi possível registar o pedido novo nas notificações: {e}")
            try:
                extensions.db.session.rollback()
            except Exception:
                pass

        try:
            extensions.notifier_manager.send_media_request_admin_notification(dados)
        except Exception as e:
            logger.warning(f"Não foi possível avisar o administrador do pedido novo: {e}")

    def _get_status_info(self, request_status_code: Optional[int], media_availability_code: Optional[int]) -> Dict[str, str]:
        """Calcula o estado final baseando-se na hierarquia do Pedido vs Média."""
        # Estados do PEDIDO — enum 'MediaRequestStatus' do Seerr
        # (server/constants/media.ts): 1 Pending, 2 Approved, 3 Declined,
        # 4 Failed, 5 Completed.
        #
        # 🐛 CORREÇÃO: faltavam aqui o 4 (Failed) e o 5 (Completed). Um pedido que
        # falhou no Radarr/Sonarr não encontrava correspondência e acabava a
        # mostrar o estado da média — ou seja, o utilizador via "Pendente" ou
        # "Processando" para sempre, num pedido que na verdade tinha falhado.
        request_status_map = {
            1: {"text": _("Pendente"), "color": "yellow"},
            2: {"text": _("Aprovado"), "color": "blue"},
            3: {"text": _("Recusado"), "color": "red"},
            4: {"text": _("Falhou"), "color": "red"},
            5: {"text": _("Concluído"), "color": "green"},
        }

        # Estados da MÉDIA — enum 'MediaStatus' do Seerr: 1 Unknown, 2 Pending,
        # 3 Processing, 4 Partially Available, 5 Available, 6 Blocklisted,
        # 7 Deleted.
        #
        # 🐛 CORREÇÃO: 6 e 7 não estavam mapeados. Um item bloqueado ou removido
        # do servidor continuava a aparecer com o estado do pedido ("Aprovado"),
        # como se ainda estivesse a caminho.
        media_availability_map = {
            1: {"text": _("Desconhecido"), "color": "gray"},
            2: {"text": _("Pendente"), "color": "yellow"},
            3: {"text": _("Processando"), "color": "blue"},
            4: {"text": _("Parcialmente Disponível"), "color": "teal"},
            5: {"text": _("Disponível"), "color": "green"},
            6: {"text": _("Bloqueado"), "color": "gray"},
            7: {"text": _("Removido"), "color": "red"},
        }

        # 🐛 Os códigos podem chegar como STRING (ex: "2") em vez de inteiro,
        # consoante a versão do Seerr e o que houver pelo caminho. Uma comparação
        # direta falhava em silêncio e o estado caía em "Desconhecido".
        def _int(valor):
            try:
                return int(valor)
            except (TypeError, ValueError):
                return None

        req_code = _int(request_status_code)
        media_code = _int(media_availability_code)

        # 🐛 A DISPONIBILIDADE MANDA. Antes, o estado da média só era considerado
        # quando o pedido estava exatamente como "Aprovado" (código 2). Um item já
        # disponível no servidor mas sem pedido formal aprovado — ou com o pedido
        # noutro estado — aparecia como "Desconhecido", mesmo quando o próprio
        # filtro "Disponível" o tinha devolvido.
        # Se a média está disponível (5) ou parcialmente disponível (4), é isso que
        # interessa ao utilizador, independentemente do estado do pedido.
        if media_code in (4, 5):
            return media_availability_map[media_code]

        # Estado base vem do pedido.
        status_info = request_status_map.get(req_code)

        # Pedido aprovado: o que importa a seguir é o progresso do download
        # (a descarregar, bloqueado, removido...).
        if req_code == 2 and media_code in media_availability_map:
            return media_availability_map[media_code]

        if status_info:
            return status_info

        # Sem estado de pedido reconhecível, tentamos ainda assim o da média antes
        # de desistir — é melhor mostrar "Processando" do que "Desconhecido".
        if media_code in media_availability_map:
            return media_availability_map[media_code]

        return {"text": _("Desconhecido"), "color": "gray"}
