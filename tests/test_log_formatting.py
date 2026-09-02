# tests/test_log_formatting.py
"""Resumo de erros, supressão de repetições e formatação compacta dos logs."""

import logging

import pytest
import requests

from app.utils import log_formatting
from app.utils.log_formatting import (
    CompactFormatter,
    RepeatSuppressFilter,
    ThrottledReporter,
    describe,
    is_network_error,
    redact_tokens,
    shorten_host,
    summarize_exception,
)

LONG_HOST = "72-21-17-85.dcabdee1234567890abcdef1234a0.plex.direct"


def _record(message, level=logging.INFO, name="app.services.plex", exc_info=None):
    record = logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1,
        msg=message, args=(), exc_info=exc_info,
    )
    return record


class TestRedactTokens:
    @pytest.mark.parametrize("param", ["X-Plex-Token", "token", "api_key", "apikey"])
    def test_remove_segredos_da_query_string(self, param):
        texto = f"GET /status?{param}=abc123SEGREDO"
        assert redact_tokens(texto) == f"GET /status?{param}=***"

    def test_nao_distingue_maiusculas(self):
        assert "SEGREDO" not in redact_tokens("/a?X-PLEX-TOKEN=SEGREDO")

    def test_texto_vazio(self):
        assert redact_tokens("") == ""


class TestShortenHost:
    def test_esconde_identificador_da_maquina(self):
        assert shorten_host(LONG_HOST) == "72-21-17-85.***.plex.direct"

    def test_host_normal_fica_intacto(self):
        assert shorten_host("plex.local") == "plex.local"

    def test_none(self):
        assert shorten_host(None) is None


class TestIsNetworkError:
    @pytest.mark.parametrize("exc", [
        requests.exceptions.ConnectionError("x"),
        requests.exceptions.ReadTimeout("x"),
        ConnectionError("x"),
        TimeoutError("x"),
    ])
    def test_falhas_de_rede(self, exc):
        assert is_network_error(exc) is True

    @pytest.mark.parametrize("exc", [ValueError("x"), FileNotFoundError("x"), KeyError("x")])
    def test_defeitos_do_codigo_nao_sao_falhas_de_rede(self, exc):
        # Estes têm de continuar a mostrar traceback completo.
        assert is_network_error(exc) is False


class TestSummarizeException:
    def test_resposta_http_repetida(self):
        exc = requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='{LONG_HOST}', port=14868): Max retries exceeded "
            "with url: /status/sessions?X-Plex-Token=segredo "
            "(Caused by ResponseError('too many 503 error responses'))"
        )
        resumo = summarize_exception(exc)

        assert "503" in resumo
        assert "esgotou as retentativas" in resumo
        assert "72-21-17-85.***.plex.direct:14868" in resumo
        # Nem o identificador da máquina nem o token podem chegar ao log.
        assert "dcabdee1234567890abcdef1234a0" not in resumo
        assert "segredo" not in resumo

    def test_motivo_declarado_pela_urllib3(self):
        exc = requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='plex.local', port=32400): Max retries exceeded "
            "(Caused by NewConnectionError('falhou'))"
        )
        assert "não foi possível abrir a ligação" in summarize_exception(exc)

    def test_usa_o_tipo_da_causa_quando_nao_ha_padrao_conhecido(self):
        causa = requests.exceptions.ConnectTimeout("tempo esgotado")
        try:
            try:
                raise causa
            except requests.exceptions.ConnectTimeout as err:
                raise requests.exceptions.ConnectionError("falhou") from err
        except requests.exceptions.ConnectionError as err:
            resumo = summarize_exception(err)

        assert resumo == "tempo esgotado ao estabelecer a ligação"

    def test_fallback_para_tipo_e_mensagem(self):
        assert summarize_exception(ConnectionError("boom")) == "ConnectionError: boom"

    def test_none(self):
        assert summarize_exception(None) == ""


class TestDescribe:
    def test_erro_de_rede_e_resumido(self):
        exc = requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='plex.local', port=32400): "
            "(Caused by ConnectionRefusedError('recusada'))"
        )
        assert "ligação recusada pelo servidor" in describe(exc)

    def test_erro_normal_mostra_tipo_e_mensagem(self):
        assert describe(ValueError("valor inválido")) == "ValueError: valor inválido"

    def test_erro_sem_mensagem(self):
        assert describe(ValueError()) == "ValueError"

    def test_tokens_sao_removidos(self):
        assert describe(ValueError("/a?token=segredo")) == "ValueError: /a?token=***"


