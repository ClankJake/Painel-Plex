# app/services/stream_manager.py
import logging
from collections import defaultdict
from flask import current_app

logger = logging.getLogger(__name__)

class StreamManager:
    """
    Gere a monitorização e o término de streams diretamente no Plex.
    """
    def __init__(self, plex_connection, data_manager):
        self.conn = plex_connection
        self.data_manager = data_manager

    def _terminate_session(self, session, reason):
        """
        Envia um comando de término de sessão com um motivo personalizado.
        """
        try:
            reason_str = str(reason)
            logger.info(f"A enviar comando de término para a sessão {session.sessionKey} para o utilizador '{session.user.title}' com o motivo: '{reason_str}'")
            session.stop(reason=reason_str)
        except Exception as e:
            logger.error(f"Falha ao enviar comando de término para a sessão {session.sessionKey}: {e}", exc_info=True)

    def block_user_sessions(self, username, reason):
        """Termina todas as sessões ativas de um utilizador específico."""
        if not self.conn.plex:
            logger.warning(f"Não foi possível bloquear as sessões de '{username}': Conexão com o Plex não disponível.")
            return
        try:
            for session in self.conn.plex.sessions():
                if session.user.title == username:
                    self._terminate_session(session, reason)
        except Exception as e:
            logger.error(f"Erro ao bloquear as sessões de '{username}': {e}", exc_info=True)


    def check_and_enforce_streams(self):
        """
        Verifica todas as sessões ativas e impõe as regras de bloqueio e limite de telas.
        """
        if not self.conn.plex:
            if not self.conn.reload(from_job=True)[0]:
                logger.debug("StreamManager: Conexão com o Plex não disponível. A saltar a verificação de streams.")
                return
        
        try:
            sessions = self.conn.plex.sessions()
            if not sessions:
                return

            blocked_users_info = self.data_manager.get_blocked_users_dict()
            
            active_usernames = {s.user.title for s in sessions if s.user}
            user_profiles = self.data_manager.get_user_profiles_by_username(list(active_usernames)) if active_usernames else {}
            
            user_sessions = defaultdict(list)
            for session in sessions:
                if session.user:
                    user_sessions[session.user.title].append(session)

            for username, user_session_list in user_sessions.items():
                # Regra 1: Utilizador Bloqueado
                if username in blocked_users_info:
                    block_info = blocked_users_info[username]
                    block_reason = block_info.get('block_reason', 'manual')
                    
                    logger.info(f"A terminar streams para o utilizador bloqueado: '{username}' (Motivo: {block_reason}).")
                    
                    # CORREÇÃO: Seleciona a mensagem correta com base no motivo do bloqueio.
                    if block_reason == 'expired':
                        msg_template_key = 'TERMINATION_MSG_BLOCKED_EXPIRED'
                        default_msg = "A sua subscrição expirou. Por favor, renove para continuar."
                    elif block_reason == 'trial_expired':
                        msg_template_key = 'TERMINATION_MSG_BLOCKED_TRIAL'
                        default_msg = "O seu período de teste terminou. Renove para continuar."
                    else:  # 'manual' ou qualquer outro motivo
                        msg_template_key = 'TERMINATION_MSG_BLOCKED_MANUAL'
                        default_msg = "O seu acesso ao servidor foi bloqueado pelo administrador."

                    msg_template = current_app.config.get(msg_template_key, default_msg)
                    reason = msg_template.format(username=username)

                    for session in user_session_list:
                        self._terminate_session(session, reason)
                    continue

                # Regra 2: Limite de Telas
                profile = user_profiles.get(username, {})
                screen_limit = profile.get('screen_limit', 0)
                
                if screen_limit > 0 and len(user_session_list) > screen_limit:
                    sessions_to_terminate_count = len(user_session_list) - screen_limit
                    logger.info(f"O utilizador '{username}' excedeu o limite de {screen_limit} tela(s). A terminar {sessions_to_terminate_count} sessão(ões) mais recente(s).")
                    
                    # Ordena as sessões do utilizador pela mais nova (menor progresso de visualização) para a mais antiga
                    sorted_user_sessions = sorted(user_session_list, key=lambda s: s.viewOffset or 0)
                    
                    msg_template = current_app.config.get(
                        'TERMINATION_MSG_SCREEN_LIMIT', 
                        "Você excedeu o seu limite de {limit} telas simultâneas."
                    )
                    reason = msg_template.format(username=username, limit=screen_limit)

                    for i in range(sessions_to_terminate_count):
                        self._terminate_session(sorted_user_sessions[i], reason)

        except Exception as e:
            logger.error(f"Erro inesperado ao verificar e impor streams: {e}", exc_info=True)

