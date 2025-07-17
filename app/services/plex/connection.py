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
        Busca as sessões de reprodução ativas no servidor Plex.
        """
        if not self.plex:
            logger.warning(_("Não é possível obter sessões ativas. A conexão com o Plex não foi estabelecida."))
            return {"success": False, "stream_count": 0}
        
        try:
            sessions = self.plex.sessions()
            return {"success": True, "stream_count": len(sessions)}
        except Exception as e:
            logger.error(_("Erro inesperado ao obter sessões do Plex: %(error)s", error=e))
            return {"success": False, "stream_count": 0}

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
