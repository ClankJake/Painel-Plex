# tests/test_tautulli_api_client.py
"""Cliente HTTP do Tautulli: parâmetros, tratamento de erros e teste de ligação."""

import pytest
import requests
from requests.exceptions import RequestException

from app.services.tautulli import api_client as api_module
from app.services.tautulli.api_client import TautulliApiClient


class RespostaFalsa:
    def __init__(self, payload=None, status_code=200, json_invalido=False):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self._json_invalido = json_invalido

    def json(self):
        if self._json_invalido:
            raise ValueError("não é JSON")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")


@pytest.fixture()
def configurar(monkeypatch):
    def _configurar(url="http://tautulli.local:8181/", api_key="chave"):
        monkeypatch.setattr(
            api_module, "load_or_create_config",
            lambda: {"TAUTULLI_URL": url, "TAUTULLI_API_KEY": api_key},
        )

    return _configurar


@pytest.fixture()
def client(app_context, configurar):
    configurar()
    return TautulliApiClient()


def responder(client, monkeypatch, payload=None, **kwargs):
    """Substitui a sessão HTTP e devolve a lista de chamadas feitas."""
    chamadas = []

    def fake_get(url, params=None, timeout=None):
        chamadas.append({"url": url, "params": params, "timeout": timeout})
        return RespostaFalsa(payload, **kwargs)

    monkeypatch.setattr(client.session, "get", fake_get)
    return chamadas


class TestReloadConfig:
    def test_configuracao_completa(self, client):
        assert client.is_configured is True
        # A barra final é removida para não gerar URLs com '//'.
        assert client.base_url == "http://tautulli.local:8181"

    @pytest.mark.parametrize("url,chave", [("", "chave"), ("http://x", ""), ("", "")])
    def test_configuracao_incompleta(self, app_context, configurar, url, chave):
        configurar(url=url, api_key=chave)

        assert TautulliApiClient().is_configured is False

    def test_recarrega_apos_mudanca(self, client, configurar):
        configurar(url="", api_key="")

        client.reload_config()

        assert client.is_configured is False


class TestMakeRequest:
    def test_sem_configuracao_rebenta_cedo(self, app_context, configurar):
        configurar(url="", api_key="")

        with pytest.raises(ValueError):
            TautulliApiClient()._make_request({"cmd": "status"})

    def test_resposta_bem_sucedida(self, client, monkeypatch):
        chamadas = responder(client, monkeypatch, {"response": {"result": "success", "data": {"a": 1}}})

        assert client._make_request({"cmd": "status"}) == {"a": 1}
        assert chamadas[0]["url"] == "http://tautulli.local:8181/api/v2"

    def test_a_chave_da_api_e_injetada(self, client, monkeypatch):
        chamadas = responder(client, monkeypatch, {"response": {"result": "success", "data": []}})

        client._make_request({"cmd": "status"})

        assert chamadas[0]["params"]["apikey"] == "chave"

    def test_erro_devolvido_pela_api(self, client, monkeypatch):
        responder(client, monkeypatch, {"response": {"result": "error", "message": "chave inválida"}})

        with pytest.raises(RequestException, match="chave inválida"):
            client._make_request({"cmd": "status"})

    def test_resposta_sem_json_valido(self, client, monkeypatch):
        # O Tautulli pode devolver uma página HTML de erro 500.
        responder(client, monkeypatch, json_invalido=True)

        with pytest.raises(RequestException):
            client._make_request({"cmd": "status"})

    @pytest.mark.parametrize("erro", [
        requests.exceptions.ConnectionError("sem rede"),
        requests.exceptions.Timeout("demorou"),
    ])
    def test_falhas_de_rede_viram_request_exception(self, client, monkeypatch, erro):
        def rebenta(*args, **kwargs):
            raise erro

        monkeypatch.setattr(client.session, "get", rebenta)

        with pytest.raises(RequestException):
            client._make_request({"cmd": "status"})

    def test_metodo_http_nao_suportado(self, client, monkeypatch):
        responder(client, monkeypatch, {"response": {"result": "success"}})

        with pytest.raises(ValueError):
            client._make_request({"cmd": "status"}, method="DELETE")


class TestMetodosDaApi:
    def test_get_history_usa_timeout_maior(self, client, monkeypatch):
        chamadas = responder(client, monkeypatch, {"response": {"result": "success", "data": []}})

        client.get_history(user_id=1, after="2026-01-01")

        pedido = chamadas[0]
        assert pedido["params"]["cmd"] == "get_history"
        assert pedido["params"]["length"] == 10000
        assert pedido["params"]["user_id"] == 1
        assert pedido["params"]["after"] == "2026-01-01"
        # Históricos extensos demoram a gerar no Tautulli.
        assert pedido["timeout"] == 20

    def test_get_metadata(self, client, monkeypatch):
        chamadas = responder(client, monkeypatch, {"response": {"result": "success", "data": {"genres": []}}})

        client.get_metadata("55")

        assert chamadas[0]["params"] == {"cmd": "get_metadata", "rating_key": "55", "apikey": "chave"}

    def test_get_recently_added(self, client, monkeypatch):
        chamadas = responder(client, monkeypatch, {"response": {"result": "success", "data": []}})

        client.get_recently_added(count=10)

        assert chamadas[0]["params"]["cmd"] == "get_recently_added"
        assert chamadas[0]["params"]["count"] == 10


class TestTestConnection:
    def test_url_e_chave_obrigatorias(self, app_context):
        assert TautulliApiClient.test_connection("", "chave")["success"] is False
        assert TautulliApiClient.test_connection("http://x", "")["success"] is False

    def test_ligacao_bem_sucedida(self, app_context, monkeypatch):
        chamadas = []

        def fake_get(url, params=None, headers=None, timeout=None):
            chamadas.append(url)
            return RespostaFalsa({"response": {"result": "success"}})

        monkeypatch.setattr(api_module.requests, "get", fake_get)

        resultado = TautulliApiClient.test_connection("http://tautulli.local:8181/", "chave")

        assert resultado["success"] is True
        assert chamadas == ["http://tautulli.local:8181/api/v2"]

    def test_credenciais_recusadas(self, app_context, monkeypatch):
        monkeypatch.setattr(
            api_module.requests, "get",
            lambda *a, **k: RespostaFalsa({"response": {"result": "error", "message": "chave inválida"}}),
        )

        resultado = TautulliApiClient.test_connection("http://x", "chave-errada")

        assert resultado["success"] is False
        assert resultado["message"] == "chave inválida"

    @pytest.mark.parametrize("erro", [
        requests.exceptions.ConnectionError("sem rede"),
        requests.exceptions.Timeout("demorou"),
        RuntimeError("inesperado"),
    ])
    def test_falhas_devolvem_mensagem_em_vez_de_rebentar(self, app_context, monkeypatch, erro):
        def rebenta(*args, **kwargs):
            raise erro

        monkeypatch.setattr(api_module.requests, "get", rebenta)

        resultado = TautulliApiClient.test_connection("http://x", "chave")

        assert resultado["success"] is False
        assert resultado["message"]
