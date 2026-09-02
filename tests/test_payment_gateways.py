# tests/test_payment_gateways.py
"""Gateways de pagamento: credenciais, estado e validação de webhooks."""

import hashlib
import hmac

import pytest
import requests

from app.services import efi_manager as efi_module
from app.services import gates2b_manager as gates2b_module
from app.services import mercado_pago_manager as mp_module
from app.services.efi_manager import EfiManager
from app.services.gates2b_manager import Gates2bManager
from app.services.mercado_pago_manager import MercadoPagoManager
from tests.conftest import FakeDataManager

SEGREDO = "segredo-do-webhook"


def assinar(secret, data_id, request_id, ts):
    """Reproduz a assinatura que o Mercado Pago envia no cabeçalho 'x-signature'."""
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    return hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()


class RespostaFalsa:
    def __init__(self, payload=None, status_code=200, texto=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = texto

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            erro = requests.exceptions.HTTPError(f"{self.status_code}")
            erro.response = self
            raise erro


# ==========================================================================
# MERCADO PAGO
# ==========================================================================

@pytest.fixture()
def mp_configurar(monkeypatch):
    def _configurar(**valores):
        config = {"MERCADOPAGO_ENABLED": False, "MERCADOPAGO_ACCESS_TOKEN": ""}
        config.update(valores)
        monkeypatch.setattr(mp_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


class TestMercadoPagoCredenciais:
    def test_desativado_nao_inicializa_o_sdk(self, app_context, mp_configurar):
        mp_configurar(MERCADOPAGO_ENABLED=False, MERCADOPAGO_ACCESS_TOKEN="TEST-123")

        gestor = MercadoPagoManager(FakeDataManager())

        assert gestor.sdk is None
        assert gestor.check_status()["status"] == "DISABLED"

    def test_ativado_com_token_fica_online(self, app_context, mp_configurar):
        mp_configurar(MERCADOPAGO_ENABLED=True, MERCADOPAGO_ACCESS_TOKEN="TEST-123")

        gestor = MercadoPagoManager(FakeDataManager())

        assert gestor.sdk is not None
        assert gestor.check_status()["status"] == "ONLINE"

    @pytest.mark.parametrize("valor", ["true", "True", "1", "yes"])
    def test_ativacao_guardada_como_texto(self, app_context, mp_configurar, valor):
        mp_configurar(MERCADOPAGO_ENABLED=valor, MERCADOPAGO_ACCESS_TOKEN="TEST-123")

        assert MercadoPagoManager(FakeDataManager()).sdk is not None

    @pytest.mark.parametrize("token", ["", "   ", None])
    def test_ativado_sem_token_fica_offline(self, app_context, mp_configurar, token):
        mp_configurar(MERCADOPAGO_ENABLED=True, MERCADOPAGO_ACCESS_TOKEN=token)

        gestor = MercadoPagoManager(FakeDataManager())

        assert gestor.sdk is None
        assert gestor.check_status()["status"] == "OFFLINE"


class TestMercadoPagoWebhookSignature:
    @pytest.fixture()
    def gestor(self, app_context, mp_configurar):
        mp_configurar(
            MERCADOPAGO_ENABLED=True,
            MERCADOPAGO_ACCESS_TOKEN="TEST-123",
            MERCADOPAGO_WEBHOOK_SECRET=SEGREDO,
        )
        return MercadoPagoManager(FakeDataManager())

    def test_assinatura_valida(self, gestor):
        assinatura = assinar(SEGREDO, "12345", "req-1", "1704908010")

        valido, motivo = gestor.validate_webhook_signature(
            f"ts=1704908010,v1={assinatura}", "req-1", "12345"
        )

        assert valido is True
        assert motivo is None

    def test_espacos_no_cabecalho_sao_tolerados(self, gestor):
        assinatura = assinar(SEGREDO, "12345", "req-1", "1704908010")

        valido, _motivo = gestor.validate_webhook_signature(
            f" ts=1704908010 , v1={assinatura} ", "req-1", "12345"
        )

        assert valido is True

    def test_assinatura_forjada_e_recusada(self, gestor):
        valido, motivo = gestor.validate_webhook_signature(
            "ts=1704908010,v1=" + "0" * 64, "req-1", "12345"
        )

        assert valido is False
        assert motivo == "Assinatura inválida."

    def test_id_do_pagamento_diferente_invalida(self, gestor):
        # Impede reaproveitar uma assinatura legítima para outro pagamento.
        assinatura = assinar(SEGREDO, "12345", "req-1", "1704908010")

        valido, _motivo = gestor.validate_webhook_signature(
            f"ts=1704908010,v1={assinatura}", "req-1", "99999"
        )

        assert valido is False

    def test_request_id_diferente_invalida(self, gestor):
        assinatura = assinar(SEGREDO, "12345", "req-1", "1704908010")

        valido, _motivo = gestor.validate_webhook_signature(
            f"ts=1704908010,v1={assinatura}", "req-outro", "12345"
        )

        assert valido is False

    def test_timestamp_diferente_invalida(self, gestor):
        assinatura = assinar(SEGREDO, "12345", "req-1", "1704908010")

        valido, _motivo = gestor.validate_webhook_signature(
            f"ts=1704908999,v1={assinatura}", "req-1", "12345"
        )

        assert valido is False

    def test_cabecalho_em_falta(self, gestor):
        valido, motivo = gestor.validate_webhook_signature(None, "req-1", "12345")

        assert valido is False
        assert "x-signature" in motivo

    @pytest.mark.parametrize("cabecalho", ["", "lixo", "ts=123", "v1=abc", "abc=1,def=2"])
    def test_cabecalho_mal_formado(self, gestor, cabecalho):
        valido, _motivo = gestor.validate_webhook_signature(cabecalho, "req-1", "12345")

        assert valido is False

    def test_sem_segredo_a_validacao_fica_desativada(self, app_context, mp_configurar):
        # Instalações antigas ainda não configuraram o segredo: não se pode partir
        # o fluxo de pagamentos delas — a proteção real é a reconfirmação na API.
        mp_configurar(MERCADOPAGO_ENABLED=True, MERCADOPAGO_ACCESS_TOKEN="T", MERCADOPAGO_WEBHOOK_SECRET="")
        gestor = MercadoPagoManager(FakeDataManager())

        assert gestor.validate_webhook_signature(None, "req-1", "12345") == (True, None)


# ==========================================================================
# GATES2B
# ==========================================================================

@pytest.fixture()
def gates2b_configurar(monkeypatch):
    def _configurar(**valores):
        config = {"GATES2B_ENABLED": False, "GATES2B_AUTH_TOKEN": ""}
        config.update(valores)
        monkeypatch.setattr(gates2b_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


class TestGates2b:
    def test_desativado_ignora_o_token_guardado(self, app_context, gates2b_configurar):
        # Um token de uma configuração anterior não pode dar a impressão de ativo.
        gates2b_configurar(GATES2B_ENABLED=False, GATES2B_AUTH_TOKEN="token-antigo")

        gestor = Gates2bManager(FakeDataManager())

        assert gestor.auth_token is None
        assert gestor.check_status()["status"] == "DISABLED"

    def test_ativado_com_token(self, app_context, gates2b_configurar):
        gates2b_configurar(GATES2B_ENABLED=True, GATES2B_AUTH_TOKEN="token")

        gestor = Gates2bManager(FakeDataManager())

        assert gestor.auth_token == "token"
        assert gestor.check_status()["status"] == "ONLINE"

    def test_ativado_sem_token(self, app_context, gates2b_configurar):
        gates2b_configurar(GATES2B_ENABLED=True, GATES2B_AUTH_TOKEN="")

        assert Gates2bManager(FakeDataManager()).check_status()["status"] == "OFFLINE"

    def test_o_estado_le_sempre_a_configuracao_atual(self, app_context, gates2b_configurar, monkeypatch):
        # Desligar o gateway sem tocar no token tem de refletir-se de imediato.
        gates2b_configurar(GATES2B_ENABLED=True, GATES2B_AUTH_TOKEN="token")
        gestor = Gates2bManager(FakeDataManager())

        monkeypatch.setattr(
            gates2b_module, "load_or_create_config",
            lambda: {"GATES2B_ENABLED": False, "GATES2B_AUTH_TOKEN": "token"},
        )

        assert gestor.check_status()["status"] == "DISABLED"


class TestGates2bWebhookUrl:
    def _gestor(self, app_context, gates2b_configurar, base_url):
        gates2b_configurar(GATES2B_ENABLED=True, GATES2B_AUTH_TOKEN="token", APP_BASE_URL=base_url)
        return Gates2bManager(FakeDataManager())

    def test_construido_a_partir_do_dominio(self, app_context, gates2b_configurar):
        gestor = self._gestor(app_context, gates2b_configurar, "https://painel.exemplo.com/")

        assert gestor._build_webhook_url() == "https://painel.exemplo.com/api/payments/webhook/gates2b"

    def test_sem_dominio_nao_ha_webhook(self, app_context, gates2b_configurar):
        assert self._gestor(app_context, gates2b_configurar, "")._build_webhook_url() is None

    def test_dominio_local_ainda_gera_url_mas_e_assinalado(self, app_context, gates2b_configurar, caplog):
        gestor = self._gestor(app_context, gates2b_configurar, "http://localhost:5000")

        with caplog.at_level("ERROR"):
            url = gestor._build_webhook_url()

        assert url == "http://localhost:5000/api/payments/webhook/gates2b"
        assert "ALERTA CRÍTICO" in caplog.text


class TestGates2bTestConnection:
    @pytest.fixture()
    def gestor(self, app_context, gates2b_configurar):
        gates2b_configurar(GATES2B_ENABLED=True, GATES2B_AUTH_TOKEN="token")
        return Gates2bManager(FakeDataManager())

    def test_token_obrigatorio(self, gestor):
        assert gestor.test_connection("")["success"] is False

    def test_chave_valida(self, gestor, monkeypatch):
        monkeypatch.setattr(
            gates2b_module.requests, "get",
            lambda *a, **k: RespostaFalsa({"response": {"valid": True, "expires_at": "2027-01-01"}}),
        )

        resultado = gestor.test_connection("token")

        assert resultado["success"] is True
        assert "2027-01-01" in resultado["message"]

    def test_chave_recusada_pela_api(self, gestor, monkeypatch):
        monkeypatch.setattr(
            gates2b_module.requests, "get",
            lambda *a, **k: RespostaFalsa({"response": {"valid": False, "message": "expirada"}}),
        )

        resultado = gestor.test_connection("token")

        assert resultado["success"] is False
        assert resultado["message"] == "expirada"

    def test_token_invalido_devolve_401(self, gestor, monkeypatch):
        monkeypatch.setattr(
            gates2b_module.requests, "get", lambda *a, **k: RespostaFalsa({}, status_code=401)
        )

        resultado = gestor.test_connection("token")

        assert resultado["success"] is False
        assert "autenticação" in resultado["message"].lower()

    def test_erro_do_gateway(self, gestor, monkeypatch):
        monkeypatch.setattr(
            gates2b_module.requests, "get",
            lambda *a, **k: RespostaFalsa({}, status_code=500, texto="boom"),
        )

        assert gestor.test_connection("token")["success"] is False

    def test_falha_de_rede(self, gestor, monkeypatch):
        def rebenta(*args, **kwargs):
            raise requests.exceptions.ConnectionError("sem rede")

        monkeypatch.setattr(gates2b_module.requests, "get", rebenta)

        assert gestor.test_connection("token")["success"] is False


# ==========================================================================
# EFÍ
# ==========================================================================

@pytest.fixture()
def efi_configurar(monkeypatch):
    def _configurar(**valores):
        config = {"EFI_ENABLED": False}
        config.update(valores)
        monkeypatch.setattr(efi_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


class TestEfi:
    def test_desativada_ignora_credenciais_guardadas(self, app_context, efi_configurar):
        efi_configurar(
            EFI_ENABLED=False, EFI_CLIENT_ID="id", EFI_CLIENT_SECRET="segredo",
            EFI_CERTIFICATE="/tmp/inexistente.pem",
        )

        gestor = EfiManager(FakeDataManager())

        assert gestor.efi is None
        assert gestor.check_status()["status"] == "DISABLED"

    def test_credenciais_incompletas(self, app_context, efi_configurar):
        efi_configurar(EFI_ENABLED=True, EFI_CLIENT_ID="id", EFI_CLIENT_SECRET="", EFI_CERTIFICATE="")

        gestor = EfiManager(FakeDataManager())

        assert gestor.efi is None
        assert gestor.check_status()["status"] == "OFFLINE"

    def test_certificado_inexistente(self, app_context, efi_configurar):
        # Sem esta verificação, o erro só aparecia na primeira cobrança real.
        efi_configurar(
            EFI_ENABLED=True, EFI_CLIENT_ID="id", EFI_CLIENT_SECRET="segredo",
            EFI_CERTIFICATE="/caminho/que/nao/existe.pem",
        )

        assert EfiManager(FakeDataManager()).efi is None

    def test_certificado_existente_inicializa_o_cliente(self, app_context, efi_configurar, tmp_path):
        certificado = tmp_path / "cert.pem"
        certificado.write_text("conteúdo falso", encoding="utf-8")
        efi_configurar(
            EFI_ENABLED=True, EFI_CLIENT_ID="id", EFI_CLIENT_SECRET="segredo",
            EFI_CERTIFICATE=str(certificado), EFI_SANDBOX=True,
        )

        gestor = EfiManager(FakeDataManager())

        assert gestor.efi is not None
        assert gestor.check_status()["status"] == "ONLINE"

    @pytest.mark.parametrize("valor", ["false", "0", "no"])
    def test_desativacao_guardada_como_texto(self, app_context, efi_configurar, valor):
        efi_configurar(EFI_ENABLED=valor, EFI_CLIENT_ID="id", EFI_CLIENT_SECRET="s", EFI_CERTIFICATE="/x.pem")

        assert EfiManager(FakeDataManager()).efi is None
