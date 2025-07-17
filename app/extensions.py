# app/extensions.py

from flask_login import LoginManager, AnonymousUserMixin
from flask_babel import Babel
from apscheduler.schedulers.background import BackgroundScheduler
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# --- Classe Personalizada para Utilizadores Anónimos ---
class MyAnonymousUser(AnonymousUserMixin):
    """Define um utilizador anónimo com os atributos esperados."""
    def __init__(self):
        self.username = 'Guest'
        self.role = 'guest'
    
    def is_admin(self):
        return False

# --- Instancia as extensões (sem inicializar) ---
# Estas variáveis serão importadas por outras partes da aplicação.
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
login_manager.anonymous_user = MyAnonymousUser
login_manager.login_view = "auth.login"
login_manager.login_message = "Por favor, faça login para acessar esta página."
login_manager.login_message_category = "info"

babel = Babel()
scheduler = BackgroundScheduler(daemon=True)

# Os 'managers' dos serviços foram removidos daqui para quebrar a importação circular.
# Eles serão inicializados dentro da 'application factory' no ficheiro __init__.py.
