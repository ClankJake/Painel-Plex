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


def _criar_perfil(media_user_id):
    """
    Cria o perfil local do utilizador.

    A aplicação encerra a sessão de quem não tem perfil na base de dados
    (`auth.before_request`), por isso um utilizador comum só chega às rotas da
    API se existir mesmo.
    """
    from app.extensions import db
    from app.models import UserProfile

    perfil = UserProfile(
        media_user_id=media_user_id,
        username=f"utilizador-{media_user_id}",
        email=f"utilizador-{media_user_id}@exemplo.test",
        status="active",
    )
    db.session.add(perfil)
    db.session.commit()
    return perfil


def _autenticar(client, media_user_id, role):
    """
    Coloca o cliente de teste autenticado como um utilizador concreto.

    O `user_loader` da aplicação reconstrói o utilizador a partir de
    `session['user_details']`, por isso basta semear a sessão — não é preciso
    percorrer todo o fluxo de PIN do Plex.
    """
    with client.session_transaction() as sessao:
        sessao["user_details"] = {
            "id": str(media_user_id),
            "username": f"utilizador-{media_user_id}",
            "email": f"utilizador-{media_user_id}@exemplo.test",
            "role": role,
        }
        sessao["_user_id"] = str(media_user_id)
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
        _autenticar(client, media_user_id=111, role="user")

        resposta = client.get("/api/users/payments/222")

        assert resposta.status_code == 403
        assert resposta.get_json()["success"] is False

    def test_permite_ver_o_proprio_historico(self, client, configurada, db_session):
        _criar_perfil(111)
        _autenticar(client, media_user_id=111, role="user")

        resposta = client.get("/api/users/payments/111")

        assert resposta.status_code == 200
        assert resposta.get_json()["success"] is True

    def test_o_administrador_ve_o_historico_de_qualquer_um(self, client, configurada, db_session):
        _autenticar(client, media_user_id=1, role="admin")

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
        _autenticar(client, media_user_id=1, role="admin")

        pagina = client.get("/users").get_data(as_text=True)

        assert 'data-app-base-url="https://painel.exemplo.test/"' in pagina

    def test_renderiza_as_chaves_de_traducao_usadas_pelo_javascript(self, client, configurada, db_session):
        """
        🐛 Regressão: `i18n.exhausted` era lido pelo `ui.js` para o crachá
        "Esgotado" de um convite, mas a chave nunca era renderizada — o texto
        ficava sempre na versão de reserva, em português, mesmo noutro idioma.
        """
        _autenticar(client, media_user_id=1, role="admin")

        pagina = client.get("/users").get_data(as_text=True)

        for chave in ("data-i18n-exhausted", "data-i18n-expired-on", "data-i18n-created-at"):
            assert chave in pagina, f"Falta o atributo {chave} na página de utilizadores."


class TestLimiteDeTelas:
    """
    🐛 REGRESSÃO: estas rotas escreviam `profile['screen_limit']` diretamente na
    base de dados, sem passar pela fachada. Nos servidores que sabem impor o
    limite (o `MaxActiveSessions` do Jellyfin), o servidor ficava com o valor da
    data do convite para sempre — e essa é a ÚNICA defesa que um reprodutor não
    pode ignorar. Quem mudasse de plano continuava limitado (ou ilimitado) pelo
    valor antigo do lado do servidor.
    """

    class FachadaEspia:
        def __init__(self, utilizadores=None):
            self.limites = []
            self._utilizadores = utilizadores or []

        def update_screen_limit(self, user_id, screens):
            self.limites.append((str(user_id), screens))

        def get_all_users(self):
            return list(self._utilizadores)

        def get_user_by_id(self, user_id):
            return next((u for u in self._utilizadores if str(u['id']) == str(user_id)), None)

    @pytest.fixture()
    def espia(self, monkeypatch):
        from app import extensions
        from app.blueprints.api import decorators as decorators_module

        def instalar(utilizadores=None):
            fachada = self.FachadaEspia(utilizadores)
            # O `user_lookup_by_id` guarda o backend POR VALOR (`from
            # ...extensions import media_server`): sem o substituir também lá, o
            # decorador continua a perguntar ao backend real e a rota nem chega
            # a correr. É a mesma armadilha que obriga a reiniciar o painel ao
            # trocar de servidor.
            monkeypatch.setattr(extensions, "media_server", fachada)
            monkeypatch.setattr(decorators_module, "media_server", fachada)
            return fachada

        return instalar

    def test_alterar_o_limite_de_um_utilizador_passa_pela_fachada(self, client, configurada, db_session, espia):
        _criar_perfil(111)
        fachada = espia([{"id": "111", "username": "utilizador-111"}])
        _autenticar(client, media_user_id=1, role="admin")

        resposta = client.post("/api/users/update-limit", json={"media_user_id": "111", "screens": 3})

        assert resposta.status_code == 200
        assert fachada.limites == [("111", 3)]

    def test_o_limite_global_passa_pela_fachada_para_cada_um(self, client, configurada, db_session, espia):
        _criar_perfil(111)
        _criar_perfil(222)
        fachada = espia([
            {"id": "111", "username": "utilizador-111"},
            {"id": "222", "username": "utilizador-222"},
        ])
        _autenticar(client, media_user_id=1, role="admin")

        resposta = client.post("/api/users/update-all-limits", json={"screens": 2})

        assert resposta.status_code == 200
        assert sorted(fachada.limites) == [("111", 2), ("222", 2)]

    def test_o_limite_global_nao_se_aplica_ao_proprio_administrador(self, client, configurada, db_session, espia):
        _criar_perfil(1)
        _criar_perfil(111)
        fachada = espia([
            {"id": "1", "username": "dono"},
            {"id": "111", "username": "utilizador-111"},
        ])
        _autenticar(client, media_user_id=1, role="admin")

        client.post("/api/users/update-all-limits", json={"screens": 2})

        assert fachada.limites == [("111", 2)]


