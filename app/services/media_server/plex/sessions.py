# app/services/media_server/plex/sessions.py

"""Leitura, encerramento e vigilância em tempo real das sessões do Plex.

Este módulo é a fronteira: daqui para fora o painel vê `MediaSession` e não
sabe que existe uma `plexapi`. Tudo o que é vocabulário do Plex — onde está o
estado do leitor, que campo tem a capa, como se identifica um Chromecast, o
formato das notificações do websocket — vive aqui dentro.
"""

import logging
import threading
import time
from collections import defaultdict
from typing import List, Optional, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode

from plexapi.exceptions import NotFound

from ....utils.identity import normalize_user_id
from ....utils.log_formatting import describe
from ....utils.url_safety import is_plex_tv_host
from ..base import MediaSession

logger = logging.getLogger(__name__)

# Silenciar o spam de INFO das bibliotecas do Plex e Websocket.
logging.getLogger('plexapi').setLevel(logging.WARNING)
logging.getLogger('websocket').setLevel(logging.WARNING)

try:
    import websocket  # noqa: F401
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False


def limpar_token(url: str) -> str:
    """Remove o X-Plex-Token de um URL antes de ele sair para o browser."""
    partes = urlparse(url)
    query = urlencode([(k, v) for k, v in parse_qsl(partes.query) if k.lower() != 'x-plex-token'])
    return partes._replace(query=query).geturl()


def thumb_source(raw_thumb: Optional[str]) -> Optional[str]:
    """Traduz um avatar cru do Plex num prefixo para o proxy de imagens.

    Um avatar pode vir como caminho relativo, como URL do plex.tv ou como URL
    de um terceiro (o Gravatar, por exemplo) — e cada caso precisa de um
    prefixo diferente para o proxy saber que credencial injetar. Está aqui, e
    não em quem consome, porque é vocabulário do Plex.
    """
    if not raw_thumb:
        return None

    # Já passou pelo proxy: nada a fazer.
    if '/image/' in raw_thumb:
        return None

    partes = urlparse(raw_thumb)
    url_limpo = limpar_token(raw_thumb)

    if is_plex_tv_host(partes.hostname) or not partes.netloc:
        return f"plex_account:{url_limpo}"
    return f"url:{url_limpo}"


