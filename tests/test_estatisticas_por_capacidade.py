# tests/test_estatisticas_por_capacidade.py
"""As estatísticas escondem-se onde não há de onde as tirar.

Pódio, XP, conquistas, recomendações e o Wrapped saem todos de uma lista de
reproduções. Num painel Plex quem a guarda é o Tautulli, e sem ele não há de
onde a tirar — a regra da casa é que o que falta ESCONDE a funcionalidade,
nunca a mostra partida. (Num painel Jellyfin a lista vem do próprio servidor:
ver `test_jellyfin_estatisticas.py`.)

⚠️ São duas perguntas parecidas e não são a mesma:

- `capabilities.estatisticas` — o servidor PODE tê-las. É o que mantém o cartão
  do Tautulli nas Conexões: escondê-lo a quem ainda não o configurou deixava-o
  sem forma nenhuma de o configurar.
- `estatisticas_disponiveis()` — HÁ agora. É por esta que se escondem o menu,
  as páginas e as sub-abas: um painel Plex sem Tautulli tem a capacidade e não
  tem os dados.

⚠️ O que NÃO se esconde em caso nenhum: o histórico e os aparelhos da página da
conta. O Jellyfin responde a eles sozinho (`jellyfin/history.py`) e o Plex sem
Tautulli também (`plex/history.py`) — é justamente a diferença que estes testes
guardam.
"""

import pytest

from app.services.media_server.base import MediaServerCapabilities

pytestmark = pytest.mark.integration


def _capacidades(estatisticas, externas=None):
    """`externas` é o que separa um painel Plex (Tautulli) de um Jellyfin."""
    return MediaServerCapabilities(
        convites_nativos=not estatisticas, cria_contas=not estatisticas,
        fontes_media_online=estatisticas, login_delegado=estatisticas,
        desativa_conta=not estatisticas, links_profundos=True,
        estatisticas=estatisticas,
        estatisticas_externas=estatisticas if externas is None else externas,
    )


class BackendFalso:
    """O mínimo que a página e o contexto dos templates pedem.

    `suporta` é a capacidade (o servidor pode tê-las); `disponiveis` é o estado
    de agora. Ficam separados de propósito: é a combinação "pode mas não tem"
    — um painel Plex sem Tautulli — que mais custou a acertar.
    """

    def __init__(self, suporta, disponiveis=None, externas=None):
        externo = suporta if externas is None else externas
        self.SERVER_TYPE = 'plex' if externo else 'jellyfin'
        self.DISPLAY_NAME = 'Plex Media Server' if externo else 'Jellyfin'
        self.SHORT_NAME = 'Plex' if externo else 'Jellyfin'
        self.CAPABILITIES = _capacidades(suporta, externas)
        self._disponiveis = suporta if disponiveis is None else disponiveis

    @property
    def capabilities(self):
        return self.CAPABILITIES

    def estatisticas_disponiveis(self):
        return self._disponiveis

    def check_status(self):
        return {"status": "ONLINE", "message": "ok"}

    def get_all_users(self, force_refresh=False):
        return []

    def get_user_devices(self, user_id):
        return {"success": True, "devices": []}

    def get_watch_history(self, user_id, page=1, length=15, search=""):
        return {"success": True, "history": [],
                "pagination": {"current_page": 1, "total_pages": 1, "total_records": 0}}


@pytest.fixture()
def servidor(monkeypatch):
    """Substitui o backend em TODOS os sítios que o guardaram por valor."""
    from app import extensions
    from app.blueprints import main as main_module
    from app.blueprints.api import system as system_module
    from app.blueprints.api import users as users_module

    def instalar(estatisticas, disponiveis=None, externas=None):
        backend = BackendFalso(estatisticas, disponiveis, externas)
        monkeypatch.setattr(extensions, 'media_server', backend)
        monkeypatch.setattr(main_module, 'media_server', backend, raising=False)
        monkeypatch.setattr(system_module, 'media_server', backend, raising=False)
        monkeypatch.setattr(users_module.extensions, 'media_server', backend, raising=False)
        return backend

    return instalar


def _autenticar(client, media_user_id=1, role="admin"):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {
            "id": str(media_user_id), "username": f"utilizador-{media_user_id}",
            "email": "a@b.test", "role": role,
        }
        sessao["_user_id"] = str(media_user_id)
        sessao["_fresh"] = True


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")


