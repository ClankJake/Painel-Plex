# tests/test_auth_security.py
"""
Testes de segurança do sistema de login.

O painel não tem palavra-passe própria: a identidade vem toda do fluxo de PIN do
Plex (`/auth/plex/...`) e do assistente de instalação (`/api/system/setup/save`).
Estes testes cobrem exatamente os pontos onde essa confiança pode ser abusada.
"""

import json

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def configurada(config_file):
    """Marca a aplicação como já configurada (sai do assistente de instalação)."""
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="")


def _obter_contexto_de_auth(client):
    """Inicia um fluxo de autenticação legítimo e devolve o client_id emitido."""
    resposta = client.get("/auth/plex/auth-context")
    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados["success"] is True
    return dados["client_id"]


class TestPinAmarradoASessao:
    """
    Um PIN só pode ser trocado por quem iniciou o fluxo. Caso contrário:
      - um atacante autoriza um PIN com a SUA conta e leva a vítima a abrir o URL
        de verificação, deixando-a autenticada na conta dele (login CSRF);
      - qualquer pessoa usa o painel como proxy para trocar PINs no plex.tv.
    """

    def test_o_contexto_de_auth_guarda_o_client_id_na_sessao(self, client, configurada):
        client_id = _obter_contexto_de_auth(client)

        with client.session_transaction() as sessao:
            assert sessao["plex_auth_client_id"] == client_id
            assert sessao["plex_auth_client_id_issued_at"] > 0

    def test_check_pin_recusa_client_id_de_outra_sessao(self, client, configurada):
        _obter_contexto_de_auth(client)

        resposta = client.get("/auth/plex/check-pin/client-id-do-atacante/424242")

        assert resposta.status_code == 403
        assert resposta.get_json()["success"] is False

    def test_check_pin_recusa_sessao_sem_fluxo_iniciado(self, client, configurada):
        resposta = client.get("/auth/plex/check-pin/qualquer-coisa/424242")

        assert resposta.status_code == 403

    def test_check_pin_for_token_recusa_client_id_de_outra_sessao(self, client, configurada):
        _obter_contexto_de_auth(client)

        resposta = client.get("/auth/plex/check-pin-for-token/client-id-do-atacante/424242")

        assert resposta.status_code == 403

    def test_client_id_expirado_deixa_de_ser_aceite(self, client, configurada, monkeypatch):
        client_id = _obter_contexto_de_auth(client)

        # Recua a emissão para além da validade do PIN do Plex (~15 minutos).
        from app.blueprints import auth as auth_module

        with client.session_transaction() as sessao:
            sessao["plex_auth_client_id_issued_at"] -= auth_module.PLEX_AUTH_CONTEXT_MAX_AGE + 1

        resposta = client.get(f"/auth/plex/check-pin/{client_id}/424242")

        assert resposta.status_code == 403

    def test_client_id_valido_passa_a_verificacao_de_sessao(self, client, configurada, monkeypatch):
        """Com o client_id certo, o pedido segue para a troca real do PIN."""
        from app.blueprints import auth as auth_module

        client_id = _obter_contexto_de_auth(client)
        monkeypatch.setattr(
            auth_module, "_get_pin_status", lambda pin_id, cid: {"authToken": None}
        )

        resposta = client.get(f"/auth/plex/check-pin/{client_id}/424242")

        assert resposta.status_code == 200
        assert resposta.get_json()["message"] == "pending"


class TestAssistenteDeInstalacaoProtegido:
    """
    `/api/system/setup/save` grava o config.json E devolve uma sessão de
    administrador. Depois da instalação concluída tem de ser inacessível a
    visitantes anónimos.
    """

    def test_rejeita_reconfiguracao_anonima_depois_de_configurado(self, client, configurada):
        resposta = client.post(
            "/api/system/setup/save",
            json={"admin_user": "atacante", "plex_url": "http://malicioso", "plex_token": "x"},
        )

        assert resposta.status_code == 403
        assert resposta.get_json()["success"] is False

    def test_a_configuracao_nao_e_alterada_pelo_pedido_recusado(self, client, configurada):
        from app import config as config_module

        client.post("/api/system/setup/save", json={"admin_user": "atacante"})

        config_atual = json.loads(open(config_module.CONFIG_FILE, encoding="utf-8").read())
        assert config_atual["ADMIN_USER"] == "dono"

    def test_o_pedido_recusado_nao_autentica_ninguem(self, client, configurada):
        client.post("/api/system/setup/save", json={"admin_user": "atacante"})

        with client.session_transaction() as sessao:
            assert "user_details" not in sessao
            assert "_user_id" not in sessao

        # E o painel de administração continua fechado.
        assert client.get("/").status_code == 302

    def test_continua_acessivel_enquanto_nao_estiver_configurado(self, client, config_file):
        """O primeiro arranque tem de conseguir concluir a instalação."""
        config_file(IS_CONFIGURED=False)

        resposta = client.post("/api/system/setup/save", json={})

        # Pode falhar por não haver Plex ligado, mas nunca por falta de permissão.
        assert resposta.status_code != 403


