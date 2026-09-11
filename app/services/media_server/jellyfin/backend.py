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
from .connection import JellyfinConnectionManager
from .sessions import JellyfinSessionsProvider
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
    )

    IMAGE_SOURCES = ('jellyfin',)

    def __init__(self, data_manager, stats_manager=None, notifier_manager=None, requests_manager=None):
        self.conn = JellyfinConnectionManager()
        self.users = JellyfinUserManager(self.conn, data_manager, stats_manager, requests_manager)
        self.sessions = JellyfinSessionsProvider(self.conn)
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

    def get_all_users(self, force_refresh=False):
        from ....utils.image_proxy import proxied_image_url

        if force_refresh:
            self.users.invalidate_user_cache()

        utilizadores = self.users.list_users() or []

        processados = []
        for bruto in utilizadores:
            utilizador = dict(bruto)
            fonte = self.sessions.user_thumb_source(utilizador.get('thumb'))
            if fonte:
                utilizador['thumb'] = proxied_image_url(fonte)
            processados.append(utilizador)
        return processados

    def get_user_by_id(self, user_id):
        return self.users.get_user_by_id(user_id)

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

    def update_screen_limit(self, user_id, screens):
        """Guarda o limite no perfil E aplica-o no servidor.

        O painel continua a impor o limite ele próprio (é ele que avisa o
        utilizador e regista a auditoria), mas o Jellyfin sabe fazê-lo
        nativamente: assim o limite continua de pé mesmo com o painel em baixo.
        """
        perfil = self.data_manager.get_user_profile(user_id)
        if perfil:
            perfil['screen_limit'] = screens
            self.data_manager.set_user_profile(user_id, perfil)
        self.users.update_screen_limit(user_id, screens)
        logger.info(f"Limite de telas para o utilizador ID '{user_id}' atualizado para {screens}.")

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
