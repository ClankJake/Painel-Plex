import os
import logging
import atexit
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from flask import Flask, request, redirect, url_for, session, jsonify, flash, send_from_directory, render_template, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import current_user, logout_user
from flask_babel import get_locale, gettext as _
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from urllib.parse import urlparse
from tzlocal import get_localzone_name

from . import extensions
from .config import load_or_create_config, is_configured
from .scheduler import setup_scheduler
from . import models
from . import sockets # Importa o novo módulo de sockets

logger = logging.getLogger(__name__)

@extensions.login_manager.user_loader
def load_user(user_id):
    """Carrega o usuário para o Flask-Login a partir dos detalhes na sessão."""
    user_details = session.get('user_details')
    if user_details and user_details.get('id') == user_id:
        return models.User(**user_details)
    return None

def shutdown_scheduler():
    """Garante que o agendador é desligado corretamente ao sair."""
    if extensions.scheduler.running:
        extensions.scheduler.shutdown()

def setup_logging(app, log_level='INFO'):
    """Configura o sistema de logging da aplicação."""
    log_level_map = {
        'DEBUG': logging.DEBUG, 'INFO': logging.INFO,
        'WARNING': logging.WARNING, 'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL,
    }
    level = log_level_map.get(log_level, logging.INFO)
    
    log_file = app.config.get('LOG_FILE')
    
    if not log_file:
        logging.critical("CRÍTICO: O caminho do ficheiro de log (LOG_FILE) não está definido ou está vazio no config.json. A aplicação não pode iniciar.")
        raise ValueError("LOG_FILE não está configurado.")

    log_dir = os.path.dirname(log_file)
    if log_dir: 
        os.makedirs(log_dir, exist_ok=True)
    
    max_bytes = app.config.get('LOG_MAX_BYTES', 1024 * 1024)
    backup_count = app.config.get('LOG_BACKUP_COUNT', 5)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)
        
    root_logger.addHandler(file_handler)
    root_logger.setLevel(level)
    
    app.logger.handlers = root_logger.handlers
    app.logger.setLevel(level)
    app.logger.propagate = False

    logger.info(f"Logging reconfigurado para nível {log_level} e ficheiro {log_file}.")


