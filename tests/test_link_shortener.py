# tests/test_link_shortener.py
"""Encurtador de links usado nas mensagens de cobrança e convite."""

import pytest

from app.models import ShortLink
from app.services import link_shortener as link_shortener_module
from app.services.link_shortener import LinkShortener

pytestmark = pytest.mark.integration


@pytest.fixture()
def shortener(db_session, monkeypatch):
    """LinkShortener com APP_BASE_URL definido, como numa instalação real."""
    monkeypatch.setattr(
        link_shortener_module,
        "load_or_create_config",
        lambda: {"APP_BASE_URL": "https://painel.exemplo.com/"},
    )
    return LinkShortener()


class TestCreateShortLink:
    def test_devolve_um_link_no_dominio_configurado(self, shortener):
        curto = shortener.create_short_link("https://painel.exemplo.com/pay/token123")

        assert curto.startswith("https://painel.exemplo.com/s/")
        assert ShortLink.query.count() == 1

    def test_a_barra_final_do_dominio_nao_e_duplicada(self, shortener):
        assert "//s/" not in shortener.create_short_link("https://destino.exemplo.com")

    def test_o_codigo_resolve_para_a_url_original(self, shortener):
        original = "https://painel.exemplo.com/pay/token123"
        codigo = shortener.create_short_link(original).rsplit("/", 1)[-1]

        assert shortener.get_original_url(codigo) == original

    def test_codigos_diferentes_para_destinos_diferentes(self, shortener):
        primeiro = shortener.create_short_link("https://exemplo.com/a")
        segundo = shortener.create_short_link("https://exemplo.com/b")

        assert primeiro != segundo
        assert ShortLink.query.count() == 2

    def test_o_link_antigo_do_mesmo_destino_e_substituido(self, shortener):
        # Evita acumular dezenas de códigos para o mesmo link de pagamento.
        destino = "https://painel.exemplo.com/pay/token123"
        antigo = shortener.create_short_link(destino).rsplit("/", 1)[-1]
        novo = shortener.create_short_link(destino).rsplit("/", 1)[-1]

        assert ShortLink.query.count() == 1
        assert shortener.get_original_url(antigo) is None
        assert shortener.get_original_url(novo) == destino

    def test_sem_dominio_configurado_usa_o_url_for(self, db_session, monkeypatch):
        monkeypatch.setattr(
            link_shortener_module, "load_or_create_config", lambda: {"APP_BASE_URL": ""}
        )

        curto = LinkShortener().create_short_link("https://exemplo.com/a")

        assert curto.startswith("http://localhost/s/")

    def test_falha_na_base_de_dados_devolve_a_url_original(self, shortener, monkeypatch):
        from sqlalchemy.exc import SQLAlchemyError

        def rebenta(*args, **kwargs):
            raise SQLAlchemyError("base de dados indisponível")

        monkeypatch.setattr(LinkShortener, "_generate_short_code", rebenta)

        # Nunca se pode devolver um link partido a um utilizador à espera de pagar.
        assert shortener.create_short_link("https://exemplo.com/a") == "https://exemplo.com/a"


class TestGetOriginalUrl:
    def test_codigo_inexistente(self, shortener):
        assert shortener.get_original_url("nao-existe") is None
