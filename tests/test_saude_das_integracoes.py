# tests/test_saude_das_integracoes.py

"""O cartão do servidor de média no "Saúde das Integrações" da Dashboard.

🐛 Dizia **"Servidor Plex"** por cima do estado de um Jellyfin. A chave da
resposta chamava-se `plex` e o rótulo estava escrito à mão no template — mais
uma marca fixa numa interface que passou a servir dois servidores, como
aconteceu com o `default.svg` e com o "Ver no Plex" das estatísticas.

Quem sabe o nome é o servidor: `media_server.short_name` (`SHORT_NAME` no
backend).
"""

import pytest

pytestmark = pytest.mark.integration


class BackendFalso:
    def __init__(self, short_name, server_type):
        self.SERVER_TYPE = server_type
        self.DISPLAY_NAME = short_name
        self.SHORT_NAME = short_name
        self.CAPABILITIES = None

    @property
    def capabilities(self):
        return self.CAPABILITIES

    def estatisticas_disponiveis(self):
        return False

    def check_status(self):
        return {"status": "ONLINE", "message": "ok"}

    def get_all_users(self, force_refresh=False):
        return []

    def get_libraries(self):
        return []

    def is_connected(self):
        return True


@pytest.fixture()
def servidor(monkeypatch):
    from app import extensions
    from app.blueprints import main as main_module
    from app.blueprints.api import system as system_module

    def instalar(short_name='Jellyfin', server_type='jellyfin'):
        backend = BackendFalso(short_name, server_type)
        monkeypatch.setattr(extensions, 'media_server', backend)
        monkeypatch.setattr(main_module, 'media_server', backend, raising=False)
        monkeypatch.setattr(system_module, 'media_server', backend, raising=False)
        return backend

    return instalar


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")


def _autenticar(client):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": "1", "username": "dono", "email": "a@b.test", "role": "admin"}
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True


class TestARespostaDaApi:
    def test_a_chave_do_servidor_nao_presume_a_marca(self, client, configurada, db_session, servidor):
        servidor()
        _autenticar(client)

        saude = client.get('/api/system/system-health').get_json()['health']

        assert 'media_server' in saude
        assert 'plex' not in saude
        assert saude['media_server']['status'] == 'ONLINE'


class TestORotuloNaPagina:
    """O nome vem do servidor configurado, não do template."""

    def test_num_painel_jellyfin_diz_jellyfin(self, client, configurada, db_session, servidor):
        servidor('Jellyfin', 'jellyfin')
        _autenticar(client)

        pagina = client.get('/').get_data(as_text=True)

        assert 'data-i18n-media-server="Servidor Jellyfin"' in pagina
        assert 'Servidor Plex' not in pagina

    def test_num_painel_plex_continua_a_dizer_plex(self, client, configurada, db_session, servidor):
        servidor('Plex', 'plex')
        _autenticar(client)

        pagina = client.get('/').get_data(as_text=True)

        assert 'data-i18n-media-server="Servidor Plex"' in pagina
