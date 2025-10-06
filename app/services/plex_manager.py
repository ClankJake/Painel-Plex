# app/services/plex_manager.py

import logging
import base64
from urllib.parse import urlparse
from flask import current_app, url_for
from flask_babel import gettext as _
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError, RequestException
from datetime import date, datetime, timezone, timedelta
from tzlocal import get_localzone

from .plex.connection import PlexConnectionManager
from .plex.user_manager import PlexUserManager
from .plex.invite_manager import PlexInviteManager
from .plex.subscription_manager import PlexSubscriptionManager

logger = logging.getLogger(__name__)

class PlexManager:
    """
    Atua como uma fachada, a coordenar vários serviços relacionados com o Plex.
    """
    def __init__(self, data_manager, tautulli_manager, notifier_manager, overseerr_manager):
        self.conn = PlexConnectionManager()
        self.users = PlexUserManager(self.conn, data_manager, tautulli_manager, overseerr_manager)
        self.invites = PlexInviteManager(self.conn, self.users, data_manager, self, overseerr_manager, notifier_manager)
        self.subscriptions = PlexSubscriptionManager(data_manager, self.users)
        self.subscriptions.plex_manager = self
        self.stream_manager = None
        self.data_manager = data_manager
        self.tautulli_manager = tautulli_manager
        self.notifier_manager = notifier_manager
        self.overseerr_manager = overseerr_manager
        self.app = None
        self.plex = None
        self.account = None

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
                self.conn.plex.library.sections()
                return {"status": "ONLINE", "message": _("Conectado com sucesso.")}
            except Exception as e:
                logger.warning(f"Falha na verificação de estado do Plex: {e}")
                return {"status": "OFFLINE", "message": _("Falha na comunicação com o servidor Plex.")}
        return {"status": "OFFLINE", "message": _("Não configurado ou falha na conexão inicial.")}

    # --- Métodos de Fachada ---
    def get_user_by_id(self, plex_user_id):
        """Método de fachada para obter um utilizador pelo ID."""
        return self.users.get_user_by_id(plex_user_id)
        
    def update_screen_limit(self, plex_user_id, screens):
        """Atualiza o limite de telas para um utilizador no banco de dados."""
        profile = self.data_manager.get_user_profile(plex_user_id)
        profile['screen_limit'] = screens
        self.data_manager.set_user_profile(plex_user_id, profile)
        logger.info(f"Limite de telas para o utilizador ID '{plex_user_id}' atualizado para {screens}.")

    def block_user(self, plex_user_id, reason='manual'):
        if self.stream_manager and not self.users.stream_manager:
            self.users.stream_manager = self.stream_manager
        return self.users.block_user(plex_user_id, reason)

    def unblock_user(self, plex_user_id):
        return self.users.unblock_user(plex_user_id)

    def get_active_sessions(self):
        if not self.conn.plex:
            return {"success": False, "sessions": [], "stream_count": 0}
        
        try:
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
                video_decision, audio_decision = "Direct Play", "Direct Play"
                transcode_progress = None
                if transcode_session := getattr(s, "transcodeSession", None):
                    if getattr(transcode_session, 'videoDecision', 'copy') == "transcode": is_transcoding, video_decision = True, "Transcode"
                    if getattr(transcode_session, 'audioDecision', 'copy') == "transcode": is_transcoding, audio_decision = True, "Transcode"
                    if is_transcoding and (t_progress := getattr(transcode_session, "progress", None)) is not None:
                        try: transcode_progress = int(t_progress)
                        except (ValueError, TypeError): pass

                media = s.media[0] if s.media else None
                video_codec = (getattr(media, "videoCodec", None) or "N/A").upper()
                audio_codec = (getattr(media, "audioCodec", None) or "N/A").upper()
                container = (getattr(media, "container", None) or "N/A").upper()
                video_resolution = getattr(media, "videoResolution", "N/A")
                try: video_resolution = f"{int(video_resolution)}p"
                except (ValueError, TypeError): pass
                
                title, subtitle = s.title, str(s.year) if hasattr(s, 'year') and s.year else ''
                if s.type == 'episode':
                    title, subtitle = s.grandparentTitle, f"S{s.parentIndex:02d} · E{s.index:02d} - {s.title}"
                
                thumb_key = s.grandparentThumb if s.type == 'episode' and hasattr(s, 'grandparentThumb') and s.grandparentThumb else s.thumb
                thumb_url = None
                if thumb_key:
                    b64_payload = base64.urlsafe_b64encode(f"plex:{thumb_key}".encode('utf-8')).decode('utf-8')
                    thumb_url = url_for('image.proxy_image', source=b64_payload)
                
                session_details.append({
                    "session_key": s.sessionKey, "user": s.user.title, "user_thumb": user_thumb_map.get(s.user.id),
                    "player": s.player.title, "platform": s.player.platform, "type": s.type,
                    "title": title, "subtitle": subtitle, "progress": round(progress, 2),
                    "view_offset": view_offset, "duration": duration, "thumb_url": thumb_url, "state": state,
                    "stream_details": { "video_decision": video_decision, "audio_decision": audio_decision, "video_codec": video_codec, "audio_codec": audio_codec, "video_resolution": video_resolution, "stream": "Transcode" if is_transcoding else "Direct Play", "container": container, "is_transcoding": is_transcoding, "transcode_progress": transcode_progress }
                })
            return {"success": True, "sessions": session_details, "stream_count": len(sessions)}
        except (ConnectionError, ReadTimeout, RequestException) as e:
            return {"success": False, "sessions": [], "stream_count": 0}
        except Exception as e:
            return {"success": False, "sessions": [], "stream_count": 0}

    def get_libraries(self): return self.conn.get_libraries()
    def get_all_plex_users(self, force_refresh=False): return self.users.get_all_plex_users(force_refresh)
    def get_user_libraries(self, plex_user_id): return self.users.get_user_libraries(plex_user_id)
    def update_user_libraries(self, plex_user_id, library_titles): return self.users.update_user_libraries(plex_user_id, library_titles)
    def update_all_users_libraries(self, library_titles): return self.users.update_all_users_libraries(library_titles)
    def remove_user(self, plex_user_id): return self.users.remove_user(plex_user_id) if not self.stream_manager or not setattr(self.users, 'stream_manager', self.stream_manager) else self.users.remove_user(plex_user_id)
    def toggle_overseerr_access(self, plex_user_id, access: bool): return self.users.toggle_overseerr_access(plex_user_id, access)
    def create_invitation(self, **kwargs): return self.invites.create_invitation(**kwargs)
    def get_invitation_by_code(self, code): return self.invites.get_invitation_by_code(code)
    def claim_invitation(self, code, plex_user_account): return self.invites.claim_invitation(code, plex_user_account)
    def list_invitations(self): return self.invites.list_invitations()
    def delete_invitation(self, code): return self.invites.delete_invitation(code)
    def renew_subscription(self, plex_user_id, months_to_add, base_mode='today', base_date_str=None, expiration_time_str=None): return self.subscriptions.renew_subscription(plex_user_id, months_to_add, base_mode, base_date_str, expiration_time_str)

    def get_users_within_notification_window(self):
        from app.config import load_or_create_config
        config = load_or_create_config()
        days_to_notify = config.get("DAYS_TO_NOTIFY_EXPIRATION", 0)
        if not days_to_notify > 0: return []
        
        user_expirations = self.data_manager.get_all_user_expirations()
        today = datetime.now(get_localzone()).date()
        users_to_check = []
        for plex_id, data in user_expirations.items():
            try:
                if data.get('expiration_date'):
                    exp_date = datetime.fromisoformat(data['expiration_date']).date()
                    if 0 <= (exp_date - today).days < days_to_notify:
                        users_to_check.append(plex_id)
            except (ValueError, TypeError): continue
        return users_to_check

    def send_expiration_notification_if_needed(self, user_info):
        plex_user_id = user_info['id']
        profile = self.data_manager.get_user_profile(plex_user_id)
        
        last_sent_str = profile.get('last_notification_sent')
        if last_sent_str:
            try:
                # CORREÇÃO: Lógica de verificação mais robusta.
                # Em vez de comparar apenas a data, verifica se já se passaram pelo menos 23 horas
                # desde o último envio. Isto evita problemas com fusos horários e pequenos atrasos na tarefa.
                last_sent_dt = datetime.fromisoformat(last_sent_str)
                if (datetime.now(timezone.utc) - last_sent_dt) < timedelta(hours=23):
                    logger.info(f"Notificação para {user_info['username']} já foi enviada nas últimas 23 horas. A saltar.")
                    return
            except (ValueError, TypeError):
                # Se o formato da data for inválido, ignora e tenta enviar a notificação.
                pass

        expiration_date_str = profile.get('expiration_date')
        if expiration_date_str:
            try:
                # A lógica para calcular os dias restantes permanece a mesma.
                days_left = (datetime.fromisoformat(expiration_date_str).date() - date.today()).days
                self.notifier_manager.send_expiration_notification(user_info, days_left, profile)
                self.data_manager.update_user_notification_timestamp(plex_user_id)
            except (ValueError, TypeError) as e:
                logger.error(f"Erro ao processar data de expiração para '{user_info['username']}': {e}")
        
    def get_users_to_remove(self):
        from app.config import load_or_create_config
        config = load_or_create_config()
        days_to_remove = config.get("DAYS_TO_REMOVE_BLOCKED_USER", 0)
        if not days_to_remove > 0: return []
            
        blocked_users_data = self.data_manager.get_blocked_users_dict()
        if not blocked_users_data: return []
            
        today = datetime.now(get_localzone()).date()
        users_to_remove = []
        for plex_id, block_data in blocked_users_data.items():
            try:
                if (today - datetime.fromisoformat(block_data.get('blocked_at')).date()).days >= days_to_remove:
                    users_to_remove.append(plex_id)
            except (ValueError, TypeError, AttributeError): continue
        return users_to_remove
