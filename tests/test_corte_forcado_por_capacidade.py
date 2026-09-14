# tests/test_corte_forcado_por_capacidade.py

"""A definição "Forçar o encerramento em aparelhos que ignoram o comando".

🐛 **Aparecia num painel Plex, onde não faz nada.** `force_terminate()` do Plex
devolve sempre `False`: não há nada mais forte do que pedir para parar (ver
`plex/sessions.py`). O interruptor prometia um comportamento que nunca
acontecia — e, com ele desligado, o log ainda aconselhava o administrador a
ligá-lo.

Quem decide é a capacidade `corte_forcado`, como em tudo o resto: uma
capacidade em falta ESCONDE a funcionalidade, nunca a mostra partida.
"""

import pytest

from app.services.media_server.base import MediaServerCapabilities

pytestmark = pytest.mark.integration


def _capacidades(corte_forcado):
    return MediaServerCapabilities(
        convites_nativos=not corte_forcado, cria_contas=corte_forcado,
        fontes_media_online=not corte_forcado, login_delegado=not corte_forcado,
        desativa_conta=corte_forcado, links_profundos=True,
        estatisticas=True, estatisticas_externas=not corte_forcado,
        corte_forcado=corte_forcado,
    )


class BackendFalso:
    def __init__(self, corte_forcado):
        self.SERVER_TYPE = 'jellyfin' if corte_forcado else 'plex'
        self.DISPLAY_NAME = 'Jellyfin' if corte_forcado else 'Plex Media Server'
        self.SHORT_NAME = 'Jellyfin' if corte_forcado else 'Plex'
        self.CAPABILITIES = _capacidades(corte_forcado)

    @property
    def capabilities(self):
        return self.CAPABILITIES

    def estatisticas_disponiveis(self):
        return False

    def get_libraries(self):
        return []

    def is_connected(self):
        return True


@pytest.fixture()
def servidor(monkeypatch):
    def instalar(corte_forcado):
        from app import extensions
        from app.blueprints import main as main_module
        from app.blueprints.api import system as system_module

        backend = BackendFalso(corte_forcado)
        monkeypatch.setattr(extensions, 'media_server', backend)
        monkeypatch.setattr(main_module, 'media_server', backend, raising=False)
        monkeypatch.setattr(system_module, 'media_server', backend, raising=False)
        return backend

    return instalar


@pytest.fixture(autouse=True)
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")


def _autenticar(client):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": "1", "username": "dono", "email": "a@b.test", "role": "admin"}
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True


class TestNasConfiguracoes:
    def test_num_painel_plex_a_definicao_nao_aparece(self, client, db_session, servidor):
        servidor(corte_forcado=False)
        _autenticar(client)

        pagina = client.get('/settings').get_data(as_text=True)

        assert 'FORCE_STREAM_TERMINATION' not in pagina
        assert 'Forçar o encerramento' not in pagina

    def test_onde_ha_um_ultimo_recurso_continua_a_aparecer(self, client, db_session, servidor):
        servidor(corte_forcado=True)
        _autenticar(client)

        pagina = client.get('/settings').get_data(as_text=True)

        assert 'FORCE_STREAM_TERMINATION' in pagina
        assert 'Forçar o encerramento' in pagina


class TestAGravacao:
    """Esconder o interruptor não pode apagar o que lá está gravado."""

    def test_gravar_noutro_separador_nao_desliga_a_definicao(self, client, db_session,
                                                              servidor, config_file):
        # 🛡️ O formulário das Configurações envia o que tem no ecrã. Com o campo
        # escondido, uma gravação a partir de um painel Plex não pode levar o
        # valor a False — num painel que troque de servidor, isso desligava em
        # silêncio uma defesa que o administrador tinha pedido.
        config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1",
                    FORCE_STREAM_TERMINATION=True)
        servidor(corte_forcado=False)
        _autenticar(client)

        resposta = client.post('/api/system/settings', json={"APP_TITLE": "Outro nome"})

        assert resposta.status_code == 200
        from app.config import load_or_create_config
        assert load_or_create_config()["FORCE_STREAM_TERMINATION"] is True
