# tests/test_plex_estatisticas.py

"""As estatísticas de um painel Plex SEM Tautulli.

🐛 REGRESSÃO REPORTADA: sem o Tautulli configurado, o pódio, o XP, as
conquistas, as recomendações e o Wrapped desapareciam do menu — e o histórico e
os aparelhos já vinham do próprio servidor, pela mesma razão pela qual estes
podem vir. O Plex sabe o que cada pessoa viu; o que ele não tem é o detalhe do
Tautulli, e isso mede-se em vez de se esconder a funcionalidade inteira.
"""

from xml.etree import ElementTree

import pytest

from app.services.media_server.plex.history import PlexHistoryManager
from app.services.media_server.plex.stats_api import (
    FonteDeEstatisticasDoPlex, PlexStatsApi,
)

pytestmark = pytest.mark.integration


def _video(**atributos):
    partes = ' '.join(f'{c}="{v}"' for c, v in atributos.items())
    return f'<Video {partes}/>'


class ServidorFalso:
    """O mínimo do PMS que a fonte de estatísticas usa."""

    def __init__(self, entradas=(), aparelhos=(), contas=(), itens=None, recentes=()):
        self.entradas = list(entradas)
        self.aparelhos = list(aparelhos)
        self.contas = list(contas)
        # ratingKey → atributos e listas de Genre/Director.
        self.itens = itens or {}
        self.recentes = list(recentes)
        self._baseurl = 'http://plex.test:32400'
        self.pedidos = []
        self.falhas = set()

    def query(self, key, headers=None, **kwargs):
        self.pedidos.append(key)
        if key.split('?')[0].rstrip('/') in self.falhas or key.split('?')[0] in self.falhas:
            raise RuntimeError("o servidor recusou")

        if key.startswith('/status/sessions/history/all'):
            return self._historico(key, headers or {})
        if key.startswith('/library/metadata/'):
            return self._metadados(key)
        if key.startswith('/library/recentlyAdded'):
            return self._recentes(headers or {})
        if key == '/devices':
            corpo = ''.join(
                f'<Device id="{a["id"]}" name="{a["name"]}" platform="{a["platform"]}"/>'
                for a in self.aparelhos
            )
            return ElementTree.fromstring(f'<MediaContainer>{corpo}</MediaContainer>')
        if key == '/accounts':
            corpo = ''.join(f'<Account id="{c["id"]}" name="{c["name"]}"/>' for c in self.contas)
            return ElementTree.fromstring(f'<MediaContainer>{corpo}</MediaContainer>')

        raise AssertionError(f"caminho inesperado: {key}")

    def _historico(self, key, headers):
        # ⚠️ Sem `accountID` o servidor devolve o histórico de TODA a gente — é
        # o que o pódio e as recomendações precisam. Com a chave presente,
        # filtra.
        if 'accountID=' in key:
            conta = key.split('accountID=')[1].split('&')[0]
            linhas = [e for e in self.entradas if str(e.get('accountID')) == conta]
        else:
            linhas = list(self.entradas)

        inicio = int(headers['X-Plex-Container-Start'])
        pagina = linhas[inicio:inicio + int(headers['X-Plex-Container-Size'])]
        corpo = ''.join(_video(**e) for e in pagina)
        return ElementTree.fromstring(
            f'<MediaContainer size="{len(pagina)}" totalSize="{len(linhas)}">{corpo}</MediaContainer>'
        )

    def _metadados(self, key):
        chaves = key.rsplit('/', 1)[1].split(',')
        corpo = ''
        for chave in chaves:
            item = self.itens.get(chave)
            if item is None:
                continue
            filhos = ''.join(f'<Genre tag="{g}"/>' for g in item.get('generos', []))
            filhos += ''.join(f'<Director tag="{d}"/>' for d in item.get('realizadores', []))
            atributos = ' '.join(
                f'{c}="{v}"' for c, v in item.items() if c not in ('generos', 'realizadores')
            )
            corpo += f'<Video ratingKey="{chave}" {atributos}>{filhos}</Video>'
        return ElementTree.fromstring(f'<MediaContainer>{corpo}</MediaContainer>')

    def _recentes(self, headers):
        quantos = int(headers['X-Plex-Container-Size'])
        corpo = ''.join(_video(**item) for item in self.recentes[:quantos])
        return ElementTree.fromstring(f'<MediaContainer>{corpo}</MediaContainer>')


class LigacaoFalsa:
    def __init__(self, plex, account=None):
        self.plex = plex
        self.account = account


class DonoFalso:
    def __init__(self, identificador, username):
        self.id = identificador
        self.username = username
        self.title = username


class BackendFalso:
    def __init__(self, servidor, dono=None):
        self.conn = LigacaoFalsa(servidor, dono)
        self.history = PlexHistoryManager(self.conn)


