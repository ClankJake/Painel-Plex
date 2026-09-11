# tests/test_setup_wizard.py
"""
Testes do assistente de instalação (`setup.html` + `/api/system/setup/save`).

O ponto crítico deste fluxo é o que acontece QUANDO ELE TERMINA: além de gravar
o config.json, tem de deixar a aplicação inteira a funcionar — administrador
autenticado com a identidade certa e todas as tarefas de fundo a correr — sem
depender de um reinício manual do serviço.
"""

import json

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def sem_rate_limit():
    """
    `/setup/save` tem um limite de 10 pedidos por hora, partilhado por todo o
    processo de testes. Sem o repor, os últimos testes do ficheiro receberiam 429
    em vez de exercitarem a rota.
    """
    from app.extensions import limiter

    limiter.reset()
    yield
    limiter.reset()


class ContaPlexFalsa:
    """Substituto do MyPlexAccount devolvido por uma ligação bem sucedida."""

    def __init__(self, id="4242", username="dono", email="dono@exemplo.pt", thumb="http://thumb"):
        self.id = id
        self.username = username
        self.email = email
        self.thumb = thumb


@pytest.fixture()
def plex_ligado(monkeypatch):
    """Finge um servidor Plex acessível e devolve a conta do administrador."""
    from app import extensions

    conta = ContaPlexFalsa()
    monkeypatch.setattr(
        extensions.media_server, "reload_connections", lambda *a, **k: (True, "ok"), raising=False
    )
    # 🐛 Isto criava `media_server.account`, um atributo que a fachada DEIXOU DE
    # TER quando o proxy de imagens parou de manusear objetos da plexapi. O
    # assistente lia esse atributo, em produção encontrava None e caía no ramo
    # de recurso — que grava o USERNAME como identificador da sessão e faz
    # exatamente o que este teste existe para impedir. Como a fixture o criava,
    # o teste passava e a regressão ficou escondida.
    # Passa a preencher o que a produção realmente lê.
    monkeypatch.setattr(extensions.media_server.conn, "account", conta, raising=False)
    return conta


@pytest.fixture()
def plex_offline(monkeypatch):
    """Finge um servidor Plex inacessível."""
    from app import extensions

    monkeypatch.setattr(
        extensions.media_server,
        "reload_connections",
        lambda *a, **k: (False, "sem rede"),
        raising=False,
    )


@pytest.fixture()
def sem_arrancar_servicos(monkeypatch):
    """
    Impede o arranque real do APScheduler (que escreveria no jobstore e ficaria a
    correr entre testes) e regista se ele teria sido pedido.
    """
    import app as app_package

    chamadas = []
    monkeypatch.setattr(
        app_package, "start_background_services", lambda app: chamadas.append(app) or True
    )
    return chamadas


