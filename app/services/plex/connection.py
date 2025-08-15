# app/services/plex/connection.py
import logging
from flask_babel import gettext as _
from plexapi.server import PlexServer
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError

# Import absoluto a partir do pacote 'app'
from app.config import load_or_create_config

logger = logging.getLogger(__name__)

class PlexConnectionManager:
    """
    Gere a conexão principal com o servidor Plex e a MyPlexAccount.
    """
    def __init__(self):
        self.plex = None
        self.account = None

    def reload(self):
        """
        Recarrega a configuração e tenta conectar-se ao servidor Plex.
        """
        logger.info(_("A recarregar a conexão com o Plex..."))
        config = load_or_create_config()
        try:
            if not all(k in config and config[k] for k in ["PLEX_URL", "PLEX_TOKEN"]):
                raise ValueError(_("Configurações do Plex (URL e Token) não encontradas ou estão vazias."))
            
            self.plex = PlexServer(config["PLEX_URL"], config["PLEX_TOKEN"], timeout=20)
            self.account = self.plex.myPlexAccount()
            logger.info(_("Conexão com o Plex recarregada com sucesso."))
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
        Busca as sessões de reprodução ativas no servidor Plex, retornando detalhes.
        """
        if not self.plex:
            logger.warning(_("Não é possível obter sessões ativas. A conexão com o Plex não foi estabelecida."))
            return {"success": False, "sessions": [], "stream_count": 0}
        
        try:
            sessions = self.plex.sessions()
            session_details = []
            for s in sessions:
                progress = (s.viewOffset / s.duration) * 100 if s.duration else 0
                
                # MELHORIA: Prioriza a arte da série para episódios
                if s.type == 'episode':
                    art_key = s.grandparentArt if hasattr(s, 'grandparentArt') and s.grandparentArt else s.art
                    thumb_key = s.grandparentThumb if hasattr(s, 'grandparentThumb') and s.grandparentThumb else s.thumb
                else:
                    art_key = s.art if hasattr(s, 'art') and s.art else None
                    thumb_key = s.thumb if hasattr(s, 'thumb') and s.thumb else None

                art_url = self.plex.url(art_key, includeToken=True) if art_key else None
                thumb_url = self.plex.url(thumb_key, includeToken=True) if thumb_key else None
                
                session_details.append({
                    "user": s.user.title,
                    "user_thumb": s.user.thumb,
                    "player": s.player.title,
                    "platform": s.player.platform,
                    "type": s.type,
                    "title": s.title,
                    "series": s.grandparentTitle if s.type == 'episode' else None,
                    "season_episode": f"S{s.parentIndex} · E{s.index}" if s.type == 'episode' else None,
                    "year": s.year if hasattr(s, 'year') else None,
                    "duration": s.duration,
                    "view_offset": s.viewOffset,
                    "progress": round(progress, 2),
                    "art_url": art_url,
                    "thumb_url": thumb_url,
                    "state": getattr(s, 'state', 'playing')
                })
            return {"success": True, "sessions": session_details, "stream_count": len(sessions)}
        except Exception as e:
            logger.error(_("Erro inesperado ao obter sessões do Plex: %(error)s", error=e))
            return {"success": False, "sessions": [], "stream_count": 0}

    def get_libraries(self):
        """
        Obtém uma lista de todas as bibliotecas do servidor Plex.
        """
        if not self.plex:
            return []
        try:
            return [{'title': s.title, 'key': s.key} for s in self.plex.library.sections()]
        except Exception as e:
            logger.error(_("Não foi possível obter as bibliotecas do servidor Plex: %(error)s", error=e))
            return []
