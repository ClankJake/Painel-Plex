# tests/test_login_forca_bruta.py

"""O travão às tentativas de adivinhar palavras-passe.

⚠️ `/auth/login/credentials` é a ÚNICA rota do painel onde se podem testar
palavras-passe — só existe em servidores de contas locais (o Jellyfin); num
painel Plex quem as verifica é o plex.tv. E o limite que lá estava conta por
ENDEREÇO: quem ataca uma conta concreta tinha as dez por minuto todas para ela,
e mudar de endereço devolvia-lhe outras dez.

O que fecha essa porta é contar por CONTA. Estes testes guardam isso e as duas
decisões que o acompanham: que o nome INVENTADO é travado como qualquer outro
(recusar só contas reais diria quais existem), e que quem acerta à quarta não
fica com as três falhas penduradas.
"""

import pytest

from app.services.media_server.base import OwnerAccount
from app.utils import tentativas_de_login

pytestmark = pytest.mark.integration

GUID = "38c3a1f0e4b24d7f9c1a0b5e6d7f8a90"


class BackendFalso:
    SERVER_TYPE = 'jellyfin'
    DISPLAY_NAME = 'Jellyfin'
    SHORT_NAME = 'Jellyfin'

    def __init__(self):
        from app.services.media_server.base import MediaServerCapabilities

        self.tentativas = []
        self.CAPABILITIES = MediaServerCapabilities(
            convites_nativos=False, cria_contas=True, fontes_media_online=False,
            login_delegado=False, desativa_conta=True, links_profundos=True,
            estatisticas=True, estatisticas_externas=False,
        )

    @property
    def capabilities(self):
        return self.CAPABILITIES

    def authenticate(self, username, password):
        self.tentativas.append((username, password))
        if (username, password) != ("ana", "segredo"):
            return None
        return OwnerAccount(id=GUID, username=username, email=None, thumb=None)

    def get_all_users(self, force_refresh=False):
        return [{"id": GUID, "username": "ana"}]


@pytest.fixture()
def jellyfin(monkeypatch):
    from app import extensions
    from app.blueprints import auth as auth_module

    backend = BackendFalso()
    monkeypatch.setattr(extensions, 'media_server', backend)
    monkeypatch.setattr(auth_module, 'media_server', backend)
    return backend


@pytest.fixture(autouse=True)
def sem_travoes():
    """O limitador por IP e o contador por conta vivem fora da base de dados."""
    from app.extensions import cache, limiter

    limiter.reset()
    cache.clear()
    yield
    limiter.reset()
    cache.clear()


@pytest.fixture(autouse=True)
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="", MEDIA_SERVER_TYPE="jellyfin")


def _tentar(client, username="ana", password="errada"):
    return client.post('/auth/login/credentials', json={"username": username, "password": password})


class TestOContadorPorConta:
    def test_as_primeiras_falhas_sao_so_falhas(self, client, jellyfin, db_session):
        for _ in range(tentativas_de_login.LIMITE_POR_CONTA - 1):
            assert _tentar(client).status_code == 401

    def test_ao_fim_do_limite_a_conta_descansa(self, client, jellyfin, db_session):
        for _ in range(tentativas_de_login.LIMITE_POR_CONTA):
            _tentar(client)

        resposta = _tentar(client)

        assert resposta.status_code == 429
        assert "tentativas" in resposta.get_json()["message"].lower()

    def test_bloqueada_nem_chega_ao_servidor(self, client, jellyfin, db_session):
        # 🛡️ O ponto do travão: as tentativas param AQUI. Continuar a passá-las
        # ao Jellyfin era deixá-lo a ser martelado pelo painel.
        for _ in range(tentativas_de_login.LIMITE_POR_CONTA):
            _tentar(client)
        antes = len(jellyfin.tentativas)

        _tentar(client)

        assert len(jellyfin.tentativas) == antes

    def test_a_palavra_passe_certa_tambem_espera(self, client, jellyfin, db_session):
        # Senão o travão não travava nada: bastava continuar a tentar.
        for _ in range(tentativas_de_login.LIMITE_POR_CONTA):
            _tentar(client)

        assert _tentar(client, password="segredo").status_code == 429

    def test_um_nome_que_nao_existe_e_travado_na_mesma(self, client, jellyfin, db_session):
        # ⚠️ Travar só contas reais diria a quem tenta quais os nomes que
        # existem neste servidor — o oposto da mensagem de erro única.
        for _ in range(tentativas_de_login.LIMITE_POR_CONTA):
            _tentar(client, username="ninguem")

        assert _tentar(client, username="ninguem").status_code == 429

    def test_outra_conta_nao_e_apanhada_pelo_caminho(self, client, jellyfin, db_session):
        for _ in range(tentativas_de_login.LIMITE_POR_CONTA):
            _tentar(client, username="ninguem")

        assert _tentar(client, username="ana", password="segredo").get_json()["success"] is True

    def test_acertar_limpa_o_que_ficou_para_tras(self, client, jellyfin, db_session):
        for _ in range(tentativas_de_login.LIMITE_POR_CONTA - 1):
            _tentar(client)

        assert _tentar(client, password="segredo").get_json()["success"] is True

        # As falhas anteriores não ficam penduradas à espera da próxima distração.
        assert tentativas_de_login.segundos_de_espera("ana", "127.0.0.1") == 0
        for _ in range(tentativas_de_login.LIMITE_POR_CONTA - 1):
            assert _tentar(client).status_code == 401


class TestOTamanhoDoQueSeAceita:
    """O pedido para no painel, antes de haver viagem ao servidor de média."""

    def test_uma_palavra_passe_enorme_e_recusada_sem_sair_daqui(self, client, jellyfin, db_session):
        resposta = _tentar(client, password="x" * (tentativas_de_login.LIMITE_POR_CONTA + 100000))

        assert resposta.status_code == 401
        assert jellyfin.tentativas == []

    def test_um_utilizador_enorme_tambem(self, client, jellyfin, db_session):
        resposta = _tentar(client, username="u" * 5000)

        assert resposta.status_code == 401
        assert jellyfin.tentativas == []


class TestOQueVaiParaOLog:
    def test_o_nome_escrito_nao_leva_quebras_de_linha_para_o_log(self):
        # 🛡️ Com uma quebra de linha lá dentro, quem tenta entrar escrevia as
        # linhas que quisesse no log do painel.
        from app.blueprints.auth import _texto_para_log

        sujo = "ana\n[ERROR] o servidor foi comprometido"

        assert "\n" not in _texto_para_log(sujo)


class TestAPaginaDeLogin:
    """A primeira linha que a pessoa lê não pode ser a marca errada."""

    def test_num_painel_jellyfin_nao_diz_plex(self, client, jellyfin, db_session):
        pagina = client.get('/auth/login').get_data(as_text=True)

        assert 'Entre com a sua conta do Jellyfin' in pagina
        assert 'conta Plex' not in pagina

    def test_o_formulario_limita_o_que_aceita(self, client, jellyfin, db_session):
        pagina = client.get('/auth/login').get_data(as_text=True)

        assert 'maxlength="128"' in pagina
        assert 'maxlength="256"' in pagina
