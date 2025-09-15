# app/services/plex_manager.py

import logging
import base64
from urllib.parse import urlparse
from flask import current_app, url_for
from flask_babel import gettext as _
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError, RequestException

from .plex.connection import PlexConnectionManager
from .plex.user_manager import PlexUserManager
from .plex.invite_manager import PlexInviteManager
from .plex.subscription_manager import PlexSubscriptionManager

logger = logging.getLogger(__name__)

class PlexManager:
    """
    Atua como uma fachada, a coordenar vários serviços relacionados com o Plex.
    Isto mantém a API pública consistente para o resto da aplicação,
    enquanto a lógica é separada em classes de gestores mais pequenas e focadas.
    """
    def __init__(self, data_manager, tautulli_manager, notifier_manager, overseerr_manager):
        # Instancia todos os sub-gestores
        self.conn = PlexConnectionManager()
        self.users = PlexUserManager(self.conn, data_manager, tautulli_manager, overseerr_manager)
        
        # CORREÇÃO: Passa 'self' (a instância do PlexManager) para o PlexInviteManager
        # em vez do 'tautulli_manager', que estava a ser passado incorretamente.
        self.invites = PlexInviteManager(self.conn, self.users, data_manager, self, overseerr_manager, notifier_manager)
        
        self.subscriptions = PlexSubscriptionManager(data_manager, self.users)
        
        # Injeta a instância do PlexManager (self) no SubscriptionManager para resolver a dependência circular
        self.subscriptions.plex_manager = self
        
        # O StreamManager será injetado após a inicialização para evitar dependência circular
        self.stream_manager = None
        
        # Mantém referências a outros gestores para injeção de dependências
        self.data_manager = data_manager
        self.tautulli_manager = tautulli_manager
        self.notifier_manager = notifier_manager
        self.overseerr_manager = overseerr_manager
        
        self.app = None
        self.plex = None # Para retrocompatibilidade
        self.account = None # Para retrocompatibilidade

    def init_app(self, app):
        from app.config import is_configured
        self.app = app
        if is_configured():
            self.reload_connections()

    def reload_connections(self, from_job=False):
        """Recarrega as conexões e atualiza as referências dos objetos principais."""
        success, message = self.conn.reload(from_job=from_job)
        if success:
            self.plex = self.conn.plex
            self.account = self.conn.account
            self.users.invalidate_user_cache()
            if self.app:
                from app.config import load_or_create_config
                self.app.config.update(load_or_create_config())
        return success, message

    def check_status(self):
        """Verifica o estado da conexão com o Plex."""
        if self.conn and self.conn.plex and self.conn.account:
            try:
                # Tenta uma operação leve para confirmar que a conexão está ativa
                self.conn.plex.library.sections()
                return {"status": "ONLINE", "message": _("Conectado com sucesso.")}
            except Exception as e:
                logger.warning(f"Falha na verificação de estado do Plex: {e}")
                return {"status": "OFFLINE", "message": _("Falha na comunicação com o servidor Plex.")}
        return {"status": "OFFLINE", "message": _("Não configurado ou falha na conexão inicial.")}

    # --- Métodos de Fachada ---
    
    # CORREÇÃO: Adiciona o método 'update_screen_limit' que estava em falta.
    def update_screen_limit(self, username, screens):
        """Atualiza o limite de telas para um utilizador no banco de dados."""
        profile = self.data_manager.get_user_profile(username)
        profile['screen_limit'] = screens
        self.data_manager.set_user_profile(username, profile)
        logger.info(f"Limite de telas para '{username}' atualizado para {screens}.")

    def block_user(self, email, reason='manual'):
        """
        Bloqueia um utilizador, terminando as suas sessões e atualizando o estado.
        """
        if self.stream_manager and not self.users.stream_manager:
            self.users.stream_manager = self.stream_manager
        return self.users.block_user(email, reason)

    def unblock_user(self, email):
        """
        Desbloqueia um utilizador.
        """
        return self.users.unblock_user(email)

    def get_active_sessions(self):
        """
        Busca as sessões de reprodução ativas no servidor Plex, retornando detalhes aprimorados.
        Este método é otimizado para evitar erros de lookup de utilizador quando um "Nome Completo" é definido no Plex.
        """
        if not self.conn.plex:
            logger.warning(_("Não é possível obter sessões ativas. A conexão com o Plex não foi estabelecida."))
            return {"success": False, "sessions": [], "stream_count": 0}
        
        try:
            # 1. Obter uma lista fiável de todos os utilizadores e criar um mapa de ID -> thumb
            all_users = self.users.get_all_plex_users() or []
            user_thumb_map = {user['id']: user['thumb'] for user in all_users}

            sessions = self.conn.plex.sessions()
            session_details = []
            for s in sessions:
                progress = 0
                view_offset = getattr(s, 'viewOffset', 0)
                duration = getattr(s, 'duration', 0)
                if view_offset and duration and duration > 0:
                    progress = (view_offset / duration) * 100

                state = "stopped"
                if s.players:
                    player_state = getattr(s.players[0], "state", "stopped")
                    state = {"paused": "paused", "playing": "playing", "buffering": "buffering"}.get(player_state, "stopped")

                is_transcoding = False
                video_decision = "Direct Play"
                audio_decision = "Direct Play"
                transcode_progress = None
                
                transcode_session = getattr(s, "transcodeSession", None)
                if transcode_session:
                    _video_decision = getattr(transcode_session, 'videoDecision', 'copy')
                    _audio_decision = getattr(transcode_session, 'audioDecision', 'copy')
                    
                    if _video_decision == "transcode": is_transcoding = True; video_decision = "Transcode"
                    if _audio_decision == "transcode": is_transcoding = True; audio_decision = "Transcode"

                    if is_transcoding:
                        t_progress = getattr(transcode_session, "progress", None)
                        if t_progress is not None:
                            try: transcode_progress = int(t_progress)
                            except (ValueError, TypeError): pass

                stream_type = "Transcode" if is_transcoding else "Direct Play"
                media = s.media[0] if s.media else None
                video_codec = (getattr(media, "videoCodec", None) or "N/A").upper()
                audio_codec = (getattr(media, "audioCodec", None) or "N/A").upper()
                container = (getattr(media, "container", None) or "N/A").upper()
                
                video_resolution_raw = getattr(media, "videoResolution", "N/A")
                video_resolution = video_resolution_raw
                try:
                    int_res = int(video_resolution_raw)
                    video_resolution = f"{int_res}p"
                except (ValueError, TypeError): pass

                title = s.title
                subtitle = str(s.year) if hasattr(s, 'year') and s.year else ''
                if s.type == 'episode':
                    title = s.grandparentTitle
                    subtitle = f"S{s.parentIndex:02d} · E{s.index:02d} - {s.title}"

                thumb_key = s.grandparentThumb if s.type == 'episode' and hasattr(s, 'grandparentThumb') and s.grandparentThumb else s.thumb
                thumb_url = None
                if thumb_key:
                    payload_str = f"plex:{thumb_key}"
                    b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                    thumb_url = url_for('image.proxy_image', source=b64_payload)
                
                # CORREÇÃO: Utiliza o mapa pré-carregado para obter o avatar do utilizador
                user_thumb_url = user_thumb_map.get(s.user.id)

                session_details.append({
                    "session_key": s.sessionKey, "user": s.user.title, "user_thumb": user_thumb_url,
                    "player": s.player.title, "platform": s.player.platform, "type": s.type,
                    "title": title, "subtitle": subtitle, "progress": round(progress, 2),
                    "view_offset": view_offset, "duration": duration, "thumb_url": thumb_url, "state": state,
                    "stream_details": {
                        "video_decision": video_decision, "audio_decision": audio_decision,
                        "video_codec": video_codec, "audio_codec": audio_codec,
                        "video_resolution": video_resolution, "stream": stream_type,
                        "container": container, "is_transcoding": is_transcoding,
                        "transcode_progress": transcode_progress
                    }
                })
            return {"success": True, "sessions": session_details, "stream_count": len(sessions)}
        except (ConnectionError, ReadTimeout, RequestException) as e:
            logger.warning(_("Erro de conexão temporário ao obter sessões do Plex: %(error)s. A tentar novamente no próximo ciclo.", error=e))
            return {"success": False, "sessions": [], "stream_count": 0}
        except Exception as e:
            logger.error(_("Erro inesperado ao obter sessões do Plex: %(error)s", error=e), exc_info=True)
            return {"success": False, "sessions": [], "stream_count": 0}

    def get_libraries(self):
        return self.conn.get_libraries()

    def get_all_plex_users(self, force_refresh=False):
        return self.users.get_all_plex_users(force_refresh)

    def get_user_libraries(self, email):
        return self.users.get_user_libraries(email)

    def update_user_libraries(self, email, library_titles):
        return self.users.update_user_libraries(email, library_titles)

    def update_all_users_libraries(self, library_titles):
        return self.users.update_all_users_libraries(library_titles)

    def remove_user(self, email):
        if self.stream_manager and not self.users.stream_manager:
            self.users.stream_manager = self.stream_manager
        return self.users.remove_user(email)

    def toggle_overseerr_access(self, email, username, access: bool):
        return self.users.toggle_overseerr_access(email, username, access)

    def create_invitation(self, **kwargs):
        return self.invites.create_invitation(**kwargs)

    def get_invitation_by_code(self, code):
        return self.invites.get_invitation_by_code(code)

    def claim_invitation(self, code, plex_user_account):
        return self.invites.claim_invitation(code, plex_user_account)

    def list_invitations(self):
        return self.invites.list_invitations()

    def delete_invitation(self, code):
        return self.invites.delete_invitation(code)

    def renew_subscription(self, username, months_to_add, base_mode='today', base_date_str=None, expiration_time_str=None):
        return self.subscriptions.renew_subscription(username, months_to_add, base_mode, base_date_str, expiration_time_str)

    # --- Métodos que permanecem no gestor principal para coordenação ---

    def get_users_to_notify(self):
        from app.config import load_or_create_config
        from datetime import datetime
        from tzlocal import get_localzone

        users_to_notify = []
        config = load_or_create_config()
        days_to_notify = config.get("DAYS_TO_NOTIFY_EXPIRATION", 0)
        if not days_to_notify > 0: return []
            
        user_expirations = self.data_manager.get_all_user_expirations()
        today = datetime.now(get_localzone()).date()
        
        for username, data in user_expirations.items():
            try:
                if data.get('expiration_date'):
                    exp_date = datetime.fromisoformat(data['expiration_date']).date()
                    days_diff = (exp_date - today).days
                    if 0 <= days_diff < days_to_notify:
                        users_to_notify.append(username)
            except (ValueError, TypeError):
                continue
        return users_to_notify
        
    def get_users_to_remove(self):
        from app.config import load_or_create_config
        from datetime import datetime
        from tzlocal import get_localzone

        users_to_remove = []
        config = load_or_create_config()
        days_to_remove = config.get("DAYS_TO_REMOVE_BLOCKED_USER", 0)
        if not days_to_remove > 0: return []

        blocked_users_data = self.data_manager.get_blocked_users_dict()
        if not blocked_users_data: return []
            
        today = datetime.now(get_localzone()).date()
        for username, block_data in blocked_users_data.items():
            try:
                ref_date = datetime.fromisoformat(block_data.get('blocked_at')).date()
                if (today - ref_date).days >= days_to_remove:
                    users_to_remove.append(username)
            except (ValueError, TypeError, AttributeError):
                continue
        return users_to_remove
