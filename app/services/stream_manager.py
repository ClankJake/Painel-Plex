# app/services/stream_manager.py

import logging
import requests
import time
import threading
import base64
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse, parse_qsl, urlencode
from tzlocal import get_localzone

from flask import current_app, url_for
from flask_babel import gettext as _, ngettext
from plexapi.exceptions import NotFound

from ..config import load_or_create_config
from ..utils.log_formatting import NETWORK_ERRORS, ThrottledReporter, describe

logger = logging.getLogger(__name__)

# Estas rotinas correm em ciclo (a cada poucos segundos, e a cada evento SSE).
# Quando o Plex fica indisponível, TODAS falham em cadeia: sem moderação, uma
# indisponibilidade de 2 minutos escrevia centenas de linhas idênticas no log.
# O reporter regista a primeira falha, resume as seguintes e assinala o retorno.
network_reporter = ThrottledReporter(logger, interval=300)

# Silenciar o spam de INFO das bibliotecas do Plex e Websocket
logging.getLogger('plexapi').setLevel(logging.WARNING)
logging.getLogger('websocket').setLevel(logging.WARNING)

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
    Potenciado por SSE (Websockets) para tempo real, com sistema Anti-Spam,
    Prevenção Proativa e Controlo de Sobrecarga (Thundering Herd).
    """
    # Janela de agregação dos eventos SSE: espera-se SSE_DEBOUNCE por um evento
    # seguinte, mas nunca mais do que SSE_MAX_DEBOUNCE desde o primeiro evento
    # pendente (evita que uma rajada contínua adie a verificação indefinidamente).
    SSE_DEBOUNCE_SECONDS = 2.0
    SSE_MAX_DEBOUNCE_SECONDS = 6.0

    # Estados que interessam ao controlo de streams.
    RELEVANT_SSE_STATES = ('playing', 'buffering', 'paused', 'stopped')
    # Uma sessão sem notificações há mais do que isto é dada como terminada e
    # esquecida (nem todos os clientes enviam 'stopped' ao desligar).
    SESSION_STATE_TTL_SECONDS = 600

    # Backoff entre tentativas de arranque do listener (Plex offline).
    LISTENER_RETRY_BASE_SECONDS = 15
    LISTENER_RETRY_MAX_SECONDS = 300

    def __init__(self, plex_connection, data_manager, user_manager):
        self.conn = plex_connection
        self.data_manager = data_manager
        self.user_manager = user_manager
        self._listener = None
        self._app = None

        # Controlo de Concorrência Otimizado
        self._delayed_check_lock = threading.Lock()
        self._delayed_check_pending = False
        self._sse_debounce_lock = threading.Lock()
        self._sse_debounce_timer = None
        # 🐛 Protege o arranque do listener: sem este lock, dois pedidos concorrentes
        # podiam passar ambos pela verificação "is_alive()" e criar DOIS listeners
        # SSE ligados ao mesmo servidor (o arranque faz I/O de rede, que é um ponto
        # de cedência com gevent), duplicando eventos e ligações websocket.
        self._listener_lock = threading.Lock()
        # Guarda a instância de PlexServer a que o listener atual está ligado, para
        # detetar quando a ligação foi recarregada e o listener ficou órfão.
        self._listener_plex_ref = None
        # Instante-limite do debounce em curso (ver SSE_MAX_DEBOUNCE_SECONDS).
        self._sse_debounce_deadline = None
        # Último estado conhecido de cada sessão do Plex. É isto que distingue
        # uma MUDANÇA real (play/pause/stop/nova sessão) de um simples "ping" de
        # progresso — o Plex reenvia o estado 'playing' de cada sessão de poucos
        # em poucos segundos, e cada um desses pings disparava antes uma
        # verificação completa (chamada à API + consultas à base de dados).
        self._session_states_lock = threading.Lock()
        self._last_session_states = {}
        # Backoff do arranque do listener: com o Plex offline, a verificação
        # periódica tentava reabrir o websocket a cada ciclo (15s), falhando
        # sempre e enchendo o log.
        self._listener_retry_at = 0.0
        self._listener_failures = 0

    # --- LÓGICA DE TEMPO REAL (SSE) ---

    def _is_listener_healthy(self):
        """
        Um listener só é considerado saudável se estiver vivo E ligado à instância
        ATUAL do PlexServer. Depois de um 'reload_connections()' (troca de token,
        de URL, ou reconexão automática), o objeto PlexServer é substituído — o
        listener antigo continua vivo mas a falar com uma ligação obsoleta, deixando
        de entregar eventos sem qualquer erro visível.
        """
        listener = getattr(self, '_listener', None)
        if not listener or not listener.is_alive():
            return False
        return self._listener_plex_ref is self.conn.plex

    def _on_listener_error(self, error):
        """
        Callback de erro do AlertListener. Sem isto, o plexapi engolia as falhas
        do websocket em silêncio: o listener morria e o painel só voltava ao tempo
        real por acaso, na próxima verificação periódica, sem nada nos logs a
        explicar porquê.
        """
        logger.warning(f"📡 Plex Real-Time Listener (SSE) reportou um erro: {error}. Será reiniciado na próxima verificação.")

    def start_listener(self, app):
        if not self.conn.plex:
            return

        if not HAS_WEBSOCKET:
            logger.error("🚨 PACOTE EM FALTA: O modo de Tempo Real (Plex SSE) não pode iniciar. Execute no terminal: pip install websocket-client")
            return

        with self._listener_lock:
            if self._is_listener_healthy():
                return

            # Enquanto o Plex não estiver contactável, espaça-se as tentativas
            # em vez de tentar (e falhar) a cada verificação periódica.
            if time.monotonic() < self._listener_retry_at:
                return

            # Se existe um listener antigo (morto ou agarrado a uma ligação obsoleta),
            # é preciso pará-lo explicitamente para não deixar threads e sockets órfãos.
            if getattr(self, '_listener', None):
                try:
                    self._listener.stop()
                except Exception as e:
                    logger.debug(f"Aviso ao parar o listener SSE antigo: {e}")
                finally:
                    self._listener = None
                    self._listener_plex_ref = None

            try:
                # Pega a instância real da App para usar nas threads
                self._app = app._get_current_object() if hasattr(app, '_get_current_object') else app
                self._listener = self.conn.plex.startAlertListener(
                    self._on_plex_event,
                    self._on_listener_error
                )
                self._listener_plex_ref = self.conn.plex
                if self._listener_failures:
                    logger.info(f"📡 Plex Real-Time Listener (SSE) restabelecido após {self._listener_failures} tentativa(s) falhada(s).")
                self._listener_retry_at = 0.0
                self._listener_failures = 0
                logger.debug("📡 Plex Real-Time Listener (SSE) iniciado com sucesso! Controlo de streams instantâneo ativado.")
            except Exception as e:
                self._listener = None
                self._listener_plex_ref = None
                self._listener_failures += 1
                delay = min(
                    self.LISTENER_RETRY_MAX_SECONDS,
                    self.LISTENER_RETRY_BASE_SECONDS * (2 ** (self._listener_failures - 1))
                )
                self._listener_retry_at = time.monotonic() + delay
                # Só a primeira falha é ERROR: as seguintes, enquanto o Plex não
                # volta, ficam em WARNING para não dominarem o log.
                level = logger.error if self._listener_failures == 1 else logger.warning
                level(f"Falha ao iniciar o Plex Listener SSE: {describe(e)}. Nova tentativa em {delay}s.")

    def stop_listener(self):
        with self._listener_lock:
            if getattr(self, '_listener', None):
                try:
                    self._listener.stop()
                except Exception as e:
                    logger.debug(f"Aviso silencioso ao parar SSE: {e}")
                finally:
                    self._listener = None
                    self._listener_plex_ref = None
                    logger.debug("📡 Plex Real-Time Listener (SSE) desligado.")

        # Cancela também qualquer verificação em debounce ainda pendente, para não
        # ficar uma thread a acordar depois do encerramento.
        with self._sse_debounce_lock:
            if self._sse_debounce_timer:
                try:
                    self._sse_debounce_timer.cancel()
                except Exception:
                    pass
                self._sse_debounce_timer = None
            self._sse_debounce_deadline = None

        # Sem listener, os estados memorizados ficam obsoletos: ao reconectar, o
        # primeiro evento de cada sessão tem de valer como mudança.
        with self._session_states_lock:
            self._last_session_states.clear()

    def _execute_debounced_check(self):
        """Executa a verificação após o tempo do debounce expirar."""
        with self._sse_debounce_lock:
            self._sse_debounce_timer = None
            self._sse_debounce_deadline = None

        if self._app:
            try:
                with self._app.app_context():
                    self.check_and_enforce_streams(from_event=True)
            except Exception as e:
                logger.error(f"Falha na verificação de streams por evento SSE: {describe(e)}")

    def _has_state_changed(self, notifications):
        """
        Filtra os "pings" de progresso, devolvendo True só quando algo mudou
        mesmo: uma sessão nova, uma transição play/pause/buffering ou o fim de
        uma sessão.

        Porque isto importa: enquanto alguém assiste, o Plex reenvia o estado
        'playing' dessa sessão de poucos em poucos segundos. Cada um desses
        eventos disparava uma verificação completa — chamada à API do Plex,
        consultas à base de dados e um refrescamento em todos os dashboards
        abertos — sem que nada tivesse mudado. Com quatro streams a decorrer,
        eram dezenas de verificações por minuto para nada, além da verificação
        periódica que já existe como rede de segurança.
        """
        changed = False
        now = time.monotonic()

        with self._session_states_lock:
            # Esquece sessões que já não dão sinal de vida. Sem isto, um cliente
            # que se desliga sem enviar 'stopped' ficaria memorizado para sempre.
            for key in [k for k, v in self._last_session_states.items()
                        if now - v[1] > self.SESSION_STATE_TTL_SECONDS]:
                del self._last_session_states[key]

            for notification in notifications:
                if not isinstance(notification, dict):
                    continue

                state = notification.get('state')
                if state not in self.RELEVANT_SSE_STATES:
                    continue

                session_key = notification.get('sessionKey')
                if session_key in (None, ''):
                    # Sem identificador não há como comparar: trata-se como
                    # mudança, para nunca perder um evento relevante.
                    changed = True
                    continue

                session_key = str(session_key)
                known = self._last_session_states.get(session_key)

                if known and known[0] == state:
                    # Ping de progresso: mesmo estado da última vez. Só se
                    # renova a marca temporal, para a sessão não expirar.
                    self._last_session_states[session_key] = (state, now)
                    continue

                if state == 'stopped':
                    self._last_session_states.pop(session_key, None)
                else:
                    self._last_session_states[session_key] = (state, now)
                changed = True

        return changed

    def _schedule_sse_check(self):
        """
        Agrega eventos próximos numa única verificação (debounce), mas com um
        teto: numa rajada contínua, a verificação corre à mesma ao fim de
        SSE_MAX_DEBOUNCE_SECONDS em vez de ser sucessivamente adiada.
        """
        now = time.monotonic()
        with self._sse_debounce_lock:
            if self._sse_debounce_deadline is None:
                self._sse_debounce_deadline = now + self.SSE_MAX_DEBOUNCE_SECONDS

            delay = min(self.SSE_DEBOUNCE_SECONDS, max(0.0, self._sse_debounce_deadline - now))

            if self._sse_debounce_timer:
                self._sse_debounce_timer.cancel()
            self._sse_debounce_timer = threading.Timer(delay, self._execute_debounced_check)
            self._sse_debounce_timer.daemon = True
            self._sse_debounce_timer.start()

    def _on_plex_event(self, data):
        # 🛡️ Este callback corre dentro da thread do websocket do plexapi. O plexapi
        # já apanha exceções aqui, mas regista-as no logger DELE ('plexapi'), o que
        # as tornava praticamente invisíveis nos nossos logs. Tratamos tudo aqui para
        # que qualquer falha apareça com o contexto certo — e nunca comprometa a
        # ligação em tempo real.
        try:
            if not isinstance(data, dict) or data.get('type') != 'playing':
                return

            state_notifications = data.get('PlaySessionStateNotification') or []
            if not isinstance(state_notifications, list):
                return

            if not self._has_state_changed(state_notifications) or not self._app:
                return

            # 1. ATUALIZAÇÃO VISUAL IMEDIATA (Sem Lock/Debounce)
            # Garante que os botões de Pausa/Play reagem instantaneamente no Frontend
            with self._app.app_context():
                try:
                    from app.extensions import socketio
                    socketio.emit('dashboard_update_streams', namespace='/dashboard')
                except Exception as e:
                    logger.debug(f"Não foi possível emitir a atualização de streams via WebSocket: {e}")

            # 2. VERIFICAÇÃO PESADA COM DEBOUNCE OTIMIZADO (Proteção do Servidor)
            self._schedule_sse_check()
        except Exception as e:
            logger.error(f"Erro ao processar evento SSE do Plex: {describe(e)}", exc_info=True)

    # --- MÉTODOS PÚBLICOS ---

    def block_user_sessions(self, plex_user_id, reason):
        if not self.conn.plex:
            return
            
        try:
            for session in self.conn.plex.sessions():
                session_user_id = self._get_session_user_id(session)
                if session_user_id and str(session_user_id) == str(plex_user_id):
                    self._terminate_session(session, reason)
        except NETWORK_ERRORS as e:
            logger.warning(f"Não foi possível bloquear as sessões do utilizador ID {plex_user_id}: {describe(e)}")
        except Exception as e:
            logger.error(f"Erro ao bloquear as sessões do utilizador ID {plex_user_id}: {describe(e)}", exc_info=True)

    def check_and_enforce_streams(self, from_event=False):
        # Reinicia o listener SSE se ele morreu OU se ficou agarrado a uma ligação
        # Plex obsoleta (ver _is_listener_healthy).
        if HAS_WEBSOCKET and not self._is_listener_healthy():
            try:
                app = current_app._get_current_object()
                self.start_listener(app)
            except RuntimeError:
                pass 

        config = load_or_create_config()

        if not self.conn.plex:
            success, _ = self.conn.reload(from_job=True)
            if not success:
                return
        
        try:
            sessions = self.conn.plex.sessions()
            network_reporter.recovered('streams', "Verificação de streams: o Plex voltou a responder.")
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
                    # Lógica Limpa de Contagem Unificada para Chromecast 
                    unique_sessions = self._filter_duplicate_cast_sessions(user_session_list)
                    self._enforce_screen_limits(user_id, username, unique_sessions, profile, config)

        except NETWORK_ERRORS as e:
            # Servidor Plex offline, sobrecarregado (503) ou inacessível: é uma
            # condição de ambiente, não um defeito. Uma linha resumida basta.
            network_reporter.failure('streams', e, prefix="Verificação de streams adiada, o Plex não respondeu")
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar e impor streams: {describe(e)}", exc_info=True)

    # --- EXTRAÇÃO DE DADOS EM TEMPO REAL ("REPRODUZINDO AGORA") ---

    def get_now_playing(self):
        """Retorna as sessões ativas com Tratamento Visual Perfeito para o Frontend."""
        if not self.conn.plex:
            return {"success": False, "stream_count": 0, "sessions": []}
            
        try:
            sessions = self.conn.plex.sessions()
            network_reporter.recovered('now_playing', "'Reproduzindo Agora': o Plex voltou a responder.")

            # Limpa sessões fantasma visualmente para não aparecerem duplicadas na Dashboard
            clean_sessions_list = []
            user_session_groups = self._group_sessions_by_user(sessions)
            for uid, s_list in user_session_groups.items():
                clean_sessions_list.extend(self._filter_duplicate_cast_sessions(s_list))

            now_playing_sessions = []
            all_users = self.user_manager.get_all_plex_users() or []
            id_to_username_map = {u['id']: u['username'] for u in all_users}
            user_thumb_map = {u['id']: u['thumb'] for u in all_users}
            
            if self.conn.account:
                admin_id = getattr(self.conn.account, 'id', None)
                if admin_id and admin_id not in id_to_username_map:
                    id_to_username_map[admin_id] = getattr(self.conn.account, 'username', 'Admin')
                    user_thumb_map[admin_id] = getattr(self.conn.account, 'thumb', None)

            for session in clean_sessions_list:
                view_offset = getattr(session, "viewOffset", 0)
                duration = getattr(session, "duration", 0)
                
                progress = 0.0
                if duration and view_offset:
                    progress = max(0.0, min(100.0, (view_offset / duration) * 100))

                media_type = getattr(session, "type", "unknown").lower()
                
                raw_state = getattr(session, "state", "stopped")
                players = getattr(session, "players", [])
                
                if players and hasattr(players[0], "state"):
                    raw_state = players[0].state
                elif hasattr(session, "player") and session.player and hasattr(session.player, "state"):
                    raw_state = session.player.state
                elif hasattr(session, "session") and session.session and hasattr(session.session, "state"):
                    raw_state = session.session.state

                safe_state = str(raw_state).lower()
                
                if 'pause' in safe_state:
                    state = 'paused'
                elif 'play' in safe_state:
                    state = 'playing'
                elif 'buffer' in safe_state:
                    state = 'buffering'
                else:
                    state = 'stopped'

                user_id = self._get_session_user_id(session)
                username = id_to_username_map.get(user_id, getattr(session.user, 'title', 'Desconhecido') if hasattr(session, 'user') else 'Desconhecido')
                
                raw_user_thumb = user_thumb_map.get(user_id)
                user_thumb = None
                if raw_user_thumb:
                    try:
                        if '/image/' not in raw_user_thumb:
                            parsed_thumb = urlparse(raw_user_thumb)
                            clean_query = urlencode([(k, v) for k, v in parse_qsl(parsed_thumb.query) if k.lower() != 'x-plex-token'])
                            clean_url = parsed_thumb._replace(query=clean_query).geturl()
                            
                            if 'plex.tv' in parsed_thumb.netloc or not parsed_thumb.netloc:
                                payload_str = f"plex_account:{clean_url}"
                            else:
                                payload_str = f"url:{clean_url}"
                                
                            b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                            try:
                                user_thumb = url_for('image.proxy_image', source=b64_payload)
                            except RuntimeError:
                                user_thumb = f"/image/?source={b64_payload}"
                        else:
                            user_thumb = raw_user_thumb
                    except Exception:
                        user_thumb = raw_user_thumb

                client_name = getattr(players[0], "product", "") if players else (getattr(session.player, 'product', '') if hasattr(session, 'player') else '')
                device_name = getattr(players[0], "title", "") if players else (getattr(session.player, 'title', '') if hasattr(session, 'player') else '')
                
                platform_css_class = self._get_platform_info(session) 
                player_string = f"{client_name} - {device_name}" if client_name and device_name else client_name or device_name or "Desconhecido"

                thumb_key = None
                images_attr = getattr(session, "image", None)
                if images_attr:
                    images_list = images_attr if isinstance(images_attr, (list, tuple, set)) else [images_attr]
                    for img in images_list:
                        if getattr(img, "type", None) == "coverPoster":
                            thumb_key = getattr(img, "key", None) or getattr(img, "thumb", None)
                            if thumb_key: break
                            
                if not thumb_key:
                    for attr in ("grandparentThumb", "parentThumb", "thumb", "thumbUrl", "art"):
                        val = getattr(session, attr, None)
                        if val:
                            thumb_key = val
                            break

                safe_thumb_url = None
                if thumb_key:
                    if str(thumb_key).startswith('http'):
                        parsed_thumb = urlparse(thumb_key)
                        clean_query = urlencode([(k, v) for k, v in parse_qsl(parsed_thumb.query) if k.lower() != 'x-plex-token'])
                        clean_url = parsed_thumb._replace(query=clean_query).geturl()
                        payload_str = f"url:{clean_url}"
                    else:
                        payload_str = f"plex:{thumb_key}"
                        
                    b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                    try:
                        safe_thumb_url = url_for('image.proxy_image', source=b64_payload)
                    except RuntimeError:
                        safe_thumb_url = f"/image/?source={b64_payload}"

                is_transcoding = False
                transcode_speed = None
                video_decision = "Direct Play"
                audio_decision = "Direct Play"

                transcode_session = getattr(session, "transcodeSession", None)
                transcode_sessions = getattr(session, "transcodeSessions", [])
                active_ts = transcode_session if transcode_session else (transcode_sessions[0] if transcode_sessions else None)

                media_list = getattr(session, "media", [])
                video_codec = audio_codec = container = video_resolution = "N/A"
                
                if media_list:
                    media_obj = media_list[0]
                    video_codec = str(getattr(media_obj, "videoCodec", "N/A")).upper()
                    audio_codec = str(getattr(media_obj, "audioCodec", "N/A")).upper()
                    container = str(getattr(media_obj, "container", "N/A")).upper()
                    v_res = getattr(media_obj, "videoResolution", "N/A")
                    video_resolution = f"{v_res}p" if str(v_res).isdigit() else str(v_res).upper()

                if active_ts:
                    v_dec = getattr(active_ts, "videoDecision", None)
                    a_dec = getattr(active_ts, "audioDecision", None)
                    if v_dec == "transcode" or v_dec == "copy":
                        is_transcoding = True
                        video_decision = v_dec.capitalize()
                    if a_dec == "transcode" or a_dec == "copy":
                        is_transcoding = True
                        audio_decision = a_dec.capitalize()
                    
                    if is_transcoding:
                        transcode_speed = getattr(active_ts, "speed", None)

                title = getattr(session, 'grandparentTitle', self._get_media_title(session)) if media_type == 'episode' else self._get_media_title(session)
                subtitle = getattr(session, 'title', '') if media_type == 'episode' else str(getattr(session, 'year', ''))

                if media_type == 'episode':
                     season_num = getattr(session, 'parentIndex', None)
                     episode_num = getattr(session, 'index', None)
                     if season_num is not None and episode_num is not None:
                         subtitle = f"S{int(season_num):02d} · E{int(episode_num):02d} - {getattr(session, 'title', '')}"

                session_info = {
                    "session_key": str(getattr(session, "sessionKey", "")),
                    "user": username,
                    "user_thumb": user_thumb,
                    "title": title,
                    "subtitle": subtitle,
                    "type": media_type,
                    "progress": round(progress, 2),
                    "state": state,
                    "platform": platform_css_class, 
                    "player": player_string, 
                    "view_offset": view_offset,
                    "duration": duration,
                    "thumb_url": safe_thumb_url,
                    "stream_details": {
                        "is_transcoding": is_transcoding,
                        "stream": "Transcode" if is_transcoding else "Direct Play",
                        "video_decision": video_decision,
                        "audio_decision": audio_decision,
                        "video_codec": video_codec,
                        "audio_codec": audio_codec,
                        "container": container,
                        "video_resolution": video_resolution,
                        "transcode_speed": transcode_speed,
                        "transcode_progress": int(getattr(active_ts, "progress", 0)) if active_ts else None
                    }
                }

                now_playing_sessions.append(session_info)

            return {
                "success": True,
                "stream_count": len(now_playing_sessions),
                "sessions": now_playing_sessions
            }

        except NETWORK_ERRORS as e:
            network_reporter.failure('now_playing', e, prefix="'Reproduzindo Agora' indisponível, o Plex não respondeu")
            return {"success": False, "stream_count": 0, "sessions": []}
        except Exception as e:
            logger.error(f"Falha ao obter estado 'Reproduzindo Agora': {describe(e)}", exc_info=True)
            return {"success": False, "stream_count": 0, "sessions": []}

    # --- MÉTODOS AUXILIARES E DE LÓGICA DE NEGÓCIO ---

    def _schedule_delayed_check(self):
        with self._delayed_check_lock:
            if self._delayed_check_pending: return
            self._delayed_check_pending = True

        def delayed_run():
            time.sleep(3.0)
            with self._delayed_check_lock:
                self._delayed_check_pending = False
            if self._app:
                try:
                    with self._app.app_context():
                        self.check_and_enforce_streams(from_event=True)
                except Exception as e:
                    logger.debug(f"Falha silenciosa na verificação atrasada: {e}")

        threading.Thread(target=delayed_run, daemon=True).start()

    def _enforce_block_rules(self, user_id, username, sessions, profile, block_info, config):
        from app.extensions import cache
        block_reason = block_info.get('block_reason', 'manual')
        spam_timeout = max(config.get("STREAM_CHECK_INTERVAL_SECONDS", 15), 60) 
        
        valid_sessions = []
        for s in sessions:
            session_key = getattr(s, 'sessionKey', None)
            if session_key and cache.get(f"kill_spam_{session_key}"): continue
            valid_sessions.append(s)

        if not valid_sessions: return

        log_cache_key = f"log_block_{username}_{block_reason}"
        if not cache.get(log_cache_key):
            logger.info(f"🚫 A terminar {len(valid_sessions)} stream(s) para o utilizador bloqueado: '{username}' (Motivo: {block_reason}).")
            cache.set(log_cache_key, True, timeout=300)

        msg_template_key = {
            'expired': 'TERMINATION_MSG_BLOCKED_EXPIRED',
            'trial_expired': 'TERMINATION_MSG_BLOCKED_TRIAL_EXPIRED'
        }.get(block_reason, 'TERMINATION_MSG_BLOCKED_MANUAL')

        default_msg = {
            'expired': "A sua subscrição expirou. Por favor, renove para continuar.",
            'trial_expired': "O seu período de teste terminou. Renove para continuar."
        }.get(block_reason, "O seu acesso ao servidor foi bloqueado pelo administrador.")

        msg_template = config.get(msg_template_key) or default_msg
        placeholders = self._build_placeholders(user_id, username, profile, valid_sessions[0])
        reason_text = msg_template.format(**placeholders)

        for session in valid_sessions:
            session_key = getattr(session, 'sessionKey', None)
            media_title = self._get_media_title(session)
            
            if session_key:
                cache.set(f"kill_spam_{session_key}", True, timeout=spam_timeout)
            else:
                buffer_lock_key = f"buffer_spam_{username}_{media_title}"
                cache.set(buffer_lock_key, True, timeout=15)

            db_log_key = f"db_log_block_{user_id}_{media_title}"
            if not cache.get(db_log_key):
                self.data_manager.log_stream_termination(
                    plex_user_id=user_id, username=username,
                    media_title=media_title,
                    platform=self._get_platform_info(session), 
                    reason=f'blocked_{block_reason}'
                )
                cache.set(db_log_key, True, timeout=120)

            self._terminate_session(session, reason_text)

    def _enforce_screen_limits(self, user_id, username, sessions, profile, config):
        from app.extensions import cache
        screen_limit = profile.get('screen_limit', 0)
        spam_timeout = max(config.get("STREAM_CHECK_INTERVAL_SECONDS", 15), 60)
        
        active_sessions = []
        for s in sessions:
            session_key = getattr(s, 'sessionKey', None)
            if session_key and cache.get(f"kill_spam_{session_key}"): continue
            active_sessions.append(s)
        
        if screen_limit > 0 and len(active_sessions) > screen_limit:
            excess_count = len(active_sessions) - screen_limit
            
            log_cache_key = f"log_limit_{username}"
            if not cache.get(log_cache_key):
                logger.info(f"⚠️ O utilizador '{username}' excedeu o limite de {screen_limit} tela(s). A terminar {excess_count} sessão(ões).")
                cache.set(log_cache_key, True, timeout=300) 
            
            sort_reverse = config.get("SCREEN_LIMIT_TERMINATION_STRATEGY", "oldest") != "newest"
            # 🎛️ ESTRATÉGIA CONFIGURÁVEL: por padrão ("oldest"), ordenamos por viewOffset
            # decrescente — a sessão com o maior progresso de reprodução tende a ser a que
            # está a correr há mais tempo, e é ela que é encerrada primeiro (comportamento
            # original do sistema). Se o admin escolher "newest", invertemos a ordenação para
            # encerrar primeiro a(s) sessão(ões) mais recente(s) (menor viewOffset), preservando
            # quem já estava a assistir há mais tempo.
            sorted_sessions = sorted(active_sessions, key=lambda s: getattr(s, 'viewOffset', 0) or 0, reverse=sort_reverse)
            
            msg_template = config.get('TERMINATION_MSG_SCREEN_LIMIT') or "Você excedeu o seu limite de {limit} telas simultâneas."
            placeholders = self._build_placeholders(user_id, username, profile, sorted_sessions[0], context={'limit': screen_limit})
            reason_text = msg_template.format(**placeholders)

            for i in range(excess_count):
                session_to_terminate = sorted_sessions[i]
                session_key = getattr(session_to_terminate, 'sessionKey', None)
                media_title = self._get_media_title(session_to_terminate)
                
                if session_key:
                    cache.set(f"kill_spam_{session_key}", True, timeout=spam_timeout)
                
                db_log_key = f"db_log_limit_{user_id}_{media_title}"
                if not cache.get(db_log_key):
                    self.data_manager.log_stream_termination(
                        plex_user_id=user_id, username=username,
                        media_title=media_title,
                        platform=self._get_platform_info(session_to_terminate),
                        reason='limit_exceeded'
                    )
                    cache.set(db_log_key, True, timeout=120)

                self._terminate_session(session_to_terminate, reason_text)

    def _terminate_session(self, session, reason):
        from app.extensions import cache
        try:
            session_key = getattr(session, 'sessionKey', None)
            plex_internal_session = getattr(session, 'session', None)
            internal_id = getattr(plex_internal_session, 'id', None) if plex_internal_session else None

            if session_key and internal_id:
                try:
                    session.stop(reason=str(reason))
                except AttributeError as e:
                    if "'NoneType' object has no attribute 'id'" not in str(e): raise
                
                try:
                    from app.extensions import socketio
                    socketio.emit('dashboard_update_streams', namespace='/dashboard')
                except Exception: pass
            else:
                user_title = getattr(session.user, 'title', 'Desconhecido') if hasattr(session, 'user') else 'Desconhecido'
                platform_info = self._get_platform_info(session)
                buffer_lock_key = f"buffer_wait_{user_title}_{self._get_media_title(session)}_{platform_info}"
                
                if not cache.get(buffer_lock_key):
                    cache.set(buffer_lock_key, True, timeout=10)
                    self._schedule_delayed_check()
        
        except NotFound: pass
        except Exception as e: pass

    # =========================================================================
    # NORMALIZAÇÕES E UTILITÁRIOS 
    # ==========================================
    
    def _filter_duplicate_cast_sessions(self, sessions):
        """
        Remove as sessões "fantasma" que ocorrem quando um cliente de telemóvel/browser
        está atuando como comando de um Chromecast a reproduzir o mesmo conteúdo.
        """
        unique_sessions = []
        active_casts_media = set()

        # Primeiro, identifica todas as sessões que SÃO os Chromecasts reais
        for s in sessions:
            platform_info = self._get_platform_info(s)
            if platform_info == 'chromecast':
                media_title = self._get_media_title(s)
                active_casts_media.add(media_title)
                unique_sessions.append(s)

        # Depois, adiciona as restantes sessões, a menos que sejam a origem do Cast
        for s in sessions:
            platform_info = self._get_platform_info(s)
            
            # Se já for um chromecast, pulamos porque já o adicionamos no loop acima
            if platform_info == 'chromecast':
                continue
                
            media_title = self._get_media_title(s)
            
            # Se o utilizador está reproduzindo o MESMO título num celular/browser 
            # E há um Chromecast tocando o mesmo título, assumimos que é uma "Sessão Remota" dupla e ignoramos.
            if media_title in active_casts_media:
                continue
                
            unique_sessions.append(s)
            
        return unique_sessions

    def _get_platform_info(self, session):
        # 1. Recupera as informações base (Platform e Product)
        platform = ""
        product = ""
        title = ""

        players = getattr(session, "players", [])
        if players:
            platform = getattr(players[0], "platform", "")
            product = getattr(players[0], "product", "")
            title = getattr(players[0], "title", "")
        elif hasattr(session, 'player') and session.player:
            platform = getattr(session.player, 'platform', "")
            product = getattr(session.player, 'product', "")
            title = getattr(session.player, 'title', "")

        # Junta todas as strings para procurar de forma mais abrangente
        full_string = f"{platform} {product} {title}".lower()

        if 'chrome' in full_string: return 'chrome'
        if 'safari' in full_string: return 'safari'
        if 'firefox' in full_string: return 'firefox'
        if 'edge' in full_string or 'microsoft edge' in full_string: return 'msedge'
        if 'opera' in full_string: return 'opera'
        if 'brave' in full_string: return 'chrome'
        
        if 'android' in full_string: return 'android'
        if 'roku' in full_string: return 'roku'
        if 'tvos' in full_string or 'apple tv' in full_string: return 'atv'
        if 'ios' in full_string or 'iphone' in full_string or 'ipad' in full_string or 'apple' in full_string: return 'ios'
        if 'playstation' in full_string or 'ps4' in full_string or 'ps5' in full_string: return 'playstation'
        if 'xbox' in full_string: return 'xbox'
        if 'samsung' in full_string or 'tizen' in full_string: return 'samsung'
        if 'lg' in full_string or 'webos' in full_string: return 'lg'
        if 'kodi' in full_string or 'xbmc' in full_string: return 'kodi'
        if 'plexamp' in full_string: return 'plexamp'
        if 'dlna' in full_string: return 'dlna'
        if 'chromecast' in full_string: return 'chromecast'
        if 'tivo' in full_string: return 'tivo'
        if 'alexa' in full_string: return 'alexa'
        
        if 'mac' in full_string: return 'macos'
        if 'windows' in full_string: return 'windows'
        if 'linux' in full_string: return 'linux'
        
        if 'plex' in full_string: return 'plex'
        
        return 'default'

    def _get_session_user_id(self, session):
        try:
            if hasattr(session, 'user') and session.user:
                return getattr(session.user, 'id', None)
            if hasattr(session, 'userID'):
                return session.userID
            users = getattr(session, 'users', [])
            if users and hasattr(users[0], 'id'):
                return users[0].id
        except Exception: pass
        return None

    def _get_media_title(self, session):
        media_title = getattr(session, 'title', 'Desconhecido')
        media_type = getattr(session, 'type', None)
        
        if media_type == 'episode':
            grandparent_title = getattr(session, 'grandparentTitle', '')
            season_num = getattr(session, 'parentIndex', None)
            episode_num = getattr(session, 'index', None)
            
            if grandparent_title:
                media_title = f"{grandparent_title}"

                if season_num is not None and episode_num is not None:
                    try: media_title += f" S{int(season_num):02d}E{int(episode_num):02d}"
                    except (ValueError, TypeError): pass
                
                episode_title = getattr(session, 'title', '')
                if episode_title: media_title += f" - {episode_title}"
                    
        return media_title

    def _group_sessions_by_user(self, sessions):
        user_sessions_by_id = defaultdict(list)
        for session in sessions:
            user_id = self._get_session_user_id(session)
            if user_id: user_sessions_by_id[user_id].append(session)
        return user_sessions_by_id

    def _build_user_maps(self):
        all_users = self.user_manager.get_all_plex_users() or []
        admin_account = self.conn.account
        admin_user_id = getattr(admin_account, 'id', None)

        id_to_username_map = {user['id']: user['username'] for user in all_users}
        if admin_user_id and admin_account.username:
            id_to_username_map[admin_user_id] = admin_account.username
            
        return id_to_username_map, admin_user_id

    def _build_placeholders(self, user_id, username, profile, session, context=None):
        placeholders = {
            'username': username,
            'name': profile.get('name') or username,
            'email': getattr(session.user, 'email', '') if hasattr(session, 'user') else '',
            'greeting': get_greeting(),
            'telegram_user': profile.get('telegram_user', ''),
            'discord_user_id': profile.get('discord_user_id', ''),
            'phone_number': profile.get('phone_number', '')
        }
        if context: placeholders.update(context)
        return placeholders