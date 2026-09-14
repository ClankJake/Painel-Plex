# tests/test_jellyfin_estatisticas.py

"""As estatísticas de um painel Jellyfin.

O pódio, o XP, as conquistas, as recomendações e o Wrapped saem todos de uma
lista de REPRODUÇÕES. A agregação é a mesma que já servia o Plex
(`services/tautulli/stats_handler.py`): o que muda é quem fornece essa lista.
Estes testes guardam a fonte do Jellyfin — e, sobretudo, que a agregação
partilhada continua a dar o mesmo resultado quando alimentada por ela.

O duplo do servidor responde ao `/Items` conforme os PARÂMETROS, porque é neles
que está a diferença entre "o que este utilizador viu" e "os metadados destes
itens" — um duplo que devolvesse sempre a mesma coisa passaria com as duas
consultas trocadas.
"""

from datetime import datetime, timezone

import pytest

from app.services.media_server.jellyfin.stats_api import JellyfinStatsApi
from app.services.stats_manager import StatsManager
from tests.conftest import FakeDataManager
from tests.test_jellyfin_backend import GUID, OUTRO, _fonte_da_imagem, montar

pytestmark = pytest.mark.integration

ID_DO_PLUGIN = '5c534381-91a3-43cb-907a-35aa02eb9d2c'
COM_HIFENES = '38c3a1f0-e4b2-4d7f-9c1a-0b5e6d7f8a90'

# Um dia inteiro em segundos de 2026-09-12, para as datas dos testes.
QUANDO = '2026-09-12 20:23:28'


def _item(item_id='item-1', **extra):
    base = {
        'Id': item_id,
        'Name': 'Duna',
        'Type': 'Movie',
        'ProductionYear': 2021,
        'RunTimeTicks': 9000 * 10_000_000,   # 2h30
        'Genres': ['Ficção científica', 'Aventura'],
        'People': [{'Name': 'Denis Villeneuve', 'Type': 'Director'},
                   {'Name': 'Timothée Chalamet', 'Type': 'Actor'}],
        'ImageTags': {'Primary': 'capa1'},
        'DateCreated': '2026-09-01T10:00:00.0000000Z',
        'UserData': {'Played': True},
    }
    base.update(extra)
    return base


class ApiDeEstatisticas:
    """O Jellyfin, respondendo ao `/Items` conforme o que lhe é pedido."""

    def __init__(self, itens=(), plugin=None, vistos=None):
        self.itens = {i['Id']: i for i in itens}
        self.plugin = plugin          # linhas do PlaybackActivity, ou None
        self.vistos = vistos or {}    # id do utilizador -> itens vistos
        self.consultas = []
        self.pedidos = []
        self.base_url = "http://jellyfin.local:8096"
        self.api_key = "chave"
        self.is_configured = True

    # --- o que o adaptador usa ---
    def get(self, endpoint, params=None, **kwargs):
        params = params or {}
        self.pedidos.append((endpoint, params))

        if endpoint == '/Plugins':
            return [{"Id": ID_DO_PLUGIN, "Name": "Playback Reporting"}] if self.plugin is not None else []

        if endpoint == '/Users':
            return [{"Id": GUID, "Name": "ana"}, {"Id": OUTRO, "Name": "rita"}]

        if endpoint == '/Items':
            if params.get('ids'):
                pedidos = params['ids']
                return {'Items': [self.itens[i] for i in pedidos if i in self.itens]}
            if params.get('Filters') == 'IsPlayed':
                return {'Items': self.vistos.get(params.get('userId'), [])}
            # Recentes: ordenados por data de entrada na biblioteca.
            return {'Items': list(self.itens.values())[:int(params.get('Limit') or 50)]}

        return None

    def post(self, endpoint, json=None, **kwargs):
        if endpoint == '/user_usage_stats/submit_custom_query':
            self.consultas.append(json['CustomQueryString'])
            return {'results': self.plugin or []}
        return None

    def reload_config(self):
        pass


def _fonte(itens=(), plugin=None, vistos=None):
    """Uma fonte de estatísticas ligada a um backend Jellyfin falso."""
    backend = montar(data_manager=FakeDataManager())
    backend.conn.api = ApiDeEstatisticas(itens, plugin, vistos)
    return JellyfinStatsApi(lambda: backend), backend


@pytest.fixture(autouse=True)
def cache_limpa(app_context):
    """A deteção do plugin fica em cache (em disco, partilhada entre testes)."""
    from app.extensions import cache

    cache.clear()
    yield cache
    cache.clear()


