# app/services/media_server/plex/backend.py

import logging
import base64
import time
import os
import pytz
from urllib.parse import urlparse, parse_qsl, urlencode
from flask import current_app, url_for
from flask_babel import gettext as _
from datetime import datetime, timezone, timedelta
from tzlocal import get_localzone_name

from .connection import PlexConnectionManager
from .user_manager import PlexUserManager
from .invite_manager import PlexInviteManager
from .online_media import PlexOnlineMediaManager
from .subscription_manager import PlexSubscriptionManager
from .sessions import PlexSessionsProvider, thumb_source
from ..base import MediaServerCapabilities

# Importação da instância global do scheduler
from ....extensions import scheduler as global_scheduler
from ....extensions import cache
from ....utils.url_safety import is_plex_tv_host
from ....utils.identity import normalize_user_id
from ....utils.image_proxy import proxied_image_url

logger = logging.getLogger(__name__)

# --- HELPER DE FUSO HORÁRIO ---
def _get_local_tz():
    """Obtém o fuso horário real do sistema respeitando o Docker (ex: America/Sao_Paulo)."""
    tz_env = os.environ.get('TZ')
    if tz_env:
        try:
            return pytz.timezone(tz_env)
        except pytz.UnknownTimeZoneError:
            pass
    try: 
        return pytz.timezone(get_localzone_name())
    except Exception: 
        return pytz.UTC

