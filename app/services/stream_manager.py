# app/services/stream_manager.py
import logging
from collections import defaultdict
from flask import current_app, url_for
from datetime import datetime
from flask_babel import gettext as _, ngettext
import requests
from plexapi.exceptions import NotFound # Importa a exceção específica

logger = logging.getLogger(__name__)

def get_greeting():
    """Retorna uma saudação com base na hora atual."""
    current_hour = datetime.now().hour
    if 5 <= current_hour < 12:
        return _("Bom dia")
    elif 12 <= current_hour < 18:
        return _("Boa tarde")
    else:
        return _("Boa noite")

class StreamManager:
    """
    Gere a monitorização e o término de streams diretamente no Plex.
    """
    def __init__(self, plex_connection, data_manager, user_manager):
        self.conn = plex_connection
        self.data_manager = data_manager
        self.user_manager = user_manager

    def _terminate_session(self, session, reason):
        """
        Envia um comando de término de sessão com um motivo personalizado.
        """
        try:
            session_key = getattr(session, 'sessionKey', None)
            internal_session_obj = getattr(session, 'session', None)

            if session_key and internal_session_obj:
                reason_str = str(reason)
                logger.info(f"A enviar comando de término para a sessão {session_key} para o utilizador '{session.user.title}' com o motivo: '{reason_str}'")
                session.stop(reason=reason_str)
            else:
                logger.warning(
                    f"A sessão para o utilizador '{session.user.title}' ({session.title}) não pôde ser terminada porque não foi encontrado um 'sessionKey' ou o objeto de sessão interno estava em falta (provavelmente um stream a inicializar)."
                )
        except Exception as e:
            logger.error(f"Falha ao enviar comando de término para a sessão do utilizador '{session.user.title}': {e}", exc_info=True)


    def _build_placeholders(self, user_id, username, profile, session, context=None):
        """Constrói um dicionário de placeholders para as mensagens."""
        from app.config import load_or_create_config
        config = load_or_create_config()

        placeholders = {
            'username': username,
            'name': profile.get('name') or username,
            'email': getattr(session.user, 'email', ''),
            'greeting': get_greeting(),
            'telegram_user': profile.get('telegram_user', ''),
            'discord_user_id': profile.get('discord_user_id', ''),
            'phone_number': profile.get('phone_number', '')
        }
        
        if context:
            placeholders.update(context)

        return placeholders

    def block_user_sessions(self, plex_user_id, reason):
        """Termina todas as sessões ativas de um utilizador específico."""
        if not self.conn.plex:
            logger.warning(f"Não foi possível bloquear as sessões do utilizador ID {plex_user_id}: Conexão com o Plex não disponível.")
            return
        try:
            for session in self.conn.plex.sessions():
                session_user_id = None
                try:
                    # Tenta obter o ID do utilizador de forma segura, priorizando o userID numérico.
                    if hasattr(session, 'userID'):
                        session_user_id = session.userID
                    elif session.user:
                        session_user_id = session.user.id
                except NotFound as e:
                    logger.warning(
                        f"Não foi possível encontrar o utilizador '{getattr(session, '_username', 'desconhecido')}' "
                        f"ao tentar bloquear sessões. A saltar esta sessão. Erro: {e}"
                    )
                    continue

                if session_user_id and session_user_id == plex_user_id:
                    self._terminate_session(session, reason)
        except Exception as e:
            logger.error(f"Erro ao bloquear as sessões do utilizador ID {plex_user_id}: {e}", exc_info=True)


    def check_and_enforce_streams(self):
        """
        Verifica todas as sessões ativas e impõe as regras de bloqueio e limite de telas.
        """
        logger.debug("A executar a verificação de streams agendada...")
        if not self.conn.plex:
            if not self.conn.reload(from_job=True)[0]:
                logger.debug("StreamManager: Conexão com o Plex não disponível. A saltar a verificação de streams.")
                return
        
        try:
            sessions = self.conn.plex.sessions()
            if not sessions:
                return

            all_users = self.user_manager.get_all_plex_users() or []
            admin_account = self.conn.account
            admin_user_id = getattr(admin_account, 'id', None)

            id_to_username_map = {user['id']: user['username'] for user in all_users}
            if admin_user_id and admin_account.username:
                id_to_username_map[admin_user_id] = admin_account.username

            user_sessions_by_id = defaultdict(list)
            for session in sessions:
                user_id = None
                try:
                    # CORREÇÃO: Prioriza a obtenção do ID diretamente do atributo da sessão (mais estável),
                    # em vez de depender do objeto 'user' que pode falhar se o nome de utilizador mudar.
                    if hasattr(session, 'userID'):
                        user_id = session.userID
                    elif hasattr(session, 'user'):
                        # Fallback para o método antigo, agora protegido contra o erro NotFound.
                        user_id = getattr(session.user, 'id', None)
                except NotFound as e:
                    logger.warning(
                        f"Não foi possível encontrar o utilizador '{getattr(session, '_username', 'desconhecido')}' "
                        f"no Plex (pode ter sido renomeado ou removido). A saltar a sessão. Erro: {e}"
                    )
                    continue # Pula para a próxima sessão
                
                if user_id:
                    user_sessions_by_id[user_id].append(session)

            if not user_sessions_by_id:
                return
            
            active_user_ids = list(user_sessions_by_id.keys())
            user_profiles = self.data_manager.get_user_profiles_by_id(active_user_ids)
            blocked_users_info = self.data_manager.get_blocked_users_dict()
            
            for user_id, user_session_list in user_sessions_by_id.items():
                if admin_user_id and user_id == admin_user_id:
                    continue
                
                username = id_to_username_map.get(user_id)
                if not username:
                    continue

                profile = user_profiles.get(user_id, {})
                first_session = user_session_list[0]

                if user_id in blocked_users_info:
                    block_info = blocked_users_info[user_id]
                    block_reason = block_info.get('block_reason', 'manual')
                    
                    for session in user_session_list:
                        media_title = session.title
                        if getattr(session, 'type', None) == 'episode' and hasattr(session, 'grandparentTitle'):
                            media_title = f"{session.grandparentTitle} - {session.title}"
                        
                        self.data_manager.log_stream_termination(
                            plex_user_id=user_id,
                            username=username,
                            media_title=media_title,
                            platform=session.player.platform if session.player else 'Desconhecido',
                            reason=f'blocked_{block_reason}'
                        )
                    
                    logger.info(f"A terminar streams para o utilizador bloqueado: '{username}' (Motivo: {block_reason}).")
                    
                    msg_template_key = {
                        'expired': 'TERMINATION_MSG_BLOCKED_EXPIRED',
                        'trial_expired': 'TERMINATION_MSG_BLOCKED_TRIAL_EXPIRED'
                    }.get(block_reason, 'TERMINATION_MSG_BLOCKED_MANUAL')

                    default_msg = {
                        'expired': "A sua subscrição expirou. Por favor, renove para continuar.",
                        'trial_expired': "O seu período de teste terminou. Renove para continuar."
                    }.get(block_reason, "O seu acesso ao servidor foi bloqueado pelo administrador.")

                    msg_template = current_app.config.get(msg_template_key) or default_msg
                    placeholders = self._build_placeholders(user_id, username, profile, first_session)
                    reason = msg_template.format(**placeholders)

                    for session in user_session_list:
                        self._terminate_session(session, reason)
                    continue

                screen_limit = profile.get('screen_limit', 0)
                
                if screen_limit > 0 and len(user_session_list) > screen_limit:
                    sessions_to_terminate_count = len(user_session_list) - screen_limit
                    logger.info(f"O utilizador '{username}' excedeu o limite de {screen_limit} tela(s). A terminar {sessions_to_terminate_count} sessão(ões) mais antiga(s).")
                    
                    sorted_user_sessions = sorted(user_session_list, key=lambda s: s.viewOffset or 0, reverse=True)
                    
                    msg_template = current_app.config.get(
                        'TERMINATION_MSG_SCREEN_LIMIT'
                    ) or "Você excedeu o seu limite de {limit} telas simultâneas."

                    placeholders = self._build_placeholders(user_id, username, profile, first_session, context={'limit': screen_limit})
                    reason = msg_template.format(**placeholders)

                    for i in range(sessions_to_terminate_count):
                        session_to_terminate = sorted_user_sessions[i]
                        media_title = session_to_terminate.title
                        if getattr(session_to_terminate, 'type', None) == 'episode' and hasattr(session_to_terminate, 'grandparentTitle'):
                            media_title = f"{session_to_terminate.grandparentTitle} - {session_to_terminate.title}"

                        self.data_manager.log_stream_termination(
                            plex_user_id=user_id,
                            username=username,
                            media_title=media_title,
                            platform=session_to_terminate.player.platform if session_to_terminate.player else 'Desconhecido',
                            reason='limit_exceeded'
                        )
                        self._terminate_session(session_to_terminate, reason)

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Erro de conexão ao verificar streams (isto pode ser temporário): {e}. A saltar esta verificação.")
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar e impor streams: {e}", exc_info=True)

