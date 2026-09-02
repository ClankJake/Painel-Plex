# tests/test_overseerr_manager.py
"""Integração com o Overseerr/Jellyseerr: pedidos HTTP, cache, estados e webhook."""

import pytest
import requests

from app.services import overseerr_manager as overseerr_module
from app.services.overseerr_manager import OverseerrManager


class RespostaFalsa:
    def __init__(self, payload=None, status_code=200, texto="ok", json_invalido=False):
        self._payload = payload if payload is not None else {}
        self._json_invalido = json_invalido
        self.status_code = status_code
        self.text = texto

    def json(self):
        if self._json_invalido:
            raise ValueError("não é JSON")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            erro = requests.exceptions.HTTPError(str(self.status_code))
            erro.response = self
            raise erro


@pytest.fixture()
def configurar(monkeypatch):
    def _configurar(**valores):
        config = {
            "OVERSEERR_ENABLED": True,
            "OVERSEERR_URL": "https://seerr.exemplo.com/",
            "OVERSEERR_API_KEY": "chave",
        }
        config.update(valores)
        monkeypatch.setattr(overseerr_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


@pytest.fixture()
def manager(app_context, configurar):
    configurar()
    return OverseerrManager()


def responder(monkeypatch, respostas):
    """Substitui requests.request e devolve o registo das chamadas feitas."""
    chamadas = []
    fila = list(respostas)

    def fake_request(method, url, headers=None, timeout=None, **kwargs):
        chamadas.append({"method": method, "url": url, "headers": headers, **kwargs})
        return fila.pop(0) if len(fila) > 1 else fila[0]

    monkeypatch.setattr(overseerr_module.requests, "request", fake_request)
    return chamadas


class TestConfiguracao:
    def test_ativo_e_configurado(self, manager):
        assert manager.enabled is True
        assert manager.api_url == "https://seerr.exemplo.com"

    def test_desativado(self, app_context, configurar):
        configurar(OVERSEERR_ENABLED=False)

        assert OverseerrManager().enabled is False

    @pytest.mark.parametrize("valores", [
        {"OVERSEERR_URL": ""},
        {"OVERSEERR_API_KEY": ""},
    ])
    def test_configuracao_incompleta_bloqueia_os_pedidos(self, app_context, configurar, valores):
        configurar(**valores)

        resultado = OverseerrManager()._make_request("GET", "/user")

        assert resultado["success"] is False

    def test_o_estado_e_lido_da_configuracao_atual(self, manager, configurar):
        # Desligar o módulo tem de refletir-se sem reiniciar a aplicação.
        configurar(OVERSEERR_ENABLED=False)

        assert manager.enabled is False


class TestMakeRequest:
    def test_pedido_bem_sucedido(self, manager, monkeypatch):
        chamadas = responder(monkeypatch, [RespostaFalsa({"results": []})])

        resultado = manager._make_request("GET", "/user")

        assert resultado == {"success": True, "data": {"results": []}}
        assert chamadas[0]["url"] == "https://seerr.exemplo.com/api/v1/user"
        assert chamadas[0]["headers"]["X-Api-Key"] == "chave"

    def test_erro_http_com_mensagem_do_servidor(self, manager, monkeypatch):
        responder(monkeypatch, [RespostaFalsa({"message": "não autorizado"}, status_code=401)])

        resultado = manager._make_request("GET", "/user")

        assert resultado["success"] is False
        assert "não autorizado" in resultado["message"]

    def test_erro_http_sem_json(self, manager, monkeypatch):
        responder(monkeypatch, [RespostaFalsa(status_code=500, texto="<html>erro</html>", json_invalido=True)])

        resultado = manager._make_request("GET", "/user")

        assert resultado["success"] is False
        assert "500" in resultado["message"]

    def test_falha_de_rede(self, manager, monkeypatch):
        def rebenta(*args, **kwargs):
            raise requests.exceptions.ConnectionError("sem rede")

        monkeypatch.setattr(overseerr_module.requests, "request", rebenta)

        assert manager._make_request("GET", "/user")["success"] is False

    def test_resposta_vazia(self, manager, monkeypatch):
        responder(monkeypatch, [RespostaFalsa({}, texto="")])

        assert manager._make_request("DELETE", "/user/1") == {"success": True, "data": {}}


class TestFindUserByEmail:
    def _pagina(self, utilizadores, total=None):
        return RespostaFalsa({
            "results": utilizadores,
            "pageInfo": {"results": total if total is not None else len(utilizadores)},
        })

    def test_encontra_na_primeira_pagina(self, manager, monkeypatch):
        responder(monkeypatch, [self._pagina([{"id": 7, "email": "ana@exemplo.com"}])])

        assert manager.find_user_by_email("ana@exemplo.com")["id"] == 7

    def test_ignora_maiusculas_e_espacos(self, manager, monkeypatch):
        responder(monkeypatch, [self._pagina([{"id": 7, "email": "Ana@Exemplo.com"}])])

        assert manager.find_user_by_email("  ANA@exemplo.com ")["id"] == 7

    def test_utilizador_inexistente(self, manager, monkeypatch):
        responder(monkeypatch, [self._pagina([{"id": 1, "email": "outro@exemplo.com"}])])

        assert manager.find_user_by_email("ana@exemplo.com") is None

    def test_email_vazio_nao_faz_pedidos(self, manager, monkeypatch):
        chamadas = responder(monkeypatch, [self._pagina([])])

        assert manager.find_user_by_email("") is None
        assert chamadas == []

    def test_o_resultado_fica_em_cache(self, manager, monkeypatch):
        chamadas = responder(monkeypatch, [self._pagina([{"id": 7, "email": "ana@exemplo.com"}])])

        manager.find_user_by_email("ana@exemplo.com")
        manager.find_user_by_email("ana@exemplo.com")

        # A segunda consulta não pode voltar a bater na API.
        assert len(chamadas) == 1

    def test_a_cache_expira(self, manager, monkeypatch):
        chamadas = responder(monkeypatch, [self._pagina([{"id": 7, "email": "ana@exemplo.com"}])])
        manager.find_user_by_email("ana@exemplo.com")

        agora = overseerr_module.time.time()
        monkeypatch.setattr(
            overseerr_module.time, "time", lambda: agora + manager.USER_CACHE_TTL + 1
        )
        manager.find_user_by_email("ana@exemplo.com")

        assert len(chamadas) == 2

    def test_a_cache_pode_ser_invalidada(self, manager, monkeypatch):
        chamadas = responder(monkeypatch, [self._pagina([{"id": 7, "email": "ana@exemplo.com"}])])
        manager.find_user_by_email("ana@exemplo.com")

        manager.invalidate_user_cache("ana@exemplo.com")
        manager.find_user_by_email("ana@exemplo.com")

        assert len(chamadas) == 2

    def test_invalidar_tudo(self, manager, monkeypatch):
        responder(monkeypatch, [self._pagina([{"id": 7, "email": "ana@exemplo.com"}])])
        manager.find_user_by_email("ana@exemplo.com")

        manager.invalidate_user_cache()

        assert manager._user_cache == {}

    def test_pagina_ate_encontrar(self, manager, monkeypatch):
        primeira = self._pagina([{"id": i, "email": f"u{i}@x.com"} for i in range(100)], total=200)
        segunda = self._pagina([{"id": 150, "email": "ana@exemplo.com"}], total=200)
        chamadas = responder(monkeypatch, [primeira, segunda])

        assert manager.find_user_by_email("ana@exemplo.com")["id"] == 150
        assert chamadas[1]["params"]["skip"] == 100

    def test_falha_na_api_devolve_none(self, manager, monkeypatch):
        responder(monkeypatch, [RespostaFalsa({"message": "erro"}, status_code=500)])

        assert manager.find_user_by_email("ana@exemplo.com") is None


class TestRemoveUser:
    def test_remove_e_limpa_a_cache(self, manager, monkeypatch):
        pagina = RespostaFalsa({"results": [{"id": 7, "email": "ana@exemplo.com"}], "pageInfo": {"results": 1}})
        chamadas = responder(monkeypatch, [pagina, RespostaFalsa({}, texto="")])

        resultado = manager.remove_user("ana@exemplo.com")

        assert resultado["success"] is True
        assert chamadas[-1]["method"] == "DELETE"
        assert chamadas[-1]["url"].endswith("/user/7")
        assert manager._user_cache == {}

    def test_utilizador_inexistente_nao_e_erro(self, manager, monkeypatch):
        responder(monkeypatch, [RespostaFalsa({"results": [], "pageInfo": {"results": 0}})])

        # Remover quem já não existe é um sucesso silencioso.
        assert manager.remove_user("ana@exemplo.com")["success"] is True


class TestImportFromPlex:
    def test_importa_pelo_plex_id(self, manager, monkeypatch):
        chamadas = responder(monkeypatch, [RespostaFalsa({}, texto="")])

        resultado = manager.import_from_plex({"id": 42, "username": "ana", "email": "ana@exemplo.com"})

        assert resultado["success"] is True
        assert chamadas[0]["json"] == {"plexIds": ["42"]}

    def test_sem_plex_id(self, manager, monkeypatch):
        chamadas = responder(monkeypatch, [RespostaFalsa({})])

        assert manager.import_from_plex({"username": "ana"})["success"] is False
        assert chamadas == []

    def test_falha_da_api_e_propagada(self, manager, monkeypatch):
        responder(monkeypatch, [RespostaFalsa({"message": "recusado"}, status_code=400)])

        assert manager.import_from_plex({"id": 42, "username": "ana"})["success"] is False


class TestTestConnection:
    def test_credenciais_obrigatorias(self, manager):
        assert manager.test_connection("", "chave")["success"] is False
        assert manager.test_connection("https://x", "")["success"] is False

    def test_ligacao_bem_sucedida(self, manager, monkeypatch):
        chamadas = []

        def fake_get(url, headers=None, timeout=None):
            chamadas.append(url)
            return RespostaFalsa({"version": "1.33.0"})

        monkeypatch.setattr(overseerr_module.requests, "get", fake_get)

        assert manager.test_connection("https://seerr.exemplo.com/", "chave")["success"] is True
        assert chamadas == ["https://seerr.exemplo.com/api/v1/settings/about"]

    def test_falha_de_ligacao(self, manager, monkeypatch):
        def rebenta(*args, **kwargs):
            raise requests.exceptions.ConnectionError("sem rede")

        monkeypatch.setattr(overseerr_module.requests, "get", rebenta)

        assert manager.test_connection("https://x", "chave")["success"] is False


class TestStatusInfo:
    @pytest.mark.parametrize("pedido,esperado", [(1, "Pendente"), (3, "Recusado")])
    def test_estado_do_pedido(self, manager, pedido, esperado):
        assert manager._get_status_info(pedido, 1)["text"] == esperado

    @pytest.mark.parametrize("media", [4, 5])
    def test_disponibilidade_manda_sobre_o_pedido(self, manager, media):
        # Um item já disponível no servidor não pode aparecer como "Pendente".
        resultado = manager._get_status_info(1, media)

        assert resultado["color"] in ("teal", "green")

    def test_pedido_aprovado_mostra_o_progresso(self, manager):
        assert manager._get_status_info(2, 3)["text"] == "Processando"

    def test_codigos_em_texto_sao_aceites(self, manager):
        # Consoante a versão do Seerr, os códigos podem chegar como string.
        assert manager._get_status_info("2", "3")["text"] == "Processando"

    def test_sem_pedido_usa_o_estado_da_media(self, manager):
        assert manager._get_status_info(None, 3)["text"] == "Processando"

    def test_sem_informacao_nenhuma(self, manager):
        assert manager._get_status_info(None, None)["text"] == "Desconhecido"

    def test_codigos_desconhecidos(self, manager):
        assert manager._get_status_info(99, 99)["text"] == "Desconhecido"


class TestWebhook:
    @pytest.fixture()
    def extensoes(self, monkeypatch):
        """Substitui os gestores globais usados pelo tratamento do webhook."""
        from app import extensions
        from tests.conftest import FakeDataManager

        class NotifierEspiao:
            def __init__(self):
                self.enviadas = []

            def send_media_request_notification(self, perfil, dados):
                self.enviadas.append((perfil, dados))

        dados = FakeDataManager(profiles={
            1: {"plex_user_id": 1, "username": "ana", "email": "ana@exemplo.com"},
        })
        notifier = NotifierEspiao()
        monkeypatch.setattr(extensions, "data_manager", dados)
        monkeypatch.setattr(extensions, "notifier_manager", notifier)
        return dados, notifier

    def _payload(self, **extra):
        payload = {
            "notification_type": "MEDIA_APPROVED",
            "subject": "Duna (2021)",
            "message": "Um jovem enfrenta o seu destino.",
            "image": "https://img/duna.jpg",
            "media": {"media_type": "movie", "tmdbId": 438631, "status": "APPROVED"},
            "request": {"requestedBy_email": "ana@exemplo.com", "requestedBy_username": "ana"},
        }
        payload.update(extra)
        return payload

    def test_notificacao_de_teste_do_seerr(self, manager, extensoes):
        # O botão "Testar" não envia 'request' nem 'media'.
        resultado = manager.handle_notification_webhook({"notification_type": "TEST_NOTIFICATION"})

        assert resultado["success"] is True

    def test_encaminha_para_o_utilizador_do_painel(self, manager, extensoes):
        _dados, notifier = extensoes

        resultado = manager.handle_notification_webhook(self._payload())

        assert resultado["success"] is True
        perfil, conteudo = notifier.enviadas[0]
        assert perfil["username"] == "ana"
        assert conteudo["title"] == "Duna (2021)"
        assert conteudo["media_url"] == "https://seerr.exemplo.com/movie/438631"
        assert conteudo["notification_type"] == "MEDIA_APPROVED"

    def test_sem_email_nao_da_para_identificar(self, manager, extensoes):
        payload = self._payload(request={"requestedBy_username": "ana"})

        assert manager.handle_notification_webhook(payload)["success"] is False

    def test_email_desconhecido_e_ignorado_com_sucesso(self, manager, extensoes):
        _dados, notifier = extensoes
        payload = self._payload(request={"requestedBy_email": "outro@exemplo.com"})

        resultado = manager.handle_notification_webhook(payload)

        # Devolve sucesso para o Seerr não repetir a notificação indefinidamente.
        assert resultado["success"] is True
        assert notifier.enviadas == []

    def test_sem_tmdb_id_o_link_fica_vazio(self, manager, extensoes):
        _dados, notifier = extensoes
        payload = self._payload(media={"media_type": "movie"})

        manager.handle_notification_webhook(payload)

        assert notifier.enviadas[0][1]["media_url"] == ""

    def test_falha_a_notificar_nao_rebenta(self, manager, extensoes, monkeypatch):
        _dados, notifier = extensoes

        def rebenta(perfil, dados):
            raise RuntimeError("telegram offline")

        monkeypatch.setattr(notifier, "send_media_request_notification", rebenta)

        assert manager.handle_notification_webhook(self._payload())["success"] is False