class PlexSessionsProvider:
    """Cumpre o contrato `SessionsProvider` para o Plex."""

    # Estados que interessam ao controlo de streams.
    RELEVANT_STATES = ('playing', 'buffering', 'paused', 'stopped')
    # Uma sessão sem notificações há mais do que isto é dada como terminada e
    # esquecida (nem todos os clientes enviam 'stopped' ao desligar).
    SESSION_STATE_TTL_SECONDS = 600
    # Backoff entre tentativas de arranque do listener (Plex offline).
    LISTENER_RETRY_BASE_SECONDS = 15
    LISTENER_RETRY_MAX_SECONDS = 300

    def __init__(self, connection):
        self.conn = connection

        self._listener = None
        self._on_change = None
        # 🐛 Protege o arranque do listener: sem este lock, dois pedidos concorrentes
        # podiam passar ambos pela verificação "is_alive()" e criar DOIS listeners
        # SSE ligados ao mesmo servidor (o arranque faz I/O de rede, que é um ponto
        # de cedência com gevent), duplicando eventos e ligações websocket.
        self._listener_lock = threading.Lock()
        # Guarda a instância de PlexServer a que o listener atual está ligado, para
        # detetar quando a ligação foi recarregada e o listener ficou órfão.
        self._listener_plex_ref = None
        self._listener_retry_at = 0.0
        self._listener_failures = 0

        # Último estado conhecido de cada sessão. É isto que distingue uma
        # MUDANÇA real de um simples "ping" de progresso.
        self._session_states_lock = threading.Lock()
        self._last_session_states = {}

    # =========================================================================
    # LIGAÇÃO
    # =========================================================================

    def is_connected(self) -> bool:
        return bool(self.conn and self.conn.plex)

    def reconnect(self) -> Tuple[bool, str]:
        return self.conn.reload(from_job=True)

    def get_owner_id(self) -> Optional[str]:
        """A identidade do dono do servidor, que não está sujeito a limites."""
        conta = getattr(self.conn, 'account', None)
        return normalize_user_id(getattr(conta, 'id', None)) if conta else None

    def user_thumb_source(self, raw_thumb: Optional[str]) -> Optional[str]:
        return thumb_source(raw_thumb)

    # =========================================================================
    # LEITURA DAS SESSÕES
    # =========================================================================

    def list_sessions(self) -> List[MediaSession]:
        """As reproduções a decorrer, já traduzidas.

        As falhas de rede propagam-se de propósito: quem chama é que sabe
        distinguir "o servidor está offline" (condição de ambiente, uma linha
        de log resumida) de um defeito.
        """
        if not self.is_connected():
            return []
        return [self._traduzir(sessao) for sessao in self.conn.plex.sessions()]

    def _traduzir(self, sessao) -> MediaSession:
        leitores = getattr(sessao, "players", [])
        leitor = leitores[0] if leitores else getattr(sessao, 'player', None)

        tipo = str(getattr(sessao, "type", "unknown")).lower()
        view_offset = getattr(sessao, "viewOffset", 0)
        duracao = getattr(sessao, "duration", 0)

        progresso = 0.0
        if duracao and view_offset:
            progresso = max(0.0, min(100.0, (view_offset / duracao) * 100))

        produto = getattr(leitor, "product", "") if leitor else ""
        dispositivo = getattr(leitor, "title", "") if leitor else ""
        leitor_texto = f"{produto} - {dispositivo}" if produto and dispositivo else produto or dispositivo or "Desconhecido"

        utilizador = getattr(sessao, 'user', None)

        return MediaSession(
            user_id=self._id_do_utilizador(sessao),
            username_fallback=getattr(utilizador, 'title', 'Desconhecido') if utilizador else 'Desconhecido',
            user_email=getattr(utilizador, 'email', '') if utilizador else '',
            session_key=str(getattr(sessao, "sessionKey", "")),
            media_title=self._titulo_para_registo(sessao),
            title=self._titulo(sessao, tipo),
            subtitle=self._subtitulo(sessao, tipo),
            media_type=tipo,
            state=self._estado(sessao, leitores),
            platform=self._plataforma(sessao),
            player=leitor_texto,
            progress=round(progresso, 2),
            view_offset=view_offset,
            duration=duracao,
            artwork_source=self._capa(sessao),
            stream_details=self._detalhes_do_stream(sessao),
            raw=sessao,
        )

    def _estado(self, sessao, leitores) -> str:
        bruto = getattr(sessao, "state", "stopped")

        if leitores and hasattr(leitores[0], "state"):
            bruto = leitores[0].state
        elif hasattr(sessao, "player") and sessao.player and hasattr(sessao.player, "state"):
            bruto = sessao.player.state
        elif hasattr(sessao, "session") and sessao.session and hasattr(sessao.session, "state"):
            bruto = sessao.session.state

        texto = str(bruto).lower()
        if 'pause' in texto:
            return 'paused'
        if 'play' in texto:
            return 'playing'
        if 'buffer' in texto:
            return 'buffering'
        return 'stopped'

    def _titulo(self, sessao, tipo) -> str:
        if tipo == 'episode':
            return getattr(sessao, 'grandparentTitle', self._titulo_para_registo(sessao))
        return self._titulo_para_registo(sessao)

    def _subtitulo(self, sessao, tipo) -> str:
        if tipo != 'episode':
            return str(getattr(sessao, 'year', ''))

        temporada = getattr(sessao, 'parentIndex', None)
        episodio = getattr(sessao, 'index', None)
        if temporada is not None and episodio is not None:
            return f"S{int(temporada):02d} · E{int(episodio):02d} - {getattr(sessao, 'title', '')}"
        return getattr(sessao, 'title', '')

    def _capa(self, sessao) -> Optional[str]:
        chave = None
        imagens = getattr(sessao, "image", None)
        if imagens:
            lista = imagens if isinstance(imagens, (list, tuple, set)) else [imagens]
            for img in lista:
                if getattr(img, "type", None) == "coverPoster":
                    chave = getattr(img, "key", None) or getattr(img, "thumb", None)
                    if chave:
                        break

        if not chave:
            for atributo in ("grandparentThumb", "parentThumb", "thumb", "thumbUrl", "art"):
                valor = getattr(sessao, atributo, None)
                if valor:
                    chave = valor
                    break

        if not chave:
            return None

        if str(chave).startswith('http'):
            return f"url:{limpar_token(chave)}"
        return f"plex:{chave}"

    def _detalhes_do_stream(self, sessao) -> dict:
        a_transcodificar = False
        velocidade = None
        decisao_video = "Direct Play"
        decisao_audio = "Direct Play"

        transcode = getattr(sessao, "transcodeSession", None)
        transcodes = getattr(sessao, "transcodeSessions", [])
        ativo = transcode if transcode else (transcodes[0] if transcodes else None)

        medias = getattr(sessao, "media", [])
        codec_video = codec_audio = container = resolucao = "N/A"

        if medias:
            media = medias[0]
            codec_video = str(getattr(media, "videoCodec", "N/A")).upper()
            codec_audio = str(getattr(media, "audioCodec", "N/A")).upper()
            container = str(getattr(media, "container", "N/A")).upper()
            res = getattr(media, "videoResolution", "N/A")
            resolucao = f"{res}p" if str(res).isdigit() else str(res).upper()

        if ativo:
            v_dec = getattr(ativo, "videoDecision", None)
            a_dec = getattr(ativo, "audioDecision", None)
            if v_dec in ("transcode", "copy"):
                a_transcodificar = True
                decisao_video = v_dec.capitalize()
            if a_dec in ("transcode", "copy"):
                a_transcodificar = True
                decisao_audio = a_dec.capitalize()
            if a_transcodificar:
                velocidade = getattr(ativo, "speed", None)

        return {
            "is_transcoding": a_transcodificar,
            "stream": "Transcode" if a_transcodificar else "Direct Play",
            "video_decision": decisao_video,
            "audio_decision": decisao_audio,
            "video_codec": codec_video,
            "audio_codec": codec_audio,
            "container": container,
            "video_resolution": resolucao,
            "transcode_speed": velocidade,
            "transcode_progress": int(getattr(ativo, "progress", 0)) if ativo else None,
        }

    def _id_do_utilizador(self, sessao) -> Optional[str]:
        """A identidade do dono da sessão, normalizada.

        🐛 O servidor devolve o ID no formato dele (o Plex, um inteiro) mas os
        perfis e a lista de bloqueados vêm da base de dados como texto. Sem
        normalizar aqui, `user_id in blocked_users_info` era SEMPRE falso e os
        utilizadores bloqueados deixavam de ser expulsos — em silêncio, porque
        um dicionário que não encontra a chave não dá erro nenhum.
        """
        try:
            if hasattr(sessao, 'user') and sessao.user:
                return normalize_user_id(getattr(sessao.user, 'id', None))
            if hasattr(sessao, 'userID'):
                return normalize_user_id(sessao.userID)
            utilizadores = getattr(sessao, 'users', [])
            if utilizadores and hasattr(utilizadores[0], 'id'):
                return normalize_user_id(utilizadores[0].id)
        except Exception:
            pass
        return None

    def _titulo_para_registo(self, sessao) -> str:
        titulo = getattr(sessao, 'title', 'Desconhecido')
        tipo = getattr(sessao, 'type', None)

        if tipo == 'episode':
            serie = getattr(sessao, 'grandparentTitle', '')
            temporada = getattr(sessao, 'parentIndex', None)
            episodio = getattr(sessao, 'index', None)

            if serie:
                titulo = f"{serie}"
                if temporada is not None and episodio is not None:
                    try:
                        titulo += f" S{int(temporada):02d}E{int(episodio):02d}"
                    except (ValueError, TypeError):
                        pass
                titulo_episodio = getattr(sessao, 'title', '')
                if titulo_episodio:
                    titulo += f" - {titulo_episodio}"

        return titulo

    def _plataforma(self, sessao) -> str:
        plataforma = produto = titulo = ""

        leitores = getattr(sessao, "players", [])
        if leitores:
            plataforma = getattr(leitores[0], "platform", "")
            produto = getattr(leitores[0], "product", "")
            titulo = getattr(leitores[0], "title", "")
        elif hasattr(sessao, 'player') and sessao.player:
            plataforma = getattr(sessao.player, 'platform', "")
            produto = getattr(sessao.player, 'product', "")
            titulo = getattr(sessao.player, 'title', "")

        texto = f"{plataforma} {produto} {titulo}".lower()

        # ⚠️ A ORDEM IMPORTA: as verificações são por SUBSTRING, por isso um termo
        # que esteja contido noutro tem de ser testado primeiro.
        #
        # 🐛 CORREÇÃO: 'chromecast' contém 'chrome', e a verificação do Chrome vinha
        # antes — um Chromecast era SEMPRE identificado como browser Chrome e o
        # ramo do 'chromecast' nunca era alcançado. Além do ícone errado, isso
        # desativava na prática o filtro de sessões duplicadas de Cast, que depende
        # deste valor: o telemóvel que apenas comanda o Chromecast contava como uma
        # segunda tela e podia fazer o utilizador ser cortado por um limite que não
        # estava a exceder. Pela mesma razão vem também antes do 'android' (o
        # Chromecast com Google TV identifica-se como Android).
        if 'chromecast' in texto: return 'chromecast'

        if 'chrome' in texto: return 'chrome'
        if 'safari' in texto: return 'safari'
        if 'firefox' in texto: return 'firefox'
        if 'edge' in texto or 'microsoft edge' in texto: return 'msedge'
        if 'opera' in texto: return 'opera'
        if 'brave' in texto: return 'chrome'

        if 'android' in texto: return 'android'
        if 'roku' in texto: return 'roku'
        if 'tvos' in texto or 'apple tv' in texto: return 'atv'
        if 'ios' in texto or 'iphone' in texto or 'ipad' in texto or 'apple' in texto: return 'ios'
        if 'playstation' in texto or 'ps4' in texto or 'ps5' in texto: return 'playstation'
        if 'xbox' in texto: return 'xbox'
        if 'samsung' in texto or 'tizen' in texto: return 'samsung'
        if 'lg' in texto or 'webos' in texto: return 'lg'
        if 'kodi' in texto or 'xbmc' in texto: return 'kodi'
        if 'plexamp' in texto: return 'plexamp'
        if 'dlna' in texto: return 'dlna'
        if 'tivo' in texto: return 'tivo'
        if 'alexa' in texto: return 'alexa'

        if 'mac' in texto: return 'macos'
        if 'windows' in texto: return 'windows'
        if 'linux' in texto: return 'linux'

        if 'plex' in texto: return 'plex'

        return 'default'

    # =========================================================================
    # ENCERRAMENTO
    # =========================================================================

    def terminate(self, session: MediaSession, reason: str) -> bool:
        """Encerra a reprodução. False quando o Plex ainda não a pode encerrar."""
        bruta = session.raw
        try:
            sessao_interna = getattr(bruta, 'session', None)
            id_interno = getattr(sessao_interna, 'id', None) if sessao_interna else None

            # Sem identificador interno (sessão ainda a carregar), o Plex não
            # aceita o comando de paragem: dizemos que não foi encerrada para
            # que quem chama volte a tentar.
            if not (session.session_key and id_interno):
                return False

            try:
                bruta.stop(reason=str(reason))
            except AttributeError as e:
                if "'NoneType' object has no attribute 'id'" not in str(e):
                    raise
            return True
        except NotFound:
            # A sessão já não existe: para efeitos práticos está encerrada.
            return True
        except Exception as e:
            logger.debug(f"Não foi possível encerrar a sessão {session.session_key}: {describe(e)}")
            return True

    # =========================================================================
    # TEMPO REAL
    # =========================================================================

    def supports_realtime(self) -> bool:
        return HAS_WEBSOCKET

    def is_listener_healthy(self) -> bool:
        """
        Um listener só é considerado saudável se estiver vivo E ligado à instância
        ATUAL do PlexServer. Depois de um 'reload_connections()' (troca de token,
        de URL, ou reconexão automática), o objeto PlexServer é substituído — o
        listener antigo continua vivo mas a falar com uma ligação obsoleta,
        deixando de entregar eventos sem qualquer erro visível.
        """
        listener = self._listener
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

    def start_listener(self, on_change) -> None:
        if not self.is_connected():
            return

        if not HAS_WEBSOCKET:
            logger.error("🚨 PACOTE EM FALTA: O modo de Tempo Real (Plex SSE) não pode iniciar. Execute no terminal: pip install websocket-client")
            return

        with self._listener_lock:
            if self.is_listener_healthy():
                return

            # Enquanto o Plex não estiver contactável, espaça-se as tentativas
            # em vez de tentar (e falhar) a cada verificação periódica.
            if time.monotonic() < self._listener_retry_at:
                return

            # Se existe um listener antigo (morto ou agarrado a uma ligação obsoleta),
            # é preciso pará-lo explicitamente para não deixar threads e sockets órfãos.
            if self._listener:
                try:
                    self._listener.stop()
                except Exception as e:
                    logger.debug(f"Aviso ao parar o listener SSE antigo: {e}")
                finally:
                    self._listener = None
                    self._listener_plex_ref = None

            self._on_change = on_change

            try:
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
                atraso = min(
                    self.LISTENER_RETRY_MAX_SECONDS,
                    self.LISTENER_RETRY_BASE_SECONDS * (2 ** (self._listener_failures - 1))
                )
                self._listener_retry_at = time.monotonic() + atraso
                # Só a primeira falha é ERROR: as seguintes, enquanto o Plex não
                # volta, ficam em WARNING para não dominarem o log.
                nivel = logger.error if self._listener_failures == 1 else logger.warning
                nivel(f"Falha ao iniciar o Plex Listener SSE: {describe(e)}. Nova tentativa em {atraso}s.")

    def stop_listener(self) -> None:
        with self._listener_lock:
            if self._listener:
                try:
                    self._listener.stop()
                except Exception as e:
                    logger.debug(f"Aviso silencioso ao parar SSE: {e}")
                finally:
                    self._listener = None
                    self._listener_plex_ref = None
                    logger.debug("📡 Plex Real-Time Listener (SSE) desligado.")

        # Sem listener, os estados memorizados ficam obsoletos: ao reconectar, o
        # primeiro evento de cada sessão tem de valer como mudança.
        with self._session_states_lock:
            self._last_session_states.clear()

    def _on_plex_event(self, data):
        # 🛡️ Este callback corre dentro da thread do websocket do plexapi. O plexapi
        # já apanha exceções aqui, mas regista-as no logger DELE ('plexapi'), o que
        # as tornava praticamente invisíveis nos nossos logs. Tratamos tudo aqui para
        # que qualquer falha apareça com o contexto certo — e nunca comprometa a
        # ligação em tempo real.
        try:
            if not isinstance(data, dict) or data.get('type') != 'playing':
                return

            notificacoes = data.get('PlaySessionStateNotification') or []
            if not isinstance(notificacoes, list):
                return

            if not self.has_state_changed(notificacoes):
                return

            if self._on_change:
                self._on_change()
        except Exception as e:
            logger.error(f"Erro ao processar evento SSE do Plex: {describe(e)}", exc_info=True)

    def has_state_changed(self, notifications) -> bool:
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
        mudou = False
        agora = time.monotonic()

        with self._session_states_lock:
            # Esquece sessões que já não dão sinal de vida. Sem isto, um cliente
            # que se desliga sem enviar 'stopped' ficaria memorizado para sempre.
            for chave in [k for k, v in self._last_session_states.items()
                          if agora - v[1] > self.SESSION_STATE_TTL_SECONDS]:
                del self._last_session_states[chave]

            for notificacao in notifications:
                if not isinstance(notificacao, dict):
                    continue

                estado = notificacao.get('state')
                if estado not in self.RELEVANT_STATES:
                    continue

                chave = notificacao.get('sessionKey')
                if chave in (None, ''):
                    # Sem identificador não há como comparar: trata-se como
                    # mudança, para nunca perder um evento relevante.
                    mudou = True
                    continue

                chave = str(chave)
                conhecido = self._last_session_states.get(chave)

                if conhecido and conhecido[0] == estado:
                    # Ping de progresso: mesmo estado da última vez. Só se
                    # renova a marca temporal, para a sessão não expirar.
                    self._last_session_states[chave] = (estado, agora)
                    continue

                if estado == 'stopped':
                    self._last_session_states.pop(chave, None)
                else:
                    self._last_session_states[chave] = (estado, agora)
                mudou = True

        return mudou