@pytest.fixture(autouse=True)
def cache_limpa(app_context):
    """A cache das contas e dos aparelhos é de ficheiro: sobrevive aos testes."""
    from app.extensions import cache

    cache.clear()
    yield
    cache.clear()


ITENS = {
    '10': {'duration': '9000000', 'year': '2021', 'addedAt': '1690000000',
           'generos': ['Ficção científica', 'Aventura'], 'realizadores': ['Denis Villeneuve']},
    '20': {'duration': '2700000', 'year': '2008', 'addedAt': '1680000000', 'generos': ['Drama']},
}


def _filme(**extra):
    base = {'type': 'movie', 'title': 'Duna', 'year': '2021', 'viewedAt': '1700000000',
            'accountID': '77', 'deviceID': '5', 'ratingKey': '10',
            'thumb': '/library/metadata/10/thumb/1'}
    base.update(extra)
    return base


def _fonte(entradas=(), aparelhos=(), contas=None, dono=None, itens=None, recentes=()):
    contas = contas if contas is not None else [{'id': '77', 'name': 'ana'}]
    servidor = ServidorFalso(entradas, aparelhos, contas, itens or ITENS, recentes)
    backend = BackendFalso(servidor, dono)
    return PlexStatsApi(lambda: backend), servidor


class TestEstado:
    def test_ligado_e_o_que_basta(self):
        fonte, _servidor = _fonte()

        assert fonte.is_configured is True

    def test_sem_ligacao_nao_ha_fonte(self):
        fonte = PlexStatsApi(lambda: BackendFalso(None))

        assert fonte.is_configured is False
        assert fonte.get_history() == {"data": [], "recordsFiltered": 0}

    def test_nao_tem_credenciais_proprias(self):
        # Quem as lê (o estado do sistema, a allowlist do proxy de imagens) tem
        # de encontrar None, e não um AttributeError.
        fonte, _servidor = _fonte()

        assert fonte.base_url is None and fonte.api_key is None
        fonte.reload_config()


class TestHistorico:
    def test_traduz_uma_reproducao(self):
        fonte, _servidor = _fonte(
            entradas=[_filme()],
            aparelhos=[{'id': '5', 'name': 'Sala', 'platform': 'Android TV'}],
        )

        linha = fonte.get_history()['data'][0]

        assert linha['title'] == 'Duna'
        assert linha['media_type'] == 'movie'
        assert linha['rating_key'] == '10'
        assert linha['user_id'] == '77'
        assert linha['user'] == 'ana'
        assert linha['player'] == 'Sala'
        assert linha['platform'] == 'Android TV'
        assert linha['date'] == 1700000000

    def test_a_duracao_vem_dos_metadados_em_segundos(self):
        # ⚠️ O Plex conta em MILISSEGUNDOS e a agregação em segundos, como o
        # Tautulli devolve. Sem dividir, uma hora de filme valia mil vezes o XP.
        fonte, _servidor = _fonte(entradas=[_filme()])

        assert fonte.get_history()['data'][0]['duration'] == 9000

    def test_os_generos_e_o_realizador_vem_dos_metadados(self):
        fonte, _servidor = _fonte(entradas=[_filme()])

        linha = fonte.get_history()['data'][0]

        assert linha['genres'] == ['Ficção científica', 'Aventura']
        assert linha['directors'] == ['Denis Villeneuve']
        assert linha['added_at'] == 1690000000

    def test_os_metadados_sao_pedidos_numa_chamada_so(self):
        # Uma chamada por linha punha o pódio a fazer milhares de pedidos.
        entradas = [_filme(ratingKey='10', viewedAt='1700000001'),
                    _filme(ratingKey='20', viewedAt='1700000002'),
                    _filme(ratingKey='10', viewedAt='1700000003')]
        fonte, servidor = _fonte(entradas=entradas)

        fonte.get_history()

        metadados = [p for p in servidor.pedidos if p.startswith('/library/metadata/')]
        assert metadados == ['/library/metadata/10,20']

    def test_um_item_apagado_da_biblioteca_conta_na_mesma(self):
        # O histórico guarda o título; os metadados é que já não voltam.
        fonte, _servidor = _fonte(entradas=[_filme(ratingKey='99', title='Apagado')])

        linha = fonte.get_history()['data'][0]

        assert linha['title'] == 'Apagado'
        assert linha['duration'] == 0
        assert linha['genres'] == []

    def test_um_episodio_traz_a_serie(self):
        fonte, _servidor = _fonte(entradas=[_filme(
            type='episode', title='O Piloto', grandparentTitle='Severance',
            grandparentRatingKey='500', index='1', parentIndex='2',
            parentTitle='Temporada 2', grandparentThumb='/serie/thumb',
        )])

        linha = fonte.get_history()['data'][0]

        assert linha['media_type'] == 'episode'
        assert linha['title'] == 'O Piloto'
        assert linha['grandparent_title'] == 'Severance'
        assert linha['grandparent_rating_key'] == '500'
        assert linha['media_index'] == 1 and linha['parent_media_index'] == 2
        assert linha['grandparent_thumb'] == '/serie/thumb'

    def test_a_entrada_existir_quer_dizer_visto_ate_ao_fim(self):
        # ⚠️ O Plex só cria a entrada quando o item é dado por VISTO. Um 0 aqui
        # fazia o mínimo de percentagem das recomendações deitar fora tudo.
        fonte, _servidor = _fonte(entradas=[_filme()])

        assert fonte.get_history()['data'][0]['percent_complete'] == 100


