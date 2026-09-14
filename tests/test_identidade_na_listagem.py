# tests/test_identidade_na_listagem.py

"""A junção entre o que o SERVIDOR devolve e o que o PAINEL tem guardado.

⚠️ **O Plex identifica as contas por um INTEIRO e o painel guarda-as como
TEXTO.** A base de dados normaliza sozinha (o tipo `UserId`), por isso tudo o
que sai de lá é `'123456'`; a lista de utilizadores do servidor continua a
trazer `123456`. Juntar as duas com `in` ou `==` dá sempre falso — e o sintoma
não é um erro no log, é o cartão do utilizador sem nada:

- sem nome, sem vencimento e com o limite de telas a zero, porque esses quatro
  campos são os únicos que vêm do PERFIL (o nome de utilizador e o avatar vêm
  do servidor, e por isso apareciam);
- sem `payment_token`, que é o que faz o "copiar link de pagamento" dizer que
  a pessoa não tem link nenhum;
- e o perfil verdadeiro marcado como `inactive`, porque o ramo que não o
  encontra na lista do servidor conclui que ele já lá não está.

Quem migra de um painel só-Plex vê isto no primeiro arranque depois de
restaurar o backup: a migração `a9f3c17b2e04` passa os IDs guardados a texto, e
é aí que os dois lados deixam de casar.
"""

import pytest

pytestmark = pytest.mark.integration

# Como o Plex os dá: inteiros.
ANA = 90183591
RITA = 77123456


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")


class ServidorFalso:
    """Um servidor de média que identifica as contas por inteiro, como o Plex."""

    def __init__(self, utilizadores):
        self._utilizadores = utilizadores

    def is_connected(self):
        return True

    def get_all_users(self, force_refresh=False):
        return [dict(u) for u in self._utilizadores]

    def get_libraries(self):
        return []


@pytest.fixture()
def servidor(monkeypatch):
    def instalar(utilizadores):
        from app import extensions

        backend = ServidorFalso(utilizadores)
        monkeypatch.setattr(extensions, 'media_server', backend)
        return backend

    return instalar


def _autenticar(client):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": "1", "username": "dono", "email": "a@b.test", "role": "admin"}
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True


def _perfil_completo(data_manager, media_user_id):
    return data_manager.set_user_profile(media_user_id, {
        'username': 'ana',
        'name': 'Ana Maria',
        'email': 'ana@exemplo.test',
        'status': 'active',
        'screen_limit': 3,
        'expiration_date': '2099-01-31T23:59:00+00:00',
        'payment_token': 'token-da-ana',
    })


@pytest.fixture()
def listagem(client, configurada, db_session, servidor):
    def pedir(utilizadores):
        servidor(utilizadores)
        _autenticar(client)
        resposta = client.get('/api/users/status')
        assert resposta.status_code == 200
        return resposta.get_json()['users']

    return pedir


class TestOCartaoDeQuemTemPerfil:
    def test_o_perfil_guardado_chega_ao_cartao(self, listagem, data_manager):
        # 🐛 O ID vem inteiro do servidor e texto da base de dados: sem
        # normalizar, o painel não reconhecia o perfil e devolvia um cartão
        # vazio — com o nome de utilizador certo, que é o que enganava.
        _perfil_completo(data_manager, ANA)

        cartoes = listagem([{'id': ANA, 'username': 'ana', 'email': 'ana@exemplo.test', 'thumb': None}])

        assert len(cartoes) == 1
        cartao = cartoes[0]
        assert cartao['name'] == 'Ana Maria'
        assert cartao['screen_limit'] == 3
        assert cartao['expiration_date'] == '2099-01-31T23:59:00+00:00'
        assert cartao['payment_token'] == 'token-da-ana'
        assert cartao['status'] == 'active'

    def test_o_perfil_nao_e_desativado_nem_reescrito(self, listagem, data_manager):
        # O ramo do "já não está no servidor" marcava-o `inactive` e o ramo do
        # "é novo" reescrevia-lhe o limite de telas a zero. Em cada visita à
        # página de utilizadores.
        _perfil_completo(data_manager, ANA)

        listagem([{'id': ANA, 'username': 'ana', 'email': 'ana@exemplo.test', 'thumb': None}])

        perfil = data_manager.get_user_profile(ANA)
        assert perfil['status'] == 'active'
        assert perfil['screen_limit'] == 3
        assert perfil['payment_token'] == 'token-da-ana'

    def test_cada_pessoa_aparece_uma_vez_so(self, listagem, data_manager):
        # Sem a normalização, a mesma pessoa entrava duas vezes na lista: uma
        # pelo ramo do "novo" (vazia) e outra pelo ramo do perfil (inativa).
        _perfil_completo(data_manager, ANA)

        cartoes = listagem([{'id': ANA, 'username': 'ana', 'email': 'ana@exemplo.test', 'thumb': None}])

        assert [c['id'] for c in cartoes] == ['90183591']

    def test_o_identificador_sai_sempre_como_texto(self, listagem, data_manager):
        # O resto do painel (URLs, sessões, base de dados) trata-o como texto.
        _perfil_completo(data_manager, ANA)

        cartoes = listagem([{'id': ANA, 'username': 'ana', 'email': 'ana@exemplo.test', 'thumb': None}])

        assert isinstance(cartoes[0]['id'], str)


