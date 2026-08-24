import os
import logging
import atexit
import signal
from datetime import datetime, timedelta
from urllib.parse import urlparse
from tzlocal import get_localzone_name

from flask import Flask, request, redirect, url_for, session, jsonify, render_template, send_from_directory, has_request_context
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_login import current_user
from flask_babel import get_locale, gettext as _
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import event

from . import extensions
from .config import load_or_create_config, is_configured
from .scheduler import setup_scheduler, set_app_for_jobs
from . import models
from . import sockets
from .logging_config import setup_logging

logger = logging.getLogger(__name__)

def set_sqlite_pragma(dbapi_connection, connection_record):
    """
    Configurações avançadas do SQLite para alta concorrência.
    Ativa WAL, ajusta sincronização e timeouts para evitar bloqueios de DB.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.execute("PRAGMA cache_size=-64000;")
        cursor.execute("PRAGMA temp_store=MEMORY;")
    except Exception as e:
        logger.warning(f"Erro ao definir PRAGMAS do SQLite: {e}")
    finally:
        cursor.close()

@extensions.login_manager.user_loader
def load_user(user_id):
    """Carrega o utilizador para o Flask-Login a partir dos detalhes da sessão."""
    user_details = session.get('user_details')
    if user_details and str(user_details.get('id')) == str(user_id):
        return models.User(**user_details)
    return None

def shutdown_scheduler(signum=None, frame=None):
    """Garante que o agendador é desligado de forma segura e elegante ao sair."""
    if extensions.scheduler.running:
        logger.info("A encerrar o Scheduler (APScheduler) de forma segura...")
        extensions.scheduler.shutdown(wait=False)

    # 📡 Encerra também o listener SSE do Plex. Sem isto, a thread do websocket e
    # eventuais timers de debounce ficavam a correr durante o encerramento,
    # podendo atrasar o shutdown ou gerar erros em contexto já destruído.
    try:
        if extensions.stream_manager:
            extensions.stream_manager.stop_listener()
    except Exception as e:
        logger.debug(f"Aviso ao encerrar o listener SSE: {e}")

def create_app() -> Flask:
    """
    Cria e configura a instância principal da aplicação Flask (Application Factory).
    """
    app = Flask(__name__)

    # ==========================================
    # CARREGAMENTO DE CONFIGURAÇÕES
    # ==========================================
    
    app_config = load_or_create_config()
    app.config.update(app_config)

    # Definição de Caminhos
    config_dir_path = os.path.join(app.root_path, '..', 'config')
    db_path = os.path.join(config_dir_path, 'app_data.db')
    scheduler_db_path = os.path.join(config_dir_path, 'scheduler_jobs.db')
    cache_dir_path = os.path.join(config_dir_path, 'cache', 'web_cache')

    # Configurações do Flask e Segurança de Sessão
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}?timeout=30'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
    base_url_for_cookie = app.config.get('APP_BASE_URL', '')
    app.config['SESSION_COOKIE_SECURE'] = base_url_for_cookie.startswith('https://')
    
    # Otimização do SQLAlchemy
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "connect_args": {"timeout": 30},
        "pool_pre_ping": True, 
        "pool_recycle": 300,
    }
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Configurações de Cache e Rate Limit
    app.config['CACHE_TYPE'] = 'FileSystemCache'
    app.config['CACHE_DIR'] = cache_dir_path
    app.config['CACHE_DEFAULT_TIMEOUT'] = 300
    
    app.config['RATELIMIT_DEFAULT'] = "200 per day; 50 per hour"
    app.config['RATELIMIT_STORAGE_URI'] = "memory://"

    # Confiança em Reverse Proxies (ex: Nginx, Cloudflare)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    # Configuração de Idioma Dinâmico
    app.config['LANGUAGES'] = {'pt_BR': 'Português', 'en': 'English'}
    app.config['BABEL_DEFAULT_LOCALE'] = 'pt_BR'
    
    def get_user_locale():
        if has_request_context():
            return session.get('language') or request.accept_languages.best_match(app.config['LANGUAGES'].keys())
        return app.config['BABEL_DEFAULT_LOCALE']

    # Inicializa os Logs
    setup_logging(app, app.config.get('LOG_LEVEL', 'INFO'))

    # ==========================================
    # INICIALIZAÇÃO DE EXTENSÕES
    # ==========================================
    
    extensions.db.init_app(app)
    
    # Injeta a otimização de concorrência do SQLite na criação da base de dados
    with app.app_context():
        event.listen(extensions.db.engine, 'connect', set_sqlite_pragma)

    extensions.migrate.init_app(app, extensions.db)
    extensions.login_manager.init_app(app)
    extensions.babel.init_app(app, locale_selector=get_user_locale)
    extensions.cache.init_app(app)
    extensions.limiter.init_app(app)

    # Bugfix para compatibilidade com certas bibliotecas que procuram por 'cache'
    if 'cache' not in app.extensions:
        app.extensions['cache'] = app.extensions.get('caching')

    # Inicializa Sockets
    extensions.socketio.init_app(app, async_mode='gevent', cors_allowed_origins="*")
    sockets.app_instance = app

    # ==========================================
    # INICIALIZAÇÃO DE MANAGERS E SERVIÇOS
    # ==========================================
    from .services import (
        DataManager, TautulliManager, PlexManager, 
        NotifierManager, EfiManager, MercadoPagoManager,
        OverseerrManager, LinkShortener, Gates2bManager, StreamManager,
        PricingManager, BackupManager, ReferralManager
    )

    extensions.data_manager = DataManager()
    extensions.pricing_manager = PricingManager(data_manager=extensions.data_manager)
    extensions.tautulli_manager = TautulliManager(data_manager=extensions.data_manager)
    extensions.link_shortener = LinkShortener()
    extensions.notifier_manager = NotifierManager(link_shortener_service=extensions.link_shortener, socketio_instance=extensions.socketio)
    extensions.efi_manager = EfiManager(data_manager=extensions.data_manager)
    extensions.mercado_pago_manager = MercadoPagoManager(data_manager=extensions.data_manager)
    extensions.gates2b_manager = Gates2bManager(data_manager=extensions.data_manager)
    extensions.overseerr_manager = OverseerrManager()
    extensions.backup_manager = BackupManager(config_dir=config_dir_path)
    extensions.referral_manager = ReferralManager(
        data_manager=extensions.data_manager,
        notifier_manager=extensions.notifier_manager
    )
    
    extensions.plex_manager = PlexManager(
        data_manager=extensions.data_manager, 
        tautulli_manager=extensions.tautulli_manager,
        notifier_manager=extensions.notifier_manager,
        overseerr_manager=extensions.overseerr_manager
    )
    extensions.plex_manager.init_app(app)
    
    extensions.stream_manager = StreamManager(
        plex_connection=extensions.plex_manager.conn,
        data_manager=extensions.data_manager,
        user_manager=extensions.plex_manager.users
    )
    extensions.plex_manager.stream_manager = extensions.stream_manager

    # 🔗 Injeção tardia: o ReferralManager precisa do SubscriptionManager para
    # somar dias grátis, mas este só existe depois do PlexManager ser construído.
    extensions.referral_manager.subscription_manager = extensions.plex_manager.subscriptions

    # ==========================================
    # CONFIGURAÇÃO DO SCHEDULER
    # ==========================================
    set_app_for_jobs(app)
    
    if not extensions.scheduler.running:
        jobstores = {
            'default': SQLAlchemyJobStore(url=f'sqlite:///{scheduler_db_path}?timeout=30')
        }
        try:
            local_tz_name = get_localzone_name()
        except Exception:
            local_tz_name = 'UTC'
            
        extensions.scheduler.configure(jobstores=jobstores, timezone=local_tz_name)

        if is_configured():
            try:
                setup_scheduler(app)
                # Regista handlers de encerramento seguro
                atexit.register(shutdown_scheduler)
                signal.signal(signal.SIGTERM, shutdown_scheduler)
                signal.signal(signal.SIGINT, shutdown_scheduler)
            except Exception as e:
                logger.error(f"Falha ao iniciar o agendador de tarefas: {e}")

    # ==========================================
    # HOOKS E ERROR HANDLERS
    # ==========================================

    @app.context_processor
    def inject_global_vars():
        return {
            'current_locale': get_locale(),
            'app_title': app.config.get('APP_TITLE', 'Painel Plex'),
            'cache_buster': int(datetime.now().timestamp())
        }

    @app.errorhandler(429)
    def ratelimit_handler(e):
        if request.path.startswith('/api/') or request.is_json:
            return jsonify({"success": False, "message": _("Demasiados pedidos. Aguarde um momento e tente de novo.")}), 429
        return render_template('payment_unavailable.html', 
                               reason_title=_("Limite Excedido"),
                               reason_message=_("Por segurança, limitámos os acessos temporariamente. Tente novamente dentro de alguns minutos.")), 429

    @app.before_request
    def check_configuration_and_user():
        """
        Garante que a aplicação não avança se ainda não foi configurada (Setup Inicial)
        e encaminha utilizadores não administradores para fora de áreas sensíveis.
        """
        # Se for um ficheiro estático ou rota de webhook/pagamento público, ignorar
        if request.endpoint in ('static', 'serve_sw', 'serve_manifest') or request.path.startswith('/socket.io'):
            return

        exempt_from_setup = {
            'main.setup', 'system_api.save_setup', 'system_api.setup_restore_backup',
            'main.referral_landing',
            'system_api.test_tautulli_connection', 'system_api.test_overseerr_connection',
            'system_api.get_plex_servers',
            'auth.get_plex_auth_context', 'auth.check_plex_pin', 
            'auth.check_plex_pin_for_token', 'auth.auth_status'
        }

        # Força o ecrã de setup inicial se o config.json for virgem
        if not is_configured() and request.endpoint not in exempt_from_setup:
            return redirect(url_for('main.setup'))

        # Regras de Redirecionamento Baseadas em Papel (Role-based)
        if current_user.is_authenticated:
            # Bloqueia utilizadores logados de voltar à página de login
            if request.endpoint == 'auth.login':
                return redirect(url_for('main.index'))
                
            # Se for um utilizador comum (NÃO Admin), não deve estar no painel de controlo principal
            if not current_user.is_admin():
                # A UI deve forçar este utilizador para o `/statistics` em vez do dashboard de admin (`/`)
                if request.endpoint in ('main.index', 'main.settings_page', 'main.users_page'):
                    return redirect(url_for('main.statistics_page'))

    # ==========================================
    # REGISTO DE ROTAS (BLUEPRINTS)
    # ==========================================
    
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
        return send_from_directory(os.path.join(app.root_path, 'static', 'js'), 'service-worker.js', mimetype='application/javascript')

    from .blueprints.main import main_bp
    from .blueprints.auth import auth_bp
    from .blueprints.redirect import redirect_bp
    from .blueprints.image import image_bp
    from .blueprints.api.system import system_api_bp
    from .blueprints.api.users import users_api_bp
    from .blueprints.api.invites import invites_api_bp
    from .blueprints.api.payments import payments_api_bp
    from .blueprints.api.stats import stats_api_bp
    from .blueprints.api.notifications import notifications_api_bp
    from .blueprints.api.coupons import coupons_api_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(redirect_bp)
    app.register_blueprint(image_bp, url_prefix='/image')
    app.register_blueprint(system_api_bp, url_prefix='/api/system')
    app.register_blueprint(users_api_bp, url_prefix='/api/users')
    app.register_blueprint(invites_api_bp, url_prefix='/api/invites')
    app.register_blueprint(payments_api_bp, url_prefix='/api/payments')
    app.register_blueprint(stats_api_bp, url_prefix='/api/statistics')
    app.register_blueprint(notifications_api_bp, url_prefix='/api/notifications')
    app.register_blueprint(coupons_api_bp, url_prefix='/api/coupons') 

    # Compatibilidade de CORS com Sockets se usado fora do domínio direto
    app.config['CORS_HEADERS'] = 'Content-Type'

    return app