class TestSessaoDeAdministrador:
    """O papel 'admin' vive na sessão durante 30 dias: tem de ser revalidado."""

    def _sessao_de_admin(self, client, user_id, username):
        with client.session_transaction() as sessao:
            sessao["user_details"] = {
                "id": user_id, "username": username, "email": None,
                "thumb": None, "role": "admin",
            }
            sessao["_user_id"] = user_id
            sessao["_fresh"] = True

    def test_admin_configurado_mantem_o_acesso(self, client, configurada):
        self._sessao_de_admin(client, "dono", "dono")

        assert client.get("/").status_code == 200

    def test_sessao_e_revogada_quando_o_admin_configurado_muda(self, client, config_file):
        config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="")
        self._sessao_de_admin(client, "antigo-dono", "antigo-dono")

        resposta = client.get("/")

        assert resposta.status_code == 302
        assert "/auth/login" in resposta.headers["Location"]
        with client.session_transaction() as sessao:
            assert "user_details" not in sessao

    def test_sessao_e_revogada_quando_o_id_plex_do_admin_muda(self, client, config_file):
        config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="111")
        # Mesmo username, mas o ID Plex registado é outro: não é o administrador.
        self._sessao_de_admin(client, "999", "dono")

        assert client.get("/").status_code == 302

    def test_id_plex_correto_mantem_o_acesso(self, client, config_file):
        config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="111")
        self._sessao_de_admin(client, "111", "dono")

        assert client.get("/").status_code == 200

    def test_api_recebe_401_em_vez_de_redirecionamento(self, client, config_file):
        config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="111")
        self._sessao_de_admin(client, "999", "dono")

        resposta = client.get("/api/users/list")

        assert resposta.status_code == 401


class TestDecoradorAdminRequired:
    """
    `app.decorators.admin_required` testava um método sem o chamar, pelo que
    nunca bloqueava ninguém.
    """

    def test_visitante_anonimo_e_bloqueado(self, app, client, configurada):
        from app.decorators import admin_required

        @admin_required
        def rota_protegida():
            return "segredo"

        with app.test_request_context("/"):
            from werkzeug.exceptions import HTTPException

            with pytest.raises(HTTPException) as erro:
                rota_protegida()

            assert erro.value.code in (401, 403)

    def test_utilizador_comum_e_bloqueado(self, app, configurada, monkeypatch):
        from werkzeug.exceptions import HTTPException

        from app import decorators
        from app.models import User

        @decorators.admin_required
        def rota_protegida():
            return "segredo"

        comum = User(id="7", username="joao", role="user")
        with app.test_request_context("/"):
            monkeypatch.setattr(decorators, "current_user", comum)

            with pytest.raises(HTTPException) as erro:
                rota_protegida()

            assert erro.value.code == 403

    def test_administrador_passa(self, app, configurada, monkeypatch):
        from app import decorators
        from app.models import User

        @decorators.admin_required
        def rota_protegida():
            return "segredo"

        admin = User(id="1", username="dono", role="admin")
        with app.test_request_context("/"):
            monkeypatch.setattr(decorators, "current_user", admin)

            assert rota_protegida() == "segredo"