class TestQuemViu:
    def test_o_dono_e_traduzido_para_o_id_do_painel(self):
        # ⚠️ No servidor o dono é a conta 1 e no plex.tv tem outro id. Sem
        # traduzir, as reproduções dele ficavam agrupadas sob "1": no pódio
        # aparecia como um estranho, sem nível, sem cara e sem perfil.
        fonte, _servidor = _fonte(
            entradas=[_filme(accountID='1')],
            contas=[{'id': '1', 'name': 'dono'}, {'id': '77', 'name': 'ana'}],
            dono=DonoFalso(4242, 'dono'),
        )

        assert fonte.get_history()['data'][0]['user_id'] == '4242'

    def test_uma_conta_partilhada_mantem_o_id(self):
        fonte, _servidor = _fonte(
            entradas=[_filme(accountID='77')],
            contas=[{'id': '1', 'name': 'dono'}, {'id': '77', 'name': 'ana'}],
            dono=DonoFalso(4242, 'dono'),
        )

        assert fonte.get_history()['data'][0]['user_id'] == '77'

    def test_pedir_o_historico_de_uma_pessoa_filtra_no_servidor(self):
        fonte, servidor = _fonte(
            entradas=[_filme(accountID='77'), _filme(accountID='88', title='Outro')],
            contas=[{'id': '77', 'name': 'ana'}, {'id': '88', 'name': 'bruno'}],
        )

        linhas = fonte.get_history(user_id='88')['data']

        assert [l['title'] for l in linhas] == ['Outro']
        historico = [p for p in servidor.pedidos if p.startswith('/status/sessions/history/all')]
        assert 'accountID=88' in historico[0]

    def test_sem_user_id_vem_o_historico_de_toda_a_gente(self):
        # É o que o pódio pede. Mandar `accountID` vazio não devolvia nada.
        fonte, servidor = _fonte(
            entradas=[_filme(accountID='77'), _filme(accountID='88')],
            contas=[{'id': '77', 'name': 'ana'}, {'id': '88', 'name': 'bruno'}],
        )

        assert len(fonte.get_history()['data']) == 2
        historico = [p for p in servidor.pedidos if p.startswith('/status/sessions/history/all')]
        assert 'accountID' not in historico[0]

    def test_quem_o_servidor_nao_conhece_nao_traz_o_de_todos(self):
        # Devolver o histórico inteiro aqui mostrava a uma pessoa o que as
        # outras viram.
        fonte, _servidor = _fonte(entradas=[_filme(accountID='77')])

        assert fonte.get_history(user_id='999') == {"data": [], "recordsFiltered": 0}


class TestFiltros:
    def test_after_corta_pela_data(self):
        fonte, _servidor = _fonte(entradas=[
            _filme(viewedAt='1700000000', title='Recente'),
            _filme(viewedAt='1600000000', title='Antigo'),
        ])

        linhas = fonte.get_history(after='2023-06-01')['data']

        assert [l['title'] for l in linhas] == ['Recente']

    def test_uma_data_ilegivel_nao_deita_o_historico_fora(self):
        fonte, _servidor = _fonte(entradas=[_filme()])

        assert len(fonte.get_history(after='ontem')['data']) == 1

    def test_a_pesquisa_cobre_o_titulo_e_a_serie(self):
        fonte, _servidor = _fonte(entradas=[
            _filme(title='Duna'),
            _filme(type='episode', title='O Piloto', grandparentTitle='Severance'),
        ])

        assert len(fonte.get_history(search='sever')['data']) == 1
        assert len(fonte.get_history(search='duna')['data']) == 1

    def test_a_paginacao_conta_o_total_antes_de_cortar(self):
        entradas = [_filme(viewedAt=str(1700000000 + i)) for i in range(5)]
        fonte, _servidor = _fonte(entradas=entradas)

        resposta = fonte.get_history(start=2, length=2)

        assert len(resposta['data']) == 2
        assert resposta['recordsFiltered'] == 5

    def test_uma_falha_do_servidor_nao_rebenta(self):
        fonte, servidor = _fonte(entradas=[_filme()])
        servidor.falhas.add('/status/sessions/history/all')

        assert fonte.get_history() == {"data": [], "recordsFiltered": 0}


