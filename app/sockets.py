# app/sockets.py

import logging
from datetime import datetime
from . import extensions
import threading
from flask_login import current_user

# Variável para manter a instância da aplicação, que será definida em app/__init__.py
app_instance = None
logger = logging.getLogger(__name__)

# --- INÍCIO DA ALTERAÇÃO: Gestão de Estado da Tarefa ---
# Variáveis globais para gerir o estado da tarefa em segundo plano
background_task_greenlet = None
connected_clients = 0
lock = threading.Lock()
# --- FIM DA ALTERAÇÃO ---

def get_data_for_socket():
    """
    Busca tanto os dados de resumo quanto os detalhes dos streams ativos.
    Esta função opera dentro de um contexto de aplicação para acesso seguro.
    """
    if not app_instance:
        logger.error("A instância da aplicação não foi definida para a tarefa de socket.")
        return None, None

    with app_instance.app_context():
        with app_instance.test_request_context():
            try:
                # Tenta reconectar se a conexão não estiver estabelecida.
                if not extensions.plex_manager.conn.plex:
                    logger.info("Conexão com o Plex não encontrada no socket, tentando reconectar...")
                    # A flag from_job=True torna o log menos verboso
                    if not extensions.plex_manager.reload_connections(from_job=True)[0]:
                        logger.warning("Falha ao reconectar ao Plex a partir do socket. A saltar a busca de dados.")
                        return None, None # Retorna None para evitar o envio de dados possivelmente incorretos

                # Busca detalhes dos streams ativos e a contagem
                active_streams_data = extensions.plex_manager.get_active_sessions()
                
                # Adiciona uma verificação do sucesso da chamada
                if not active_streams_data.get('success'):
                    logger.warning("Não foi possível obter streams ativos no socket. A conexão com o Plex pode estar instável.")
                    # Continua, mas com dados vazios para streams
                    active_streams_count = 0
                    active_sessions_details = []
                else:
                    active_streams_count = active_streams_data.get('stream_count', 0)
                    active_sessions_details = active_streams_data.get('sessions', [])
                
                # Busca os restantes dados de resumo
                all_users = extensions.plex_manager.get_all_plex_users()
                total_users = len(all_users) if all_users else 0
                
                blocked_users_list = extensions.data_manager.get_blocked_users_list()
                blocked_users = len(blocked_users_list)
                active_users = total_users - blocked_users

                now = datetime.now()
                financial_summary = extensions.data_manager.get_financial_summary(now.year, now.month)
                
                summary_data = {
                    "active_streams": active_streams_count,
                    "total_users": total_users,
                    "active_users": active_users,
                    "blocked_users": blocked_users,
                    "monthly_revenue": financial_summary.get('total_revenue', 0),
                    "upcoming_renewals": len(financial_summary.get('upcoming_expirations', [])),
                    "daily_revenue": financial_summary.get('daily_revenue', {})
                }
                return summary_data, active_sessions_details
            except Exception as e:
                logger.error(f"Erro ao buscar dados para o socket: {e}", exc_info=True)
                return None, None

def background_task():
    """
    Tarefa em segundo plano que envia atualizações do dashboard e dos streams ativos.
    Esta tarefa agora termina automaticamente quando não há clientes conectados.
    """
    logger.debug("Tarefa de fundo do SocketIO iniciada.")
    count = 0
    while connected_clients > 0:
        extensions.socketio.sleep(5)
        count += 1
        logger.debug(f"A executar a tarefa de fundo do SocketIO - Contagem: {count}, Clientes: {connected_clients}")
        
        summary_data, active_sessions = get_data_for_socket()
        
        # Envia o resumo do dashboard
        if summary_data:
            extensions.socketio.emit('dashboard_update', {'summary': summary_data}, namespace='/dashboard')
            logger.debug("Dados de resumo do dashboard enviados para os clientes.")
        
        # Envia os detalhes dos streams ativos num evento separado
        if active_sessions is not None:
            extensions.socketio.emit('active_streams_update', {'sessions': active_sessions}, namespace='/dashboard')
            logger.debug("Dados de streams ativos enviados para os clientes.")

    logger.debug("Tarefa de fundo do SocketIO parada. Nenhum cliente conectado.")
    global background_task_greenlet
    with lock:
        background_task_greenlet = None


@extensions.socketio.on('connect', namespace='/dashboard')
def handle_dashboard_connect():
    """
    Lida com novas conexões de clientes ao namespace do dashboard.
    Inicia a tarefa em segundo plano apenas se for a primeira conexão.
    """
    global connected_clients, background_task_greenlet
    with lock:
        connected_clients += 1
        logger.debug(f'Cliente conectado ao dashboard. Clientes ativos: {connected_clients}')
        if connected_clients == 1 and (background_task_greenlet is None or getattr(background_task_greenlet, 'dead', True)):
            background_task_greenlet = extensions.socketio.start_background_task(background_task)

@extensions.socketio.on('disconnect', namespace='/dashboard')
def handle_dashboard_disconnect():
    """
    Lida com desconexões de clientes.
    A tarefa em segundo plano irá parar sozinha quando o contador chegar a zero.
    """
    global connected_clients
    with lock:
        if connected_clients > 0:
            connected_clients -= 1
        logger.debug(f'Cliente desconectado do dashboard. Clientes ativos: {connected_clients}')

@extensions.socketio.on('connect', namespace='/')
def handle_main_connect():
    if current_user.is_authenticated:
        logger.debug(f'Cliente {current_user.username} (ID: {current_user.id}) conectado ao namespace principal para notificações.')
    else:
        logger.debug('Cliente anónimo conectado ao namespace principal.')

@extensions.socketio.on('disconnect', namespace='/')
def handle_main_disconnect():
    if current_user.is_authenticated:
        logger.debug(f'Cliente {current_user.username} desconectado do namespace principal.')
    else:
        logger.debug('Cliente anónimo desconectado do namespace principal.')