class TestRepeatSuppressFilter:
    def test_primeira_passa_e_repeticao_e_suprimida(self):
        filtro = RepeatSuppressFilter(window_seconds=60)

        assert filtro.filter(_record("Plex offline")) is True
        assert filtro.filter(_record("Plex offline")) is False
        assert filtro.filter(_record("Plex offline")) is False

    def test_mensagens_diferentes_nao_se_afetam(self):
        filtro = RepeatSuppressFilter(window_seconds=60)

        assert filtro.filter(_record("Erro A")) is True
        assert filtro.filter(_record("Erro B")) is True

    def test_janela_zero_desativa_a_supressao(self):
        filtro = RepeatSuppressFilter(window_seconds=0)

        assert filtro.filter(_record("Plex offline")) is True
        assert filtro.filter(_record("Plex offline")) is True

    def test_debug_nunca_e_suprimido(self):
        filtro = RepeatSuppressFilter(window_seconds=60)

        assert filtro.filter(_record("detalhe", level=logging.DEBUG)) is True
        assert filtro.filter(_record("detalhe", level=logging.DEBUG)) is True

    def test_apos_a_janela_regista_quantas_foram_ocultadas(self, monkeypatch):
        relogio = {"agora": 1000.0}
        monkeypatch.setattr(log_formatting.time, "monotonic", lambda: relogio["agora"])

        filtro = RepeatSuppressFilter(window_seconds=60)
        assert filtro.filter(_record("Plex offline")) is True
        for _ in range(4):
            filtro.filter(_record("Plex offline"))

        relogio["agora"] += 61
        registo = _record("Plex offline")
        assert filtro.filter(registo) is True
        assert registo.getMessage() == "Plex offline (repetida 4x nos últimos 60s)"


class TestCompactFormatter:
    def test_retira_o_prefixo_app_do_nome_do_logger(self):
        saida = CompactFormatter().format(_record("mensagem"))

        assert "services.plex" in saida
        assert "app.services.plex" not in saida
        assert "mensagem" in saida

    def test_traceback_de_rede_e_omitido(self):
        try:
            raise requests.exceptions.ConnectionError("sem ligação")
        except requests.exceptions.ConnectionError:
            import sys
            saida = CompactFormatter().formatException(sys.exc_info())

        assert "falha de rede: traceback omitido" in saida
        assert "ConnectionError" in saida

    def test_traceback_normal_mostra_ficheiro_e_linha(self):
        try:
            raise ValueError("rebentou")
        except ValueError:
            import sys
            saida = CompactFormatter().formatException(sys.exc_info())

        assert "test_log_formatting.py:" in saida
        assert "ValueError: rebentou" in saida

    def test_detalhe_muito_longo_e_truncado(self):
        try:
            raise ValueError("x" * 500)
        except ValueError:
            import sys
            saida = CompactFormatter().formatException(sys.exc_info())

        assert "…" in saida
        assert len(saida) < 500


class _LoggerEspiao:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, message):
        self.warnings.append(message)

    def info(self, message):
        self.infos.append(message)


class TestThrottledReporter:
    def test_primeira_falha_e_registada_e_as_seguintes_nao(self):
        espiao = _LoggerEspiao()
        reporter = ThrottledReporter(espiao, interval=300)

        for _ in range(5):
            reporter.failure("plex", ConnectionError("offline"), prefix="Plex")

        assert len(espiao.warnings) == 1
        assert "Plex" in espiao.warnings[0]

    def test_apos_o_intervalo_indica_o_total_de_tentativas(self, monkeypatch):
        relogio = {"agora": 500.0}
        monkeypatch.setattr(log_formatting.time, "monotonic", lambda: relogio["agora"])

        espiao = _LoggerEspiao()
        reporter = ThrottledReporter(espiao, interval=300)
        for _ in range(3):
            reporter.failure("plex", ConnectionError("offline"))

        relogio["agora"] += 301
        reporter.failure("plex", ConnectionError("offline"))

        assert len(espiao.warnings) == 2
        assert "4 tentativas falhadas" in espiao.warnings[1]

    def test_recuperacao_e_assinalada_uma_unica_vez(self):
        espiao = _LoggerEspiao()
        reporter = ThrottledReporter(espiao, interval=300)

        reporter.failure("plex", ConnectionError("offline"))
        reporter.recovered("plex")
        reporter.recovered("plex")

        assert len(espiao.infos) == 1
        assert "restabelecida" in espiao.infos[0]

    def test_recuperacao_sem_falha_previa_nao_gera_ruido(self):
        espiao = _LoggerEspiao()
        ThrottledReporter(espiao).recovered("plex")

        assert espiao.infos == []