class TestMinhaContaDoAdministrador:
    """
    🐛 REGRESSÃO REPORTADA (AttributeError em `_get_expiration_details`): o
    administrador abria a "Minha Conta" e a rota rebentava.

    O administrador NÃO TEM PERFIL LOCAL, por desenho: o login dele devolve na
    primeira ramificação de `_autorizar_e_iniciar_sessao`, antes da parte que
    cria perfis, e a sincronização da lista de utilizadores salta-o de
    propósito (`username != admin_username`). No Plex ele nem sequer aparece na
    lista de amigos do servidor. Estas rotas assumiam um dicionário.
    """

    @pytest.fixture(autouse=True)
    def sem_servicos_externos(self, monkeypatch):
        """O Tautulli e o servidor de média não são o que está a ser testado."""
        from app.blueprints.api import users as users_module

        class Vazio:
            def get_user_watch_details(self, **kwargs):
                return {"success": True, "details": {}}

            def get_user_libraries(self, user_id):
                return {"success": True, "libraries": []}

            def get_user_devices(self, user_id):
                return {"success": True, "devices": []}

            def get_watch_history(self, user_id, page=1, length=15, search=""):
                return {"success": True, "history": [],
                        "pagination": {"current_page": 1, "total_pages": 1, "total_records": 0}}

        monkeypatch.setattr(users_module.extensions, "tautulli_manager", Vazio(), raising=False)
        monkeypatch.setattr(users_module.extensions, "media_server", Vazio(), raising=False)

    def test_os_detalhes_da_conta_respondem_sem_perfil(self, client, configurada, db_session):
        _autenticar(client, media_user_id=1, role="admin")

        resposta = client.get("/api/users/account/details")

        assert resposta.status_code == 200
        assert resposta.get_json()["success"] is True

    def test_sem_perfil_o_limite_de_telas_e_ilimitado(self, client, configurada, db_session):
        _autenticar(client, media_user_id=1, role="admin")

        corpo = client.get("/api/users/account/details").get_json()

        assert corpo["expiration_info"]["date"] is None
        assert corpo["is_on_trial"] is False
        assert corpo["profile_details"]["name"] is None

    def test_gravar_o_perfil_cria_um_em_vez_de_rebentar(self, client, configurada, db_session):
        _autenticar(client, media_user_id=1, role="admin")

        resposta = client.post("/api/users/account/profile", json={"name": "Dono"})

        assert resposta.status_code == 200
        from app.extensions import db
        from app.models import UserProfile
        assert db.session.get(UserProfile, "1").name == "Dono"

    def test_a_privacidade_tambem_se_guarda_sem_perfil(self, client, configurada, db_session):
        _autenticar(client, media_user_id=1, role="admin")

        resposta = client.post("/api/users/account/privacy", json={"hide": True})

        assert resposta.status_code == 200
        from app.extensions import db
        from app.models import UserProfile
        assert db.session.get(UserProfile, "1").hide_from_leaderboard is True

    @pytest.mark.parametrize("rota", [
        "/api/users/account/details",
        "/api/payments/options",
        "/api/users/account/devices",
        "/api/users/payments/1",
        "/api/users/referral/me",
        "/api/statistics/user/history",
    ])
    def test_nenhum_pedido_da_pagina_rebenta(self, client, configurada, db_session, rota):
        """
        🐛 REGRESSÃO REPORTADA, segunda volta: corrigir só os `/account/*`
        não chegou. A página faz vários pedidos em paralelo e o `fetchAPI`
        levanta em qualquer resposta que não seja 2xx — um `Promise.all` com um
        deles a falhar derruba o carregamento INTEIRO. Por isso o que se testa é
        a página toda, e não uma rota de cada vez.
        """
        _autenticar(client, media_user_id=1, role="admin")

        resposta = client.get(rota)

        assert resposta.status_code == 200, f"{rota} devolveu {resposta.status_code}"

    def test_o_administrador_nao_tem_planos_para_renovar(self, client, configurada, db_session):
        # Sem assinatura não há nada a comprar — e isso não é um erro do pedido.
        _autenticar(client, media_user_id=1, role="admin")

        corpo = client.get("/api/payments/options").get_json()

        assert corpo["success"] is True
        assert corpo["prices"] == {}

    def test_um_token_de_pagamento_invalido_continua_a_ser_um_erro(self, client, configurada, db_session):
        # A correção não pode transformar um link de pagamento adulterado em
        # "sem planos": isso esconderia o problema de quem o recebeu.
        resposta = client.get("/api/payments/options?token=nao-existe")

        assert resposta.status_code == 400

    def test_quem_tem_perfil_continua_a_usa_lo(self, client, configurada, db_session):
        # A correção não pode passar a ignorar o perfil de quem o tem.
        perfil = _criar_perfil(111)
        perfil.name = "Ana Silva"
        from app.extensions import db
        db.session.commit()
        _autenticar(client, media_user_id=111, role="user")

        corpo = client.get("/api/users/account/details").get_json()

        assert corpo["profile_details"]["name"] == "Ana Silva"
