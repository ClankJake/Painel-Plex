import logging
from datetime import datetime
import threading
from flask_login import current_user

from . import extensions

logger = logging.getLogger(__name__)

# Referência global à aplicação (injetada no app/__init__.py)
app_instance = None

# Estado da Tarefa de Background (Protegido por Lock Thread-Safe)
background_task_greenlet = None
connected_clients = 0
task_lock = threading.Lock()

def _safe_get_active_sessions():
    """Busca as sessões com tratamento seguro de erros de rede."""
    try:
        if not extensions.plex_manager.conn.plex:
            success, _ = extensions.plex_manager.reload_connections(from_job=True)
            if not success:
                logger.debug("Socket: Plex inacessível. A saltar verificação de streams.")
                return 0, []

        streams_data = extensions.plex_manager.get_active_sessions()
        if streams_data and streams_data.get('success'):
            return streams_data.get('stream_count', 0), streams_data.get('sessions', [])
            
    except Exception as e:
        logger.error(f"Erro ao buscar sessões para Socket: {e}")
        
    return 0, []

def _build_summary_data(active_streams_count):
    """Constrói o objeto de sumário com os dados da Base de Dados."""
    try:
        all_users = extensions.plex_manager.get_all_plex_users()
        total_users = len(all_users) if all_users else 0
        
        blocked_users_list = extensions.data_manager.get_blocked_users_list()
        blocked_users = len(blocked_users_list)
        active_users = total_users - blocked_users

        now = datetime.now()
        financial_summary = extensions.data_manager.get_financial_summary(now.year, now.month)
        
        return {
            "active_streams": active_streams_count,
            "total_users": total_users,
            "active_users": active_users,
            "blocked_users": blocked_users,
            "monthly_revenue": financial_summary.get('total_revenue', 0),
            "upcoming_renewals": len(financial_summary.get('upcoming_expirations', [])),
            "daily_revenue": financial_summary.get('daily_revenue', {})
        }
    except Exception as e:
        logger.error(f"Erro ao calcular sumário para Socket: {e}")
        return None

def get_data_for_socket():
    """
    Busca os dados de resumo e os detalhes dos streams ativos.
    Encapsulado de forma segura num App Context real.
    """
    if not app_instance:
        logger.error("Socket: app_instance não definida.")
        return None, None

    with app_instance.app_context():
        # RESTAURADO: O test_request_context é estritamente necessário 
        # para que o Flask consiga usar o url_for() e gerar as capas/posters em background!
        with app_instance.test_request_context('/'):
            active_streams_count, active_sessions_details = _safe_get_active_sessions()
            summary_data = _build_summary_data(active_streams_count)

            return summary_data, active_sessions_details

def background_task():
    """
    Rotina infinita (Greenlet) que emite dados em tempo real.
    Termina imediatamente de forma elegante se não houver clientes.
    """
    logger.debug("Socket: Tarefa de background (Dashboard) INICIADA.")
    
    while True:
        # Verifica a condição no início do loop de forma thread-safe
        with task_lock:
            if connected_clients <= 0:
                break
                
        summary_data, active_sessions = get_data_for_socket()
        
        if summary_data:
            extensions.socketio.emit('dashboard_update', {'summary': summary_data}, namespace='/dashboard')
            
        if active_sessions is not None:
            extensions.socketio.emit('active_streams_update', {'sessions': active_sessions}, namespace='/dashboard')

        # Dorme por 5 segundos. O SocketIO gere o sleep para não bloquear a thread principal (Eventlet)
        extensions.socketio.sleep(5)

    # Marca a tarefa como morta de forma segura
    global background_task_greenlet
    with task_lock:
        background_task_greenlet = None
        logger.debug("Socket: Tarefa de background PARADA (0 clientes).")


# ==========================================
# EVENTOS DE LIGAÇÃO (CONNECT/DISCONNECT)
# ==========================================

@extensions.socketio.on('connect', namespace='/dashboard')
def handle_dashboard_connect():
    """Acionado quando um utilizador entra no Painel de Controlo."""
    global connected_clients, background_task_greenlet
    
    with task_lock:
        connected_clients += 1
        logger.debug(f"Socket: Novo cliente conectado ao Dashboard. Total: {connected_clients}")
        
        if connected_clients == 1 and background_task_greenlet is None:
            background_task_greenlet = extensions.socketio.start_background_task(background_task)

@extensions.socketio.on('disconnect', namespace='/dashboard')
def handle_dashboard_disconnect():
    """Acionado quando um utilizador fecha a aba do Painel."""
    global connected_clients
    
    with task_lock:
        if connected_clients > 0:
            connected_clients -= 1
        logger.debug(f"Socket: Cliente desconectado do Dashboard. Restantes: {connected_clients}")

@extensions.socketio.on('connect', namespace='/')
def handle_main_connect():
    if current_user.is_authenticated:
        logger.debug(f"Socket: Cliente '{current_user.username}' associado ao namespace principal.")

@extensions.socketio.on('disconnect', namespace='/')
def handle_main_disconnect():
    if current_user.is_authenticated:
        logger.debug(f"Socket: Cliente '{current_user.username}' saiu do namespace principal.")
