# tests/test_api_decorators.py
"""Decoradores da API: validação do corpo JSON e procura do utilizador."""

import pytest

from app.blueprints.api import decorators as decorators_module
from app.blueprints.api.decorators import user_lookup_by_id, validate_json
from app.blueprints.api.schemas import CreateInviteSchema
from app.utils.identity import normalize_user_id

pytestmark = pytest.mark.integration


@validate_json(CreateInviteSchema)
def rota_protegida(validated_data):
    return {"ok": True, "screens": validated_data.screens}


class TestValidateJson:
    def test_json_valido_chega_ja_convertido_a_rota(self, app):
        with app.test_request_context(json={"libraries": ["Filmes"], "screens": 2}):
            assert rota_protegida() == {"ok": True, "screens": 2}

    def test_corpo_vazio(self, app):
        with app.test_request_context(json={}):
            resposta, codigo = rota_protegida()

        assert codigo == 400
        assert resposta.get_json()["success"] is False

    def test_erros_de_validacao_sao_devolvidos_por_campo(self, app):
        with app.test_request_context(json={"libraries": [], "screens": 99}):
            resposta, codigo = rota_protegida()

        corpo = resposta.get_json()
        assert codigo == 400
        assert corpo["success"] is False
        assert set(corpo["errors"]) >= {"libraries", "screens"}

    def test_json_mal_formado(self, app):
        from werkzeug.exceptions import BadRequest

        # O próprio Flask rejeita o corpo antes do decorador — o cliente recebe
        # na mesma um 400, mas através do tratamento de erros do framework.
        with app.test_request_context(data="{ isto nao e json", content_type="application/json"):
            with pytest.raises(BadRequest):
                rota_protegida()


class BackendFalso:
    def __init__(self, utilizadores=None):
        # As chaves são normalizadas como o backend real faz: o
        # PlexUserManager compara `str(u['id']) == str(id_pedido)`, por isso um
        # duplo que só case por inteiro mentiria sobre o comportamento real.
        self.utilizadores = {
            normalize_user_id(chave): valor for chave, valor in (utilizadores or {}).items()
        }

    def get_user_by_id(self, user_id):
        return self.utilizadores.get(normalize_user_id(user_id))


@user_lookup_by_id
def rota_com_utilizador(user):
    return {"username": user["username"]}


class TestUserLookupById:
    @pytest.fixture(autouse=True)
    def media_server(self, monkeypatch):
        gestor = BackendFalso({42: {"id": 42, "username": "ana"}})
        monkeypatch.setattr(decorators_module, "media_server", gestor)
        return gestor

    def test_utilizador_injetado_a_partir_do_url(self, app):
        with app.test_request_context():
            assert rota_com_utilizador(media_user_id=42) == {"username": "ana"}

    def test_utilizador_injetado_a_partir_do_corpo(self, app):
        with app.test_request_context(json={"media_user_id": 42}):
            assert rota_com_utilizador() == {"username": "ana"}

    def test_id_em_texto_e_o_formato_nativo(self, app):
        # A identidade é texto: o 42 inteiro e o "42" em texto são o mesmo
        # utilizador, venham de onde vierem.
        with app.test_request_context():
            assert rota_com_utilizador(media_user_id="42") == {"username": "ana"}

    def test_id_nao_numerico_e_valido(self, app):
        # Um GUID do Jellyfin não é um número. Antes, o decorador rejeitava-o
        # com 400 ("ID inválido") antes sequer de procurar o utilizador.
        gestor = BackendFalso({"38c3a1f0e4b2": {"id": "38c3a1f0e4b2", "username": "bruno"}})
        with app.test_request_context():
            import app.blueprints.api.decorators as mod
            original, mod.media_server = mod.media_server, gestor
            try:
                assert rota_com_utilizador(media_user_id="38c3a1f0e4b2") == {"username": "bruno"}
            finally:
                mod.media_server = original

    def test_id_em_falta(self, app):
        with app.test_request_context(json={}):
            _resposta, codigo = rota_com_utilizador()

        assert codigo == 400

    def test_id_so_com_espacos_e_recusado(self, app):
        # O que torna um ID inválido passou a ser estar vazio, não ser
        # não-numérico.
        with app.test_request_context():
            _resposta, codigo = rota_com_utilizador(media_user_id="   ")

        assert codigo == 400

    def test_utilizador_inexistente(self, app):
        with app.test_request_context():
            resposta, codigo = rota_com_utilizador(media_user_id=999)

        assert codigo == 404
        assert resposta.get_json()["success"] is False


@pytest.mark.integration
class TestPedidoSemCorpoJson:
    """
    🐛 REGRESSÃO REAL, reportada de um servidor Jellyfin em produção:

        GET /api/users/libraries/44874bdd... 400 (BAD REQUEST)
        SyntaxError: Unexpected token '<', "<!doctype "... is not valid JSON

    O `fetchAPI` do painel envia SEMPRE 'Content-Type: application/json', mesmo
    num GET sem corpo. `request.json` levanta 400 — com uma página de erro em
    HTML, daí o "<!doctype" onde o JS esperava JSON — quando o pedido se diz
    JSON e vem vazio.

    Isto funcionava porque o acesso ao corpo estava do lado DIREITO de um `or`,
    que o Python não avalia quando o ID já veio no URL. Ao passar a ler o corpo
    antes da cadeia, todas as rotas GET com este decorador partiram de uma vez.
    """

    GUID = "44874bddd6264037b45f671fa20ca85b"

    @pytest.fixture()
    def backend(self, monkeypatch):
        gestor = BackendFalso({self.GUID: {"id": self.GUID, "username": "ana"}})
        monkeypatch.setattr(decorators_module, "media_server", gestor)
        return gestor

    def test_get_com_cabecalho_json_e_sem_corpo(self, app, backend):
        # É exatamente o que o browser envia ao abrir a gestão de bibliotecas.
        with app.test_request_context(
            f"/api/users/libraries/{self.GUID}",
            method="GET",
            headers={"Content-Type": "application/json"},
        ):
            assert rota_com_utilizador(media_user_id=self.GUID) == {"username": "ana"}

    def test_post_com_cabecalho_json_e_sem_corpo(self, app, backend):
        # O mesmo padrão existe em POSTs que não enviam corpo nenhum.
        with app.test_request_context(
            "/api/users/block", method="POST", headers={"Content-Type": "application/json"},
        ):
            assert rota_com_utilizador(media_user_id=self.GUID) == {"username": "ana"}

    def test_corpo_json_mal_formado_nao_rebenta(self, app, backend):
        # Um corpo inválido não deve dar 400 em HTML quando o ID veio no URL.
        with app.test_request_context(
            f"/api/users/libraries/{self.GUID}", method="POST",
            data="{ isto nao e json", content_type="application/json",
        ):
            assert rota_com_utilizador(media_user_id=self.GUID) == {"username": "ana"}

    def test_corpo_json_que_nao_e_um_objeto(self, app, backend):
        # `_do_corpo` protege contra um corpo que seja uma lista ou um número.
        with app.test_request_context("/api/users/block", method="POST", json=[1, 2, 3]):
            _resposta, codigo = rota_com_utilizador()

        assert codigo == 400

    def test_o_id_continua_a_poder_vir_no_corpo(self, app, backend):
        # A leitura do corpo tem de continuar a funcionar onde é mesmo precisa.
        with app.test_request_context("/api/users/block", method="POST",
                                      json={"media_user_id": self.GUID}):
            assert rota_com_utilizador() == {"username": "ana"}
