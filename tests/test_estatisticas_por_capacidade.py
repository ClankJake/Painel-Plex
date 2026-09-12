# tests/test_estatisticas_por_capacidade.py
"""As estatísticas escondem-se onde o servidor não as suporta.

Pódio, XP, conquistas, recomendações e o Wrapped saem todos do histórico do
Tautulli, que só fala com o Plex. Num painel ligado ao Jellyfin não há de onde
os tirar — e a regra da casa é que uma capacidade em falta ESCONDE a
funcionalidade, nunca a mostra partida.

⚠️ O que NÃO se esconde: o histórico e os aparelhos da página da conta. Esses
o Jellyfin sabe responder sozinho (ver `jellyfin/history.py`), e é justamente a
diferença que estes testes guardam.
"""

import pytest

from app.services.media_server.base import MediaServerCapabilities

pytestmark = pytest.mark.integration


def _capacidades(estatisticas):
    return MediaServerCapabilities(
        convites_nativos=not estatisticas, cria_contas=not estatisticas,
        fontes_media_online=estatisticas, login_delegado=estatisticas,
        desativa_conta=not estatisticas, links_profundos=True,
        estatisticas=estatisticas,
    )


class BackendFalso:
    """O mínimo que a página e o contexto dos templates pedem."""

    def __init__(self, estatisticas):
        self.SERVER_TYPE = 'plex' if estatisticas else 'jellyfin'
        self.DISPLAY_NAME = 'Plex Media Server' if estatisticas else 'Jellyfin'
        self.CAPABILITIES = _capacidades(estatisticas)

    @property
    def capabilities(self):
        return self.CAPABILITIES

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
    from app.blueprints.api import users as users_module

    def instalar(estatisticas):
        backend = BackendFalso(estatisticas)
        monkeypatch.setattr(extensions, 'media_server', backend)
        monkeypatch.setattr(main_module, 'media_server', backend, raising=False)
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
