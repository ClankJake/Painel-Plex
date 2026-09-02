# tests/test_app.py
"""Testes de fumo da aplicação: arranque, rotas públicas e proteção de acesso."""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def configurada(config_file):
    """Marca a aplicação como já configurada (sai do assistente de instalação)."""
    return config_file(IS_CONFIGURED=True)


class TestCriacaoDaAplicacao:
    def test_blueprints_registados(self, app):
        esperados = {
            "main", "auth", "redirect", "image", "system_api", "users_api",
            "invites_api", "payments_api", "stats_api", "notifications_api", "coupons_api",
        }

        assert esperados <= set(app.blueprints)

    def test_extensoes_e_servicos_inicializados(self, app):
        from app import extensions

        assert extensions.data_manager is not None
        assert extensions.pricing_manager is not None
        assert extensions.plex_manager is not None
        assert extensions.referral_manager is not None
        # Injeção tardia: o ReferralManager precisa do SubscriptionManager.
        assert extensions.referral_manager.subscription_manager is not None

    def test_base_de_dados_e_cache_ficam_no_diretorio_de_configuracao(self, app, config_dir):
        assert str(config_dir) in app.config["SQLALCHEMY_DATABASE_URI"]
        assert str(config_dir) in app.config["CACHE_DIR"]

    def test_idiomas_disponiveis(self, app):
        assert set(app.config["LANGUAGES"]) == {"pt_BR", "en"}
        assert app.config["BABEL_DEFAULT_LOCALE"] == "pt_BR"


class TestAssistenteDeInstalacao:
    def test_sem_configuracao_tudo_e_encaminhado_para_o_setup(self, client):
        resposta = client.get("/")

        assert resposta.status_code == 302
        assert resposta.headers["Location"].endswith("/setup")

    def test_a_pagina_de_setup_esta_acessivel(self, client):
        assert client.get("/setup").status_code == 200

    def test_depois_de_configurado_o_setup_reencaminha_para_o_login(self, client, configurada):
        resposta = client.get("/setup")

        assert resposta.status_code == 302
        assert "/auth/login" in resposta.headers["Location"]

    def test_force_sem_admin_nao_permite_reconfigurar(self, client, configurada):
        # Sem esta proteção, qualquer visitante podia reabrir o assistente.
        resposta = client.get("/setup?force=true")

        assert resposta.status_code == 302
        assert "/auth/login" in resposta.headers["Location"]


class TestRotasPublicas:
    def test_manifest_disponivel_mesmo_sem_configuracao(self, client):
        assert client.get("/manifest.json").status_code == 200

    def test_service_worker(self, client):
        resposta = client.get("/service-worker.js")

        assert resposta.status_code == 200
        assert "javascript" in resposta.headers["Content-Type"]

    def test_pagina_de_login(self, client, configurada):
        assert client.get("/auth/login").status_code == 200

    def test_rota_inexistente(self, client, configurada):
        assert client.get("/nao-existe").status_code == 404


class TestControloDeAcesso:
    def test_dashboard_exige_autenticacao(self, client, configurada):
        resposta = client.get("/")

        assert resposta.status_code == 302
        assert "/auth/login" in resposta.headers["Location"]

    @pytest.mark.parametrize("rota", ["/users", "/settings", "/financial"])
    def test_paginas_de_admin_exigem_autenticacao(self, client, configurada, rota):
        assert client.get(rota).status_code == 302

    def test_api_privada_exige_autenticacao(self, client, configurada):
        assert client.get("/api/users/list").status_code in (302, 401, 403)


class TestIdioma:
    def test_muda_o_idioma_da_sessao(self, client, configurada):
        resposta = client.get("/language/en")

        assert resposta.status_code == 302
        with client.session_transaction() as sessao:
            assert sessao["language"] == "en"

    def test_idioma_desconhecido_e_ignorado(self, client, configurada):
        client.get("/language/klingon")

        with client.session_transaction() as sessao:
            assert "language" not in sessao
