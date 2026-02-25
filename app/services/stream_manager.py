# app/services/stream_manager.py

import logging
import requests
from collections import defaultdict
from datetime import datetime
from tzlocal import get_localzone

from flask import current_app, url_for
from flask_babel import gettext as _, ngettext
from plexapi.exceptions import NotFound

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

def get_greeting():
    """Retorna uma saudação com base na hora local atual configurada no servidor."""
    # CORREÇÃO: Utiliza o fuso horário local para garantir que a saudação é correta (evita UTC do Docker)
    current_hour = datetime.now(get_localzone()).hour
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

    # --- MÉTODOS PÚBLICOS ---

    def block_user_sessions(self, plex_user_id, reason):
        """Termina todas as sessões ativas de um utilizador específico."""
        if not self.conn.plex:
            logger.warning(f"Não foi possível bloquear as sessões do utilizador ID {plex_user_id}: Conexão com o Plex não disponível.")
            return
            
        try:
            for session in self.conn.plex.sessions():
                session_user_id = self._get_session_user_id(session)
                if session_user_id and str(session_user_id) == str(plex_user_id):
                    self._terminate_session(session, reason)
        except Exception as e:
            logger.error(f"Erro ao bloquear as sessões do utilizador ID {plex_user_id}: {e}", exc_info=True)

    def check_and_enforce_streams(self):
        """
        Verifica todas as sessões ativas e impõe as regras de bloqueio e limite de telas.
        (Refatorado para melhor leitura e SRP).
        """
        logger.debug("A executar a verificação de streams agendada...")
        config = load_or_create_config()

        if not self.conn.plex:
            success, _ = self.conn.reload(from_job=True)
            if not success:
                logger.debug("StreamManager: Conexão com o Plex não disponível. A saltar a verificação.")
                return
        
        try:
            sessions = self.conn.plex.sessions()
            if not sessions:
                return

            # 1. Agrupar sessões por Utilizador
            user_sessions_by_id = self._group_sessions_by_user(sessions)
            if not user_sessions_by_id:
                return
            
            # 2. Obter dados auxiliares necessários para a validação
            id_to_username_map, admin_user_id = self._build_user_maps()
            active_user_ids = list(user_sessions_by_id.keys())
            user_profiles = self.data_manager.get_user_profiles_by_id(active_user_ids)
            blocked_users_info = self.data_manager.get_blocked_users_dict()
            
            # 3. Processar cada utilizador ativo
            for user_id, user_session_list in user_sessions_by_id.items():
                if admin_user_id and str(user_id) == str(admin_user_id):
                    continue
                
                username = id_to_username_map.get(user_id)
                if not username:
                    continue

                profile = user_profiles.get(user_id, {})
                
                # Se o utilizador estiver bloqueado, termina tudo.
                # Caso contrário, verifica se excede o limite de ecrãs.
                if user_id in blocked_users_info:
                    self._enforce_block_rules(
                        user_id, username, user_session_list, profile, 
                        blocked_users_info[user_id], config
                    )
                else:
                    self._enforce_screen_limits(
                        user_id, username, user_session_list, profile, config
                    )

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Erro de conexão ao verificar streams (temporário): {e}. A saltar.")
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar e impor streams: {e}", exc_info=True)

    # --- MÉTODOS AUXILIARES E DE LÓGICA DE NEGÓCIO (SRP) ---

    def _terminate_session(self, session, reason):
        """Envia o comando de paragem (stop) para a API do Plex."""
        try:
            session_key = getattr(session, 'sessionKey', None)
            internal_session_obj = getattr(session, 'session', None)

            if session_key and internal_session_obj:
                logger.info(f"A enviar comando de término para a sessão {session_key} (Utilizador: '{session.user.title}') | Motivo: '{reason}'")
                session.stop(reason=str(reason))
            else:
                logger.warning(f"A sessão ({session.title}) não pôde ser terminada (falta de sessionKey ou a iniciar).")
        except Exception as e:
            logger.error(f"Falha ao terminar a sessão do utilizador '{session.user.title}': {e}", exc_info=True)

    def _get_session_user_id(self, session):
        """Tenta obter o ID do utilizador de forma segura."""
        try:
            if hasattr(session, 'userID'):
                return session.userID
            if hasattr(session, 'user'):
                return getattr(session.user, 'id', None)
        except NotFound as e:
            logger.warning(f"Utilizador '{getattr(session, '_username', 'desconhecido')}' não encontrado no Plex (Erro: {e}).")
        return None

    def _group_sessions_by_user(self, sessions):
        """Agrupa uma lista de sessões num dicionário tendo o ID do utilizador como chave."""
        user_sessions_by_id = defaultdict(list)
        for session in sessions:
            user_id = self._get_session_user_id(session)
            if user_id:
                user_sessions_by_id[user_id].append(session)
        return user_sessions_by_id

    def _build_user_maps(self):
        """Constrói o mapa de ID -> Username e identifica o Admin."""
        all_users = self.user_manager.get_all_plex_users() or []
        admin_account = self.conn.account
        admin_user_id = getattr(admin_account, 'id', None)

        id_to_username_map = {user['id']: user['username'] for user in all_users}
        if admin_user_id and admin_account.username:
            id_to_username_map[admin_user_id] = admin_account.username
            
        return id_to_username_map, admin_user_id

    def _get_media_title(self, session):
        """Extrai o título formatado da sessão (séries vs filmes)."""
        if getattr(session, 'type', None) == 'episode' and hasattr(session, 'grandparentTitle'):
            return f"{session.grandparentTitle} - {session.title}"
        return session.title

    def _build_placeholders(self, user_id, username, profile, session, context=None):
        """Constrói um dicionário com os placeholders para as mensagens (saudação, nome, limites)."""
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

    def _enforce_block_rules(self, user_id, username, sessions, profile, block_info, config):
        """Aplica a regra de bloqueio: Termina todas as sessões ativas deste utilizador."""
        block_reason = block_info.get('block_reason', 'manual')
        first_session = sessions[0]
        
        logger.info(f"A terminar {len(sessions)} stream(s) para o utilizador bloqueado: '{username}' (Motivo: {block_reason}).")
        
        # 1. Obter a mensagem template da configuração
        msg_template_key = {
            'expired': 'TERMINATION_MSG_BLOCKED_EXPIRED',
            'trial_expired': 'TERMINATION_MSG_BLOCKED_TRIAL_EXPIRED'
        }.get(block_reason, 'TERMINATION_MSG_BLOCKED_MANUAL')

        default_msg = {
            'expired': "A sua subscrição expirou. Por favor, renove para continuar.",
            'trial_expired': "O seu período de teste terminou. Renove para continuar."
        }.get(block_reason, "O seu acesso ao servidor foi bloqueado pelo administrador.")

        msg_template = config.get(msg_template_key) or default_msg
        placeholders = self._build_placeholders(user_id, username, profile, first_session)
        reason_text = msg_template.format(**placeholders)

        # 2. Terminar sessões e registar na Base de Dados
        for session in sessions:
            self.data_manager.log_stream_termination(
                plex_user_id=user_id,
                username=username,
                media_title=self._get_media_title(session),
                platform=getattr(session.player, 'platform', 'Desconhecido') if session.player else 'Desconhecido',
                reason=f'blocked_{block_reason}'
            )
            self._terminate_session(session, reason_text)

    def _enforce_screen_limits(self, user_id, username, sessions, profile, config):
        """Aplica a regra de limite de ecrãs simultâneos."""
        screen_limit = profile.get('screen_limit', 0)
        
        if screen_limit > 0 and len(sessions) > screen_limit:
            excess_count = len(sessions) - screen_limit
            logger.info(f"O utilizador '{username}' excedeu o limite de {screen_limit} tela(s). A terminar {excess_count} sessão(ões).")
            
            # Ordena: Termina as sessões que estão há mais tempo a correr
            sorted_sessions = sorted(sessions, key=lambda s: s.viewOffset or 0, reverse=True)
            
            msg_template = config.get('TERMINATION_MSG_SCREEN_LIMIT') or "Você excedeu o seu limite de {limit} telas simultâneas."
            placeholders = self._build_placeholders(user_id, username, profile, sorted_sessions[0], context={'limit': screen_limit})
            reason_text = msg_template.format(**placeholders)

            # Termina apenas a quantidade excedente
            for i in range(excess_count):
                session_to_terminate = sorted_sessions[i]
                
                self.data_manager.log_stream_termination(
                    plex_user_id=user_id,
                    username=username,
                    media_title=self._get_media_title(session_to_terminate),
                    platform=getattr(session_to_terminate.player, 'platform', 'Desconhecido') if session_to_terminate.player else 'Desconhecido',
                    reason='limit_exceeded'
                )
                self._terminate_session(session_to_terminate, reason_text)