class TestOLinkDePagamento:
    """🐛 "Este usuário ainda não tem um link de pagamento gerado."

    O token é gerado ao gravar o perfil, e a coluna nasceu depois de algumas
    instalações. Um perfil que venha de um backup antigo e que ninguém volte a
    gravar ficava sem ele para sempre — e o botão de copiar o link recusava-se
    a funcionar, sem nada no log.
    """

    def test_um_perfil_sem_token_ganha_um(self, listagem, data_manager, app_context):
        from app.extensions import db
        from app.models import UserProfile

        _perfil_completo(data_manager, ANA)
        UserProfile.query.get(str(ANA)).payment_token = None
        db.session.commit()

        cartoes = listagem([{'id': ANA, 'username': 'ana', 'email': 'ana@exemplo.test', 'thumb': None}])

        assert cartoes[0]['payment_token']
        assert data_manager.get_user_profile(ANA)['payment_token'] == cartoes[0]['payment_token']


class TestQuemAindaNaoTemPerfil:
    def test_um_utilizador_novo_ganha_um_perfil(self, listagem, data_manager):
        cartoes = listagem([{'id': RITA, 'username': 'rita', 'email': 'rita@exemplo.test', 'thumb': None}])

        assert len(cartoes) == 1
        assert cartoes[0]['username'] == 'rita'
        assert data_manager.get_user_profile(RITA) is not None


class TestQuemSaiuDoServidor:
    def test_o_perfil_de_quem_ja_nao_esta_la_fica_inativo(self, listagem, data_manager):
        _perfil_completo(data_manager, ANA)

        cartoes = listagem([])

        assert [c['status'] for c in cartoes] == ['inactive']
        assert data_manager.get_user_profile(ANA)['status'] == 'inactive'


class TestAFachadaNaoDeixaSairInteiros:
    """A tradução é da fachada: fora dela a identidade é sempre texto.

    Corrigir só quem junta as listas deixaria a armadilha montada para a
    próxima pessoa que consuma `get_all_users()` — e já eram três sítios a
    normalizar por sua conta.
    """

    def _fachada(self, utilizadores):
        from app.services.media_server.plex.backend import PlexManager

        class UtilizadoresFalsos:
            def list_users(self, force_refresh_signal=None):
                return utilizadores

            def get_user_by_id(self, media_user_id):
                return next((u for u in utilizadores if str(u['id']) == str(media_user_id)), None)

            def invalidate_user_cache(self):
                pass

        gestor = PlexManager(None, None, None, None)
        gestor.users = UtilizadoresFalsos()
        return gestor

    def test_a_lista_sai_com_a_identidade_em_texto(self, app_context):
        gestor = self._fachada([{'id': ANA, 'username': 'ana', 'email': None, 'thumb': None}])

        assert [u['id'] for u in gestor.get_all_users()] == ['90183591']

    def test_um_utilizador_sozinho_tambem(self, app_context):
        gestor = self._fachada([{'id': ANA, 'username': 'ana', 'email': None, 'thumb': None}])

        assert gestor.get_user_by_id(ANA)['id'] == '90183591'

    def test_quem_nao_existe_continua_a_ser_None(self, app_context):
        gestor = self._fachada([])

        assert gestor.get_user_by_id(ANA) is None


class TestOPodio:
    """O pódio junta o histórico aos perfis pelo mesmo identificador.

    ⚠️ O Tautulli devolve o `user_id` como inteiro. O nível de XP vem da base
    de dados (texto) e o avatar da lista do servidor (texto, desde que a
    fachada o traduz): com o inteiro pelo meio, nenhum dos dois casava, e o
    pódio ficava com os nomes certos, sem níveis e sem caras.
    """

    def _handler(self, historico):
        from tests.conftest import FakeDataManager
        from app.services.tautulli.stats_handler import StatsHandler

        class FonteFalsa:
            def get_history(self, **kwargs):
                return {"data": historico}

            def get_recently_added(self, **kwargs):
                return {"recently_added": []}

            def get_metadata(self, rating_key):
                return {}

            def image_payload(self, thumb, width=300, height=450):
                return None

        dados = FakeDataManager(profiles={'90183591': {'username': 'ana', 'xp': 500}})
        return StatsHandler(FonteFalsa(), data_manager=dados)

    def _reproducao(self):
        return {"user_id": ANA, "user": "ana", "duration": 3600}

    def test_a_identidade_sai_em_texto(self, app_context):
        handler = self._handler([self._reproducao()])

        linhas = handler.get_watch_stats(days=7)['stats']

        assert [l['user_id'] for l in linhas] == ['90183591']

    def test_o_avatar_da_lista_do_servidor_casa(self, app_context):
        handler = self._handler([self._reproducao()])

        linhas = handler.get_watch_stats(days=7, plex_users_info={'90183591': '/image/?source=ana'})['stats']

        assert linhas[0]['thumb'] == '/image/?source=ana'

    def test_o_nivel_de_xp_do_perfil_casa(self, app_context):
        handler = self._handler([self._reproducao()])

        linhas = handler.get_watch_stats(days=7)['stats']

        assert linhas[0]['level_info'] is not None