class PlexManager:
    """
    Atua como uma fachada (Facade), a coordenar vários serviços relacionados com o Plex.

    Cumpre o contrato `MediaServerBackend` (ver `app/services/media_server/base.py`):
    é o backend de referência, e é contra o comportamento dele que os outros
    backends devem ser comparados.
    """

    SERVER_TYPE = 'plex'
    DISPLAY_NAME = 'Plex Media Server'

    # O Plex convida contas que já existem em plex.tv; nunca cria contas. O
    # bloqueio é feito retirando as partilhas, porque não há forma de suspender
    # uma conta que não é nossa.
    CAPABILITIES = MediaServerCapabilities(
        convites_nativos=True,
        cria_contas=False,
        fontes_media_online=True,
        login_delegado=True,
        desativa_conta=False,
        links_profundos=True,
    )

    def __init__(self, data_manager, tautulli_manager, notifier_manager, overseerr_manager):
        self.conn = PlexConnectionManager()
        self.users = PlexUserManager(self.conn, data_manager, tautulli_manager, overseerr_manager)
        self.online_media = PlexOnlineMediaManager(self.conn)
        self.sessions = PlexSessionsProvider(self.conn)
        self.invites = PlexInviteManager(self.conn, self.users, data_manager, self, overseerr_manager, notifier_manager)
        # 🛡️ CORREÇÃO: Injeta o global_scheduler no SubscriptionManager
        self.subscriptions = PlexSubscriptionManager(data_manager, self.users, scheduler=global_scheduler)
        self.subscriptions.plex_manager = self
        self.stream_manager = None
        self.data_manager = data_manager
        self.tautulli_manager = tautulli_manager
        self.notifier_manager = notifier_manager
        self.overseerr_manager = overseerr_manager
        self.app = None

    def init_app(self, app):
        from app.config import is_configured
        self.app = app
        if is_configured():
            self.reload_connections()

    def reload_connections(self, from_job=False):
        """Recarrega as conexões e atualiza as referências dos objetos principais."""
        success, message = self.conn.reload(from_job=from_job)

        # Invalida sempre a cache do estado: acabámos de tentar reconectar, por isso
        # um "OFFLINE" (ou "ONLINE") anterior deixou de ser válido — sem isto, o painel
        # podia continuar a mostrar o estado antigo até 30s depois de a ligação
        # ter sido reposta (ou perdida).
        try:
            cache.delete_memoized(self._check_status_cached)
        except Exception:
            pass

        if success:
            self.users.invalidate_user_cache()

            # 📡 A instância de PlexServer acabou de ser substituída: o listener SSE
            # antigo ficou agarrado à ligação obsoleta e deixaria de entregar eventos
            # silenciosamente. Paramo-lo aqui; será recriado automaticamente na
            # próxima verificação de streams, já ligado à nova conexão.
            try:
                self.sessions.stop_listener()
            except Exception as e:
                logger.debug(f"Aviso ao reiniciar o listener SSE após reload: {e}")
            
            if self.app:
                with self.app.app_context():
                    cache.set('last_plex_user_sync', time.time(), timeout=86400)
                    from app.config import load_or_create_config
                    self.app.config.update(load_or_create_config())
                
        return success, message

    def check_status(self):
        """
        Verifica o estado da conexão com o Plex.

        ⚡ Com cache curto (30s): 'library.sections()' é uma chamada de rede real ao
        servidor Plex, e esta função é invocada sempre que o painel de administração
        é carregado. Sem cache, cada F5 do administrador — ou vários admins em
        simultâneo — martelava o Plex desnecessariamente. 30 segundos é curto o
        suficiente para o estado continuar a ser útil em diagnóstico.
        """
        return self._check_status_cached()

    @cache.memoize(timeout=30)
    def _check_status_cached(self):
        if self.conn and self.conn.plex and self.conn.account:
            try:
                self.conn.plex.library.sections()
                return {"status": "ONLINE", "message": _("Conectado com sucesso.")}
            except Exception as e:
                logger.warning(f"Falha na verificação de estado do Plex: {e}")
                return {"status": "OFFLINE", "message": _("Falha na comunicação com o servidor Plex.")}
        return {"status": "OFFLINE", "message": _("Não configurado ou falha na conexão inicial.")}

    @property
    def capabilities(self):
        return self.CAPABILITIES

    def is_connected(self):
        """Há uma ligação utilizável neste momento?

        Substitui os `if plex_manager.conn.plex:` que existiam espalhados pelo
        painel e obrigavam quem chamava a saber que o objeto interno da ligação
        se chama 'plex'.
        """
        return bool(self.conn and self.conn.plex)

    def invalidate_user_cache(self):
        return self.users.invalidate_user_cache()

    # Prefixos que este backend reconhece no proxy de imagens (`/image/?source=`).
    # O vocabulário é do backend: é ele que sabe que 'plex_account' significa
    # uma imagem em plex.tv e que a autenticação se faz com o X-Plex-Token.
    IMAGE_SOURCES = ('plex', 'plex_account')

    def get_owner_account(self):
        """O dono do servidor, tal como a plexapi o descreve.

        🐛 A Fase 0.5 removeu o espelho `self.account` da fachada e este ponto
        ficou sem nada: o assistente de instalação deixou de encontrar a conta e
        caía no ramo de recurso, que grava o USERNAME como identificador da
        sessão. Numa reconfiguração isso atirava o administrador de volta para o
        ecrã de login logo no pedido seguinte, mesmo tendo acabado de concluir o
        assistente com sucesso.
        """
        return getattr(self.conn, 'account', None)

    def get_base_url(self):
        """O endereço HTTP do servidor, tal como está configurado.

        O proxy de imagens precisa disto para pôr o servidor na allowlist em
        tempo de execução: é dali que vêm as capas. Sem este método, quem
        chamava tinha de ir buscar o `_baseurl` ao objeto da plexapi.
        """
        return getattr(self.conn.plex, '_baseurl', None) if self.conn.plex else None

    def authorize_image_url(self, source, image_path):
        """Constrói o URL e os parâmetros de autenticação de uma imagem do servidor.

        Vive aqui, e não no proxy de imagens, porque injetar credenciais é
        conhecimento do servidor: o proxy só precisa de saber que pediu um URL
        autorizado e recebeu um. Levanta `ValueError` quando o caminho pedido
        não é de confiança — quem chama trata isso como um bloqueio.
        """
        if source == 'plex':
            if not self.conn.plex:
                return None, {}
            return (
                self.conn.plex.url(image_path, includeToken=False),
                {'X-Plex-Token': self.conn.plex._token},
            )

        if source == 'plex_account':
            if not self.conn.account:
                return None, {}

            # 🛡️ FIX DE SEGURANÇA: obriga as imagens a serem relativas a plex.tv.
            if image_path.startswith('http://') or image_path.startswith('https://'):
                parsed = urlparse(image_path)
                # `'plex.tv' in parsed.netloc` aceitava 'plex.tv.atacante.com' e
                # entregava-lhe o token da conta Plex.
                if is_plex_tv_host(parsed.hostname):
                    image_path = parsed.path + ("?" + parsed.query if parsed.query else "")
                else:
                    raise ValueError("URL absoluto inválido para o prefixo plex_account.")

            if not image_path.startswith('/'):
                image_path = '/' + image_path

            return f"https://plex.tv{image_path}", {'X-Plex-Token': self.conn.account._token}

        return None, {}

    # --- DELEGAÇÕES SIMPLES ---
    def get_user_by_id(self, media_user_id):
        return self.users.get_user_by_id(media_user_id)
        
    def update_screen_limit(self, media_user_id, screens):
        profile = self.data_manager.get_user_profile(media_user_id)
        if profile:
            profile['screen_limit'] = screens
            self.data_manager.set_user_profile(media_user_id, profile)
            logger.info(f"Limite de telas para o utilizador ID '{media_user_id}' atualizado para {screens}.")

    def block_user(self, media_user_id, reason='manual'):
        if self.stream_manager and not self.users.stream_manager:
            self.users.stream_manager = self.stream_manager
        return self.users.block_user(media_user_id, reason)

    def unblock_user(self, media_user_id):
        return self.users.unblock_user(media_user_id)

    def remove_user(self, media_user_id):
        if self.stream_manager and not getattr(self.users, 'stream_manager', None):
            self.users.stream_manager = self.stream_manager
        return self.users.remove_user(media_user_id)

    # --- SESSÕES E STREAMING ---
    def get_active_sessions(self):
        if not self.conn.plex or not self.stream_manager:
            return {"success": False, "sessions": [], "stream_count": 0}
        
        try:
            return self.stream_manager.get_now_playing()
        except Exception as e:
            logger.error(f"Erro inesperado ao delegar sessões ao Motor de Streams: {e}", exc_info=True)
            return {"success": False, "sessions": [], "stream_count": 0}

    # --- BIBLIOTECAS E ACESSOS ---
    def get_libraries(self): return self.conn.get_libraries()

    def get_server_identifier(self): return self.conn.get_server_identifier()
    
    def sync_profiles_from_server(self, only_missing=True):
        """
        Preenche os perfis locais com os dados que o Plex já disponibiliza —
        sobretudo o EMAIL — sem que o utilizador tenha de iniciar sessão no painel.

        Porque isto é preciso: o email só era gravado quando alguém resgatava um
        convite ou entrava no painel. Quem já estava no servidor antes disso (ou
        foi adicionado como amigo diretamente no Plex) ficava sem email na base de
        dados — e sem email não é possível ligar o utilizador ao Seerr, nem enviar
        notificações que dependam desse dado.

        'only_missing=True' (padrão) atualiza apenas quem está SEM email, para não
        sobrescrever correções feitas manualmente pelo administrador. Com False,
        força a atualização a partir do Plex em todos os perfis.

        Devolve um resumo com o que foi feito.
        """
        resumo = {"verificados": 0, "atualizados": 0, "sem_email_no_plex": 0, "erros": 0}

        try:
            utilizadores_plex = self.users.list_users() or []
        except Exception as e:
            logger.error(f"Não foi possível obter a lista de utilizadores do Plex: {e}", exc_info=True)
            return {"success": False, "message": str(e), **resumo}

        for utilizador in utilizadores_plex:
            resumo["verificados"] += 1
            try:
                media_user_id = normalize_user_id(utilizador.get('id'))
                email = (utilizador.get('email') or '').strip()
                username = (utilizador.get('username') or '').strip()

                if not email:
                    # Nem todos os amigos do Plex expõem o email (depende das
                    # definições de privacidade da conta deles).
                    resumo["sem_email_no_plex"] += 1
                    continue

                perfil = self.data_manager.get_user_profile(media_user_id)
                if not perfil:
                    continue

                email_atual = (perfil.get('email') or '').strip()
                if only_missing and email_atual:
                    continue
                if email_atual.lower() == email.lower() and perfil.get('username') == username:
                    continue

                perfil['email'] = email
                if username:
                    perfil['username'] = username
                self.data_manager.set_user_profile(media_user_id, perfil)
                resumo["atualizados"] += 1
                logger.info(f"Perfil de '{username}' sincronizado a partir do Plex (email preenchido).")
            except Exception as e:
                resumo["erros"] += 1
                logger.error(f"Falha ao sincronizar o perfil do utilizador {utilizador.get('username')}: {e}")

        logger.info(
            f"Sincronização de perfis concluída: {resumo['atualizados']} atualizado(s) "
            f"de {resumo['verificados']} verificado(s); {resumo['sem_email_no_plex']} sem email no Plex."
        )
        return {"success": True, **resumo}

    def get_all_users(self, force_refresh=False):
        from app.extensions import cache
        
        last_sync = cache.get('last_plex_user_sync')
        current_time = time.time()
        
        if not force_refresh and (not last_sync or (current_time - last_sync > 21600)):
            force_refresh = True
            logger.debug("🔄 Auto-sincronização global de utilizadores ativada.")
            
        if force_refresh:
            self.users.invalidate_user_cache()
            cache.set('last_plex_user_sync', current_time, timeout=86400)
            
        cached_users = self.users.list_users()
        
        if not cached_users:
            return []

        processed_users = []
        
        for u in cached_users:
            user = dict(u) 
            original_thumb = user.get('thumb')
            
            if original_thumb:
                try:
                    # A tradução do avatar para o vocabulário do proxy vive no
                    # provider de sessões, que é quem já a fazia para as sessões
                    # em curso — eram duas cópias da mesma regra.
                    fonte = thumb_source(original_thumb)
                    user['thumb'] = proxied_image_url(fonte) if fonte else original_thumb
                except Exception as e:
                    logger.debug(f"Erro ao converter imagem do utilizador {user.get('username')}: {e}")
            
            processed_users.append(user)

        return processed_users

    def get_user_libraries(self, media_user_id): return self.users.get_user_libraries(media_user_id)
    def update_user_libraries(self, media_user_id, library_titles, allow_sync=None): return self.users.update_user_libraries(media_user_id, library_titles, allow_sync=allow_sync)
    def update_all_users_libraries(self, library_titles): return self.users.update_all_users_libraries(library_titles)
    def toggle_overseerr_access(self, media_user_id, access: bool): return self.users.toggle_overseerr_access(media_user_id, access)
    
    # --- CONVITES E TOKENS ---
    def create_invitation(self, **kwargs): return self.invites.create_invitation(**kwargs)
    def get_invitation_by_code(self, code): return self.invites.get_invitation_by_code(code)
    def claim_invitation(self, code, plex_user_account): return self.invites.claim_invitation(code, plex_user_account)
    def list_invitations(self): return self.invites.list_invitations()
    def delete_invitation(self, code): return self.invites.delete_invitation(code)
    def reactivate_invitation(self, code): return self.invites.reactivate_invitation(code)

    # --- ASSINATURAS E RENOVAÇÕES ---
    def renew_subscription(self, media_user_id, months_to_add, screens=None, base_mode='today', base_date_str=None, expiration_time_str=None, is_reactivation=False):
        return self.subscriptions.renew_subscription(
            media_user_id, months_to_add, screens=screens, base_mode=base_mode, 
            base_date_str=base_date_str, expiration_time_str=expiration_time_str, 
            is_reactivation=is_reactivation
        )

    # --- NOTIFICAÇÕES E EXPIRAÇÕES ---
    def get_users_within_notification_window(self):
        from app.config import load_or_create_config
        config = load_or_create_config()
        days_to_notify = config.get("DAYS_TO_NOTIFY_EXPIRATION", 0)
        
        if not days_to_notify > 0: 
            return []
        
        user_expirations = self.data_manager.get_all_user_expirations()
        
        local_tz = _get_local_tz()
        today_local = datetime.now(local_tz).date()
        users_to_check = []
        
        for plex_id, data in user_expirations.items():
            try:
                if data.get('expiration_date'):
                    exp_date_utc = datetime.fromisoformat(data['expiration_date'])
                    if exp_date_utc.tzinfo is None:
                        exp_date_utc = exp_date_utc.replace(tzinfo=timezone.utc)
                    
                    exp_date_local = exp_date_utc.astimezone(local_tz).date()
                    
                    days_left = (exp_date_local - today_local).days

                    if 0 <= days_left < days_to_notify:
                        users_to_check.append(plex_id)
            except (ValueError, TypeError): 
                continue
                
        return users_to_check

    def send_expiration_notification_if_needed(self, user_info):
        media_user_id = user_info['id']
        profile = self.data_manager.get_user_profile(media_user_id)
        if not profile:
            return
        
        from app.config import load_or_create_config
        config = load_or_create_config()
        days_to_notify = config.get("DAYS_TO_NOTIFY_EXPIRATION", 0)

        last_sent_str = profile.get('last_notification_sent')
        if last_sent_str:
            try:
                last_sent_dt = datetime.fromisoformat(last_sent_str)
                if last_sent_dt.tzinfo is None:
                    last_sent_dt = last_sent_dt.replace(tzinfo=timezone.utc)

                if (datetime.now(timezone.utc) - last_sent_dt) < timedelta(hours=23):
                    logger.info(f"Notificação para {user_info['username']} já foi enviada nas últimas 23 horas. A saltar.")
                    return
            except (ValueError, TypeError):
                pass

        expiration_date_str = profile.get('expiration_date')
        if expiration_date_str:
            try:
                local_tz = _get_local_tz()
                today_local = datetime.now(local_tz).date()

                exp_date_utc = datetime.fromisoformat(expiration_date_str)
                if exp_date_utc.tzinfo is None:
                    exp_date_utc = exp_date_utc.replace(tzinfo=timezone.utc)

                days_left = (exp_date_utc.astimezone(local_tz).date() - today_local).days

                if days_left >= days_to_notify:
                    return

                if days_left < 0:
                    logger.debug(f"Utilizador {user_info.get('username')} expirou há {abs(days_left)} dia(s). Notificação preventiva ignorada.")
                    return

                self.notifier_manager.send_expiration_notification(user_info, days_left, profile)
                self.data_manager.update_user_notification_timestamp(media_user_id)
            except (ValueError, TypeError) as e:
                logger.error(f"Erro ao processar data de expiração para '{user_info['username']}': {e}")
        
    def get_users_to_remove(self):
        from app.config import load_or_create_config
        config = load_or_create_config()
        days_to_remove = config.get("DAYS_TO_REMOVE_BLOCKED_USER", 0)
        
        if not days_to_remove > 0: 
            return []
            
        blocked_users_data = self.data_manager.get_blocked_users_dict()
        if not blocked_users_data: 
            return []

        local_tz = _get_local_tz()
        today_local = datetime.now(local_tz).date()
        users_to_remove = []
        
        for plex_id, block_data in blocked_users_data.items():
            try:
                blocked_at_str = block_data.get('blocked_at')
                if not blocked_at_str:
                    continue

                blocked_utc = datetime.fromisoformat(blocked_at_str)
                if blocked_utc.tzinfo is None:
                    blocked_utc = blocked_utc.replace(tzinfo=timezone.utc)

                blocked_local = blocked_utc.astimezone(local_tz).date()
                
                if (today_local - blocked_local).days >= days_to_remove:
                    users_to_remove.append(plex_id)

            except (ValueError, TypeError, AttributeError): 
                continue
                
        return users_to_remove