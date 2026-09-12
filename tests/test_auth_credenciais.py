# tests/test_auth_credenciais.py
"""Login com utilizador e palavra-passe, para servidores de contas locais.

O Plex delega a autenticação ao plex.tv (fluxo de PIN); o Jellyfin tem contas
locais e valida a palavra-passe ele próprio. O que muda é SÓ isso — tudo o que
vem depois (é o administrador? tem acesso? o perfil está ativo?) é partilhado em
`_autorizar_e_iniciar_sessao`, e estes testes verificam as duas pontas.
"""

import pytest

from app.services.media_server.base import OwnerAccount

pytestmark = pytest.mark.integration

GUID = "38c3a1f0e4b24d7f9c1a0b5e6d7f8a90"


class BackendFalso:
    """Um servidor de contas locais que aceita um único par de credenciais."""

    SERVER_TYPE = 'jellyfin'
    DISPLAY_NAME = 'Jellyfin'

    def __init__(self, credenciais=("ana", "segredo"), utilizadores=None, cria_contas=True):
        from app.services.media_server.base import MediaServerCapabilities

        self._credenciais = credenciais
        self._utilizadores = utilizadores if utilizadores is not None else [{"id": GUID, "username": "ana"}]
        self.tentativas = []
        self.CAPABILITIES = MediaServerCapabilities(
            convites_nativos=False, cria_contas=cria_contas, fontes_media_online=False,
            login_delegado=False, desativa_conta=True, links_profundos=True,
        )

    @property
    def capabilities(self):
        return self.CAPABILITIES

    def authenticate(self, username, password):
        self.tentativas.append((username, password))
        if (username, password) != self._credenciais:
            return None
        return OwnerAccount(id=GUID, username=username, email=None, thumb=None)

    def get_all_users(self, force_refresh=False):
        return list(self._utilizadores)


@pytest.fixture()
def jellyfin(monkeypatch):
    """Substitui o backend nos módulos que o guardaram por valor."""
    from app import extensions
    from app.blueprints import auth as auth_module
    from app.blueprints.api import invites as invites_module

    backend = BackendFalso()
    # Em produção são todos o MESMO objeto: os blueprints importaram-no por
    # valor no arranque e o contexto dos templates lê-o do módulo `extensions`.
    # Um duplo que só substitua um dos sítios testa meia aplicação.
    monkeypatch.setattr(extensions, 'media_server', backend)
    monkeypatch.setattr(auth_module, 'media_server', backend)
    monkeypatch.setattr(invites_module, 'media_server', backend)
    return backend


@pytest.fixture(autouse=True)
def sem_rate_limit():
    """A rota de login é limitada a 10/minuto: sem repor, os últimos testes levavam 429."""
    from app.extensions import limiter

    limiter.reset()
    yield
    limiter.reset()


class TestLoginComCredenciais:
    def test_credenciais_certas_abrem_sessao(self, client, config_file, jellyfin, db_session, data_manager):
        config_file(IS_CONFIGURED=True, ADMIN_USER="")
        data_manager.set_user_profile(GUID, {"username": "ana", "status": "active"})

        resposta = client.post('/auth/login/credentials',
                               json={"username": "ana", "password": "segredo"})

        assert resposta.get_json()["success"] is True
        with client.session_transaction() as sessao:
            assert sessao["user_details"]["id"] == GUID

    def test_o_administrador_entra_como_administrador(self, client, config_file, jellyfin, db_session):
        config_file(IS_CONFIGURED=True, ADMIN_USER="ana")

        resposta = client.post('/auth/login/credentials',
                               json={"username": "ana", "password": "segredo"})

        assert resposta.get_json()["success"] is True
        with client.session_transaction() as sessao:
            assert sessao["user_details"]["role"] == "admin"

    def test_palavra_passe_errada_e_recusada(self, client, config_file, jellyfin, db_session):
        config_file(IS_CONFIGURED=True)

        resposta = client.post('/auth/login/credentials',
                               json={"username": "ana", "password": "errada"})

        assert resposta.status_code == 401
        assert resposta.get_json()["success"] is False

    def test_utilizador_inexistente_da_a_mesma_mensagem(self, client, config_file, jellyfin, db_session):
        # 🔒 Distinguir "não existe" de "palavra-passe errada" diz a quem tenta
        # adivinhar quais as contas que existem neste servidor.
        config_file(IS_CONFIGURED=True)

        errada = client.post('/auth/login/credentials', json={"username": "ana", "password": "x"})
        inexistente = client.post('/auth/login/credentials', json={"username": "ninguem", "password": "x"})

        assert errada.get_json()["message"] == inexistente.get_json()["message"]

    @pytest.mark.parametrize("corpo", [
        {"username": "", "password": "segredo"},
        {"username": "ana", "password": ""},
        {},
    ])
    def test_credenciais_incompletas_nao_chegam_ao_servidor(self, client, config_file, jellyfin, db_session, corpo):
        config_file(IS_CONFIGURED=True)

        resposta = client.post('/auth/login/credentials', json=corpo)

        assert resposta.status_code == 400
        assert jellyfin.tentativas == []

    def test_num_servidor_de_login_delegado_a_rota_recusa(self, client, config_file, db_session):
        # 🔒 No Plex, aceitar credenciais aqui seria pedir a palavra-passe da
        # conta plex.tv a quem entra — exatamente o que o fluxo de PIN evita.
        config_file(IS_CONFIGURED=True)

        resposta = client.post('/auth/login/credentials',
                               json={"username": "ana", "password": "segredo"})

        assert resposta.status_code == 400
        assert "externa" in resposta.get_json()["message"]


