# tests/test_api_decorators.py
"""Decoradores da API: validação do corpo JSON e procura do utilizador."""

import pytest

from app.blueprints.api import decorators as decorators_module
from app.blueprints.api.decorators import user_lookup_by_id, validate_json
from app.blueprints.api.schemas import CreateInviteSchema

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


class PlexManagerFalso:
    def __init__(self, utilizadores=None):
        self.utilizadores = utilizadores or {}

    def get_user_by_id(self, plex_user_id):
        return self.utilizadores.get(plex_user_id)


@user_lookup_by_id
def rota_com_utilizador(user):
    return {"username": user["username"]}


class TestUserLookupById:
    @pytest.fixture(autouse=True)
    def plex_manager(self, monkeypatch):
        gestor = PlexManagerFalso({42: {"id": 42, "username": "ana"}})
        monkeypatch.setattr(decorators_module, "plex_manager", gestor)
        return gestor

    def test_utilizador_injetado_a_partir_do_url(self, app):
        with app.test_request_context():
            assert rota_com_utilizador(plex_user_id=42) == {"username": "ana"}

    def test_utilizador_injetado_a_partir_do_corpo(self, app):
        with app.test_request_context(json={"plex_user_id": 42}):
            assert rota_com_utilizador() == {"username": "ana"}

    def test_id_em_texto_e_convertido(self, app):
        with app.test_request_context():
            assert rota_com_utilizador(plex_user_id="42") == {"username": "ana"}

    def test_id_em_falta(self, app):
        with app.test_request_context(json={}):
            _resposta, codigo = rota_com_utilizador()

        assert codigo == 400

    def test_id_invalido(self, app):
        with app.test_request_context():
            _resposta, codigo = rota_com_utilizador(plex_user_id="abc")

        assert codigo == 400

    def test_utilizador_inexistente(self, app):
        with app.test_request_context():
            resposta, codigo = rota_com_utilizador(plex_user_id=999)

        assert codigo == 404
        assert resposta.get_json()["success"] is False