class TestMenu:
    def test_com_estatisticas_o_menu_mostra_as_ligacoes(self, client, configurada, db_session, servidor):
        servidor(True)
        _autenticar(client)

        pagina = client.get('/account').get_data(as_text=True)

        assert 'href="/statistics"' in pagina
        assert 'href="/wrapped"' in pagina

    def test_sem_estatisticas_as_ligacoes_desaparecem(self, client, configurada, db_session, servidor):
        servidor(False)
        _autenticar(client)

        pagina = client.get('/account').get_data(as_text=True)

        # Só as LIGAÇÕES do menu. A rota `/api/statistics/user/history` fica —
        # é o histórico da página da conta, que o Jellyfin sabe responder.
        assert 'href="/statistics"' not in pagina
        assert 'href="/wrapped"' not in pagina
        assert '/api/statistics/user/history' in pagina


class TestPaginas:
    def test_sem_estatisticas_a_pagina_redireciona_em_vez_de_vir_vazia(self, client, configurada, db_session, servidor):
        # Esconder a ligação no menu não chega: um marcador nos favoritos, ou o
        # endereço escrito à mão, davam uma página sem explicação nenhuma.
        servidor(False)
        _autenticar(client)

        resposta = client.get('/statistics')

        assert resposta.status_code == 302
        assert '/account' in resposta.headers['Location']

    def test_o_wrapped_segue_a_mesma_regra(self, client, configurada, db_session, servidor):
        servidor(False)
        _autenticar(client)

        assert client.get('/wrapped').status_code == 302

    def test_com_estatisticas_a_pagina_abre(self, client, configurada, db_session, servidor):
        servidor(True)
        _autenticar(client)

        assert client.get('/statistics').status_code == 200


class TestOndeAterraQuemNaoEAdministrador:
    """
    🐛 As estatísticas eram a casa de toda a gente, escrita à mão em cinco
    sítios. Ao escondê-las num painel Jellyfin, um utilizador comum era
    mandado para uma página que já não existe para ele — e ficava num
    redireccionamento sem saída logo a seguir a entrar.
    """

    def test_com_estatisticas_vai_para_as_estatisticas(self, app_context, servidor):
        from app.utils.navigation import endpoint_inicial_do_utilizador

        servidor(True)
        assert endpoint_inicial_do_utilizador() == 'main.statistics_page'

    def test_sem_estatisticas_vai_para_a_conta(self, app_context, servidor):
        from app.utils.navigation import endpoint_inicial_do_utilizador

        servidor(False)
        assert endpoint_inicial_do_utilizador() == 'main.account_page'

    def test_um_backend_sem_capacidades_nao_rebenta(self, app_context, monkeypatch):
        # Acontece a meio da construção do backend, e num duplo incompleto.
        from app import extensions
        from app.utils.navigation import endpoint_inicial_do_utilizador

        monkeypatch.setattr(extensions, 'media_server', None)
        assert endpoint_inicial_do_utilizador() == 'main.account_page'


class TestOQueNaoSeEsconde:
    def test_o_historico_e_os_aparelhos_ficam_na_pagina_da_conta(self, client, configurada, db_session, servidor):
        # São a razão de ser desta separação: o Jellyfin responde a estes dois
        # sozinho, por isso não dependem da capacidade das estatísticas.
        servidor(False)
        _autenticar(client)

        pagina = client.get('/account').get_data(as_text=True)

        assert 'device-list-container' in pagina
        assert 'history-container' in pagina

    def test_a_rota_dos_aparelhos_responde(self, client, configurada, db_session, servidor):
        servidor(False)
        _autenticar(client)

        resposta = client.get('/api/users/account/devices')

        assert resposta.status_code == 200
        assert resposta.get_json()["success"] is True


class TestPlexSemTautulli:
    """
    🐛 O Tautulli é opcional, mas quem não o configurava ficava com o pódio, o
    XP e o Wrapped no menu — páginas que não tinham de onde se encher. E, na
    página da conta, um histórico vazio a dizer que ninguém tinha visto nada.
    """

    def test_o_menu_esconde_as_estatisticas(self, client, configurada, db_session, servidor):
        servidor(True, disponiveis=False)
        _autenticar(client)

        pagina = client.get('/account').get_data(as_text=True)

        assert 'href="/statistics"' not in pagina
        assert 'href="/wrapped"' not in pagina

    def test_as_paginas_redirecionam(self, client, configurada, db_session, servidor):
        servidor(True, disponiveis=False)
        _autenticar(client)

        assert client.get('/statistics').status_code == 302
        assert client.get('/wrapped').status_code == 302

    def test_quem_nao_e_administrador_aterra_na_conta(self, app_context, servidor):
        from app.utils.navigation import endpoint_inicial_do_utilizador

        servidor(True, disponiveis=False)
        assert endpoint_inicial_do_utilizador() == 'main.account_page'

    def test_o_historico_e_os_aparelhos_ficam(self, client, configurada, db_session, servidor):
        # São a razão de ser da separação: o Plex responde a estes sozinho.
        servidor(True, disponiveis=False)
        _autenticar(client)

        pagina = client.get('/account').get_data(as_text=True)

        assert 'device-list-container' in pagina
        assert 'history-container' in pagina

    def test_o_cartao_do_tautulli_continua_nas_conexoes(self, client, configurada, db_session, servidor):
        # ⚠️ Se este cartão seguisse `estatisticas_disponiveis()`, desaparecia
        # exatamente a quem falta configurar o Tautulli — e não haveria como
        # voltar a ligá-lo a não ser editando o config.json à mão.
        servidor(True, disponiveis=False)
        _autenticar(client)

        pagina = client.get('/settings').get_data(as_text=True)

        assert 'TAUTULLI_API_KEY' in pagina
        # E fica dito o que se perde sem ele.
        assert 'mais lento' in pagina

    def test_num_painel_jellyfin_o_cartao_sai(self, client, configurada, db_session, servidor):
        servidor(False)
        _autenticar(client)

        assert 'TAUTULLI_API_KEY' not in client.get('/settings').get_data(as_text=True)


