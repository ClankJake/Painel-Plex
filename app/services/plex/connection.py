# app/services/plex/connection.py
import logging
import base64
from urllib.parse import urlparse
from flask_babel import gettext as _
from plexapi.server import PlexServer
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError, RequestException
from flask import url_for

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

class PlexConnectionManager:
    """
    Gere a conexão principal com o servidor Plex e a MyPlexAccount.
    """
    def __init__(self):
        self.plex = None
        self.account = None

    def reload(self, from_job=False):
        """
        Recarrega a configuração e tenta conectar-se ao servidor Plex.
        """
        if not from_job:
            logger.info(_("A recarregar a conexão com o Plex..."))
        else:
            logger.debug("A estabelecer conexão com o Plex para tarefa agendada...")

        config = load_or_create_config()
        try:
            if not all(k in config and config[k] for k in ["PLEX_URL", "PLEX_TOKEN"]):
                raise ValueError(_("Configurações do Plex (URL e Token) não encontradas ou estão vazias."))
            
            self.plex = PlexServer(config["PLEX_URL"], config["PLEX_TOKEN"], timeout=20)
            self.account = self.plex.myPlexAccount()
            
            if not from_job:
                logger.info(_("Conexão com o Plex recarregada com sucesso."))
            else:
                logger.debug("Conexão com o Plex estabelecida para tarefa agendada.")

            return True, _("Configurações aplicadas e conexões testadas com sucesso.")
        
        except (ConnectTimeout, ReadTimeout, ConnectionError) as e:
            error_message = _(
                "Não foi possível conectar ao servidor Plex em '%(url)s'. A conexão expirou. "
                "Verifique se o servidor Plex está online, a URL e a porta estão corretas, e se um firewall não está a bloquear o acesso.",
                url=config.get("PLEX_URL")
            )
            logger.warning(f"{error_message} Erro original: {e}")
            self.plex = None
            self.account = None
            return False, error_message
        
        except Exception as e:
            logger.warning(_("Não foi possível conectar ao Plex: %(error)s. Verifique as configurações.", error=e))
            self.plex = None
            self.account = None
            return False, _("Falha ao aplicar novas configurações: %(error)s", error=e)

    def get_active_sessions(self):
        """
        Busca as sessões de reprodução ativas no servidor Plex, retornando detalhes aprimorados.
        """
        if not self.plex:
            logger.warning(_("Não é possível obter sessões ativas. A conexão com o Plex não foi estabelecida."))
            return {"success": False, "sessions": [], "stream_count": 0}
        
        try:
            sessions = self.plex.sessions()
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
                
                transcode_session = getattr(s, "transcodeSession", None)
                if transcode_session:
                    _video_decision = getattr(transcode_session, 'videoDecision', 'copy')
                    _audio_decision = getattr(transcode_session, 'audioDecision', 'copy')
                    
                    if _video_decision == "transcode": is_transcoding = True; video_decision = "Transcode"
                    if _audio_decision == "transcode": is_transcoding = True; audio_decision = "Transcode"

                stream_type = "Transcode" if is_transcoding else "Direct Play"
                media = s.media[0] if s.media else None
                video_codec = (getattr(media, "videoCodec", None) or "N/A").upper()
                audio_codec = (getattr(media, "audioCodec", None) or "N/A").upper()
                container = (getattr(media, "container", None) or "N/A").upper()
                video_resolution = getattr(media, "videoResolution", "N/A")

                title = s.title
                subtitle = str(s.year) if hasattr(s, 'year') and s.year else ''
                if s.type == 'episode':
                    title = s.grandparentTitle
                    subtitle = f"S{s.parentIndex:02d} · E{s.index:02d} - {s.title}"

                # Arte (Thumbnail) - Geração de URL Segura
                thumb_key = s.grandparentThumb if s.type == 'episode' and hasattr(s, 'grandparentThumb') and s.grandparentThumb else s.thumb
                thumb_url = None
                if thumb_key:
                    payload_str = f"plex:{thumb_key}"
                    b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                    thumb_url = url_for('image.proxy_image', source=b64_payload)
                
                # Arte do utilizador - Geração de URL Segura
                user_thumb_path = s.user.thumb
                user_thumb_url = None
                if user_thumb_path:
                    parsed_thumb = urlparse(user_thumb_path)
                    path_with_query = parsed_thumb.path
                    if parsed_thumb.query: path_with_query += "?" + parsed_thumb.query
                    
                    payload_str = f"plex_account:{path_with_query}"
                    b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                    user_thumb_url = url_for('image.proxy_image', source=b64_payload)

                session_details.append({
                    "session_key": s.sessionKey, "user": s.user.title, "user_thumb": user_thumb_url,
                    "player": s.player.title, "platform": s.player.platform, "type": s.type,
                    "title": title, "subtitle": subtitle, "progress": round(progress, 2),
                    "view_offset": view_offset, "duration": duration, "thumb_url": thumb_url, "state": state,
                    "stream_details": {
                        "video": f"{video_decision} ({video_codec} {video_resolution}p)",
                        "audio": f"{audio_decision} ({audio_codec})", "stream": stream_type, "container": container
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
        if not self.plex: return []
        try:
            return [{'title': s.title, 'key': s.key} for s in self.plex.library.sections()]
        except Exception as e:
            logger.error(_("Não foi possível obter as bibliotecas do servidor Plex: %(error)s", error=e))
            return []