def _config_atual():
    from app import config as config_module

    with open(config_module.CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


PEDIDO_VALIDO = {
    "plex_url": "http://plex:32400",
    "plex_token": "token-do-plex",
    "admin_user": "dono",
    "APP_TITLE": "Meu Painel",
}


class TestValidacaoDoPedido:
    """Um pedido incompleto não pode sujar o config.json nem rebentar com 500."""

    def test_corpo_sem_json_e_recusado(self, client, config_file):
        config_file(IS_CONFIGURED=False)

        resposta = client.post(
            "/api/system/setup/save", data="isto-nao-e-json", content_type="text/plain"
        )

        assert resposta.status_code == 400
        assert resposta.get_json()["success"] is False

    @pytest.mark.parametrize("campo", ["plex_url", "plex_token", "admin_user"])
    def test_campos_obrigatorios_em_falta(self, client, config_file, campo):
        config_file(IS_CONFIGURED=False)
        pedido = dict(PEDIDO_VALIDO)
        pedido[campo] = ""

        resposta = client.post("/api/system/setup/save", json=pedido)

        assert resposta.status_code == 400
        # E o sistema continua por configurar: nada foi gravado a meio.
        assert _config_atual().get("IS_CONFIGURED") is not True

    def test_pedido_incompleto_nao_marca_como_configurado(self, client, config_file):
        config_file(IS_CONFIGURED=False)

        client.post("/api/system/setup/save", json={})

        assert _config_atual().get("IS_CONFIGURED") is not True


class TestConclusaoDaInstalacao:
    def test_grava_a_configuracao_e_autentica_o_administrador(
        self, client, config_file, plex_ligado, sem_arrancar_servicos
    ):
        config_file(IS_CONFIGURED=False)

        resposta = client.post("/api/system/setup/save", json=PEDIDO_VALIDO)

        assert resposta.status_code == 200
        dados = resposta.get_json()
        assert dados["success"] is True
        assert dados["redirect_url"] == "/"

        config = _config_atual()
        assert config["IS_CONFIGURED"] is True
        assert config["PLEX_URL"] == "http://plex:32400"
        assert config["ADMIN_USER"] == "dono"

    def test_a_sessao_usa_o_id_plex_e_nao_o_username(
        self, client, config_file, plex_ligado, sem_arrancar_servicos
    ):
        """
        🐛 O assistente gravava o USERNAME no campo 'id' da sessão, ao contrário do
        login normal (que usa o ID numérico da conta Plex). Numa reconfiguração,
        onde o ADMIN_USER_ID já está preenchido, a revalidação por ID falhava e o
        administrador era expulso para o login logo a seguir a concluir.
        """
        config_file(IS_CONFIGURED=False)

        client.post("/api/system/setup/save", json=PEDIDO_VALIDO)

        with client.session_transaction() as sessao:
            detalhes = sessao["user_details"]
            assert detalhes["id"] == "4242"
            assert detalhes["username"] == "dono"
            assert detalhes["email"] == "dono@exemplo.pt"
            assert detalhes["role"] == "admin"
            # Sessão permanente, como no login normal (senão expirava ao fechar o browser).
            assert sessao.permanent is True

    def test_o_token_plex_do_assistente_nao_fica_na_sessao(
        self, client, config_file, plex_ligado, sem_arrancar_servicos
    ):
        config_file(IS_CONFIGURED=False)

        client.post("/api/system/setup/save", json=PEDIDO_VALIDO)

        with client.session_transaction() as sessao:
            assert "plex_token" not in sessao
            assert "plex_username" not in sessao

    def test_o_id_do_administrador_fica_registado(
        self, client, config_file, plex_ligado, sem_arrancar_servicos
    ):
        """
        Sem o ADMIN_USER_ID, o 'removal_job' e a revalidação da sessão dependem
        apenas do username — que o Plex permite trocar — durante os 30 dias de
        validade da sessão criada aqui.
        """
        config_file(IS_CONFIGURED=False)

        client.post("/api/system/setup/save", json=PEDIDO_VALIDO)

        assert _config_atual()["ADMIN_USER_ID"] == "4242"

    def test_o_painel_fica_mesmo_acessivel_no_fim(
        self, client, config_file, plex_ligado, sem_arrancar_servicos
    ):
        """O teste que reproduz a queixa: concluir o assistente e cair no login."""
        config_file(IS_CONFIGURED=False, ADMIN_USER="dono", ADMIN_USER_ID="4242")

        client.post("/api/system/setup/save", json=PEDIDO_VALIDO)

        assert client.get("/").status_code == 200

    def test_arranca_os_servicos_de_fundo(
        self, client, config_file, plex_ligado, sem_arrancar_servicos
    ):
        """
        🐛 O agendador só era iniciado no `create_app()`, e apenas com a aplicação
        já configurada. Numa instalação nova ficava tudo parado (controlo de telas,
        avisos de vencimento, remoções, limpezas, backups) até ao reinício seguinte.
        """
        config_file(IS_CONFIGURED=False)

        client.post("/api/system/setup/save", json=PEDIDO_VALIDO)

        assert len(sem_arrancar_servicos) == 1

    def test_falha_de_ligacao_nao_deixa_o_sistema_meio_configurado(
        self, client, config_file, plex_offline, sem_arrancar_servicos
    ):
        config_file(IS_CONFIGURED=False)

        resposta = client.post("/api/system/setup/save", json=PEDIDO_VALIDO)

        assert resposta.get_json()["success"] is False
        assert _config_atual()["IS_CONFIGURED"] is False
        assert sem_arrancar_servicos == []
        with client.session_transaction() as sessao:
            assert "user_details" not in sessao


class TestReconfiguracao:
    """`/setup?force=true` reabre o assistente numa instalação a funcionar."""

    def test_chaves_de_api_em_branco_nao_apagam_as_guardadas(
        self, client, config_file, plex_ligado, sem_arrancar_servicos
    ):
        """
        🐛 O assistente nunca mostra segredos já guardados, por isso reenviava os
        campos vazios — e apagava as chaves do Tautulli e do Seerr de uma
        instalação a funcionar.
        """
        config_file(
            IS_CONFIGURED=False,
            TAUTULLI_API_KEY="chave-tautulli",
            OVERSEERR_API_KEY="chave-seerr",
        )

        pedido = dict(PEDIDO_VALIDO, TAUTULLI_API_KEY="", OVERSEERR_API_KEY="")
        client.post("/api/system/setup/save", json=pedido)

        config = _config_atual()
        assert config["TAUTULLI_API_KEY"] == "chave-tautulli"
        assert config["OVERSEERR_API_KEY"] == "chave-seerr"

    def test_uma_chave_nova_substitui_a_antiga(
        self, client, config_file, plex_ligado, sem_arrancar_servicos
    ):
        config_file(IS_CONFIGURED=False, TAUTULLI_API_KEY="chave-antiga")

        client.post("/api/system/setup/save", json=dict(PEDIDO_VALIDO, TAUTULLI_API_KEY="chave-nova"))

        assert _config_atual()["TAUTULLI_API_KEY"] == "chave-nova"

    def test_trocar_de_administrador_limpa_o_id_antigo(
        self, client, config_file, plex_ligado, sem_arrancar_servicos
    ):
        """O ID guardado é do administrador ANTERIOR: tem de ser substituído."""
        config_file(IS_CONFIGURED=False, ADMIN_USER="antigo", ADMIN_USER_ID="111")

        client.post("/api/system/setup/save", json=PEDIDO_VALIDO)

        # Passa a ser o ID da conta Plex que concluiu o assistente.
        assert _config_atual()["ADMIN_USER_ID"] == "4242"


class TestArranqueDosServicosDeFundo:
    """`start_background_services` é chamado no arranque E no fim do assistente."""

    def test_e_idempotente_com_o_agendador_a_correr(self, app, monkeypatch):
        import app as app_package
        from app import extensions

        chamadas = []
        monkeypatch.setattr(
            app_package, "setup_scheduler", lambda a: chamadas.append(a), raising=False
        )
        monkeypatch.setattr(type(extensions.scheduler), "running", property(lambda self: True))

        assert app_package.start_background_services(app) is True
        assert chamadas == []

    def test_uma_falha_no_agendador_nao_rebenta_o_pedido(self, app, monkeypatch):
        import app as app_package

        def explode(_app):
            raise RuntimeError("jobstore indisponível")

        monkeypatch.setattr(app_package, "setup_scheduler", explode, raising=False)

        assert app_package.start_background_services(app) is False


class TestPaginaDoAssistente:
    """O último passo tem de refletir a configuração em vigor."""

    def test_campos_preenchidos_com_os_valores_atuais(self, client, config_file):
        config_file(
            IS_CONFIGURED=False,
            APP_TITLE="Painel da Casa",
            TAUTULLI_URL="http://tautulli:8181",
            OVERSEERR_URL="http://seerr:5055",
            OVERSEERR_ENABLED=True,
        )

        html = client.get("/setup").get_data(as_text=True)

        assert 'value="Painel da Casa"' in html
        assert 'value="http://tautulli:8181"' in html
        assert 'value="http://seerr:5055"' in html
        assert 'id="overseerr_enabled"' in html and "checked" in html

    def test_segredos_guardados_nunca_vao_para_o_html(self, client, config_file):
        config_file(
            IS_CONFIGURED=False,
            TAUTULLI_API_KEY="segredo-tautulli",
            OVERSEERR_API_KEY="segredo-seerr",
            PLEX_TOKEN="segredo-plex",
            SECRET_KEY="segredo-flask",
        )

        html = client.get("/setup").get_data(as_text=True)

        assert "segredo-tautulli" not in html
        assert "segredo-seerr" not in html
        assert "segredo-plex" not in html
        assert "segredo-flask" not in html


class TestLigacaoAoJellyfin:
    """As rotas que o assistente usa para validar um servidor Jellyfin."""

    def _resposta_falsa(self, monkeypatch, **retorno):
        from app.services.media_server.jellyfin import api_client

        monkeypatch.setattr(api_client, 'test_connection', lambda url, key: retorno)
        return retorno

    def test_testar_ligacao_devolve_o_nome_do_servidor(self, client, config_file, monkeypatch):
        config_file(IS_CONFIGURED=False)
        from app.blueprints.api import system as system_module
        monkeypatch.setattr(
            'app.services.media_server.jellyfin.api_client.test_connection',
            lambda url, key: {"success": True, "message": "Ligado a 'Casa'.", "server_name": "Casa"},
        )

        resposta = client.post('/api/system/test/jellyfin-connection',
                               json={"url": "http://jf:8096", "api_key": "chave"})

        assert resposta.status_code == 200
        assert resposta.get_json()["server_name"] == "Casa"

    def test_sem_url_e_recusado(self, client, config_file):
        config_file(IS_CONFIGURED=False)

        resposta = client.post('/api/system/test/jellyfin-connection', json={"api_key": "chave"})

        assert resposta.status_code == 400

    def test_sem_chave_e_recusado(self, client, config_file):
        config_file(IS_CONFIGURED=False)

        resposta = client.post('/api/system/test/jellyfin-connection', json={"url": "http://jf:8096"})

        assert resposta.status_code == 400

    def test_a_chave_guardada_e_usada_quando_chegam_asteriscos(self, client, config_file, monkeypatch):
        # A interface nunca mostra segredos guardados: envia asteriscos. Sem
        # isto, o botão "Testar" falhava sempre numa instalação a funcionar.
        config_file(IS_CONFIGURED=False, JELLYFIN_API_KEY="chave-real")
        recebidas = []
        monkeypatch.setattr(
            'app.services.media_server.jellyfin.api_client.test_connection',
            lambda url, key: recebidas.append(key) or {"success": True},
        )

        client.post('/api/system/test/jellyfin-connection',
                    json={"url": "http://jf:8096", "api_key": "********"})

        assert recebidas == ["chave-real"]

    def test_listar_contas_para_escolher_o_administrador(self, client, config_file, monkeypatch):
        config_file(IS_CONFIGURED=False)
        monkeypatch.setattr(
            'app.services.media_server.jellyfin.api_client.list_administrators',
            lambda url, key: {"success": True, "users": [
                {"id": "g1", "name": "ana", "is_admin": True},
                {"id": "g2", "name": "bruno", "is_admin": False},
            ]},
        )

        resposta = client.post('/api/system/setup/jellyfin-users',
                               json={"url": "http://jf:8096", "api_key": "chave"})

        assert [u["name"] for u in resposta.get_json()["users"]] == ["ana", "bruno"]

    def test_a_listagem_de_contas_fecha_depois_de_configurado(self, client, config_file):
        # 🔒 Tem de funcionar antes de existir administrador, e só até aí — senão
        # torna-se uma forma de listar utilizadores sem sessão nenhuma.
        config_file(IS_CONFIGURED=True)

        resposta = client.post('/api/system/setup/jellyfin-users',
                               json={"url": "http://jf:8096", "api_key": "chave"})

        assert resposta.status_code == 403


class TestInstalacaoComJellyfin:
    def _pedido(self, **extra):
        pedido = {
            "media_server_type": "jellyfin",
            "jellyfin_url": "http://jf:8096",
            "jellyfin_api_key": "chave",
            "admin_user": "ana",
            "admin_user_id": "g1",
            "APP_TITLE": "Painel",
        }
        pedido.update(extra)
        return pedido

    @pytest.fixture()
    def sem_reiniciar(self, monkeypatch):
        """O fim da instalação reinicia o processo: aqui só se regista que foi pedido."""
        from app.blueprints.api import system as system_module

        pedidos = []
        monkeypatch.setattr(system_module, '_agendar_reinicio', lambda motivo: pedidos.append(motivo))
        return pedidos

    @pytest.fixture()
    def jellyfin_ligado(self, monkeypatch):
        from app.services.media_server import factory
        from app.services.media_server.base import OwnerAccount

        class BackendFalso:
            SERVER_TYPE = 'jellyfin'
            DISPLAY_NAME = 'Jellyfin'

            def reload_connections(self, from_job=False):
                return True, "ok"

            def get_owner_account(self):
                return OwnerAccount(id="g1", username="ana")

        monkeypatch.setitem(factory._BACKENDS, 'jellyfin', lambda **kwargs: BackendFalso())

    def test_faltam_credenciais_do_jellyfin(self, client, config_file):
        # As obrigatoriedades do assistente dependem do servidor escolhido: um
        # pedido Jellyfin não pode ser recusado por falta de token do Plex.
        config_file(IS_CONFIGURED=False)

        resposta = client.post('/api/system/setup/save', json=self._pedido(jellyfin_api_key=""))

        assert resposta.status_code == 400
        assert "Jellyfin" in resposta.get_json()["message"]

    def test_instalacao_completa_guarda_e_pede_reinicio(
        self, client, config_file, jellyfin_ligado, sem_reiniciar
    ):
        # ⚠️ Os blueprints guardam a referência ao backend por valor: trocar de
        # tipo de servidor em memória deixaria metade do painel a falar com o
        # servidor antigo. Por isso a resposta pede um reinício em vez de
        # redirecionar como no caminho normal.
        config_file(IS_CONFIGURED=False)

        resposta = client.post('/api/system/setup/save', json=self._pedido())
        corpo = resposta.get_json()

        assert corpo["success"] is True
        assert corpo["restarting"] is True
        assert len(sem_reiniciar) == 1

        from app.config import load_or_create_config
        config = load_or_create_config()
        assert config["MEDIA_SERVER_TYPE"] == "jellyfin"
        assert config["JELLYFIN_URL"] == "http://jf:8096"
        assert config["IS_CONFIGURED"] is True

    def test_a_sessao_de_administrador_sobrevive_ao_reinicio(
        self, client, config_file, jellyfin_ligado, sem_reiniciar
    ):
        # A sessão vive num cookie: o administrador volta já autenticado.
        config_file(IS_CONFIGURED=False)

        client.post('/api/system/setup/save', json=self._pedido())

        with client.session_transaction() as sessao:
            assert sessao["user_details"]["id"] == "g1"
            assert sessao["user_details"]["role"] == "admin"

    def test_uma_ligacao_falhada_nao_marca_como_configurado(self, client, config_file, monkeypatch):
        from app.services.media_server import factory

        class BackendQueFalha:
            SERVER_TYPE = 'jellyfin'
            DISPLAY_NAME = 'Jellyfin'

            def reload_connections(self, from_job=False):
                return False, "sem rede"

        monkeypatch.setitem(factory._BACKENDS, 'jellyfin', lambda **kwargs: BackendQueFalha())
        config_file(IS_CONFIGURED=False)

        resposta = client.post('/api/system/setup/save', json=self._pedido())

        assert resposta.get_json()["success"] is False
        from app.config import load_or_create_config
        assert load_or_create_config()["IS_CONFIGURED"] is False
