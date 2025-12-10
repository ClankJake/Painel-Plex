# app/extensions.py

from flask_login import LoginManager, AnonymousUserMixin
from flask_babel import Babel
from apscheduler.schedulers.background import BackgroundScheduler
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- Classe Personalizada para Utilizadores Anónimos ---
class MyAnonymousUser(AnonymousUserMixin):
    """Define um utilizador anónimo com os atributos esperados."""
    def __init__(self):
        self.username = 'Guest'
        self.role = 'guest'
    
    def is_admin(self):
        return False

# --- Instancia as extensões (sem inicializar) ---
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.anonymous_user = MyAnonymousUser
login_manager.login_view = "auth.login"
login_manager.login_message = "Por favor, faça login para acessar esta página."
login_manager.login_message_category = "info"

babel = Babel()
scheduler = BackgroundScheduler(daemon=True)
socketio = SocketIO()
# Adiciona a instância do cache
cache = Cache()

# Adiciona o Limiter (Rate Limiting)
# Usa o endereço IP remoto como chave para limitar requisições
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")

# Os 'managers' dos serviços são inicializados como None
data_manager = None
plex_manager = None
tautulli_manager = None
stream_manager = None 
notifier_manager = None
efi_manager = None
mercado_pago_manager = None
bpix_manager = None
overseerr_manager = None
link_shortener = None
pricing_manager = None
