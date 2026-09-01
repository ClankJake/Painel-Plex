# app/utils/log_formatting.py
"""
Ferramentas para manter os registos (logs) legíveis.

O problema que isto resolve: uma única falha de rede com o Plex produzia ~40
linhas de log. A biblioteca `requests` encadeia três exceções (urllib3 ->
requests -> plexapi) e cada uma trazia o traceback completo, quase todo dentro
de `site-packages` — código que o administrador do painel nunca vai depurar.
Como estas verificações correm de poucos em poucos segundos, um servidor Plex
temporariamente indisponível enchia o ficheiro de log (e o visualizador de logs
do painel) com centenas de linhas repetidas, escondendo tudo o resto.

A abordagem aqui tem três partes independentes:

1. `summarize_exception` — transforma a cascata de exceções numa frase curta
   ("o servidor respondeu 503 ... após esgotar as retentativas"), preservando o
   que é preciso para diagnosticar: o que falhou, onde e porquê.
2. `CompactFormatter` — quando um traceback é mesmo necessário (erro
   inesperado), mostra apenas as linhas do código da aplicação, uma por linha,
   em vez de dezenas de frames de bibliotecas.
3. `RepeatSuppressFilter` — colapsa mensagens idênticas repetidas dentro de uma
   janela de tempo, registando no fim quantas vezes se repetiram.
"""

import logging
import os
import re
import socket
import threading
import time

# --- Exceções consideradas "falhas de rede" -------------------------------
# São falhas de ambiente (servidor offline, DNS, firewall, sobrecarga), não
# defeitos do código: não precisam de traceback, precisam de uma linha clara.
try:
    import requests
    _REQUESTS_ERRORS = (requests.exceptions.RequestException,)
except Exception:  # pragma: no cover - requests é dependência obrigatória
    _REQUESTS_ERRORS = ()

try:
    import urllib3
    _URLLIB3_ERRORS = (urllib3.exceptions.HTTPError,)
except Exception:  # pragma: no cover
    _URLLIB3_ERRORS = ()

# Nota: mantém-se propositadamente restrito. Usar `OSError` apanharia também
# erros de ficheiros (FileNotFoundError, PermissionError...), que são defeitos
# reais e devem continuar a mostrar o traceback completo.
NETWORK_ERRORS = _REQUESTS_ERRORS + _URLLIB3_ERRORS + (
    ConnectionError, TimeoutError, socket.gaierror,
)

# Padrões usados para extrair o essencial das mensagens verbosas da urllib3.
_HOST_PORT_RE = re.compile(r"host='([^']+)',\s*port=(\d+)")
_URL_RE = re.compile(r"url:\s*(\S+)")
_CAUSED_BY_RE = re.compile(r"Caused by\s+(\w+)\(([^)]*)\)")
_STATUS_RE = re.compile(r"too many (\d{3}) error responses")
_TOKEN_RE = re.compile(r"([?&](?:X-Plex-Token|token|api_key|apikey)=)[^&\s\"']+", re.IGNORECASE)

# Tradução dos nomes técnicos das exceções para linguagem de diagnóstico.
_REASONS = {
    'ConnectTimeout': "tempo esgotado ao estabelecer a ligação",
    'ConnectTimeoutError': "tempo esgotado ao estabelecer a ligação",
    'ReadTimeout': "tempo esgotado à espera da resposta",
    'ReadTimeoutError': "tempo esgotado à espera da resposta",
    'Timeout': "tempo esgotado",
    'ConnectionRefusedError': "ligação recusada pelo servidor",
    'NewConnectionError': "não foi possível abrir a ligação",
    'NameResolutionError': "não foi possível resolver o endereço (DNS)",
    'ProtocolError': "a ligação foi interrompida a meio",
    'RemoteDisconnected': "o servidor fechou a ligação sem responder",
    'SSLError': "falha na negociação TLS/SSL",
    'ProxyError': "falha ao contactar através do proxy",
    'ChunkedEncodingError': "resposta incompleta do servidor",
    'ContentDecodingError': "não foi possível descodificar a resposta",
}


def is_network_error(exc):
    """Indica se a exceção é uma falha de rede/indisponibilidade externa."""
    return isinstance(exc, NETWORK_ERRORS)


def redact_tokens(text):
    """Remove tokens de query strings que apareçam em mensagens de erro."""
    if not text:
        return text
    return _TOKEN_RE.sub(r"\1***", str(text))


