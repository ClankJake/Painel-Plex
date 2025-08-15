# app/sockets.py

import logging
from datetime import datetime
from . import extensions

# Variável para manter a instância da aplicação, que será definida em app/__init__.py
app_instance = None
logger = logging.getLogger(__name__)

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
                # Busca detalhes dos streams ativos e a contagem
                active_streams_data = extensions.plex_manager.get_active_sessions()
                active_streams_count = active_streams_data.get('stream_count', 0)
                active_sessions_details = active_streams_data.get('sessions', [])
                
                # Busca os restantes dados de resumo
                all_users = extensions.plex_manager.get_all_plex_users()
                total_users = len(all_users) if all_users else 0
                
                blocked_users_list = extensions.data_manager.get_blocked_users()
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
    """Tarefa em segundo plano que envia atualizações do dashboard e dos streams ativos."""
    count = 0
    while True:
        # Intervalo de atualização reduzido para 5 segundos para maior reatividade
        extensions.socketio.sleep(5)
        count += 1
        logger.debug(f"A executar a tarefa de fundo do SocketIO - Contagem: {count}")
        
        summary_data, active_sessions = get_data_for_socket()
        
        # Envia o resumo do dashboard
        if summary_data:
            extensions.socketio.emit('dashboard_update', {'summary': summary_data}, namespace='/dashboard')
            logger.debug("Dados de resumo do dashboard enviados para os clientes.")
        
        # Envia os detalhes dos streams ativos num evento separado
        if active_sessions is not None:
            extensions.socketio.emit('active_streams_update', {'sessions': active_sessions}, namespace='/dashboard')
            logger.debug("Dados de streams ativos enviados para os clientes.")

@extensions.socketio.on('connect', namespace='/dashboard')
def handle_dashboard_connect():
    """Lida com novas conexões de clientes ao namespace do dashboard."""
    logger.info('Cliente conectado ao dashboard em tempo real.')
    # Inicia a tarefa em segundo plano se ainda não estiver a correr
    if not hasattr(handle_dashboard_connect, 'task_started') or not handle_dashboard_connect.task_started:
        extensions.socketio.start_background_task(background_task)
        handle_dashboard_connect.task_started = True
        logger.info("Tarefa de fundo do dashboard iniciada.")
