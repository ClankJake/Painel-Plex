import logging
from datetime import datetime
from . import extensions

# Variável para manter a instância da aplicação, que será definida em app/__init__.py
app_instance = None
logger = logging.getLogger(__name__)

def get_summary_data_for_socket():
    """
    Busca os dados de resumo do dashboard. Esta função agora opera dentro de um contexto de aplicação
    e de um contexto de pedido para garantir o acesso seguro a todas as extensões do Flask.
    """
    if not app_instance:
        logger.error("A instância da aplicação não foi definida para a tarefa de socket.")
        return None

    # Envolve a lógica com um contexto de aplicação e de pedido
    with app_instance.app_context():
        with app_instance.test_request_context():
            try:
                # Acessa os gestores através do módulo 'extensions' para garantir que as instâncias corretas são usadas.
                active_streams_data = extensions.plex_manager.get_active_sessions()
                active_streams = active_streams_data.get('stream_count', 0)
                
                all_users = extensions.plex_manager.get_all_plex_users()
                total_users = len(all_users) if all_users else 0
                
                blocked_users_list = extensions.data_manager.get_blocked_users()
                blocked_users = len(blocked_users_list)
                active_users = total_users - blocked_users

                now = datetime.now()
                financial_summary = extensions.data_manager.get_financial_summary(now.year, now.month)
                
                summary_data = {
                    "active_streams": active_streams,
                    "total_users": total_users,
                    "active_users": active_users,
                    "blocked_users": blocked_users,
                    "monthly_revenue": financial_summary.get('total_revenue', 0),
                    "upcoming_renewals": len(financial_summary.get('upcoming_expirations', [])),
                    "daily_revenue": financial_summary.get('daily_revenue', {})
                }
                return summary_data
            except Exception as e:
                logger.error(f"Erro ao buscar dados de resumo para o socket: {e}", exc_info=True)
                return None

def background_task():
    """Tarefa em segundo plano que envia atualizações do dashboard."""
    count = 0
    while True:
        extensions.socketio.sleep(10)
        count += 1
        logger.debug(f"A executar a tarefa de fundo do SocketIO - Contagem: {count}")
        summary_data = get_summary_data_for_socket()
        if summary_data:
            extensions.socketio.emit('dashboard_update', {'summary': summary_data}, namespace='/dashboard')
            logger.debug("Dados do dashboard enviados para os clientes.")

@extensions.socketio.on('connect', namespace='/dashboard')
def handle_dashboard_connect():
    """Lida com novas conexões de clientes ao namespace do dashboard."""
    logger.info('Cliente conectado ao dashboard em tempo real.')
    # Inicia a tarefa em segundo plano se ainda não estiver a correr
    if not hasattr(handle_dashboard_connect, 'task_started') or not handle_dashboard_connect.task_started:
        extensions.socketio.start_background_task(background_task)
        handle_dashboard_connect.task_started = True
        logger.info("Tarefa de fundo do dashboard iniciada.")
