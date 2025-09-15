# app/services/stream_manager.py
import logging
from collections import defaultdict
from flask import current_app, url_for
from datetime import datetime
from flask_babel import gettext as _, ngettext
import requests

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
        Este método foi atualizado para ser mais robusto, utilizando o sessionKey diretamente,
        o que funciona de forma fiável tanto para Direct Play como para Transcode.
        """
        try:
            session_key = getattr(session, 'sessionKey', None)
            if session_key:
                reason_str = str(reason)
                logger.info(f"A enviar comando de término para a sessão {session_key} para o utilizador '{session.user.title}' com o motivo: '{reason_str}'")
                session.stop(reason=reason_str)
            else:
                logger.warning(
                    f"A sessão para o utilizador '{session.user.title}' ({session.title}) não pôde ser terminada porque não foi encontrado um 'sessionKey'. A ignorar."
                )
        except Exception as e:
            logger.error(f"Falha ao enviar comando de término para a sessão do utilizador '{session.user.title}': {e}", exc_info=True)


    def _build_placeholders(self, username, profile, session, context=None):
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

        expiration_date_str = profile.get('expiration_date')
        if expiration_date_str:
            try:
                exp_date = datetime.fromisoformat(expiration_date_str)
                days_left = (exp_date.date() - datetime.now().date()).days
                placeholders['date'] = exp_date.strftime('%d/%m/%Y')
                placeholders['days'] = days_left
            except (ValueError, TypeError):
                placeholders['date'] = 'N/A'
                placeholders['days'] = 'N/A'
        else:
            placeholders['date'] = 'N/A'
            placeholders['days'] = 'N/A'
        
        payment_token = profile.get('payment_token')
        payment_link = '' 

        if payment_token:
            try:
                long_url = url_for('main.payment_page', token=payment_token, _external=True)
                payment_link = long_url
            except RuntimeError:
                base_url = config.get("APP_BASE_URL", "").rstrip('/')
                if base_url:
                    payment_link = f"{base_url}/pay/{payment_token}"
                    logger.warning(
                        "Não foi possível construir a URL via url_for() fora do contexto da requisição. "
                        f"A recorrer à construção manual da URL: {payment_link}"
                    )
                else:
                    logger.warning(
                        "Não foi possível construir a URL de pagamento. 'APP_BASE_URL' não está configurada."
                    )
        placeholders['payment_link'] = payment_link

        user_screen_limit = profile.get('screen_limit', 0)
        screen_prices = config.get("SCREEN_PRICES", {})
        renewal_price_str = config.get("RENEWAL_PRICE", "0.00")
        if str(user_screen_limit) in screen_prices:
            renewal_price_str = screen_prices[str(user_screen_limit)]
        
        try:
            price_value = float(renewal_price_str.replace(',', '.'))
            formatted_price = f"R$ {price_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            formatted_price = "N/A"
        
        if user_screen_limit > 0:
            plan_name = ngettext('%(num)d Tela', '%(num)d Telas', user_screen_limit) % {'num': user_screen_limit}
        else:
            plan_name = _("Plano Padrão")

        placeholders['price'] = formatted_price
        placeholders['plan_name'] = plan_name

        if context:
            placeholders.update(context)

        return placeholders

    def block_user_sessions(self, username, reason):
        """Termina todas as sessões ativas de um utilizador específico."""
        if not self.conn.plex:
            logger.warning(f"Não foi possível bloquear as sessões de '{username}': Conexão com o Plex não disponível.")
            return
        try:
            all_users = self.user_manager.get_all_plex_users() or []
            user_id_map = {u['username']: u['id'] for u in all_users}
            target_user_id = user_id_map.get(username)

            if not target_user_id:
                logger.warning(f"Não foi possível encontrar o ID do utilizador '{username}' para bloquear a sua sessão.")
                return

            for session in self.conn.plex.sessions():
                if session.user and session.user.id == target_user_id:
                    self._terminate_session(session, reason)
        except Exception as e:
            logger.error(f"Erro ao bloquear as sessões de '{username}': {e}", exc_info=True)


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

            # CORREÇÃO DEFINITIVA: Mapeia IDs de utilizador para nomes de utilizador únicos.
            # Isto resolve o problema de 'user.name' em falta e a ambiguidade de 'user.title'.
            all_users = self.user_manager.get_all_plex_users() or []
            if not all_users:
                logger.warning("Não foi possível obter a lista de utilizadores do Plex. A verificação de streams pode ser imprecisa.")
                id_to_username_map = {}
            else:
                id_to_username_map = {user['id']: user['username'] for user in all_users}

            user_sessions = defaultdict(list)
            for session in sessions:
                if session.user and hasattr(session.user, 'id'):
                    # Obtém o nome de utilizador fiável a partir do ID
                    username = id_to_username_map.get(session.user.id)
                    if username:
                        user_sessions[username].append(session)
                    else:
                        logger.warning(f"Sessão encontrada para o ID de utilizador '{session.user.id}' ('{session.user.title}'), mas este utilizador não foi encontrado na lista de amigos do servidor. A ignorar a sessão.")
                elif session.user:
                    # Fallback para o caso de não haver ID (muito improvável)
                    logger.warning(f"Sessão encontrada sem um 'user.id'. A usar 'user.title' como fallback: {session.user.title}")
                    user_sessions[session.user.title].append(session)

            if not user_sessions:
                return
            
            active_usernames = list(user_sessions.keys())

            # Busca os dados uma vez para todos os utilizadores ativos
            user_profiles = self.data_manager.get_user_profiles_by_username(active_usernames)
            blocked_users_info = self.data_manager.get_blocked_users_dict()

            for username, user_session_list in user_sessions.items():
                # Agora 'username' é sempre o nome de utilizador único e correto
                profile = user_profiles.get(username, {})
                first_session = user_session_list[0]

                # A verificação de bloqueio agora usa o nome de utilizador único
                if username in blocked_users_info:
                    block_info = blocked_users_info[username]
                    block_reason = block_info.get('block_reason', 'manual')
                    
                    logger.info(f"A terminar streams para o utilizador bloqueado: '{username}' (Motivo: {block_reason}).")
                    
                    if block_reason == 'expired':
                        msg_template_key = 'TERMINATION_MSG_BLOCKED_EXPIRED'
                        default_msg = "A sua subscrição expirou. Por favor, renove para continuar."
                    elif block_reason == 'trial_expired':
                        msg_template_key = 'TERMINATION_MSG_BLOCKED_TRIAL_EXPIRED'
                        default_msg = "O seu período de teste terminou. Renove para continuar."
                    else:
                        msg_template_key = 'TERMINATION_MSG_BLOCKED_MANUAL'
                        default_msg = "O seu acesso ao servidor foi bloqueado pelo administrador."

                    msg_template = current_app.config.get(msg_template_key) or default_msg
                    placeholders = self._build_placeholders(username, profile, first_session)
                    reason = msg_template.format(**placeholders)

                    for session in user_session_list:
                        self._terminate_session(session, reason)
                    continue

                screen_limit = profile.get('screen_limit', 0)
                
                if screen_limit > 0 and len(user_session_list) > screen_limit:
                    sessions_to_terminate_count = len(user_session_list) - screen_limit
                    logger.info(f"O utilizador '{username}' excedeu o limite de {screen_limit} tela(s). A terminar {sessions_to_terminate_count} sessão(ões) mais recente(s).")
                    
                    sorted_user_sessions = sorted(user_session_list, key=lambda s: s.viewOffset or 0)
                    
                    msg_template = current_app.config.get(
                        'TERMINATION_MSG_SCREEN_LIMIT'
                    ) or "Você excedeu o seu limite de {limit} telas simultâneas."

                    placeholders = self._build_placeholders(username, profile, first_session, context={'limit': screen_limit})
                    reason = msg_template.format(**placeholders)

                    for i in range(sessions_to_terminate_count):
                        self._terminate_session(sorted_user_sessions[i], reason)

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Erro de conexão ao verificar streams (isto pode ser temporário): {e}. A saltar esta verificação.")
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar e impor streams: {e}", exc_info=True)

