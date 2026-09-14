# tests/test_administrador_visivel.py

"""Como o administrador aparece no painel, agora que conta como toda a gente.

Ele entrou nas estatísticas (pódio, XP, conquistas) sem deixar de ser o DONO do
servidor: não foi convidado, não tem plano, não tem vencimento e o painel não
lhe impõe limite de telas. Mostrar esses campos vazios — "Membro desde: Não
disponível", "Limite de Telas: Ilimitado" — só levanta a pergunta de porquê.

E, no pódio, ele era o único sem avatar: o dono não está na lista de
utilizadores do servidor (no Plex a API só devolve os amigos), e a linha dele
ficava com o "?" para toda a gente.
"""

import pytest

pytestmark = pytest.mark.integration

ADMIN = '90183591'
OUTRA = '77123456'


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID=ADMIN)


def _autenticar(client, media_user_id=ADMIN, role="admin", username="dono"):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {
            "id": str(media_user_id), "username": username,
            "email": "a@b.test", "role": role,
        }
        sessao["_user_id"] = str(media_user_id)
        sessao["_fresh"] = True


class DonoFalso:
    """O que `get_owner_account()` devolve — o dono do servidor."""

    id = ADMIN
    username = 'dono'
    email = None
    thumb = 'https://plex.tv/users/abc/avatar'


class ServidorFalso:
    """O mínimo que estas rotas pedem ao servidor de média."""

    def __init__(self, utilizadores=None, dono=DonoFalso()):
        self._utilizadores = utilizadores if utilizadores is not None else []
        self._dono = dono

    def get_all_users(self, force_refresh=False):
        return self._utilizadores

    def get_owner_account(self):
        return self._dono

    def thumb_para_interface(self, thumb):
        # O de verdade traduz para o proxy do painel; aqui basta ser idempotente.
        return f"/image/?source={thumb}" if thumb else None

    def get_user_libraries(self, user_id):
        return {"libraries": []}

    def get_user_by_id(self, user_id):
        return {"id": user_id, "username": "dono", "thumb": None}


@pytest.fixture()
def servidor(monkeypatch):
    def instalar(backend=None):
        from app import extensions
        from app.blueprints.api import stats as stats_module
        from app.blueprints.api import users as users_module

        backend = backend or ServidorFalso()
        monkeypatch.setattr(extensions, 'media_server', backend)
        monkeypatch.setattr(stats_module, 'media_server', backend, raising=False)
        monkeypatch.setattr(users_module.extensions, 'media_server', backend, raising=False)
        return backend

    return instalar