def shorten_host(host):
    """
    Encurta hostnames longos do Plex, que incluem o identificador da máquina.

        72-21-17-85.dcabdee...24a0.plex.direct  ->  72-21-17-85.***.plex.direct

    O identificador não ajuda no diagnóstico (é sempre o mesmo servidor) e é
    efetivamente um segredo partilhável, por isso não vai para o log.
    """
    if not host:
        return host
    parts = str(host).split('.')
    if len(parts) >= 3 and any(len(p) >= 24 for p in parts):
        return '.'.join([parts[0], '***'] + parts[-2:])
    return host


def _exception_chain(exc):
    """Percorre a cadeia de exceções (`raise ... from ...`) sem entrar em ciclos."""
    chain, seen, current = [], set(), exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def summarize_exception(exc):
    """
    Resume uma exceção — incluindo toda a sua cadeia de causas — numa frase.

    Exemplo do que substitui (40 linhas de traceback urllib3/requests/plexapi):

        o servidor respondeu 503 (indisponível) e esgotou as retentativas
        [72-21-17-85.***.plex.direct:14868/status/sessions]
    """
    if exc is None:
        return ""

    chain = _exception_chain(exc)
    text = " | ".join(str(e) for e in chain if str(e))

    host_port = ""
    match = _HOST_PORT_RE.search(text)
    if match:
        host_port = f"{shorten_host(match.group(1))}:{match.group(2)}"

    path = ""
    match = _URL_RE.search(text)
    if match:
        path = redact_tokens(match.group(1))

    location = f"{host_port}{path}" if (host_port or path) else ""

    # 1) Resposta HTTP de erro repetida (o caso mais comum com o Plex).
    match = _STATUS_RE.search(text)
    if match:
        status = match.group(1)
        extra = " (serviço indisponível/sobrecarregado)" if status == '503' else ""
        reason = f"o servidor respondeu {status}{extra} e esgotou as retentativas"
    else:
        # 2) Motivo declarado pela urllib3 em "Caused by XxxError(...)".
        reason = ""
        match = _CAUSED_BY_RE.search(text)
        if match:
            reason = _REASONS.get(match.group(1), "")
        # 3) Caso contrário, o tipo da exceção mais específica da cadeia.
        if not reason:
            for candidate in reversed(chain):
                reason = _REASONS.get(type(candidate).__name__, "")
                if reason:
                    break
        if not reason:
            root = chain[-1]
            detail = redact_tokens(str(root)).strip()
            # Mensagens da urllib3 já são longas de mais para caberem numa linha.
            if len(detail) > 160 or 'ConnectionPool' in detail:
                detail = ""
            reason = f"{type(root).__name__}: {detail}" if detail else type(root).__name__

    return f"{reason} [{location}]" if location else reason


def describe(exc):
    """Descrição curta de qualquer exceção, para interpolar em mensagens de log."""
    if is_network_error(exc):
        return summarize_exception(exc)
    detail = redact_tokens(str(exc)).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class RepeatSuppressFilter(logging.Filter):
    """
    Colapsa mensagens idênticas repetidas dentro de uma janela de tempo.

    A primeira ocorrência passa imediatamente (não se atrasa o diagnóstico). As
    repetições são contadas em silêncio e, quando a janela expira e a mensagem
    volta a surgir, é registada uma única vez com o sufixo
    "(repetida Nx nos últimos Xs)" — o administrador continua a saber que o
    problema persiste, sem centenas de linhas iguais.
    """

    def __init__(self, window_seconds=60, max_tracked=512):
        super().__init__()
        self.window = max(0, window_seconds)
        self.max_tracked = max_tracked
        self._lock = threading.Lock()
        self._seen = {}  # chave -> [instante_do_ultimo_registo, repeticoes_ocultas]

    def _prune(self, now):
        """Evita crescimento indefinido do dicionário em processos de longa duração."""
        if len(self._seen) <= self.max_tracked:
            return
        cutoff = now - (self.window * 2)
        for key in [k for k, v in self._seen.items() if v[0] < cutoff]:
            del self._seen[key]

    def filter(self, record):
        if self.window <= 0 or record.levelno < logging.INFO:
            return True

        try:
            message = record.getMessage()
        except Exception:
            return True

        key = (record.name, record.levelno, message)
        now = time.monotonic()

        with self._lock:
            entry = self._seen.get(key)
            if entry and (now - entry[0]) < self.window:
                entry[1] += 1
                return False

            hidden = entry[1] if entry else 0
            self._seen[key] = [now, 0]
            self._prune(now)

        if hidden:
            # Reescreve a mensagem já formatada; os args foram consumidos aqui.
            record.msg = f"{message} (repetida {hidden}x nos últimos {self.window}s)"
            record.args = ()
        return True