class TestCookiesDeSessao:
    def test_cookies_partilham_as_mesmas_defesas(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
        # O cookie "remember me" do Flask-Login é independente e tem de ser
        # configurado explicitamente.
        assert app.config["REMEMBER_COOKIE_HTTPONLY"] is True
        assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"
        assert app.config["REMEMBER_COOKIE_SECURE"] == app.config["SESSION_COOKIE_SECURE"]


class _ContaPlexFalsa:
    """Substituto de `plexapi.myplex.MyPlexAccount` para o fluxo de login."""

    def __init__(self, account_id, username, email=None):
        self.id = account_id
        self.username = username
        self.email = email
        self.thumb = None


@pytest.fixture()
def login_plex_simulado(monkeypatch):
    """
    Faz `check_plex_pin` acreditar que o PIN foi autorizado por uma dada conta,
    sem qualquer chamada ao plex.tv.
    """
    from app.blueprints import auth as auth_module

    def configurar(conta, plex_users=None, perfis=None):
        monkeypatch.setattr(
            auth_module, "_get_pin_status", lambda pin_id, cid: {"authToken": "token-falso"}
        )
        monkeypatch.setattr(auth_module, "MyPlexAccount", lambda token=None: conta)
        monkeypatch.setattr(
            auth_module.plex_manager, "get_all_plex_users", lambda: plex_users or []
        )

        registados = dict(perfis or {})
        monkeypatch.setattr(
            auth_module.data_manager, "get_user_profile",
            lambda uid: registados.get(int(uid)),
        )
        monkeypatch.setattr(
            auth_module.data_manager, "get_user_profile_by_email", lambda email: None
        )
        monkeypatch.setattr(
            auth_module.data_manager, "get_user_profile_by_username", lambda nome: None
        )
        monkeypatch.setattr(
            auth_module.data_manager, "set_user_profile",
            lambda uid, dados: registados.setdefault(int(uid), {}).update(dados),
        )
        return registados

    return configurar


class TestRenovacaoDaSessaoNoLogin:
    """
    Tudo o que existia na sessão ANTES do login é descartado: uma sessão preparada
    por terceiros não pode sobreviver à autenticação (fixação de sessão).
    """

    def test_lixo_de_pre_login_nao_sobrevive_a_autenticacao(
        self, client, configurada, login_plex_simulado
    ):
        conta = _ContaPlexFalsa(55, "maria", "maria@exemplo.pt")
        login_plex_simulado(conta, plex_users=[{"id": 55, "username": "maria"}])

        client_id = _obter_contexto_de_auth(client)
        with client.session_transaction() as sessao:
            sessao["valor_plantado"] = "nao-deve-sobreviver"
            sessao["plex_token"] = "token-de-outro-fluxo"

        resposta = client.get(f"/auth/plex/check-pin/{client_id}/424242")

        assert resposta.get_json()["success"] is True
        with client.session_transaction() as sessao:
            assert "valor_plantado" not in sessao
            assert "plex_token" not in sessao
            # O client_id já usado também não fica para trás.
            assert "plex_auth_client_id" not in sessao
            assert sessao["user_details"]["id"] == "55"

    def test_o_idioma_escolhido_e_preservado(
        self, client, configurada, login_plex_simulado
    ):
        conta = _ContaPlexFalsa(55, "maria", "maria@exemplo.pt")
        login_plex_simulado(conta, plex_users=[{"id": 55, "username": "maria"}])

        client.get("/language/en")
        client_id = _obter_contexto_de_auth(client)
        client.get(f"/auth/plex/check-pin/{client_id}/424242")

        with client.session_transaction() as sessao:
            assert sessao["language"] == "en"


class TestUsernameReutilizadoNoPlex:
    """
    O Plex permite libertar e reutilizar usernames. Quando a entrada da lista de
    amigos tem ID, é esse o único critério de acesso.
    """

    def test_username_igual_com_id_diferente_nao_da_acesso(
        self, client, configurada, login_plex_simulado
    ):
        # 'maria' foi-se embora; outra pessoa registou o mesmo username no Plex.
        conta = _ContaPlexFalsa(999, "maria", "impostora@exemplo.pt")
        login_plex_simulado(conta, plex_users=[{"id": 55, "username": "maria"}])

        client_id = _obter_contexto_de_auth(client)
        resposta = client.get(f"/auth/plex/check-pin/{client_id}/424242")

        dados = resposta.get_json()
        assert dados["success"] is False
        assert dados["message"] == "auth_denied"

    def test_id_correspondente_da_acesso(
        self, client, configurada, login_plex_simulado
    ):
        conta = _ContaPlexFalsa(55, "maria-novo-nome", "maria@exemplo.pt")
        login_plex_simulado(conta, plex_users=[{"id": 55, "username": "maria"}])

        client_id = _obter_contexto_de_auth(client)
        resposta = client.get(f"/auth/plex/check-pin/{client_id}/424242")

        assert resposta.get_json()["success"] is True

    def test_entrada_sem_id_ainda_recorre_ao_username(
        self, client, configurada, login_plex_simulado
    ):
        conta = _ContaPlexFalsa(55, "Maria", "maria@exemplo.pt")
        login_plex_simulado(conta, plex_users=[{"id": None, "username": "maria"}])

        client_id = _obter_contexto_de_auth(client)
        resposta = client.get(f"/auth/plex/check-pin/{client_id}/424242")

        assert resposta.get_json()["success"] is True