class TestOutrasPerguntas:
    def test_recem_adicionados(self):
        fonte, _servidor = _fonte(recentes=[
            {'type': 'movie', 'title': 'Duna', 'year': '2021',
             'addedAt': '1690000000', 'ratingKey': '10', 'thumb': '/t'},
        ])

        recente = fonte.get_recently_added(count=10)['recently_added'][0]

        assert recente['title'] == 'Duna'
        assert recente['added_at'] == 1690000000
        assert recente['media_type'] == 'movie'

    def test_os_generos_de_uma_serie_pelo_id_do_avo(self):
        # É assim que um EPISÓDIO ganha géneros: eles vivem na série.
        fonte, _servidor = _fonte(itens={'500': {'generos': ['Mistério'], 'year': '2022'}})

        assert fonte.get_metadata('500')['genres'] == ['Mistério']

    def test_a_capa_vai_pelo_proxy_do_plex(self):
        fonte, _servidor = _fonte()

        assert fonte.image_payload('/library/metadata/10/thumb/1') == 'plex:/library/metadata/10/thumb/1'
        assert fonte.image_payload(None) is None


class TestDespachante:
    """Tautulli quando o há; o servidor quando não."""

    class TautulliFalso:
        def __init__(self, configurado):
            self.is_configured = configurado
            self.base_url = 'http://tautulli:8181' if configurado else ''
            self.api_key = 'chave' if configurado else None
            self.recarregado = False

        def get_history(self, **kwargs):
            return {"data": ["do tautulli"], "recordsFiltered": 1}

        def get_recently_added(self, **kwargs):
            return {"recently_added": ["do tautulli"]}

        def get_metadata(self, rating_key):
            return {"genres": ["do tautulli"]}

        def image_payload(self, thumb, width=300, height=450):
            return f"tautulli:{thumb}"

        def test_connection(self, url, api_key):
            return {"success": True, "message": "do tautulli"}

        def reload_config(self):
            self.recarregado = True

    def _despachante(self, configurado, entradas=(_filme(),)):
        servidor = ServidorFalso(entradas, [], [{'id': '77', 'name': 'ana'}], ITENS, [])
        backend = BackendFalso(servidor)
        return FonteDeEstatisticasDoPlex(
            lambda: backend, cliente_externo=self.TautulliFalso(configurado)
        )

    def test_com_tautulli_e_o_tautulli_que_responde(self):
        fonte = self._despachante(True)

        assert fonte.get_history()['data'] == ["do tautulli"]
        assert fonte.get_recently_added()['recently_added'] == ["do tautulli"]
        assert fonte.get_metadata('10')['genres'] == ["do tautulli"]
        assert fonte.image_payload('/t') == "tautulli:/t"

    def test_sem_tautulli_responde_o_proprio_plex(self):
        fonte = self._despachante(False)

        assert fonte.get_history()['data'][0]['title'] == 'Duna'
        assert fonte.image_payload('/t') == "plex:/t"

    def test_ha_estatisticas_nos_dois_casos(self):
        assert self._despachante(True).is_configured is True
        assert self._despachante(False).is_configured is True

    def test_o_estado_do_tautulli_e_perguntado_a_parte(self):
        # ⚠️ `is_configured` responde "há estatísticas"; quem quer saber se o
        # TAUTULLI está ligado pergunta `externa_ativa`. Sem esta distinção, o
        # estado do sistema mostrava o Tautulli OFFLINE num painel a funcionar
        # perfeitamente sem ele.
        assert self._despachante(True).externa_ativa is True
        assert self._despachante(False).externa_ativa is False

    def test_o_que_e_do_tautulli_continua_a_ser_do_tautulli(self):
        # É deles que vivem o cartão das Conexões e o botão "Testar".
        fonte = self._despachante(True)

        assert fonte.base_url == 'http://tautulli:8181'
        assert fonte.api_key == 'chave'
        assert fonte.test_connection('u', 'k')['message'] == 'do tautulli'

        fonte.reload_config()
        assert fonte.externa.recarregado is True

    def test_a_escolha_e_feita_a_cada_pergunta(self):
        # Configurar o Tautulli não pode obrigar a reiniciar o painel.
        fonte = self._despachante(False)
        assert fonte.get_history()['data'][0]['title'] == 'Duna'

        fonte.externa.is_configured = True
        assert fonte.get_history()['data'] == ["do tautulli"]