class TestPaginaDeLogin:
    def test_servidor_de_contas_locais_mostra_o_formulario(self, client, config_file, jellyfin):
        config_file(IS_CONFIGURED=True)

        html = client.get('/auth/login').get_data(as_text=True)

        assert 'id="credentials-form"' in html
        assert 'id="login-button"' not in html

    def test_servidor_de_login_delegado_mostra_o_botao(self, client, config_file):
        config_file(IS_CONFIGURED=True)

        html = client.get('/auth/login').get_data(as_text=True)

        assert 'id="login-button"' in html
        assert 'id="credentials-form"' not in html


class TestResgateComCredenciais:
    def _convite(self, data_manager, **extra):
        detalhes = {
            "libraries": ["Filmes"], "screen_limit": 1, "allow_downloads": False,
            "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None,
            "trial_duration_minutes": 0, "overseerr_access": False,
            "max_uses": 1, "use_count": 0, "claimed_by_users": [], "telegram_id": None,
        }
        detalhes.update(extra)
        data_manager.add_invitation("CODIGO", detalhes)
        return "CODIGO"

    def test_o_registo_cria_a_conta(self, client, config_file, jellyfin, data_manager, monkeypatch):
        config_file(IS_CONFIGURED=True)
        codigo = self._convite(data_manager)
        recebidos = {}
        jellyfin.claim_invitation = lambda code, conta: recebidos.update(
            code=code, username=conta.username, password=conta.password, email=conta.email
        ) or {"success": True, "user_data": {}}

        resposta = client.post('/api/invites/claim', json={
            "code": codigo, "username": "bruno", "password": "segredo123", "email": "b@x.pt",
        })

        assert resposta.get_json()["success"] is True
        assert recebidos == {"code": codigo, "username": "bruno", "password": "segredo123", "email": "b@x.pt"}

    def test_sem_credenciais_e_recusado(self, client, config_file, jellyfin, data_manager):
        config_file(IS_CONFIGURED=True)
        codigo = self._convite(data_manager)

        resposta = client.post('/api/invites/claim', json={"code": codigo, "username": "bruno"})

        assert resposta.status_code == 400

    def test_palavra_passe_curta_e_recusada_antes_de_criar_a_conta(
        self, client, config_file, jellyfin, data_manager
    ):
        # A validação também existe no browser, mas quem chama a API diretamente
        # não passa por ela — e uma conta com palavra-passe fraca fica no
        # servidor para sempre.
        config_file(IS_CONFIGURED=True)
        codigo = self._convite(data_manager)
        criadas = []
        jellyfin.claim_invitation = lambda code, conta: criadas.append(conta) or {"success": True}

        resposta = client.post('/api/invites/claim', json={
            "code": codigo, "username": "bruno", "password": "123",
        })

        assert resposta.status_code == 400
        assert criadas == []
