# app/sockets.py

import logging
from datetime import datetime
from flask_login import current_user

from . import extensions

logger = logging.getLogger(__name__)

# Referência global à aplicação (injetada no app/__init__.py)
app_instance = None

# Estado da Tarefa de Background
background_task_greenlet = None
connected_clients = 0

# Última carga emitida em cada canal. A tarefa corre de 5 em 5 segundos, mas o
# resumo (utilizadores, receita) e a lista de sessões passam a maior parte do
# tempo iguais: reemiti-los sem mudanças obrigava cada painel aberto a refazer
# o DOM e os gráficos a cada 5 segundos, sem nada de novo para mostrar.
_last_summary_emitted = None
_last_sessions_emitted = None
# Ligou um cliente novo: a próxima ronda emite sempre, mesmo que nada tenha
# mudado, para que ele receba o estado atual em vez de esperar pela primeira
# alteração real.
_force_next_emit = False

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
        
        blocked_users = extensions.data_manager.count_blocked_users()
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

    # O test_request_context é estritamente necessário para gerar URLs de imagens (url_for)
    try:
        with app_instance.test_request_context('/'):
            active_streams_count, active_sessions_details = _safe_get_active_sessions()
            summary_data = _build_summary_data(active_streams_count)

            return summary_data, active_sessions_details
    except Exception as e:
        logger.error(f"Socket: Falha na construção do Request Context: {e}")
        return None, None

def background_task():
    """
    Rotina infinita (Greenlet) que emite dados em tempo real.
    Termina imediatamente de forma elegante se não houver clientes.
    """
    logger.debug("Socket: Tarefa de background (Dashboard) INICIADA.")
    global background_task_greenlet
    global connected_clients
    global _last_summary_emitted, _last_sessions_emitted, _force_next_emit
    
    while True:
        # Verifica a condição de parada sem o bloqueio pesado do threading.Lock
        # Em eventlet/gevent, como não há multi-threading preemptivo, isto é seguro
        if connected_clients <= 0:
            background_task_greenlet = None
            logger.debug("Socket: Tarefa de background PARADA (0 clientes).")
            break
        
        # O try/except global impede que a Thread morra se houver erro isolado
        try:
            summary_data, active_sessions = get_data_for_socket()
            force = _force_next_emit
            _force_next_emit = False

            if summary_data and (force or summary_data != _last_summary_emitted):
                extensions.socketio.emit('dashboard_update', {'summary': summary_data}, namespace='/dashboard')
                _last_summary_emitted = summary_data

            if active_sessions is not None and (force or active_sessions != _last_sessions_emitted):
                extensions.socketio.emit('active_streams_update', {'sessions': active_sessions}, namespace='/dashboard')
                _last_sessions_emitted = active_sessions
                
        except Exception as e:
            logger.error(f"Socket: Falha na iteração do background task: {e}")
        finally:
            # 🛡️ PROTEÇÃO CRÍTICA: Limpa a sessão da DB neste greenlet para não travar a base de dados
            if app_instance:
                with app_instance.app_context():
                    extensions.db.session.remove()

        # Dorme por 5 segundos sem bloquear a thread principal
        extensions.socketio.sleep(5)

# ==========================================
# EVENTOS DE LIGAÇÃO (CONNECT/DISCONNECT)
# ==========================================

@extensions.socketio.on('connect', namespace='/dashboard')
def handle_dashboard_connect():
    """Acionado quando um utilizador entra no Painel de Controlo."""
    global connected_clients
    global background_task_greenlet
    global _force_next_emit

    connected_clients += 1
    # O painel que acabou de ligar ainda não recebeu nada por esta via: a próxima
    # ronda é emitida mesmo que os dados não tenham mudado desde a anterior.
    _force_next_emit = True
    logger.debug(f"Socket: Novo cliente conectado ao Dashboard. Total: {connected_clients}")
    
    # Inicia a tarefa de background apenas se ela não estiver a correr
    if background_task_greenlet is None:
        background_task_greenlet = extensions.socketio.start_background_task(background_task)

@extensions.socketio.on('disconnect', namespace='/dashboard')
def handle_dashboard_disconnect():
    """Acionado quando um utilizador fecha a aba do Painel."""
    global connected_clients
    
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