class TestEstadoDoSistema:
    """O cartão do Tautulli na Dashboard."""

    def test_num_painel_jellyfin_o_tautulli_nao_e_um_servico(self, client, configurada, db_session, servidor):
        # Um cartão permanentemente apagado é uma pergunta que o administrador
        # não tem como fechar: ali o Tautulli não está desligado, não existe.
        servidor(False)
        _autenticar(client)

        saude = client.get('/api/system/system-health').get_json()['health']

        assert 'tautulli' not in saude

    def test_num_painel_plex_continua_a_aparecer(self, client, configurada, db_session, servidor):
        # Aqui "desativado" é informação: diz ao administrador que pode ligá-lo.
        servidor(True, disponiveis=False)
        _autenticar(client)

        saude = client.get('/api/system/system-health').get_json()['health']

        assert saude['tautulli']['status'] == 'DISABLED'


class TestTarefasDeFundo:
    """
    🔇 O `sync_xp_job` corre todas as madrugadas sobre TODOS os perfis. Sem
    Tautulli, cada um deles dava um erro — com repetições — a dizer o mesmo:
    que não há de onde ler o histórico.
    """

    def test_sem_estatisticas_o_xp_nem_se_tenta(self, app, servidor, monkeypatch):
        from app import extensions, scheduler as agendador

        servidor(True, disponiveis=False)
        agendador.set_app_for_jobs(app)
        chamadas = []
        monkeypatch.setattr(extensions.data_manager, 'get_all_user_profiles',
                            lambda: chamadas.append(True) or [])

        agendador.sync_xp_job()

        assert chamadas == []

    def test_com_estatisticas_corre_como_sempre(self, app, servidor, monkeypatch):
        from app import extensions, scheduler as agendador

        servidor(True)
        agendador.set_app_for_jobs(app)
        chamadas = []
        monkeypatch.setattr(extensions.data_manager, 'get_all_user_profiles',
                            lambda: chamadas.append(True) or [])
        monkeypatch.setattr(extensions.stats_manager, 'reset_season_if_due',
                            lambda: {"reset": False})

        agendador.sync_xp_job()

        assert chamadas == [True]


class TestJellyfinTemEstatisticas:
    """
    As do Jellyfin saem do próprio servidor (`jellyfin/stats_api.py`), por isso
    não há nada para o administrador ligar — mas há tudo para mostrar. É a
    combinação que faltava: estatísticas SEM fonte externa.
    """

    def _jellyfin(self, servidor):
        return servidor(True, disponiveis=True, externas=False)

    def test_o_menu_mostra_as_ligacoes(self, client, configurada, db_session, servidor):
        self._jellyfin(servidor)
        _autenticar(client)

        pagina = client.get('/account').get_data(as_text=True)

        assert 'href="/statistics"' in pagina
        assert 'href="/wrapped"' in pagina

    def test_as_paginas_abrem(self, client, configurada, db_session, servidor):
        self._jellyfin(servidor)
        _autenticar(client)

        assert client.get('/statistics').status_code == 200

    def test_o_cartao_do_tautulli_nao_aparece(self, client, configurada, db_session, servidor):
        # Pedir as credenciais de um serviço que não vai ser usado é pior do
        # que não as pedir: o Tautulli não fala com o Jellyfin.
        self._jellyfin(servidor)
        _autenticar(client)

        assert 'TAUTULLI_API_KEY' not in client.get('/settings').get_data(as_text=True)

    def test_o_tautulli_tambem_sai_do_estado_do_sistema(self, client, configurada, db_session, servidor):
        self._jellyfin(servidor)
        _autenticar(client)

        saude = client.get('/api/system/system-health').get_json()['health']

        assert 'tautulli' not in saude