def create_app(log_level='INFO', _from_job=False):
    """
    Cria e configura uma instância da aplicação Flask (Application Factory).
    """
    app = Flask(__name__)
    
    def get_user_locale():
        """Seleciona o idioma a ser usado para a request atual."""
        if 'language' in session:
            return session['language']
        return request.accept_languages.best_match(app.config['LANGUAGES'].keys())

    app.config['LANGUAGES'] = {'pt_BR': 'Português'}
    app.config['BABEL_DEFAULT_LOCALE'] = 'pt_BR'
    
    app_config = load_or_create_config()
    app.config.update(app_config)
    
    app.config['SECRET_KEY'] = app_config.get('SECRET_KEY')
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = True

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'app_data.db')
    
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}?timeout=20'
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "connect_args": {
            "check_same_thread": False,
        }
    }
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    base_url = app.config.get('APP_BASE_URL')
    if base_url:
        parsed_url = urlparse(base_url)
        app.config['SERVER_NAME'] = parsed_url.netloc
        app.config['APPLICATION_ROOT'] = parsed_url.path or '/'
        app.config['PREFERRED_URL_SCHEME'] = parsed_url.scheme

    setup_logging(app, log_level)

    extensions.db.init_app(app)
    extensions.migrate.init_app(app, extensions.db)
    extensions.login_manager.init_app(app)
    extensions.babel.init_app(app, locale_selector=get_user_locale)
    
    # Inicializa o SocketIO com a app
    extensions.socketio.init_app(app, async_mode='eventlet')
    # Passa a instância da app para o módulo de sockets para que o contexto possa ser usado na tarefa de fundo
    sockets.app_instance = app

    if not extensions.scheduler.running:
        jobstores = {
            'default': SQLAlchemyJobStore(url=app.config['SQLALCHEMY_DATABASE_URI'])
        }
        try:
            local_tz_name = get_localzone_name()
        except Exception:
            local_tz_name = 'UTC'
        
        logger.info(f"Configurando o fuso horário do agendador para: {local_tz_name}")
        extensions.scheduler.configure(jobstores=jobstores, timezone=local_tz_name)

    from .services import (
        DataManager, TautulliManager, PlexManager, 
        NotifierManager, EfiManager, MercadoPagoManager,
        OverseerrManager, LinkShortener
    )

    extensions.data_manager = DataManager()
    extensions.tautulli_manager = TautulliManager(data_manager=extensions.data_manager)
    extensions.link_shortener = LinkShortener()
    extensions.notifier_manager = NotifierManager(link_shortener_service=extensions.link_shortener)
    extensions.efi_manager = EfiManager(data_manager=extensions.data_manager)
    extensions.mercado_pago_manager = MercadoPagoManager(data_manager=extensions.data_manager)
    extensions.overseerr_manager = OverseerrManager()
    extensions.plex_manager = PlexManager(
        data_manager=extensions.data_manager, 
        tautulli_manager=extensions.tautulli_manager,
        notifier_manager=extensions.notifier_manager,
        overseerr_manager=extensions.overseerr_manager
    )
    extensions.plex_manager.init_app(app)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    
    try:
        if is_configured() and not _from_job and not extensions.scheduler.running:
            if not extensions.scheduler.running:
                setup_scheduler(app)
                atexit.register(shutdown_scheduler)
    except Exception as e:
        logger.error(f"Falha ao iniciar o agendador de tarefas: {e}")

    @app.context_processor
    def inject_global_vars():
        return {
            'current_locale': get_locale(),
            'app_title': app.config.get('APP_TITLE', 'Painel Plex'),
            'cache_buster': int(datetime.now().timestamp())
        }

    @app.route('/language/<lang>')
    def set_language(lang=None):
        if lang in app.config['LANGUAGES'].keys():
            session['language'] = lang
        return redirect(request.referrer or url_for('main.index'))

    @app.route('/manifest.json')
    def serve_manifest():
        return render_template('manifest.json')

    @app.route('/service-worker.js')
    def serve_sw():
        return send_from_directory(os.path.join(app.root_path, 'static', 'js'),
                                   'service-worker.js',
                                   mimetype='application/javascript')

    @app.before_request
    def check_configuration_and_user():
        exempt_endpoints = {
            'static', 'main.setup', 'auth.login', 'auth.get_plex_auth_context', 
            'auth.check_plex_pin', 'auth.check_plex_pin_for_token', 'auth.redirect_to_auth', 'auth.auth_status',
            'system_api.save_setup', 'system_api.get_plex_servers', 'system_api.test_tautulli_connection', 
            'system_api.test_overseerr_connection', 'system_api.auto_configure_tautulli_notifier',
            'system_api.get_logs', 'system_api.clear_logs',
            'invites_api.get_invite_details_route', 'invites_api.claim_invite_route',
            'payments_api.efi_webhook', 'payments_api.mercadopago_webhook',
            'set_language', 'main.claim_invite_page', 'serve_manifest', 'serve_sw',
            'main.payment_page', 'users_api.get_public_user_profile_by_token', 'payments_api.get_payment_options',
            'payments_api.create_charge_route', 'payments_api.get_payment_status',
            'redirect.redirect_to_url'
        }
        if request.endpoint in exempt_endpoints or request.path.startswith('/socket.io'):
            return

        if not is_configured():
            return redirect(url_for('main.setup'))
        
        if current_user.is_authenticated and not current_user.is_admin():
            logger.debug(f"DIAGNÓSTICO: A iniciar verificação de acesso para o utilizador '{current_user.username}' no endpoint '{request.endpoint}'.")
            try:
                all_users = extensions.plex_manager.get_all_plex_users(force_refresh=False)

                if all_users is None:
                    logger.warning(f"DIAGNÓSTICO: Não foi possível obter a lista de utilizadores do Plex. A verificação para '{current_user.username}' foi ignorada nesta requisição.")
                    return

                plex_usernames = [user['username'] for user in all_users]
                logger.debug(f"DIAGNÓSTICO: Encontrados {len(plex_usernames)} utilizadores no servidor. A verificar se '{current_user.username}' está na lista.")
                
                if current_user.username not in plex_usernames:
                    logger.warning(f"DIAGNÓSTICO: O utilizador '{current_user.username}' NÃO foi encontrado na lista de acesso atual do Plex. A terminar a sessão.")
                    flash(_("O seu acesso a este servidor foi removido. Você foi desconectado."), "warning")
                    logout_user()
                    return redirect(url_for('auth.login'))
                else:
                    logger.debug(f"DIAGNÓSTICO: O utilizador '{current_user.username}' foi encontrado. Acesso permitido.")
                    
            except Exception as e:
                logger.error(f"DIAGNÓSTICO: Ocorreu uma exceção ao verificar o acesso do utilizador '{current_user.username}': {e}")
        
        if current_user.is_authenticated and not current_user.is_admin():
            if request.endpoint in ('main.index', 'main.settings_page', 'main.users_page'):
                return redirect(url_for('main.statistics_page'))

        if current_user.is_authenticated and request.endpoint == 'auth.login':
            return redirect(url_for('main.index'))

    # --- REGISTO DOS BLUEPRINTS ---
    from .blueprints.main import main_bp
    from .blueprints.auth import auth_bp
    from .blueprints.redirect import redirect_bp
    # Importar os novos blueprints da API
    from .blueprints.api.system import system_api_bp
    from .blueprints.api.users import users_api_bp
    from .blueprints.api.invites import invites_api_bp
    from .blueprints.api.payments import payments_api_bp
    from .blueprints.api.stats import stats_api_bp
    from .blueprints.api.notifications import notifications_api_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(redirect_bp)
    # Registar os novos blueprints, todos sob o prefixo /api
    app.register_blueprint(system_api_bp, url_prefix='/api')
    app.register_blueprint(users_api_bp, url_prefix='/api/users')
    app.register_blueprint(invites_api_bp, url_prefix='/api/invites')
    app.register_blueprint(payments_api_bp, url_prefix='/api/payments')
    app.register_blueprint(stats_api_bp, url_prefix='/api/statistics')
    app.register_blueprint(notifications_api_bp, url_prefix='/api/notifications')

    return app
