# app/services/stream_manager.py

import logging
import requests
import time
import threading
from collections import defaultdict
from datetime import datetime
from tzlocal import get_localzone

from flask import current_app
from flask_babel import gettext as _, ngettext
from plexapi.exceptions import NotFound

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

# Verificação de segurança para o pacote WebSocket
try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False

def get_greeting():
    """Retorna uma saudação com base na hora local atual configurada no servidor."""
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
    Potenciado por SSE (Websockets) para tempo real, com sistema Anti-Spam.
    """
    def __init__(self, plex_connection, data_manager, user_manager):
        self.conn = plex_connection
        self.data_manager = data_manager
        self.user_manager = user_manager
        self._listener = None
        self._app = None
        self._recently_killed_sessions = {} 

    # --- LÓGICA DE TEMPO REAL (SSE) ---

    def start_listener(self, app):
        """Inicia o ouvinte de eventos em tempo real (Websocket) do Plex."""
        if not self.conn.plex:
            return

        if getattr(self, '_listener', None) and self._listener.is_alive():
            return

        if not HAS_WEBSOCKET:
            logger.error("🚨 PACOTE EM FALTA: O modo de Tempo Real (Plex SSE) não pode iniciar. Execute no terminal: pip install websocket-client")
            return

        try:
            self._app = app
            self._listener = self.conn.plex.startAlertListener(self._on_plex_event)
            logger.info("📡 Plex Real-Time Listener (SSE) iniciado com sucesso! Controlo de streams instantâneo ativado.")
        except Exception as e:
            logger.error(f"Falha ao iniciar o Plex Listener SSE: {e}")

    def stop_listener(self):
        """Pára o ouvinte de eventos."""
        if getattr(self, '_listener', None):
            self._listener.stop()
            self._listener = None
            logger.info("📡 Plex Real-Time Listener (SSE) desligado.")

    def _on_plex_event(self, data):
        """Callback acionado no milissegundo em que algo muda no Plex."""
        if isinstance(data, dict) and data.get('type') == 'playing':
            state_notifications = data.get('PlaySessionStateNotification', [])
            
            should_check = False
            for notif in state_notifications:
                if notif.get('state') in ['playing', 'buffering', 'paused', 'stopped']:
                    should_check = True
                    break
            
            if should_check and self._app:
                # 1. Avisa o Frontend IMEDIATAMENTE (Latência 0ms)
                try:
                    from app.extensions import socketio
                    socketio.emit('dashboard_update_streams', namespace='/dashboard')
                except Exception as e:
                    logger.debug(f"Aviso ao tentar emitir socket SSE: {e}")

                # 2. Executa a verificação pesada numa thread separada para não encravar o Plex
                def background_check():
                    with self._app.app_context():
                        self.check_and_enforce_streams(from_event=True)
                        
                threading.Thread(target=background_check).start()

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

    def check_and_enforce_streams(self, from_event=False):
        """Verifica todas as sessões ativas e impõe as regras de bloqueio e limite de telas."""
        if not from_event:
            logger.debug("A executar a verificação de streams (Job Fallback/Sweep)...")

        # Tenta ligar o listener apenas se o pacote WebSocket estiver instalado
        if HAS_WEBSOCKET and (not getattr(self, '_listener', None) or not self._listener.is_alive()):
            try:
                app = current_app._get_current_object()
                self.start_listener(app)
            except RuntimeError:
                pass 

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

            user_sessions_by_id = self._group_sessions_by_user(sessions)
            if not user_sessions_by_id:
                return
            
            id_to_username_map, admin_user_id = self._build_user_maps()
            active_user_ids = list(user_sessions_by_id.keys())
            user_profiles = self.data_manager.get_user_profiles_by_id(active_user_ids)
            blocked_users_info = self.data_manager.get_blocked_users_dict()
            
            for user_id, user_session_list in user_sessions_by_id.items():
                if admin_user_id and str(user_id) == str(admin_user_id):
                    continue
                
                username = id_to_username_map.get(user_id)
                if not username:
                    continue

                profile = user_profiles.get(user_id, {})
                
                if user_id in blocked_users_info:
                    self._enforce_block_rules(user_id, username, user_session_list, profile, blocked_users_info[user_id], config)
                else:
                    self._enforce_screen_limits(user_id, username, user_session_list, profile, config)

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Erro de conexão ao verificar streams (temporário): {e}. A saltar.")
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar e impor streams: {e}", exc_info=True)

    # --- MÉTODOS AUXILIARES E DE LÓGICA DE NEGÓCIO ---

    def _terminate_session(self, session, reason):
        """Envia o comando de paragem (stop) para a API do Plex com proteção Anti-Spam e 404."""
        try:
            session_key = getattr(session, 'sessionKey', None)

            if session_key:
                current_time = time.time()
                
                # Limpa a memória de sessões mortas há mais de 30 segundos
                self._recently_killed_sessions = {k: v for k, v in self._recently_killed_sessions.items() if current_time - v < 30}
                
                # Sistema Anti-Spam: Verifica se já mandámos matar esta sessão há pouco tempo
                if session_key in self._recently_killed_sessions:
                    logger.debug(f"⏭️ A sessão {session_key} já recebeu comando KILL recentemente. A ignorar duplicado.")
                    return

                logger.info(f"⚡ A enviar comando de término (KILL) para a sessão {session_key} (Utilizador: '{session.user.title}') | Motivo: '{reason}'")
                
                # Regista que a sessão levou tiro
                self._recently_killed_sessions[session_key] = current_time

                # Manda o comando para o Plex
                session.stop(reason=str(reason))
                
                # Dispara evento SocketIO
                try:
                    from app.extensions import socketio
                    socketio.emit('dashboard_update_streams', namespace='/dashboard')
                except Exception:
                    pass
            else:
                user_title = getattr(session.user, 'title', 'Desconhecido') if hasattr(session, 'user') else 'Desconhecido'
                logger.info(f"⏳ A sessão ({session.title}) de '{user_title}' está a carregar o buffer e ainda não tem ID. A agendar novo corte em 3 segundos...")
                
                # Cria uma tarefa em background para verificar novamente daqui a 3 segundos
                def delayed_check():
                    app = self._app
                    if not app:
                        try:
                            from flask import current_app
                            app = current_app._get_current_object()
                        except RuntimeError:
                            pass
                    
                    if app:
                        with app.app_context():
                            self.check_and_enforce_streams(from_event=True)

                threading.Timer(3.0, delayed_check).start()
        
        except NotFound:
            # Erro 404: O Plex já limpou a sessão da base de dados dele
            user_title = getattr(session.user, 'title', 'Desconhecido') if hasattr(session, 'user') else 'Desconhecido'
            logger.info(f"✅ Sessão de '{user_title}' já não existe no Plex (Foi terminada com sucesso).")
        except Exception as e:
            user_title = getattr(session.user, 'title', 'Desconhecido') if hasattr(session, 'user') else 'Desconhecido'
            logger.error(f"Falha ao terminar a sessão do utilizador '{user_title}': {e}", exc_info=True)

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
        """Constrói um dicionário com os placeholders para as mensagens."""
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
        
        logger.info(f"🚫 A terminar {len(sessions)} stream(s) para o utilizador bloqueado: '{username}' (Motivo: {block_reason}).")
        
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
            logger.info(f"⚠️ O utilizador '{username}' excedeu o limite de {screen_limit} tela(s). A terminar {excess_count} sessão(ões).")
            
            sorted_sessions = sorted(sessions, key=lambda s: s.viewOffset or 0, reverse=True)
            
            msg_template = config.get('TERMINATION_MSG_SCREEN_LIMIT') or "Você excedeu o seu limite de {limit} telas simultâneas."
            placeholders = self._build_placeholders(user_id, username, profile, sorted_sessions[0], context={'limit': screen_limit})
            reason_text = msg_template.format(**placeholders)

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
