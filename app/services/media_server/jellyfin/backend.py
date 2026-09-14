# app/services/media_server/jellyfin/backend.py

"""Fachada do backend Jellyfin: cumpre o contrato `MediaServerBackend`."""

import logging
from datetime import datetime, timedelta, timezone

from flask_babel import gettext as _

from ....extensions import cache
from ....extensions import scheduler as global_scheduler
from ....utils.identity import normalize_user_id
from ....utils.log_formatting import describe
from ..base import MediaServerCapabilities
from ..plex.subscription_manager import PlexSubscriptionManager
from .account_manager import JellyfinAccountManager
from .api_client import JellyfinApiError
from .connection import JellyfinConnectionManager
from .history import JellyfinHistoryManager
from .identity import chave_de
from .sessions import JellyfinSessionsProvider
from .stream_limit import JellyfinStreamLimit
from .stream_gate_log import RAZAO_DO_CORTE, JellyfinStreamGateLog
from .user_manager import JellyfinUserManager

logger = logging.getLogger(__name__)


class JellyfinManager:
    """Administra um servidor Jellyfin.

    A lógica de assinaturas é a MESMA do Plex — vencimentos, renovações e os
    jobs que os fazem cumprir não dependem do servidor —, por isso reutiliza-se
    o `PlexSubscriptionManager` tal como está. O nome dele é herança de quando
    só havia um backend; o que faz é agnóstico.
    """

    SERVER_TYPE = 'jellyfin'
    DISPLAY_NAME = 'Jellyfin'
    SHORT_NAME = 'Jellyfin'

    # As contas são locais ao servidor: o painel cria-as e é responsável pelas
    # credenciais. Não há convites a contas externas, não há fontes de mídia
    # online, e a autenticação não é delegada a lado nenhum. Em troca, o
    # bloqueio é limpo: `Policy.IsDisabled`, sem mexer nas bibliotecas.
    CAPABILITIES = MediaServerCapabilities(
        convites_nativos=False,
        cria_contas=True,
        fontes_media_online=False,
        login_delegado=False,
        desativa_conta=True,
        links_profundos=True,
        # Há estatísticas, e saem do próprio servidor: do plugin Playback
        # Reporting quando existe, do registo do núcleo quando não
        # (`stats_api.py`). O Tautulli não entra aqui — nem há o que configurar.
        estatisticas=True,
        estatisticas_externas=False,
    )

    IMAGE_SOURCES = ('jellyfin',)

    def __init__(self, data_manager, stats_manager=None, notifier_manager=None, requests_manager=None):
        self.conn = JellyfinConnectionManager()
        self.users = JellyfinUserManager(self.conn, data_manager, stats_manager, requests_manager)
        self.sessions = JellyfinSessionsProvider(self.conn)
        self.history = JellyfinHistoryManager(self.conn)
        self.stream_limit = JellyfinStreamLimit(self.conn)
        self.gate_log = JellyfinStreamGateLog(self.conn)
        self.invites = JellyfinAccountManager(
            self.conn, self.users, data_manager, self, requests_manager, notifier_manager
        )
        self.subscriptions = PlexSubscriptionManager(data_manager, self.users, scheduler=global_scheduler)
        self.subscriptions.plex_manager = self

        self.stream_manager = None
        self.data_manager = data_manager
        self.stats_manager = stats_manager
        self.notifier_manager = notifier_manager
        self.requests_manager = requests_manager
        self.app = None

    # =========================================================================
    # CICLO DE VIDA
    # =========================================================================

    @property
    def capabilities(self):
        return self.CAPABILITIES

    def estatisticas_disponiveis(self):
        """Há: saem do próprio servidor (ver `stats_api.py`).

        Não depende de mais nada estar instalado. Com o plugin Playback
        Reporting são por REPRODUÇÃO, com o tempo que foi mesmo visto; sem ele,
        o núcleo só sabe que itens cada pessoa deu por vistos, e o tempo é o
        que o item dura — uma aproximação, dita na interface.
        """
        return True

    def init_app(self, app):
        from app.config import is_configured
        self.app = app
        if is_configured():
            self.reload_connections()

    def reload_connections(self, from_job=False):
        sucesso, mensagem = self.conn.reload(from_job=from_job)

        # Invalida sempre a cache do estado: acabámos de tentar reconectar, por
        # isso um "OFFLINE" (ou "ONLINE") anterior deixou de ser válido.
        try:
            cache.delete_memoized(self._check_status_cached)
        except Exception:
            pass

        if sucesso:
            self.users.invalidate_user_cache()

        return sucesso, mensagem

    def check_status(self):
        return self._check_status_cached()

    @cache.memoize(timeout=30)
    def _check_status_cached(self):
        """⚡ Cache curta (30s), pelo mesmo motivo do Plex: testar a ligação é
        uma chamada de rede real e o painel de saúde pede-a a cada
        carregamento."""
        if not self.conn.connected:
            return {"status": "OFFLINE", "message": _("Não configurado ou falha na conexão inicial.")}

        try:
            self.conn.api.get('/System/Info')
            return {"status": "ONLINE", "message": _("Conectado com sucesso.")}
        except Exception as e:
            logger.warning(f"Falha na verificação de estado do Jellyfin: {describe(e)}")
            return {"status": "OFFLINE", "message": _("Falha na comunicação com o servidor Jellyfin.")}

    def is_connected(self):
        return self.conn.connected

    # =========================================================================
    # SERVIDOR
    # =========================================================================

    def get_libraries(self):
        return self.conn.get_libraries()

    def get_server_identifier(self):
        return self.conn.get_server_identifier()

    def get_base_url(self):
        return self.conn.api.base_url

    def authenticate(self, username, password):
        """Valida as credenciais contra o próprio Jellyfin.

        O servidor devolve também um `AccessToken` de sessão, que o painel NÃO
        guarda: não precisa dele (fala com o servidor pela chave de API) e
        guardá-lo seria mais um segredo de terceiros a proteger sem motivo.
        """
        from ..base import OwnerAccount

        if not self.conn.connected or not username or not password:
            return None

        try:
            resultado = self.conn.api.post(
                '/Users/AuthenticateByName',
                json={'Username': username, 'Pw': password},
            ) or {}
        except JellyfinApiError as e:
            # 401 é o caso NORMAL de credenciais erradas, não um erro do painel.
            if e.status_code in (401, 403):
                return None
            logger.warning(f"O Jellyfin recusou a autenticação de '{username}': {describe(e)}")
            return None
        except Exception as e:
            logger.error(f"Falha ao autenticar '{username}' no Jellyfin: {describe(e)}", exc_info=True)
            return None

        # 🐛 Autenticar ABRE uma sessão no Jellyfin, e ela não desaparece por o
        # painel deitar o token fora: fica na lista de sessões do servidor até
        # expirar. Cada entrada no painel deixava uma sessão órfã — e num
        # servidor com "sessões simultâneas" limitadas, a pessoa tinha de sair
        # de um aparelho para conseguir entrar no painel. O painel só queria
        # saber se a palavra-passe está certa; a sessão fecha-se já.
        self._encerrar_sessao_de_validacao(resultado.get('AccessToken'), username)

        utilizador = resultado.get('User') or {}
        user_id = normalize_user_id(utilizador.get('Id'))
        if not user_id:
            return None

        etiqueta = utilizador.get('PrimaryImageTag')
        caminho = f"/Users/{user_id}/Images/Primary?tag={etiqueta}" if etiqueta else None
        return OwnerAccount(
            id=user_id,
            username=utilizador.get('Name') or username,
            email=None,
            # Vai direto para a sessão e daí para um `<img src>` do painel: tem
            # de ser o URL do proxy, não o caminho do Jellyfin.
            thumb=self._thumb_para_a_interface(caminho),
        )

    def _encerrar_sessao_de_validacao(self, token, username):
        """Fecha a sessão que o `AuthenticateByName` acabou de abrir.

        `/Sessions/Logout` encerra a sessão de QUEM CHAMA, por isso o pedido vai
        com o token do utilizador e não com a chave de API do painel. Uma falha
        aqui não pode impedir o login: no pior caso fica a sessão órfã que
        existia antes desta correção.
        """
        if not token:
            return
        try:
            self.conn.api.request('POST', '/Sessions/Logout', token=token)
        except Exception as e:
            logger.warning(
                "Não foi possível fechar a sessão de validação de '%s' no Jellyfin: %s",
                username, describe(e),
            )

    def get_owner_account(self):
        """A conta de administrador configurada no painel.

        O Jellyfin não tem "dono" como o Plex: qualquer conta com
        `IsAdministrator` administra o servidor. O painel guarda qual escolheu
        no assistente (ADMIN_USER_ID) e é essa que devolve aqui.
        """
        from app.config import load_or_create_config
        from ..base import OwnerAccount

        config = load_or_create_config()
        admin_id = normalize_user_id(config.get('ADMIN_USER_ID'))
        if not admin_id:
            return None

        utilizador = self.users.get_user_by_id(admin_id)
        return OwnerAccount(
            id=admin_id,
            username=(utilizador or {}).get('username') or config.get('ADMIN_USER') or '',
            email=None,
            thumb=self._thumb_para_a_interface((utilizador or {}).get('thumb')),
        )

    def authorize_image_url(self, source, image_path):
        """Monta o URL autenticado de uma imagem do Jellyfin.

        O Jellyfin serve imagens sem sessão, mas a chave de API é aceite como
        parâmetro e mantém o comportamento consistente com bibliotecas
        privadas. `image_path` já vem no formato `/Items/<id>/Images/...`.
        """
        if source != 'jellyfin' or not self.conn.api.base_url:
            return None, {}

        caminho = image_path if image_path.startswith('/') else f'/{image_path}'
        return f"{self.conn.api.base_url}{caminho}", {'ApiKey': self.conn.api.api_key}

    # =========================================================================
    # UTILIZADORES
    # =========================================================================

    def _thumb_para_a_interface(self, thumb_cru):
        """O caminho cru da imagem do Jellyfin no URL que o browser consegue pedir.

        🐛 A leitura crua (`users.list_users()`) devolve
        `/Users/<id>/Images/Primary?tag=...`, que é um caminho do JELLYFIN. Posto
        num `<img src>` do painel, o browser pede-o ao PAINEL — que não tem essa
        rota e responde 404. O sintoma é a imagem de perfil a não aparecer, sem
        erro nenhum no log.

        Tudo o que sai da fachada com uma imagem tem de passar por aqui.
        """
        from ....utils.image_proxy import proxied_image_url

        fonte = self.sessions.user_thumb_source(thumb_cru)
        return proxied_image_url(fonte) if fonte else None

    def thumb_para_interface(self, thumb):
        """Idempotente: o que já é um URL do proxy volta intacto.

        🐛 É o que salva quem JÁ ESTAVA autenticado quando o formato mudou: o
        avatar vive numa cópia dentro do cookie da sessão, e essa não se
        reescreve sozinha. Sem isto, essas pessoas ficavam com um 404 no
        cabeçalho até voltarem a entrar — até 30 dias depois.
        """
        if not thumb:
            return None
        if '/image/' in thumb:
            return thumb
        return self._thumb_para_a_interface(thumb)

    def get_all_users(self, force_refresh=False):
        if force_refresh:
            self.users.invalidate_user_cache()

        utilizadores = self.users.list_users() or []

        processados = []
        for bruto in utilizadores:
            utilizador = dict(bruto)
            utilizador['thumb'] = self._thumb_para_a_interface(utilizador.get('thumb'))
            processados.append(utilizador)
        return processados

    def get_user_by_id(self, user_id):
        """Um utilizador tratado para a interface — imagem incluída.

        🐛 Isto devolvia a leitura CRUA do submanager, com o thumb no formato do
        Jellyfin. Quem o punha num `<img src>` (a página pública de pagamento, o
        detalhe de um utilizador) recebia um 404 do painel.
        """
        utilizador = self.users.get_user_by_id(user_id)
        if not utilizador:
            return None

        tratado = dict(utilizador)
        tratado['thumb'] = self._thumb_para_a_interface(tratado.get('thumb'))
        return tratado

    def get_user_libraries(self, user_id):
        return self.users.get_user_libraries(user_id)

    def update_user_libraries(self, user_id, library_titles, allow_sync=None):
        return self.users.update_user_libraries(user_id, library_titles, allow_sync=allow_sync)

    def update_all_users_libraries(self, library_titles):
        return self.users.update_all_users_libraries(library_titles)

    def block_user(self, user_id, reason='manual'):
        if self.stream_manager and not self.users.stream_manager:
            self.users.stream_manager = self.stream_manager
        return self.users.block_user(user_id, reason)

    def unblock_user(self, user_id):
        return self.users.unblock_user(user_id)

    def remove_user(self, user_id):
        if self.stream_manager and not self.users.stream_manager:
            self.users.stream_manager = self.stream_manager
        return self.users.remove_user(user_id)

    def restaurar_acesso(self, media_user_id, profile, libraries=None):
        """Aqui a conta é LOCAL: devolver o acesso é reativá-la, não convidar.

        ⚠️ O caminho partilhado da reativação chamava `invites.send_invite()` —
        que num servidor de contas locais quer dizer CRIAR uma conta. Com a
        conta a existir já, a tentativa era uma conta duplicada com o email por
        nome e uma palavra-passe que ninguém veria; e a notificação levava um
        endereço de `clients.plex.tv` a quem nunca teve conta no Plex.

        O que é preciso fazer é tirar-lhe o `IsDisabled` e repor as bibliotecas
        que o perfil guardou. Não há nada para aceitar, por isso não há
        `link_pendente` — o link é o do próprio servidor, onde a pessoa entra
        com as credenciais que já tem.

        ⚠️ **Se a conta já não existir, isto NÃO a recria.** Depois de
        `DAYS_TO_REMOVE_BLOCKED_USER` dias, o `removal_job` apaga-a mesmo
        (`DELETE /Users`), e recriá-la seria inventar uma palavra-passe nova
        que o painel teria de entregar. Dizer que não se conseguiu é melhor do
        que dar por reativado um acesso que não existe.
        """
        import json

        media_user_id = normalize_user_id(media_user_id)

        if not self.get_user_by_id(media_user_id):
            logger.error(
                f"Reativação de '{(profile or {}).get('username')}': a conta já não existe no "
                "Jellyfin (foi removida). É preciso criá-la de novo e entregar as credenciais."
            )
            return {
                "success": False,
                "message": _("A conta já não existe no servidor e tem de ser criada de novo."),
                "link": self.get_base_url(),
                "link_pendente": None,
            }

        resultado = self.users.unblock_user(media_user_id)
        if not resultado.get('success'):
            return {**resultado, "link": self.get_base_url(), "link_pendente": None}

        if libraries is None:
            libraries = (profile or {}).get('libraries', '[]')
        if isinstance(libraries, str):
            try:
                libraries = json.loads(libraries)
            except (ValueError, TypeError):
                libraries = []

        if libraries:
            self.users.update_user_libraries(
                media_user_id, libraries, allow_sync=(profile or {}).get('allow_downloads')
            )

        return {
            "success": True,
            "message": _("Acesso reposto no servidor."),
            "link": self.get_base_url(),
            "link_pendente": None,
        }

    def update_screen_limit(self, user_id, screens):
        """Grava o limite no perfil e, se houver quem o imponha, no servidor.

        ⚠️ Isto chegou a escrever `Policy.MaxActiveSessions`. Não serve: esse
        campo limita AUTENTICAÇÕES, não reproduções. Ver `clear_session_limits`
        no `user_manager` para o que isso partia.

        Quem o impõe de verdade é o plugin StreamLimiter, quando está
        instalado: recusa o PEDIDO HTTP da mídia antes de servir um byte, e
        nenhum cliente pode ignorar isso. Sem ele, o limite continua a ser só
        do painel — e continua a valer, porque é o painel que corta.
        """
        perfil = self.data_manager.get_user_profile(user_id)
        if perfil:
            perfil['screen_limit'] = screens
            self.data_manager.set_user_profile(user_id, perfil)
        self.stream_limit.definir_limite(user_id, screens)
        logger.info(f"Limite de telas para o utilizador ID '{user_id}' atualizado para {screens}.")

    def sync_screen_limits(self):
        return self.stream_limit.sincronizar(self.data_manager.get_all_user_profiles() or [])

    def importar_bloqueios_do_servidor(self):
        """Traz para a auditoria os cortes que o PLUGIN deu sozinho.

        O StreamLimiter recusa o pedido da mídia dentro do processo do Jellyfin
        — o painel não participa, e por isso não sabia nada desses cortes: a
        "Auditoria de Cortes" mostrava só os seus, e quem visse o limite a ser
        cumprido não encontrava rasto nenhum no painel.

        Como o plugin não tem rota de eventos, a única fonte é o log do
        servidor (ver `stream_gate_log.py`).
        """
        if not self.stream_limit.esta_disponivel():
            return {"success": True, "importados": 0}

        bloqueios = self.gate_log.ler_bloqueios()
        if not bloqueios:
            return {"success": True, "importados": 0}

        # A marca de água vem da própria auditoria: o último corte já lá
        # registado. Assim uma releitura do ficheiro não duplica nada.
        marca = self.data_manager.get_last_termination_timestamp(RAZAO_DO_CORTE)
        if marca is not None and marca.tzinfo is None:
            # O SQLite guarda a hora sem o fuso; é sempre UTC (ver o DataManager).
            marca = marca.replace(tzinfo=timezone.utc)

        novos = [b for b in bloqueios if marca is None or b.quando > marca]
        if not novos:
            return {"success": True, "importados": 0}

        aparelhos = self.gate_log.nomes_de_aparelhos()
        nomes = self._nomes_dos_utilizadores()

        importados = 0
        for bloqueio in sorted(novos, key=lambda b: b.quando):
            perfil = self.data_manager.get_user_profile(bloqueio.user_id) or {}
            nome = perfil.get('username') or nomes.get(chave_de(bloqueio.user_id))
            if not nome:
                # Sem nome não vale a pena a linha: a auditoria é lida por
                # pessoas, e um GUID não diz a ninguém quem foi.
                logger.debug(f"Bloqueio do plugin para um utilizador desconhecido: {bloqueio}")
                continue

            registo = self.data_manager.log_stream_termination(
                media_user_id=bloqueio.user_id,
                username=nome,
                # Não há título: o plugin recusa ANTES de haver reprodução. O
                # que se sabe, e é o que interessa, é o limite que se atingiu.
                media_title=_("Limite de %(limite)s tela(s)", limite=bloqueio.limite),
                platform=aparelhos.get(chave_de(bloqueio.aparelho)) or '',
                reason=RAZAO_DO_CORTE,
                timestamp=bloqueio.quando,
            )
            importados += 1
            self._anunciar_corte(registo)

        if importados:
            logger.info(f"Auditoria: {importados} corte(s) do plugin StreamLimiter importado(s) do log do Jellyfin.")
        return {"success": True, "importados": importados}

    def _nomes_dos_utilizadores(self):
        """`id` → nome, para quem ainda não tem perfil local no painel."""
        try:
            utilizadores = self.users.list_users() or []
        except Exception as e:
            logger.debug(f"Não foi possível obter os nomes dos utilizadores: {describe(e)}")
            return {}
        return {chave_de(u.get('id')): u.get('username') for u in utilizadores if u.get('id')}

    @staticmethod
    def _anunciar_corte(registo):
        """Põe o corte na Dashboard sem esperar pelo próximo carregamento.

        A interface já sabia ouvir isto (`new_termination_log`); não havia era
        ninguém a dizê-lo.

        ⚠️ A hora vai no MESMO formato que a rota da auditoria usa: a interface
        faz `new Date(timestamp + 'Z')`, e um `datetime` serializado à maneira
        do Flask dava "Invalid Date" — sem erro nenhum, só a data em branco.
        """
        if not registo:
            return

        try:
            from ....extensions import socketio

            payload = dict(registo)
            if isinstance(payload.get('timestamp'), datetime):
                payload['timestamp'] = payload['timestamp'].strftime('%Y-%m-%dT%H:%M:%S')
            socketio.emit('new_termination_log', payload, namespace='/dashboard')
        except Exception as e:
            logger.debug(f"Não foi possível anunciar o corte na Dashboard: {describe(e)}")

    def link_para_item(self, item_id):
        """A página do item na interface web do próprio servidor."""
        base = (self.get_base_url() or '').rstrip('/')
        identificador = self.get_server_identifier()
        if not base or not item_id:
            return None

        endereco = f"{base}/web/#/details?id={item_id}"
        return f"{endereco}&serverId={identificador}" if identificador else endereco

    def get_user_devices(self, user_id):
        return self.history.get_user_devices(user_id)

    def get_watch_history(self, user_id, page=1, length=15, search=""):
        return self.history.get_watch_history(user_id, page=page, length=length, search=search)

    def clear_session_limits(self):
        return self.users.clear_session_limits()

    def invalidate_user_cache(self):
        return self.users.invalidate_user_cache()

    def toggle_overseerr_access(self, user_id, access: bool):
        return self.users.toggle_overseerr_access(user_id, access)

    def sync_profiles_from_server(self, only_missing=True):
        """O Jellyfin não guarda email nas contas.

        Não há nada para importar do servidor: o email é recolhido pelo painel
        no registo. Devolver um resumo vazio (em vez de falhar) mantém a rota
        que faz esta sincronização a funcionar em qualquer backend.
        """
        return {
            "success": True, "verificados": 0, "atualizados": 0,
            "sem_email_no_plex": 0, "erros": 0,
            "message": _("O Jellyfin não associa email às contas: não há nada a importar."),
        }

    # =========================================================================
    # SESSÕES
    # =========================================================================

    def get_active_sessions(self):
        if not self.conn.connected or not self.stream_manager:
            return {"success": False, "sessions": [], "stream_count": 0}
        try:
            return self.stream_manager.get_now_playing()
        except Exception as e:
            logger.error(f"Erro ao obter sessões do Jellyfin: {describe(e)}", exc_info=True)
            return {"success": False, "sessions": [], "stream_count": 0}

    # =========================================================================
    # CONVITES E ASSINATURAS
    # =========================================================================

    def create_invitation(self, **kwargs):
        return self.invites.create_invitation(**kwargs)

    def get_invitation_by_code(self, code):
        return self.invites.get_invitation_by_code(code)

    def claim_invitation(self, code, account):
        return self.invites.claim_invitation(code, account)

    def list_invitations(self):
        return self.invites.list_invitations()

    def delete_invitation(self, code):
        return self.invites.delete_invitation(code)

    def reactivate_invitation(self, code):
        return self.invites.reactivate_invitation(code)

    def renew_subscription(self, user_id, months_to_add, **kwargs):
        return self.subscriptions.renew_subscription(user_id, months_to_add, **kwargs)

    # =========================================================================
    # NOTIFICAÇÕES DE VENCIMENTO
    # =========================================================================

    def get_users_within_notification_window(self):
        from app.config import load_or_create_config
        config = load_or_create_config()
        dias = config.get("DAYS_TO_NOTIFY_EXPIRATION", 0)
        if not dias > 0:
            return []

        hoje = datetime.now(timezone.utc).date()
        a_avisar = []
        for user_id, dados in self.data_manager.get_all_user_expirations().items():
            try:
                if not dados.get('expiration_date'):
                    continue
                vencimento = datetime.fromisoformat(dados['expiration_date'])
                if vencimento.tzinfo is None:
                    vencimento = vencimento.replace(tzinfo=timezone.utc)
                if 0 <= (vencimento.date() - hoje).days < dias:
                    a_avisar.append(user_id)
            except (ValueError, TypeError):
                continue
        return a_avisar

    def send_expiration_notification_if_needed(self, user_info):
        user_id = normalize_user_id(user_info['id'])
        perfil = self.data_manager.get_user_profile(user_id)
        if not perfil or not self.notifier_manager:
            return

        from app.config import load_or_create_config
        dias_a_avisar = load_or_create_config().get("DAYS_TO_NOTIFY_EXPIRATION", 0)

        ultimo = perfil.get('last_notification_sent')
        if ultimo:
            try:
                enviado = datetime.fromisoformat(ultimo)
                if enviado.tzinfo is None:
                    enviado = enviado.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - enviado) < timedelta(hours=23):
                    return
            except (ValueError, TypeError):
                pass

        vencimento_txt = perfil.get('expiration_date')
        if not vencimento_txt:
            return

        try:
            vencimento = datetime.fromisoformat(vencimento_txt)
            if vencimento.tzinfo is None:
                vencimento = vencimento.replace(tzinfo=timezone.utc)
            faltam = (vencimento.date() - datetime.now(timezone.utc).date()).days
            if faltam < 0 or faltam >= dias_a_avisar:
                return
            self.notifier_manager.send_expiration_notification(user_info, faltam, perfil)
            self.data_manager.update_user_notification_timestamp(user_id)
        except (ValueError, TypeError) as e:
            logger.error(f"Erro ao processar data de expiração para '{user_info.get('username')}': {e}")

    def get_users_to_remove(self):
        from app.config import load_or_create_config
        dias = load_or_create_config().get("DAYS_TO_REMOVE_BLOCKED_USER", 0)
        if not dias > 0:
            return []

        hoje = datetime.now(timezone.utc).date()
        a_remover = []
        for user_id, bloqueio in (self.data_manager.get_blocked_users_dict() or {}).items():
            try:
                quando = bloqueio.get('blocked_at')
                if not quando:
                    continue
                bloqueado_em = datetime.fromisoformat(quando)
                if bloqueado_em.tzinfo is None:
                    bloqueado_em = bloqueado_em.replace(tzinfo=timezone.utc)
                if (hoje - bloqueado_em.date()).days >= dias:
                    a_remover.append(user_id)
            except (ValueError, TypeError, AttributeError):
                continue
        return a_remover