class TestComOPlugin:
    """O bom caminho: uma linha por reprodução, com o tempo que foi visto."""

    def test_traduz_uma_reproducao(self):
        fonte, _backend = _fonte(
            itens=[_item()],
            plugin=[[QUANDO, COM_HIFENES, 'item-1', 'Movie', 'Duna', 'Jellyfin Android', 'M23', 4500]],
        )

        linha = fonte.get_history()['data'][0]

        assert linha['date'] == int(datetime(2026, 9, 12, 20, 23, 28, tzinfo=timezone.utc).timestamp())
        assert linha['duration'] == 4500
        assert linha['media_type'] == 'movie'
        assert linha['title'] == 'Duna'
        assert linha['year'] == 2021
        assert linha['genres'] == ['Ficção científica', 'Aventura']
        assert linha['directors'] == ['Denis Villeneuve']
        assert linha['platform'] == 'Jellyfin Android'
        assert linha['player'] == 'M23'
        # 4500s de 9000s: o plugin guarda os segundos vistos, não a percentagem.
        assert linha['percent_complete'] == 50

    def test_o_id_do_utilizador_perde_os_hifenes(self):
        # ⚠️ O plugin grava o GUID com hífenes e o painel guarda-o sem. O pódio
        # junta o histórico aos perfis por este id: com as duas grafias, cada
        # pessoa aparecia duas vezes — ou nenhuma.
        fonte, _backend = _fonte(
            itens=[_item()],
            plugin=[[QUANDO, COM_HIFENES, 'item-1', 'Movie', 'Duna', 'Web', 'Chrome', 100]],
        )

        assert fonte.get_history()['data'][0]['user_id'] == GUID
        assert fonte.get_history()['data'][0]['user'] == 'ana'

    def test_um_episodio_traz_a_serie_e_a_capa_da_serie(self):
        item = _item('ep-1', Name='O Início', Type='Episode', SeriesName='Arcane',
                     SeriesId='serie-1', SeriesPrimaryImageTag='capaserie',
                     IndexNumber=3, ParentIndexNumber=2)
        fonte, _backend = _fonte(
            itens=[item],
            plugin=[[QUANDO, COM_HIFENES, 'ep-1', 'Episode', 'Arcane - S02E03 - O Início',
                     'Web', 'Chrome', 1000]],
        )

        linha = fonte.get_history()['data'][0]

        assert linha['media_type'] == 'episode'
        assert linha['grandparent_title'] == 'Arcane'
        assert linha['grandparent_rating_key'] == 'serie-1'
        assert 'serie-1' in linha['grandparent_thumb']

    def test_o_periodo_e_o_utilizador_vao_na_consulta(self):
        fonte, backend = _fonte(itens=[_item()], plugin=[])

        fonte.get_history(after='2026-09-01', user_id=GUID)

        sql = backend.conn.api.consultas[-1]
        assert "DateCreated >= '2026-09-01'" in sql
        # As duas grafias do mesmo GUID: o plugin pode ter gravado qualquer uma.
        assert GUID in sql and COM_HIFENES in sql

    def test_um_id_que_nao_e_um_guid_nao_chega_ao_sql(self):
        # 🛡️ O plugin só aceita SQL em texto: um id que não pareça um GUID não
        # é um engano de escrita, e aí nem se tenta a consulta.
        fonte, backend = _fonte(itens=[_item()], plugin=[])

        resposta = fonte.get_history(user_id="'; DROP TABLE PlaybackActivity; --")

        assert resposta == {"data": [], "recordsFiltered": 0}
        assert backend.conn.api.consultas == []

    def test_uma_data_ilegivel_salta_a_linha_em_vez_de_rebentar(self):
        fonte, _backend = _fonte(
            itens=[_item()],
            plugin=[['nao é uma data', COM_HIFENES, 'item-1', 'Movie', 'Duna', 'Web', 'Chrome', 10],
                    [QUANDO, COM_HIFENES, 'item-1', 'Movie', 'Duna', 'Web', 'Chrome', 10]],
        )

        assert len(fonte.get_history()['data']) == 1

    def test_um_item_apagado_da_biblioteca_continua_a_contar(self):
        # O plugin guarda o nome; a capa e os géneros é que se perdem.
        fonte, _backend = _fonte(
            itens=[],
            plugin=[[QUANDO, COM_HIFENES, 'item-sumiu', 'Movie', 'Duna', 'Web', 'Chrome', 500]],
        )

        linha = fonte.get_history()['data'][0]

        assert linha['title'] == 'Duna'
        assert linha['duration'] == 500
        assert linha['genres'] == []