class CompactFormatter(logging.Formatter):
    """
    Formatador legível:

        2026-09-01 19:18:47 | ERROR   | services.stream_manager | Falha ao obter ...
                              ↳ stream_manager.py:281 (get_now_playing)

    - O prefixo redundante "app." é retirado do nome do logger.
    - Falhas de rede nunca mostram traceback: são resumidas numa linha.
    - Nos restantes erros, o traceback mostra apenas os frames do código da
      aplicação (os de `site-packages` só aparecem se não houver mais nada),
      um por linha, limitados aos últimos `max_frames`.
    """

    DEFAULT_FORMAT = '%(asctime)s | %(levelname)-7s | %(short_name)s | %(message)s'
    DEFAULT_DATEFMT = '%Y-%m-%d %H:%M:%S'
    INDENT = '                              ↳ '

    def __init__(self, fmt=None, datefmt=None, max_frames=4):
        super().__init__(fmt or self.DEFAULT_FORMAT, datefmt or self.DEFAULT_DATEFMT)
        self.max_frames = max_frames

    def format(self, record):
        # `short_name` é um atributo extra: não altera o nome real do logger,
        # por isso outros handlers continuam a ver o nome completo.
        name = record.name
        record.short_name = name[4:] if name.startswith('app.') else name
        return super().format(record)

    @staticmethod
    def _frames(tb):
        """Frames do traceback, dando prioridade ao código da aplicação."""
        frames = []
        while tb is not None:
            frames.append(tb.tb_frame)
            tb = tb.tb_next
        own = [f for f in frames if 'site-packages' not in f.f_code.co_filename
               and 'lib/python' not in f.f_code.co_filename]
        return own or frames

    @staticmethod
    def _location(frame):
        return f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno} ({frame.f_code.co_name})"

    def formatException(self, exc_info):
        exc_type, exc_value, tb = exc_info
        frames = self._frames(tb)
        type_name = exc_type.__name__ if exc_type else 'Exception'

        if exc_value is not None and is_network_error(exc_value):
            # O traceback de uma falha de rede é sempre o mesmo empilhamento de
            # requests/urllib3 e não diz nada útil. Guarda-se só o ponto do
            # nosso código onde ocorreu — o resto está resumido na mensagem.
            where = f" em {self._location(frames[-1])}" if frames else ""
            return f"{self.INDENT}{type_name}{where} (falha de rede: traceback omitido)"

        lines = []
        for frame in frames[-self.max_frames:]:
            lines.append(f"{self.INDENT}{self._location(frame)}")

        detail = redact_tokens(str(exc_value)).strip() if exc_value is not None else ''
        if len(detail) > 200:
            detail = detail[:200] + '…'
        lines.append(f"{self.INDENT}{type_name}: {detail}" if detail else f"{self.INDENT}{type_name}")

        return "\n".join(lines)


class ThrottledReporter:
    """
    Regista falhas recorrentes de um mesmo contexto com moderação, e assinala a
    recuperação.

    Usado nas rotinas que correm em ciclo (verificação de streams, "Reproduzindo
    Agora"): a primeira falha é registada como WARNING; enquanto o problema
    persistir só volta a ser registado a cada `interval` segundos, indicando
    quantas tentativas falharam; quando o serviço volta, regista-se uma linha de
    recuperação em INFO.
    """

    def __init__(self, logger_obj, interval=300):
        self._logger = logger_obj
        self._interval = interval
        self._lock = threading.Lock()
        self._state = {}  # contexto -> [instante_do_ultimo_log, falhas_desde_entao]

    def failure(self, context, exc, prefix=None):
        now = time.monotonic()
        with self._lock:
            entry = self._state.get(context)
            if entry is None:
                self._state[context] = [now, 1]
                should_log, failures = True, 1
            else:
                entry[1] += 1
                failures = entry[1]
                should_log = (now - entry[0]) >= self._interval
                if should_log:
                    entry[0] = now

        if not should_log:
            return

        message = f"{prefix or context}: {describe(exc)}"
        if failures > 1:
            minutes = max(1, int(self._interval / 60))
            message += f" — {failures} tentativas falhadas nos últimos ~{minutes} min"
        self._logger.warning(message)

    def recovered(self, context, message=None):
        with self._lock:
            entry = self._state.pop(context, None)

        if entry and entry[1] > 0:
            self._logger.info(message or f"{context}: ligação restabelecida após {entry[1]} falha(s).")
