# app/services/stream_manager.py

import copy
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime
from tzlocal import get_localzone

from flask import current_app
from flask_babel import gettext as _

from ..config import load_or_create_config
from ..utils.image_proxy import proxied_image_url
from ..utils.log_formatting import NETWORK_ERRORS, ThrottledReporter, describe

logger = logging.getLogger(__name__)

# Estas rotinas correm em ciclo (a cada poucos segundos, e a cada evento do
# servidor). Quando o servidor fica indisponível, TODAS falham em cadeia: sem
# moderação, uma indisponibilidade de 2 minutos escrevia centenas de linhas
# idênticas no log. O reporter regista a primeira falha, resume as seguintes e
# assinala o retorno.
network_reporter = ThrottledReporter(logger, interval=300)


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
    Monitoriza e encerra reproduções: limites de telas, utilizadores bloqueados
    e o estado "Reproduzindo Agora" do painel.

    Não fala com nenhum servidor de média diretamente. Recebe um
    `SessionsProvider` (ver `app/services/media_server/base.py`) que lhe entrega
    `MediaSession` já traduzidas e sabe encerrá-las — aqui vive apenas a
    POLÍTICA: o que conta como uma tela, quem é cortado primeiro, com que
    atraso reagir a um evento e durante quanto tempo não repetir um corte.
    """

    # Janela de agregação dos eventos em tempo real: espera-se SSE_DEBOUNCE por
    # um evento seguinte, mas nunca mais do que SSE_MAX_DEBOUNCE desde o
    # primeiro evento pendente (evita que uma rajada contínua adie a
    # verificação indefinidamente).
    SSE_DEBOUNCE_SECONDS = 2.0
    SSE_MAX_DEBOUNCE_SECONDS = 6.0

    # Janela em que o resultado de 'get_now_playing' é reaproveitado. Vários
    # consumidores pedem o mesmo estado quase em simultâneo — a tarefa de
    # background dos sockets (de 5 em 5 segundos), o pedido HTTP de cada
    # separador aberto e as rajadas de eventos — e cada um deles fazia a sua
    # própria chamada de rede ao servidor para obter exatamente a mesma
    # resposta. A cache é invalidada assim que um evento assinala uma mudança
    # real de estado, por isso nunca atrasa um play/pausa que o utilizador
    # acabou de dar.
    NOW_PLAYING_CACHE_SECONDS = 2.0

    def __init__(self, sessions_provider, data_manager, user_manager):
        self.sessions = sessions_provider
        self.data_manager = data_manager
        self.user_manager = user_manager
        self._app = None

        # Controlo de Concorrência Otimizado
        self._delayed_check_lock = threading.Lock()
        self._delayed_check_pending = False
        self._sse_debounce_lock = threading.Lock()
        self._sse_debounce_timer = None
        # Instante-limite do debounce em curso (ver SSE_MAX_DEBOUNCE_SECONDS).
        self._sse_debounce_deadline = None
        # Cache curta do estado 'Reproduzindo Agora' (ver NOW_PLAYING_CACHE_SECONDS).
        self._now_playing_lock = threading.Lock()
        self._now_playing_cache = None
        self._now_playing_cached_at = 0.0

    # =========================================================================
    # TEMPO REAL
    # =========================================================================

    def start_listener(self, app):
        """Pede ao provider que vigie o servidor e nos avise das mudanças."""
        if not self.sessions:
            return
        # Pega a instância real da App para usar nas threads.
        self._app = app._get_current_object() if hasattr(app, '_get_current_object') else app
        self.sessions.start_listener(self._on_server_change)

    def stop_listener(self):
        if self.sessions:
            self.sessions.stop_listener()

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

    def _on_server_change(self):
        """O servidor avisou que alguma coisa mudou mesmo (o provider já filtrou
        os pings de progresso)."""
        if not self._app:
            return

        # 0. O estado mudou mesmo: a leitura guardada ficou obsoleta. Sem isto,
        # o pedido que o frontend faz logo a seguir ao sinal podia ser servido
        # pela cache e mostrar ainda o estado anterior (ver NOW_PLAYING_CACHE_SECONDS).
        self.invalidate_now_playing_cache()

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
                logger.error(f"Falha na verificação de streams por evento: {describe(e)}")

    # =========================================================================
    # MÉTODOS PÚBLICOS
    # =========================================================================

    def block_user_sessions(self, media_user_id, reason):
        if not self.sessions or not self.sessions.is_connected():
            return

        try:
            for session in self.sessions.list_sessions():
                if session.user_id and str(session.user_id) == str(media_user_id):
                    self._terminate_session(session, reason)
        except NETWORK_ERRORS as e:
            logger.warning(f"Não foi possível bloquear as sessões do utilizador ID {media_user_id}: {describe(e)}")
        except Exception as e:
            logger.error(f"Erro ao bloquear as sessões do utilizador ID {media_user_id}: {describe(e)}", exc_info=True)

    def check_and_enforce_streams(self, from_event=False):
        # Reinicia o listener se ele morreu OU se ficou agarrado a uma ligação
        # obsoleta (ver is_listener_healthy no provider).
        if self.sessions and self.sessions.supports_realtime() and not self.sessions.is_listener_healthy():
            try:
                app = current_app._get_current_object()
                self.start_listener(app)
            except RuntimeError:
                pass

        config = load_or_create_config()

        if not self.sessions:
            return

        if not self.sessions.is_connected():
            success, _mensagem = self.sessions.reconnect()
            if not success:
                return

        try:
            sessions = self.sessions.list_sessions()
            network_reporter.recovered('streams', "Verificação de streams: o servidor voltou a responder.")
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
            # Servidor offline, sobrecarregado (503) ou inacessível: é uma
            # condição de ambiente, não um defeito. Uma linha resumida basta.
            network_reporter.failure('streams', e, prefix="Verificação de streams adiada, o servidor não respondeu")
        except Exception as e:
            logger.error(f"Erro inesperado ao verificar e impor streams: {describe(e)}", exc_info=True)

    # =========================================================================
    # "REPRODUZINDO AGORA"
    # =========================================================================

    def invalidate_now_playing_cache(self):
        """Descarta o estado guardado (ver NOW_PLAYING_CACHE_SECONDS)."""
        with self._now_playing_lock:
            self._now_playing_cache = None
            self._now_playing_cached_at = 0.0

    def _get_cached_now_playing(self):
        """Devolve o último estado guardado, ou None se já expirou."""
        with self._now_playing_lock:
            if self._now_playing_cache is None:
                return None
            if (time.monotonic() - self._now_playing_cached_at) >= self.NOW_PLAYING_CACHE_SECONDS:
                return None
            return self._now_playing_cache

    def get_now_playing(self, use_cache=True):
        """
        Retorna as sessões ativas com Tratamento Visual Perfeito para o Frontend.

        Por omissão reaproveita um resultado com menos de NOW_PLAYING_CACHE_SECONDS,
        para que pedidos quase simultâneos (socket + separadores abertos + rajada
        de eventos) partilhem uma única chamada ao servidor. Passe use_cache=False
        para forçar uma leitura fresca.
        """
        if not self.sessions or not self.sessions.is_connected():
            return {"success": False, "stream_count": 0, "sessions": []}

        if use_cache:
            cached = self._get_cached_now_playing()
            if cached is not None:
                return copy.deepcopy(cached)

        payload = self._build_now_playing()

        # Só se guarda uma leitura bem-sucedida: uma falha de rede é transitória e
        # não deve ficar "colada" ao painel durante a janela da cache.
        if payload.get("success"):
            with self._now_playing_lock:
                self._now_playing_cache = payload
                self._now_playing_cached_at = time.monotonic()

        return copy.deepcopy(payload)

    def get_active_stream_count(self, use_cache=True):
        """
        Conta as sessões ativas SEM construir o payload visual completo.

        O resumo do dashboard só precisa deste número, mas chamava o
        'get_now_playing()' inteiro para o obter — o que arrastava consigo a lista
        de utilizadores do servidor, a limpeza dos avatares e a construção dos URLs
        de todas as capas, tudo para depois ser deitado fora. Aqui só se agrupa e se
        contam as sessões, com o mesmo critério do painel (sessões duplicadas de
        Chromecast contam uma vez só), para que o cartão e a lista nunca discordem.
        """
        if not self.sessions or not self.sessions.is_connected():
            return 0

        if use_cache:
            cached = self._get_cached_now_playing()
            if cached is not None:
                return cached.get("stream_count", 0)

        try:
            sessions = self.sessions.list_sessions()
            network_reporter.recovered('now_playing', "'Reproduzindo Agora': o servidor voltou a responder.")

            groups = self._group_sessions_by_user(sessions)
            return sum(len(self._filter_duplicate_cast_sessions(s_list)) for s_list in groups.values())
        except NETWORK_ERRORS as e:
            network_reporter.failure('now_playing', e, prefix="Contagem de streams indisponível, o servidor não respondeu")
            return 0
        except Exception as e:
            logger.error(f"Falha ao contar as sessões ativas: {describe(e)}", exc_info=True)
            return 0

    def _build_now_playing(self):
        """Monta o payload das sessões ativas a partir das sessões traduzidas."""
        try:
            sessions = self.sessions.list_sessions()
            network_reporter.recovered('now_playing', "'Reproduzindo Agora': o servidor voltou a responder.")

            # Limpa sessões fantasma visualmente para não aparecerem duplicadas na Dashboard
            clean_sessions_list = []
            for _uid, s_list in self._group_sessions_by_user(sessions).items():
                clean_sessions_list.extend(self._filter_duplicate_cast_sessions(s_list))

            username_map, thumb_map = self._build_display_maps()

            now_playing_sessions = []
            for session in clean_sessions_list:
                username = username_map.get(session.user_id, session.username_fallback)
                user_thumb = self._user_thumb_url(thumb_map.get(session.user_id))

                now_playing_sessions.append({
                    "session_key": session.session_key,
                    "user": username,
                    "user_thumb": user_thumb,
                    "title": session.title,
                    "subtitle": session.subtitle,
                    "type": session.media_type,
                    "progress": session.progress,
                    "state": session.state,
                    "platform": session.platform,
                    "player": session.player,
                    "view_offset": session.view_offset,
                    "duration": session.duration,
                    "thumb_url": proxied_image_url(session.artwork_source),
                    "stream_details": session.stream_details,
                })

            return {
                "success": True,
                "stream_count": len(now_playing_sessions),
                "sessions": now_playing_sessions
            }

        except NETWORK_ERRORS as e:
            network_reporter.failure('now_playing', e, prefix="'Reproduzindo Agora' indisponível, o servidor não respondeu")
            return {"success": False, "stream_count": 0, "sessions": []}
        except Exception as e:
            logger.error(f"Falha ao obter estado 'Reproduzindo Agora': {describe(e)}", exc_info=True)
            return {"success": False, "stream_count": 0, "sessions": []}

    def _user_thumb_url(self, raw_thumb):
        """O avatar do utilizador, já encaminhado pelo proxy de imagens."""
        if not raw_thumb:
            return None
        try:
            fonte = self.sessions.user_thumb_source(raw_thumb)
            # Sem fonte, o avatar já é um URL do proxy: usa-se tal como está.
            return proxied_image_url(fonte) if fonte else raw_thumb
        except Exception:
            return raw_thumb

    # =========================================================================
    # AUXILIARES E LÓGICA DE NEGÓCIO
    # =========================================================================

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
        spam_timeout = self._janela_anti_repeticao(config)

        valid_sessions = [s for s in sessions if not self._acabou_de_ser_cortada(s)]

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
            if session.session_key:
                self._marcar_como_cortada(session, spam_timeout)
            else:
                cache.set(f"buffer_spam_{username}_{session.media_title}", True, timeout=15)

            db_log_key = f"db_log_block_{user_id}_{session.media_title}"
            if not cache.get(db_log_key):
                self.data_manager.log_stream_termination(
                    media_user_id=user_id, username=username,
                    media_title=session.media_title,
                    platform=session.platform,
                    reason=f'blocked_{block_reason}'
                )
                cache.set(db_log_key, True, timeout=120)

            self._terminate_session(session, reason_text)

    def _enforce_screen_limits(self, user_id, username, sessions, profile, config):
        from app.extensions import cache
        screen_limit = profile.get('screen_limit', 0)
        spam_timeout = self._janela_anti_repeticao(config)

        active_sessions = [s for s in sessions if not self._acabou_de_ser_cortada(s)]

        if screen_limit > 0 and len(active_sessions) > screen_limit:
            excess_count = len(active_sessions) - screen_limit

            log_cache_key = f"log_limit_{username}"
            if not cache.get(log_cache_key):
                logger.info(f"⚠️ O utilizador '{username}' excedeu o limite de {screen_limit} tela(s). A terminar {excess_count} sessão(ões).")
                cache.set(log_cache_key, True, timeout=300)

            sort_reverse = config.get("SCREEN_LIMIT_TERMINATION_STRATEGY", "oldest") != "newest"
            # 🎛️ ESTRATÉGIA CONFIGURÁVEL: por padrão ("oldest"), ordenamos por view_offset
            # decrescente — a sessão com o maior progresso de reprodução tende a ser a que
            # está a correr há mais tempo, e é ela que é encerrada primeiro (comportamento
            # original do sistema). Se o admin escolher "newest", invertemos a ordenação para
            # encerrar primeiro a(s) sessão(ões) mais recente(s) (menor view_offset), preservando
            # quem já estava a assistir há mais tempo.
            sorted_sessions = sorted(active_sessions, key=lambda s: s.view_offset or 0, reverse=sort_reverse)

            msg_template = config.get('TERMINATION_MSG_SCREEN_LIMIT') or "Você excedeu o seu limite de {limit} telas simultâneas."
            placeholders = self._build_placeholders(user_id, username, profile, sorted_sessions[0], context={'limit': screen_limit})
            reason_text = msg_template.format(**placeholders)

            for i in range(excess_count):
                session_to_terminate = sorted_sessions[i]

                self._marcar_como_cortada(session_to_terminate, spam_timeout)

                db_log_key = f"db_log_limit_{user_id}_{session_to_terminate.media_title}"
                if not cache.get(db_log_key):
                    self.data_manager.log_stream_termination(
                        media_user_id=user_id, username=username,
                        media_title=session_to_terminate.media_title,
                        platform=session_to_terminate.platform,
                        reason='limit_exceeded'
                    )
                    cache.set(db_log_key, True, timeout=120)

                self._terminate_session(session_to_terminate, reason_text)

    # Quantas vezes se pede educadamente antes de assumir que o cliente não vai
    # obedecer. Com a janela anti-repetição a 30s, são cerca de dois minutos.
    TENTATIVAS_ANTES_DE_FORCAR = 3

    def _contar_tentativa(self, session):
        """Quantas vezes já mandámos parar ESTA reprodução."""
        from app.extensions import cache

        chave = f"stop_tentativas_{session.playback_key}"
        tentativas = (cache.get(chave) or 0) + 1
        # A contagem dura bem mais do que a janela anti-repetição: é isso que
        # permite distinguir "ainda a obedecer" de "está a ignorar".
        cache.set(chave, tentativas, timeout=600)
        return tentativas

    def _forcar_fim_da_reproducao(self, session, reason, tentativas):
        """O cliente ignorou a ordem vezes de mais. Há algo mais forte a fazer?"""
        from app.extensions import cache

        config = load_or_create_config()

        if not config.get('FORCE_STREAM_TERMINATION'):
            # Um aviso por reprodução, para não encher o log de repetições.
            chave_aviso = f"aviso_ignorou_{session.playback_key}"
            if not cache.get(chave_aviso):
                cache.set(chave_aviso, True, timeout=600)
                logger.warning(
                    "⚠️ O cliente '%s' ignorou %s ordens para parar '%s' e continua a "
                    "reproduzir. O limite de telas não está a ser cumprido neste aparelho. "
                    "Para o painel poder forçar o fim (revogando o acesso do aparelho, o que "
                    "obriga a nova autenticação), ative FORCE_STREAM_TERMINATION.",
                    session.player, tentativas, session.media_title,
                )
            return False

        return self.sessions.force_terminate(session, reason)

    def _terminate_session(self, session, reason):
        from app.extensions import cache

        # 🐛 Há clientes que recebem a ordem de parar e continuam a reproduzir
        # (o leitor integrado da aplicação Android do Jellyfin é um deles).
        # Sem esta contagem, o painel ficava a pedir para sempre — a cada volta
        # mandava parar, o servidor aceitava, e o stream seguia. Do lado de fora
        # parecia que o limite de telas simplesmente não funcionava.
        tentativas = self._contar_tentativa(session)
        if tentativas > self.TENTATIVAS_ANTES_DE_FORCAR:
            if self._forcar_fim_da_reproducao(session, reason, tentativas):
                return

        if self.sessions.terminate(session, reason):
            try:
                from app.extensions import socketio
                socketio.emit('dashboard_update_streams', namespace='/dashboard')
            except Exception:
                pass
            return

        # O servidor ainda não consegue encerrar esta sessão (tipicamente uma
        # reprodução a carregar, sem identificador interno). Volta-se a tentar
        # daqui a pouco, mas só uma vez por sessão: sem esta trava, cada ciclo
        # agendava uma nova verificação atrasada para a mesma sessão.
        buffer_lock_key = f"buffer_wait_{session.username_fallback}_{session.media_title}_{session.platform}"
        if not cache.get(buffer_lock_key):
            cache.set(buffer_lock_key, True, timeout=10)
            self._schedule_delayed_check()

    def _janela_anti_repeticao(self, config):
        """Durante quanto tempo uma reprodução já cortada é ignorada.

        Serve para não repetir o corte (e a mensagem, e o registo de auditoria)
        enquanto o cliente demora a obedecer — o servidor ainda a lista durante
        alguns segundos depois de receber a ordem.

        🐛 Era um minuto fixo. Como a guarda também tira a reprodução da
        CONTAGEM, isso dava a quem fosse cortado um minuto inteiro de stream
        livre: bastava recomeçar. Duas voltas da verificação periódica chegam
        para o cliente obedecer, e reduzem a folga para quem tenta contornar.
        """
        intervalo = config.get("STREAM_CHECK_INTERVAL_SECONDS", 15)
        return max(intervalo * 2, 30)

    def _acabou_de_ser_cortada(self, session):
        """Esta reprodução foi cortada há pouco e ainda está a desaparecer?"""
        from app.extensions import cache

        chave = session.playback_key
        if not chave or not cache.get(f"kill_spam_{chave}"):
            return False

        logger.debug(
            "Reprodução '%s' (%s) ignorada nesta volta: foi cortada há pouco e o "
            "servidor ainda a lista.", session.media_title, chave,
        )
        return True

    def _marcar_como_cortada(self, session, timeout):
        from app.extensions import cache

        if session.playback_key:
            cache.set(f"kill_spam_{session.playback_key}", True, timeout=timeout)

    def _filter_duplicate_cast_sessions(self, sessions):
        """Pergunta ao servidor quais destas sessões são a mesma reprodução.

        Só o provider sabe se o seu servidor duplica uma reprodução, e como
        reconhecer o par — ver `SessionsProvider.deduplicate_sessions`. O que
        vive aqui é a POLÍTICA: o que sobra desta fusão é o que conta para o
        limite de telas e é o que o painel mostra.
        """
        if not self.sessions:
            return list(sessions)
        return self.sessions.deduplicate_sessions(sessions)

    def _group_sessions_by_user(self, sessions):
        user_sessions_by_id = defaultdict(list)
        for session in sessions:
            if session.user_id:
                user_sessions_by_id[session.user_id].append(session)
        return user_sessions_by_id

    def _build_user_maps(self):
        """Nome de cada utilizador e a identidade do dono do servidor."""
        all_users = self.user_manager.list_users() or []
        from ..utils.identity import normalize_user_id

        id_to_username_map = {normalize_user_id(user['id']): user['username'] for user in all_users}
        return id_to_username_map, self.sessions.get_owner_id()

    def _build_display_maps(self):
        """Nome e avatar de cada utilizador, para o painel."""
        all_users = self.user_manager.list_users() or []
        from ..utils.identity import normalize_user_id

        username_map = {}
        thumb_map = {}
        for user in all_users:
            user_id = normalize_user_id(user.get('id'))
            username_map[user_id] = user.get('username')
            thumb_map[user_id] = user.get('thumb')
        return username_map, thumb_map

    def _build_placeholders(self, user_id, username, profile, session, context=None):
        placeholders = {
            'username': username,
            'name': profile.get('name') or username,
            'email': session.user_email,
            'greeting': get_greeting(),
            'telegram_user': profile.get('telegram_user', ''),
            'discord_user_id': profile.get('discord_user_id', ''),
            'phone_number': profile.get('phone_number', '')
        }
        if context: placeholders.update(context)
        return placeholders