class TestSemOPlugin:
    """O núcleo só sabe que itens cada pessoa deu por vistos."""

    def test_conta_um_item_visto_como_uma_reproducao(self):
        visto = _item(UserData={'Played': True, 'LastPlayedDate': '2026-09-12T20:23:28.0000000Z'})
        fonte, _backend = _fonte(itens=[visto], vistos={GUID: [visto], OUTRO: []})

        linhas = fonte.get_history()['data']

        assert len(linhas) == 1
        # Sem o plugin não há tempo assistido: usa-se a duração do item.
        assert linhas[0]['duration'] == 9000
        assert linhas[0]['percent_complete'] == 100
        assert linhas[0]['user_id'] == GUID

    def test_respeita_o_periodo_pedido(self):
        antigo = _item('velho', UserData={'Played': True, 'LastPlayedDate': '2020-01-01T00:00:00Z'})
        recente = _item('novo', UserData={'Played': True, 'LastPlayedDate': '2026-09-12T10:00:00Z'})
        fonte, _backend = _fonte(itens=[antigo, recente], vistos={GUID: [antigo, recente]})

        linhas = fonte.get_history(after='2026-09-01')['data']

        assert [l['rating_key'] for l in linhas] == ['novo']

    def test_um_utilizador_de_cada_vez_quando_se_pede_um(self):
        visto = _item(UserData={'Played': True, 'LastPlayedDate': '2026-09-12T10:00:00Z'})
        fonte, backend = _fonte(itens=[visto], vistos={GUID: [visto], OUTRO: [visto]})

        linhas = fonte.get_history(user_id=OUTRO)['data']

        assert [l['user_id'] for l in linhas] == [OUTRO]
        pedidos = [p for e, p in backend.conn.api.pedidos if e == '/Items' and p.get('Filters')]
        assert [p['userId'] for p in pedidos] == [OUTRO]

    def test_um_item_sem_data_de_reproducao_nao_entra(self):
        sem_data = _item(UserData={'Played': True})
        fonte, _backend = _fonte(itens=[sem_data], vistos={GUID: [sem_data]})

        assert fonte.get_history()['data'] == []


class TestImagens:
    def test_a_capa_vai_com_o_prefixo_do_jellyfin(self):
        # 🐛 O prefixo era `tautulli:` escrito à mão no agregador: as capas do
        # Jellyfin iam pedir-se ao Tautulli, que não existe neste painel.
        fonte, _backend = _fonte()

        payload = fonte.image_payload('/Items/1/Images/Primary?tag=abc', 200, 300)

        assert payload.startswith('jellyfin:/Items/1/Images/Primary?tag=abc')
        assert 'fillWidth=200' in payload and 'fillHeight=300' in payload

    def test_sem_capa_nao_ha_payload(self):
        fonte, _backend = _fonte()

        assert fonte.image_payload(None) is None


class TestOutrasPerguntas:
    def test_os_itens_recentes(self):
        fonte, _backend = _fonte(itens=[_item()])

        recente = fonte.get_recently_added(count=10)['recently_added'][0]

        assert recente['title'] == 'Duna'
        assert recente['media_type'] == 'movie'
        assert recente['rating_key'] == 'item-1'
        assert recente['added_at'] > 0

    def test_os_generos_de_um_item(self):
        fonte, _backend = _fonte(itens=[_item()])

        assert fonte.get_metadata('item-1')['genres'] == ['Ficção científica', 'Aventura']

    def test_sem_ligacao_nao_se_inventa_historico(self):
        fonte, backend = _fonte(itens=[_item()])
        backend.conn.server_info = None

        assert fonte.get_history() == {"data": [], "recordsFiltered": 0}
        assert fonte.is_configured is False


class TestAAgregacaoPartilhada:
    """
    A razão de ser de tudo isto: a agregação não foi reescrita. O mesmo
    `StatsHandler` que serve o Plex produz o pódio e os detalhes a partir desta
    fonte — uma segunda cópia teria divergido no primeiro ajuste ao XP.
    """

    def _manager(self, perfis=None):
        plugin = [
            [QUANDO, COM_HIFENES, 'item-1', 'Movie', 'Duna', 'Jellyfin Android', 'M23', 4500],
            ['2026-09-12 21:00:00', COM_HIFENES, 'item-1', 'Movie', 'Duna', 'Web', 'Chrome', 1500],
        ]
        fonte, backend = _fonte(itens=[_item()], plugin=plugin)
        backend.data_manager = FakeDataManager(profiles=perfis or {GUID: {"username": "ana"}})
        return StatsManager(data_manager=backend.data_manager, api_client=fonte), backend

    def test_o_podio_soma_o_tempo_de_cada_pessoa(self):
        manager, _backend = self._manager()

        stats = manager.stats.get_watch_stats(days=30)['stats']

        assert len(stats) == 1
        assert stats[0]['user_id'] == GUID
        assert stats[0]['username'] == 'ana'
        assert stats[0]['plays'] == 2
        assert stats[0]['total_duration'] == 6000

    def test_os_detalhes_de_um_utilizador_saem_da_mesma_fonte(self):
        manager, _backend = self._manager()

        detalhes = manager.stats.get_user_watch_details(GUID, 'ana', days=30)['details']

        assert detalhes['plays'] == 2
        assert detalhes['movie_count'] == 2
        assert detalhes['favorite_genre'] == 'Ficção científica'
        # As capas da lista "visto recentemente" passam pelo proxy do painel, e
        # com o prefixo do JELLYFIN — não com o do Tautulli, que não existe aqui.
        assert 'jellyfin:/Items/item-1' in _fonte_da_imagem(detalhes['recent'][0]['poster_url'])

    def test_o_xp_conta_os_minutos_vistos(self):
        manager, backend = self._manager()

        xp = manager.stats.sync_user_xp(GUID, 'ana')

        # 6000 segundos = 100 minutos, mais o bónus de quem terminou o item.
        assert xp and xp >= 100


