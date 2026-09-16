import os
import logging
import atexit
import signal
import uuid
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
from .config import load_or_create_config, is_configured, CONFIG_DIR
from .scheduler import setup_scheduler, set_app_for_jobs
from . import models
from . import sockets
from .logging_config import setup_logging
from .utils.navigation import endpoint_inicial_do_utilizador
from .utils.estatisticas import estatisticas_disponiveis
from .utils.ficheiros import proteger_base_de_dados

logger = logging.getLogger(__name__)

def set_sqlite_pragma(dbapi_connection, connection_record):
    """
    Configurações avançadas do SQLite para alta concorrência.
    Ativa WAL, ajusta sincronização e timeouts para evitar bloqueios de DB.

    ⚠️ **`foreign_keys` é POR LIGAÇÃO e vem DESLIGADO por omissão no SQLite.**
    Durante muito tempo as chaves estrangeiras existiam no esquema e não eram
    impostas: apagar um perfil deixava para trás os pagamentos, os bloqueios,
    os pedidos de reposição de palavra-passe e os registos de corte que lhe
    apontavam — órfãos silenciosos, sem erro nenhum, que faziam os relatórios
    financeiros contar linhas de gente que já não existe. Pior ainda, era
    possível gravar um pagamento com um `media_user_id` que nunca existiu.

    Ligar isto obriga a que a base de dados já esteja limpa: a migração
    `e1c7a4f92db6` apaga os órfãos que houvesse ANTES de este PRAGMA passar a
    valer. Ligar sem limpar seria trocar dados órfãos por erros em produção.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.execute("PRAGMA cache_size=-64000;")
        cursor.execute("PRAGMA temp_store=MEMORY;")
        cursor.execute("PRAGMA foreign_keys=ON;")
    except Exception as e:
        logger.warning(f"Erro ao definir PRAGMAS do SQLite: {e}")
    finally:
        cursor.close()

@extensions.login_manager.user_loader
def load_user(user_id):
    """Carrega o utilizador para o Flask-Login a partir dos detalhes da sessão.

    🐛 O avatar é uma CÓPIA guardada no cookie da sessão, tirada no momento do
    login. Quando o formato do thumb mudou (passou a ir pelo proxy de imagens),
    quem já estava autenticado continuou a carregar o caminho cru do servidor
    de média — e o browser pedia-o ao PAINEL, que responde 404.
    Reescrever a sessão a cada pedido seria caro; converter aqui é uma
    operação de texto, e é o único sítio por onde uma sessão vira `current_user`.
    """
    user_details = session.get('user_details')
    if not user_details or str(user_details.get('id')) != str(user_id):
        return None

    detalhes = dict(user_details)
    try:
        backend = extensions.media_server
        if backend and detalhes.get('thumb'):
            detalhes['thumb'] = backend.thumb_para_interface(detalhes['thumb'])
    except Exception as e:
        # Um avatar não pode impedir alguém de entrar.
        logger.debug(f"Não foi possível normalizar o avatar da sessão: {e}")

    return models.User(**detalhes)

def _parar_o_agendador():
    """Pára o agendador sem terminar o processo. Silencioso se já estiver parado.

    ⚠️ **Pausar ANTES de encerrar não é um detalhe.** O `shutdown()` fecha os
    executores, mas o ciclo do APScheduler pode estar nesse instante a submeter
    as tarefas que já estão na hora — e cada uma delas rebenta com
    `RuntimeError: cannot schedule new futures after shutdown`, um erro por
    tarefa, que assusta quem lê o log e não diz nada a ninguém. Com o
    `pause()`, o ciclo vê que está em pausa e não submete nada.
    """
    try:
        if not (extensions.scheduler and extensions.scheduler.running):
            return
        logger.info("A encerrar o Scheduler (APScheduler) de forma segura...")
        extensions.scheduler.pause()
        extensions.scheduler.shutdown(wait=False)
    except Exception as e:
        logger.debug(f"Aviso ao encerrar o agendador: {e}")


def _parar_o_limitador():
    """Desmarca a limpeza periódica do Flask-Limiter.

    🐛 O armazenamento em MEMÓRIA do limitador (`limits.storage.MemoryStorage`)
    mantém um `threading.Timer` que varre as contagens expiradas, e **volta a
    marcá-lo a cada pedido** que passe pelo limitador. Sob gevent isso é um
    greenlet embrulhado na contabilidade do `threading`: quando o processo é
    terminado com um timer em voo — que é sempre, porque o assistente de
    instalação manda-se reiniciar a si próprio com SIGTERM logo a seguir a
    gravar — o greenlet acorda com o `threading._active` já desmontado e o log
    fica com isto, logo depois de uma instalação BEM-SUCEDIDA:

        File "threading.py", line 1111, in _delete
          del _active[get_ident()]
        KeyError: 271255370948160
        <Greenlet ...: <bound method Thread._bootstrap of
         <Timer(Thread-1, stopped ...)>>> failed with KeyError

    Não se perde nada — o processo ia terminar de qualquer forma e a varredura
    de contagens não tem nada a guardar — mas parece uma falha e não é.

    ⚠️ O `timer` não faz parte da API pública do `limits`, e é por isso que isto
    vai todo dentro de um `try`: se um dia deixar de existir, o pior que
    acontece é o traceback voltar.
    """
    try:
        armazenamento = getattr(extensions.limiter, 'storage', None)
        temporizador = getattr(armazenamento, 'timer', None)
        if temporizador is None:
            return

        # ⚠️ **Cancelar não o mata; acorda-o.** O `Timer.cancel()` marca o evento
        # `finished`, e o que o timer faz a seguir é sair sem chamar a função —
        # mas só quando lhe derem a vez. É precisamente essa saída que tem de
        # acontecer AGORA, com o `threading._active` ainda de pé, em vez de
        # durante o desmonte do interpretador. Por isso o `join` a seguir não é
        # um extra: é ele que torna a correção determinística.
        temporizador.cancel()
        temporizador.join(timeout=2)
    except Exception as e:
        logger.debug(f"Aviso ao parar a limpeza do limitador: {e}")


def parar_servicos_de_fundo():
    """Cala tudo o que escreve na base de dados, sem terminar o processo.

    🐛 Restaurar um backup TROCA os ficheiros `.db` por baixo de um agendador
    que está a correr. O que acontecia a seguir: o APScheduler relia o jobstore
    restaurado, encontrava lá as tarefas com a hora de execução no PASSADO (a
    do momento em que o backup foi feito), tentava submetê-las todas ao mesmo
    tempo — e apanhava com isso o encerramento que o próprio restauro agenda.
    O log enchia-se de `cannot schedule new futures after shutdown` a seguir a
    um restauro bem-sucedido.
    """
    _parar_o_agendador()
    _parar_o_limitador()
    try:
        if extensions.stream_manager:
            extensions.stream_manager.stop_listener()
    except Exception as e:
        logger.debug(f"Aviso ao encerrar o listener de eventos: {e}")


def shutdown_scheduler(signum=None, frame=None):
    """Garante que o agendador é desligado de forma segura e elegante ao sair."""
    _parar_o_agendador()

    # 📡 Encerra também o listener SSE do Plex. Sem isto, a thread do websocket e
    # eventuais timers de debounce ficavam a correr durante o encerramento,
    # podendo atrasar o shutdown ou gerar erros em contexto já destruído.
    try:
        if extensions.stream_manager:
            extensions.stream_manager.stop_listener()
    except Exception as e:
        logger.debug(f"Aviso ao encerrar o listener SSE: {e}")

    # E a varredura periódica do limitador, pela mesma razão — ver
    # `_parar_o_limitador`. É a que sobra depois de o assistente se mandar
    # reiniciar, e a que aparece no log a seguir a uma instalação bem-sucedida.
    _parar_o_limitador()

    # 🐛 CORREÇÃO: quando esta função é instalada como handler de SIGTERM/SIGINT,
    # ela SUBSTITUI o comportamento por omissão — que é terminar o processo. O
    # resultado era que, depois de limpar o agendador, a aplicação simplesmente
    # continuava a correr:
    #   • o restauro de backup (que faz `os.kill(os.getpid(), SIGTERM)` para se
    #     reiniciar) prometia um reinício que nunca acontecia, ficando com o
    #     agendador morto e sem nenhuma tarefa de fundo;
    #   • um `docker stop` esperava o tempo todo de cortesia e acabava em SIGKILL.
    # Repomos o handler por omissão e reenviamos o sinal para terminar de facto.
    if signum is not None:
        try:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        except Exception as e:  # pragma: no cover - dependente do SO
            logger.debug(f"Não foi possível reenviar o sinal {signum}: {e}")


# Os handlers de encerramento são globais ao processo: registá-los mais do que
# uma vez faria o `atexit` chamar a limpeza repetidamente.
_shutdown_handlers_registered = False


def _register_shutdown_handlers():
    """Regista o encerramento seguro do agendador (idempotente)."""
    global _shutdown_handlers_registered
    if _shutdown_handlers_registered:
        return

    atexit.register(shutdown_scheduler)
    try:
        signal.signal(signal.SIGTERM, shutdown_scheduler)
        signal.signal(signal.SIGINT, shutdown_scheduler)
    except ValueError:
        # `signal.signal` só funciona na thread principal. Quando o agendador
        # arranca a partir de um pedido HTTP (fim do assistente de instalação),
        # ficamos apenas com o `atexit` — que é suficiente para a limpeza.
        logger.debug(
            "Handlers de sinal não registados (fora da thread principal). "
            "O encerramento seguro fica a cargo do atexit."
        )

    _shutdown_handlers_registered = True


def _configure_scheduler():
    """Aponta o agendador ao seu jobstore e fuso horário (idempotente)."""
    if extensions.scheduler.running:
        return

    scheduler_db_path = os.path.join(CONFIG_DIR, 'scheduler_jobs.db')
    jobstores = {
        'default': SQLAlchemyJobStore(url=f'sqlite:///{scheduler_db_path}?timeout=30')
    }
    try:
        local_tz_name = get_localzone_name()
    except Exception:
        local_tz_name = 'UTC'

    extensions.scheduler.configure(jobstores=jobstores, timezone=local_tz_name)


def start_background_services(app) -> bool:
    """
    Arranca o agendador com todas as tarefas recorrentes e regista o encerramento
    seguro. Devolve True se o agendador ficou a correr neste processo.

    🐛 CORREÇÃO: isto era feito APENAS dentro do `create_app()`, e só quando a
    aplicação já estava configurada. Numa instalação nova o assistente gravava a
    configuração, autenticava o administrador e mandava-o para o painel — mas
    nenhuma tarefa de fundo chegava a arrancar: sem verificação de streams (e
    portanto sem controlo de telas simultâneas nem listener SSE em tempo real),
    sem avisos de vencimento, sem remoção de bloqueados, sem limpezas, sem
    processador de tarefas em lote e sem backups automáticos. Tudo isso só
    começava a funcionar no reinício seguinte do contentor — que podia demorar
    dias. O fim do assistente passa a chamar esta função.

    É idempotente: se o agendador já estiver a correr, não faz nada.
    """
    if extensions.scheduler.running:
        return True

    try:
        _configure_scheduler()
        setup_scheduler(app)
    except Exception as e:
        logger.error(f"Falha ao iniciar o agendador de tarefas: {e}", exc_info=True)
        return False

    _register_shutdown_handlers()
    return extensions.scheduler.running

def _fonte_de_estatisticas(tipo_de_servidor):
    """De onde vêm as reproduções que alimentam as estatísticas.

    ⚠️ Num painel Plex a fonte já NÃO é só o Tautulli: é ele quando está
    configurado e o próprio servidor quando não está
    (`plex/stats_api.py`). Esconder o pódio, o XP, as conquistas, as
    recomendações e o Wrapped a quem não tem Tautulli era a resposta errada —
    o Plex sabe o que cada pessoa viu, e o histórico já vinha de lá pela mesma
    razão. A escolha entre as duas é feita a cada pergunta, para configurar o
    Tautulli não obrigar a reiniciar o painel.
    """
    from .services.media_server import resolve_media_server_type

    if resolve_media_server_type(tipo_de_servidor) == 'jellyfin':
        from .services.media_server.jellyfin.stats_api import JellyfinStatsApi

        return JellyfinStatsApi(lambda: extensions.media_server)

    from .services.media_server.plex.stats_api import FonteDeEstatisticasDoPlex

    return FonteDeEstatisticasDoPlex(lambda: extensions.media_server)


def create_app() -> Flask:
    """
    Cria e configura a instância principal da aplicação Flask (Application Factory).
    """
    app = Flask(__name__)

    # 🔁 A marca deste ARRANQUE. Serve para o navegador saber que o painel que
    # lhe responde já é o processo NOVO, e não o antigo a acabar de morrer.
    #
    # 🐛 Sem ela, quem acabava o assistente (ou restaurava um backup) só tinha
    # uma contagem de oito segundos e uma esperança. O worker demora a sair — só
    # o faz quando não há ligações a ser servidas, e um separador aberto conta —
    # por isso o navegador voltava a tempo de apanhar o processo ANTIGO, o que
    # depois de escolher o Jellyfin queria dizer a página de login do Plex.
    # Agora pergunta-se, e só se recarrega quando a marca muda.
    app.config['BOOT_ID'] = uuid.uuid4().hex

    # ==========================================
    # CARREGAMENTO DE CONFIGURAÇÕES
    # ==========================================
    
    app_config = load_or_create_config()
    app.config.update(app_config)

    # Definição de Caminhos
    # Mesmo diretório usado pelo módulo de configuração (respeita a variável
    # de ambiente PAINEL_PLEX_CONFIG_DIR), para que config.json, base de dados
    # e cache vivam sempre no mesmo sítio.
    config_dir_path = CONFIG_DIR
    db_path = os.path.join(config_dir_path, 'app_data.db')
    cache_dir_path = os.path.join(config_dir_path, 'cache', 'web_cache')

    # Configurações do Flask e Segurança de Sessão
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}?timeout=30'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_HTTPONLY'] = True

    base_url_for_cookie = app.config.get('APP_BASE_URL', '')
    is_https_deployment = base_url_for_cookie.startswith('https://')
    app.config['SESSION_COOKIE_SECURE'] = is_https_deployment

    # 🔒 O cookie "remember me" do Flask-Login é INDEPENDENTE do cookie de sessão e
    # não herda nenhuma das definições acima. Como `login_user(..., remember=True)`
    # é usado no login, sem estas linhas ele era emitido com os valores por omissão
    # da biblioteca — sem SameSite e sem a marca Secure mesmo em instalações HTTPS.
    # Alinhamo-lo agora com o cookie de sessão.
    app.config['REMEMBER_COOKIE_SECURE'] = is_https_deployment
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True
    app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
    app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
    
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

    # 🛡️ As bases de dados guardam pagamentos, contactos e os resumos dos tokens
    # de reposição de palavra-passe. Nasciam com o que o umask ditasse.
    proteger_base_de_dados(db_path)
    proteger_base_de_dados(os.path.join(config_dir_path, 'scheduler_jobs.db'))

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
        DataManager, StatsManager, create_media_server,
        NotifierManager, EfiManager, MercadoPagoManager,
        OverseerrManager, LinkShortener, Gates2bManager, StreamManager,
        PricingManager, BackupManager, ReferralManager, PushManager
    )

    extensions.data_manager = DataManager()
    extensions.pricing_manager = PricingManager(data_manager=extensions.data_manager)
    # A fonte das estatísticas segue o servidor de média: o Tautulli num painel
    # Plex, o próprio servidor num painel Jellyfin. O backend ainda não existe
    # aqui (é construído mais abaixo, e precisa deste manager), por isso a fonte
    # recebe uma função que o vai buscar quando for preciso — a mesma injeção
    # tardia das outras dependências circulares.
    extensions.stats_manager = StatsManager(
        data_manager=extensions.data_manager,
        api_client=_fonte_de_estatisticas(app_config.get('MEDIA_SERVER_TYPE')),
    )
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
    extensions.push_manager = PushManager(data_manager=extensions.data_manager)
    # ⚠️ Um par de chaves VAPID em falta (ou desemparelhado por uma edição à mão
    # do config.json) faz o serviço de push responder 403 a cada envio e mais
    # nada — o painel ficava a "enviar" notificações que nunca saíam. Reparar no
    # arranque não custa nada: `garantir_chaves` não toca num par que já esteja
    # bom, e por isso NUNCA invalida as subscrições existentes.
    if app_config.get('PUSH_ENABLED'):
        try:
            extensions.push_manager.garantir_chaves()
        except Exception as e:
            logger.warning(f"Não foi possível preparar as chaves das notificações push: {e}")
    
    # O backend do servidor de média é escolhido pela configuração. Hoje só
    # existe o Plex; a fábrica é o único sítio que precisa de saber disso.
    extensions.media_server = create_media_server(
        app_config.get('MEDIA_SERVER_TYPE'),
        data_manager=extensions.data_manager,
        stats_manager=extensions.stats_manager,
        notifier_manager=extensions.notifier_manager,
        requests_manager=extensions.overseerr_manager,
    )
    extensions.media_server.init_app(app)
    
    extensions.stream_manager = StreamManager(
        sessions_provider=extensions.media_server.sessions,
        data_manager=extensions.data_manager,
        user_manager=extensions.media_server.users
    )
    extensions.media_server.stream_manager = extensions.stream_manager

    # 🔗 Injeção tardia: o ReferralManager precisa do SubscriptionManager para
    # somar dias grátis, mas este só existe depois do servidor de média ser construído.
    extensions.referral_manager.subscription_manager = extensions.media_server.subscriptions

    # ==========================================
    # CONFIGURAÇÃO DO SCHEDULER
    # ==========================================
    set_app_for_jobs(app)

    # O jobstore é preparado sempre — mesmo numa instalação por concluir — para que
    # o assistente de instalação possa arrancar o agendador no fim, sem reiniciar.
    _configure_scheduler()

    if is_configured():
        start_background_services(app)

    # ==========================================
    # HOOKS E ERROR HANDLERS
    # ==========================================

    @app.context_processor
    def inject_global_vars():
        # Os templates perguntam pelas CAPACIDADES do servidor, não pela marca:
        # `{% if media_server.capabilities.fontes_media_online %}`. É o que
        # permite esconder uma secção que não se aplica em vez de a mostrar
        # partida — e evita uma cascata de `{% if tipo == 'plex' %}`.
        backend = extensions.media_server
        info_servidor = {
            'type': getattr(backend, 'SERVER_TYPE', 'plex'),
            'name': getattr(backend, 'DISPLAY_NAME', 'Plex Media Server'),
            'short_name': getattr(backend, 'SHORT_NAME', 'Plex'),
            # `capabilities` é o que o servidor PODE fazer; `estatisticas` é o
            # que há AGORA (ver `utils/estatisticas.py`). Os templates escondem
            # o pódio, o XP e o Wrapped pela segunda — a primeira é que mantém
            # o cartão do Tautulli nas Conexões, para haver onde o configurar.
            'capabilities': getattr(backend, 'capabilities', None),
            'estatisticas': estatisticas_disponiveis(),
        }

        # As notificações push só aparecem na interface quando há mesmo como
        # as entregar: ligadas nas Configurações E com um par de chaves. Sem
        # isto, o botão "Ativar notificações" pedia permissão ao navegador para
        # depois falhar a subscrever, que é a pior ordem possível.
        gestor_push = extensions.push_manager
        info_push = {
            'ativo': bool(gestor_push and gestor_push.disponivel),
            'chave_publica': getattr(gestor_push, 'chave_publica', '') or '',
        }

        return {
            'current_locale': get_locale(),
            'app_title': app.config.get('APP_TITLE', 'Painel Plex'),
            'push': info_push,
            'cache_buster': int(datetime.now().timestamp()),
            'media_server': info_servidor,
            'endpoint_inicial_do_utilizador': endpoint_inicial_do_utilizador,
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
            # O assistente tem de poder validar um servidor Jellyfin antes de
            # existir configuração — ambas as rotas se fecham sozinhas assim que
            # o sistema fica configurado.
            'system_api.test_jellyfin_connection', 'system_api.get_jellyfin_users_for_setup',
            'auth.get_plex_auth_context', 'auth.check_plex_pin', 
            'auth.check_plex_pin_for_token', 'auth.auth_status',
            # Quem está à espera do reinício tem de poder perguntar se o painel
            # já voltou — e nessa altura pode ainda não haver configuração.
            'system_api.estado_do_processo'
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
                    return redirect(url_for(endpoint_inicial_do_utilizador()))

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