class TestNoPodio:
    """O pódio identifica o dono, e mostra-lhe a cara."""

    def _com_estatisticas(self, monkeypatch, linhas):
        from app.blueprints.api import stats as stats_module

        class StatsFalso:
            def __init__(self):
                self.recebido = {}

            def get_watch_stats(self, days=7, plex_users_info=None):
                self.recebido['plex_users_info'] = plex_users_info or {}
                return {"success": True, "stats": [dict(linha) for linha in linhas]}

        falso = StatsFalso()
        monkeypatch.setattr(stats_module, 'stats_manager', falso, raising=False)
        return falso

    def test_a_linha_do_administrador_vem_marcada(self, client, configurada, db_session,
                                                  servidor, monkeypatch):
        servidor()
        self._com_estatisticas(monkeypatch, [
            {"user_id": ADMIN, "username": "dono", "plays": 3, "total_duration": 100, "thumb": None},
            {"user_id": OUTRA, "username": "ana", "plays": 1, "total_duration": 50, "thumb": None},
        ])
        _autenticar(client)

        linhas = client.get('/api/statistics/').get_json()['stats']

        assert [l['is_admin'] for l in linhas] == [True, False]

    def test_o_avatar_do_dono_chega_ao_podio(self, client, configurada, db_session,
                                             servidor, monkeypatch):
        # 🐛 O dono não está na lista de utilizadores do servidor: a linha dele
        # ficava com o "?" enquanto toda a gente tinha cara.
        servidor()
        falso = self._com_estatisticas(monkeypatch, [])
        _autenticar(client)

        client.get('/api/statistics/')

        assert falso.recebido['plex_users_info'][ADMIN] == "/image/?source=https://plex.tv/users/abc/avatar"

    def test_um_avatar_que_ja_veio_do_servidor_nao_e_substituido(self, client, configurada,
                                                                 db_session, servidor, monkeypatch):
        # No Jellyfin o administrador ESTÁ na lista: o que vem de lá manda.
        servidor(ServidorFalso(utilizadores=[{"id": ADMIN, "username": "dono", "thumb": "/image/?source=jellyfin"}]))
        falso = self._com_estatisticas(monkeypatch, [])
        _autenticar(client)

        client.get('/api/statistics/')

        assert falso.recebido['plex_users_info'][ADMIN] == "/image/?source=jellyfin"

    def test_sem_dono_configurado_o_podio_continua_a_responder(self, client, configurada,
                                                              db_session, servidor, monkeypatch, config_file):
        config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="")
        servidor()
        self._com_estatisticas(monkeypatch, [
            {"user_id": OUTRA, "username": "ana", "plays": 1, "total_duration": 50, "thumb": None},
        ])
        _autenticar(client)

        resposta = client.get('/api/statistics/')

        assert resposta.status_code == 200
        assert resposta.get_json()['stats'][0]['is_admin'] is False

    def test_um_servidor_offline_nao_parte_o_podio(self, client, configurada, db_session,
                                                   servidor, monkeypatch):
        class Partido(ServidorFalso):
            def get_owner_account(self):
                raise RuntimeError("offline")

        servidor(Partido())
        self._com_estatisticas(monkeypatch, [
            {"user_id": ADMIN, "username": "dono", "plays": 1, "total_duration": 50, "thumb": None},
        ])
        _autenticar(client)

        resposta = client.get('/api/statistics/')

        assert resposta.status_code == 200
        # Sem avatar, mas identificado à mesma.
        assert resposta.get_json()['stats'][0]['is_admin'] is True


class TestNaMinhaConta:
    def test_a_conta_diz_que_e_a_do_administrador(self, client, configurada, db_session, servidor):
        servidor()
        _autenticar(client)

        detalhes = client.get('/api/users/account/details').get_json()

        assert detalhes['is_admin'] is True

    def test_para_os_outros_nao(self, client, configurada, db_session, servidor, data_manager):
        servidor()
        data_manager.set_user_profile(OUTRA, {"username": "ana"})
        _autenticar(client, media_user_id=OUTRA, role="user", username="ana")

        detalhes = client.get('/api/users/account/details').get_json()

        assert detalhes['is_admin'] is False


class TestComoFicaNaBaseDeDados:
    """
    O perfil do administrador passou a existir — e a existir INTEIRO. Antes
    nascia a meio de uma sincronização de XP, com o nome em falta (que a tabela
    não aceita) e sem dizer de que servidor era.
    """

    def test_um_perfil_novo_fica_marcado_com_o_servidor(self, app_context, db_session, data_manager, config_file):
        config_file(IS_CONFIGURED=True, MEDIA_SERVER_TYPE="jellyfin")

        perfil = data_manager.set_user_profile(ADMIN, {"username": "dono"})

        # A coluna existe para não confundir um ID do Plex com um GUID do
        # Jellyfin que por acaso coincida — e ficava sempre a NULL.
        assert perfil['media_server_type'] == 'jellyfin'

    def test_quem_diz_o_servidor_explicitamente_manda(self, app_context, db_session, data_manager, config_file):
        config_file(IS_CONFIGURED=True, MEDIA_SERVER_TYPE="plex")

        perfil = data_manager.set_user_profile(ADMIN, {
            "username": "dono", "media_server_type": "jellyfin",
        })

        assert perfil['media_server_type'] == 'jellyfin'

    def test_atualizar_nao_mexe_no_servidor_ja_gravado(self, app_context, db_session, data_manager, config_file):
        config_file(IS_CONFIGURED=True, MEDIA_SERVER_TYPE="plex")
        data_manager.set_user_profile(ADMIN, {"username": "dono", "media_server_type": "jellyfin"})

        perfil = data_manager.set_user_profile(ADMIN, {"xp": 10})

        assert perfil['media_server_type'] == 'jellyfin'