class TestCapacidades:
    def test_o_jellyfin_tem_estatisticas_e_nao_tem_o_que_configurar(self):
        backend = montar()

        # ⚠️ São duas bandeiras diferentes: há estatísticas, mas a fonte é o
        # próprio servidor — não há cartão do Tautulli para mostrar.
        assert backend.capabilities.estatisticas is True
        assert backend.capabilities.estatisticas_externas is False
        assert backend.estatisticas_disponiveis() is True

    def test_no_plex_a_fonte_continua_a_ser_externa(self):
        from app.services.media_server.plex import PlexManager

        assert PlexManager(None, None, None, None).capabilities.estatisticas_externas is True


class TestALigacaoNoArranque:
    """
    A fonte é escolhida no `create_app`, pelo tipo de servidor — e recebe uma
    função que vai buscar o backend, porque nesse momento ele ainda não existe.
    """

    def test_um_painel_jellyfin_usa_o_proprio_servidor(self):
        from app import _fonte_de_estatisticas

        assert isinstance(_fonte_de_estatisticas('jellyfin'), JellyfinStatsApi)

    def test_um_painel_plex_usa_o_despachante(self):
        from app import _fonte_de_estatisticas
        from app.services.media_server.plex.stats_api import FonteDeEstatisticasDoPlex

        # ⚠️ Já não é o Tautulli sozinho: é ele quando está configurado e o
        # próprio Plex quando não está. Antes devolvia None, e quem não tinha
        # Tautulli ficava sem pódio, sem XP, sem conquistas e sem Wrapped.
        for tipo in ('plex', None):
            assert isinstance(_fonte_de_estatisticas(tipo), FonteDeEstatisticasDoPlex)

    def test_o_estado_da_ligacao_nao_rebenta_sem_credenciais(self):
        # A fonte do Jellyfin não tem URL nem chave próprios: quem os lê (o
        # estado do sistema, o proxy de imagens) tem de encontrar None, e não
        # um AttributeError.
        fonte, _backend = _fonte()
        manager = StatsManager(data_manager=FakeDataManager(), api_client=fonte)

        assert manager.check_status()['status'] in ('ONLINE', 'DISABLED')
        assert fonte.base_url is None and fonte.api_key is None

    def test_a_fonte_aguenta_o_backend_ainda_nao_existir(self):
        # É o estado do arranque: o manager é construído antes do servidor.
        fonte = JellyfinStatsApi(lambda: None)

        assert fonte.is_configured is False
        assert fonte.get_history() == {"data": [], "recordsFiltered": 0}


class TestLinkParaOItem:
    """
    🐛 O link "Ver no X" das recomendações era montado na rota, à mão, com um
    endereço de app.plex.tv — num painel Jellyfin dava um botão que levava a
    lado nenhum. Quem sabe montá-lo é o backend.
    """

    def test_o_jellyfin_aponta_para_a_sua_propria_interface(self, monkeypatch):
        backend = montar()
        monkeypatch.setattr(backend.conn.api, 'base_url', 'http://jellyfin.local:8096')

        link = backend.link_para_item('item-1')

        assert link.startswith('http://jellyfin.local:8096/web/#/details?id=item-1')
        assert 'serverId=servidor-1' in link

    def test_sem_ligacao_nao_ha_link(self):
        backend = montar()
        backend.conn.api.base_url = ''

        assert backend.link_para_item('item-1') is None

    def test_o_plex_continua_a_apontar_para_o_plex_tv(self, monkeypatch):
        from app.services.media_server.plex import PlexManager

        backend = PlexManager(None, None, None, None)
        monkeypatch.setattr(backend.conn, 'get_server_identifier', lambda: 'maquina-1')

        assert backend.link_para_item(42) == (
            'https://app.plex.tv/desktop#!/server/maquina-1'
            '/details?key=%2Flibrary%2Fmetadata%2F42'
        )
