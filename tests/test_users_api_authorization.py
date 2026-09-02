# tests/test_users_api_authorization.py
"""
Testes de autorização das rotas de utilizadores.

O painel guarda o histórico de pagamentos de cada pessoa (datas, valores,
provedor). Só o próprio e o administrador o podem ver — e é isso que estes
testes garantem, para que uma regressão na condição de acesso não volte a
expor os dados de uns aos outros.
"""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def configurada(config_file):
    """Marca a aplicação como já configurada (sai do assistente de instalação)."""
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")


def _criar_perfil(plex_user_id):
    """
    Cria o perfil local do utilizador.

    A aplicação encerra a sessão de quem não tem perfil na base de dados
    (`auth.before_request`), por isso um utilizador comum só chega às rotas da
    API se existir mesmo.
    """
    from app.extensions import db
    from app.models import UserProfile

    perfil = UserProfile(
        plex_user_id=plex_user_id,
        username=f"utilizador-{plex_user_id}",
        email=f"utilizador-{plex_user_id}@exemplo.test",
        status="active",
    )
    db.session.add(perfil)
    db.session.commit()
    return perfil


def _autenticar(client, plex_user_id, role):
    """
    Coloca o cliente de teste autenticado como um utilizador concreto.

    O `user_loader` da aplicação reconstrói o utilizador a partir de
    `session['user_details']`, por isso basta semear a sessão — não é preciso
    percorrer todo o fluxo de PIN do Plex.
    """
    with client.session_transaction() as sessao:
        sessao["user_details"] = {
            "id": str(plex_user_id),
            "username": f"utilizador-{plex_user_id}",
            "email": f"utilizador-{plex_user_id}@exemplo.test",
            "role": role,
        }
        sessao["_user_id"] = str(plex_user_id)
        sessao["_fresh"] = True


class TestHistoricoDePagamentos:
    """
    🐛 Regressão: a verificação era `not current_user.is_admin`. Como `is_admin`
    é um MÉTODO, a expressão avaliava o objeto do método (sempre verdadeiro) e a
    condição nunca se cumpria. Qualquer pessoa autenticada conseguia ler o
    histórico de pagamentos de qualquer outra.
    """

    def test_recusa_ver_o_historico_de_outro_utilizador(self, client, configurada, db_session):
        _criar_perfil(111)
        _autenticar(client, plex_user_id=111, role="user")

        resposta = client.get("/api/users/payments/222")

        assert resposta.status_code == 403
        assert resposta.get_json()["success"] is False

    def test_permite_ver_o_proprio_historico(self, client, configurada, db_session):
        _criar_perfil(111)
        _autenticar(client, plex_user_id=111, role="user")

        resposta = client.get("/api/users/payments/111")

        assert resposta.status_code == 200
        assert resposta.get_json()["success"] is True

    def test_o_administrador_ve_o_historico_de_qualquer_um(self, client, configurada, db_session):
        _autenticar(client, plex_user_id=1, role="admin")

        resposta = client.get("/api/users/payments/222")

        assert resposta.status_code == 200
        assert resposta.get_json()["success"] is True

    def test_exige_autenticacao(self, client, configurada, db_session):
        resposta = client.get("/api/users/payments/222")

        assert resposta.status_code in (302, 401)


class TestPaginaDeUtilizadores:
    """
    A página injeta a configuração no `<script id="users-script">` através de
    atributos `data-*`. Se um deles deixar de ser renderizado, o JavaScript
    passa a usar um valor de reserva em silêncio — foi exatamente o que
    aconteceu com o `data-app-base-url`.
    """

    def test_injeta_a_url_base_configurada(self, client, config_file, db_session):
        config_file(
            IS_CONFIGURED=True,
            ADMIN_USER="dono",
            ADMIN_USER_ID="1",
            APP_BASE_URL="https://painel.exemplo.test/",
        )
        _autenticar(client, plex_user_id=1, role="admin")

        pagina = client.get("/users").get_data(as_text=True)

        assert 'data-app-base-url="https://painel.exemplo.test/"' in pagina

    def test_renderiza_as_chaves_de_traducao_usadas_pelo_javascript(self, client, configurada, db_session):
        """
        🐛 Regressão: `i18n.exhausted` era lido pelo `ui.js` para o crachá
        "Esgotado" de um convite, mas a chave nunca era renderizada — o texto
        ficava sempre na versão de reserva, em português, mesmo noutro idioma.
        """
        _autenticar(client, plex_user_id=1, role="admin")

        pagina = client.get("/users").get_data(as_text=True)

        for chave in ("data-i18n-exhausted", "data-i18n-expired-on", "data-i18n-created-at"):
            assert chave in pagina, f"Falta o atributo {chave} na página de utilizadores."
