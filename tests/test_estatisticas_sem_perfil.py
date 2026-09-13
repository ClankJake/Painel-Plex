# tests/test_estatisticas_sem_perfil.py

"""As estatísticas de quem não tem perfil local no painel.

⚠️ **Há sempre pelo menos uma pessoa assim: o ADMINISTRADOR.** O login dele
devolve antes da parte que cria perfis (ver `_autorizar_e_iniciar_sessao`), por
desenho. E quem nunca entrou no painel — mas vê coisas no servidor — também não
tem perfil.

O perfil local guarda o XP, a privacidade e o nome. O HISTÓRICO não está lá: é
do servidor de média. Tratar a ausência do perfil como "este utilizador não
existe" dava três sintomas diferentes para a mesma causa, e este ficheiro
guarda os três.
"""

import pytest

from tests.conftest import FakeDataManager

pytestmark = pytest.mark.integration

# O id do administrador neste painel — o caso garantido de "sem perfil local".
ADMIN = '877f45d04a454365b14484673dd7289d'
OUTRA = 'aa11bb22cc33dd44ee55ff6677889900'


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID=ADMIN)


def _autenticar(client, media_user_id=ADMIN, role="admin"):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {
            "id": str(media_user_id), "username": "dono",
            "email": "a@b.test", "role": role,
        }
        sessao["_user_id"] = str(media_user_id)
        sessao["_fresh"] = True


class TestAsRotas:
    def test_as_estatisticas_de_quem_nao_tem_perfil_respondem(self, client, configurada, db_session):
        # 🐛 `profile.get(...)` sobre None: o administrador levava com um 500
        # ao abrir as suas próprias estatísticas.
        _autenticar(client)

        resposta = client.get(f'/api/statistics/user/{ADMIN}')

        assert resposta.status_code == 200
        assert resposta.get_json()["success"] is True

    def test_o_wrapped_de_quem_nao_tem_perfil_responde(self, client, configurada, db_session):
        # 🐛 Respondia 404 "Usuário não encontrado" — mas o utilizador existe,
        # é o dono do servidor; o que falta é uma linha na base de dados do
        # painel, e o Wrapped sai do histórico do SERVIDOR.
        _autenticar(client)

        resposta = client.get(f'/api/statistics/wrapped/{ADMIN}')

        assert resposta.status_code == 200
        assert resposta.get_json()["success"] is True

    def test_a_privacidade_continua_a_valer(self, client, configurada, db_session, data_manager):
        # Quem TEM perfil e pediu para ficar de fora continua de fora.
        data_manager.set_user_profile(OUTRA, {"username": "rita", "hide_from_leaderboard": True})
        # Quem não é administrador TEM sempre perfil: sem ele, o `load_user`
        # encerra-lhe a sessão. O caso sem perfil é mesmo só o do dono.
        data_manager.set_user_profile('123', {"username": "ana"})
        _autenticar(client, media_user_id='123', role="user")

        assert client.get(f'/api/statistics/user/{OUTRA}').status_code == 403
        assert client.get(f'/api/statistics/wrapped/{OUTRA}').status_code == 403


class TestONomeDeQuemNaoTemPerfil:
    """
    O nome só serve para as notificações de conquistas e para o XP — e quem o
    sabe sempre é o servidor de média. Ir buscá-lo lá é a diferença entre
    calcular as estatísticas e devolver uma página vazia.
    """

    def _manager(self, perfis=None, utilizador=None, monkeypatch=None):
        from app import extensions
        from app.services.stats_manager import StatsManager

        class ServidorFalso:
            def get_user_by_id(self, user_id):
                return utilizador

        monkeypatch.setattr(extensions, 'media_server', ServidorFalso())
        return StatsManager(data_manager=FakeDataManager(profiles=perfis or {}))

    def test_o_perfil_local_vem_primeiro(self, app_context, monkeypatch):
        manager = self._manager(perfis={ADMIN: {"username": "do painel"}},
                                utilizador={"username": "do servidor"},
                                monkeypatch=monkeypatch)

        assert manager._nome_do_utilizador(ADMIN) == "do painel"

    def test_sem_perfil_pergunta_se_ao_servidor(self, app_context, monkeypatch):
        manager = self._manager(utilizador={"username": "dono"}, monkeypatch=monkeypatch)

        assert manager._nome_do_utilizador(ADMIN) == "dono"

    def test_quando_ninguem_o_conhece_nao_se_inventa_um_nome(self, app_context, monkeypatch):
        manager = self._manager(utilizador=None, monkeypatch=monkeypatch)

        assert manager._nome_do_utilizador(ADMIN) is None

    def test_um_servidor_que_rebenta_nao_parte_as_estatisticas(self, app_context, monkeypatch):
        from app import extensions
        from app.services.stats_manager import StatsManager

        class ServidorPartido:
            def get_user_by_id(self, user_id):
                raise RuntimeError("offline")

        monkeypatch.setattr(extensions, 'media_server', ServidorPartido())
        manager = StatsManager(data_manager=FakeDataManager())

        assert manager._nome_do_utilizador(ADMIN) is None


class TestOsDetalhesSaemNaMesma:
    """Com o nome resolvido, a agregação corre — perfil ou não."""

    def _manager(self, monkeypatch, historico):
        from app import extensions
        from app.services.stats_manager import StatsManager

        class FonteFalsa:
            is_configured = True
            base_url = None
            api_key = None

            def get_history(self, **kwargs):
                return {"data": historico, "recordsFiltered": len(historico)}

            def get_recently_added(self, **kwargs):
                return {"recently_added": []}

            def get_metadata(self, rating_key):
                return {}

            def image_payload(self, thumb, width=300, height=450):
                return f"jellyfin:{thumb}" if thumb else None

        class ServidorFalso:
            def get_user_by_id(self, user_id):
                return {"username": "dono"}

        monkeypatch.setattr(extensions, 'media_server', ServidorFalso())
        return StatsManager(data_manager=FakeDataManager(), api_client=FonteFalsa())

    def _reproducao(self, **extra):
        base = {
            'date': 1757700000, 'duration': 3600, 'media_type': 'movie',
            'title': 'Duna', 'year': 2021, 'genres': ['Ficção científica'],
            'directors': [], 'platform': 'Web', 'player': 'Chrome',
            'thumb': '/Items/1/Images/Primary', 'user_id': ADMIN, 'user': 'dono',
            'percent_complete': 100, 'rating_key': '1', 'grandparent_title': '',
        }
        base.update(extra)
        return base

    def test_o_administrador_ve_as_suas_estatisticas(self, app_context, monkeypatch):
        manager = self._manager(monkeypatch, [self._reproducao()])

        detalhes = manager.get_user_watch_details(media_user_id=ADMIN)['details']

        assert detalhes['plays'] == 1
        assert detalhes['total_duration'] == 3600

    def test_e_tambem_o_seu_wrapped(self, app_context, monkeypatch):
        from datetime import datetime, timezone

        agora = datetime.now(timezone.utc)
        manager = self._manager(monkeypatch, [self._reproducao(date=int(agora.timestamp()))])

        resposta = manager.get_wrapped_data(media_user_id=ADMIN, year=agora.year)

        assert resposta['has_data'] is True
