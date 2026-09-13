# tests/test_plex_historico_sem_tautulli.py

"""O histórico e os aparelhos quando o Plex está sozinho.

O Tautulli é opcional: quem não o configura continua a ver o histórico e os
"Dispositivos Vinculados" — lidos do próprio Plex, mais devagar e com menos
detalhe. O que estes testes guardam é a diferença entre "não sei" e "não viu
nada", que era o que a página dizia antes.

O duplo do servidor modela os campos que DECIDEM: o `accountID` pelo qual o
histórico é filtrado e os cabeçalhos de paginação. Um duplo que os ignorasse
passava com o filtro trocado — foi assim que a lista de aparelhos do Jellyfin
chegou igual para toda a gente.
"""

import base64
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import pytest

from app.services.media_server.plex.history import JANELA, PlexHistoryManager

pytestmark = pytest.mark.integration


def _fonte(url_do_proxy):
    """O payload que o proxy de imagens leva em base64."""
    bruto = parse_qs(urlparse(url_do_proxy).query)['source'][0]
    return base64.urlsafe_b64decode(bruto.encode()).decode()


def _elemento(atributos):
    partes = ' '.join(f'{chave}="{valor}"' for chave, valor in atributos.items())
    return f'<Video {partes}/>'


class ServidorFalso:
    """O mínimo do PMS que este módulo usa, com o comportamento que importa."""

    def __init__(self, entradas=(), aparelhos=(), contas=()):
        self.entradas = list(entradas)
        self.aparelhos = list(aparelhos)
        self.contas = list(contas)
        self._baseurl = 'http://plex.test:32400'
        self.pedidos = []
        self.cabecalhos = []
        self.falhas = set()

    def query(self, key, headers=None, **kwargs):
        self.pedidos.append(key)

        if key.startswith('/status/sessions/history/all'):
            self.cabecalhos.append(dict(headers or {}))
            if 'historico' in self.falhas:
                raise RuntimeError("o servidor recusou")
            return self._historico(key, headers or {})

        if key == '/devices':
            if 'devices' in self.falhas:
                raise RuntimeError("o servidor recusou")
            corpo = ''.join(
                f'<Device id="{a["id"]}" name="{a["name"]}" platform="{a["platform"]}"/>'
                for a in self.aparelhos
            )
            return ElementTree.fromstring(f'<MediaContainer>{corpo}</MediaContainer>')

        if key == '/accounts':
            if 'accounts' in self.falhas:
                raise RuntimeError("o servidor recusou")
            corpo = ''.join(
                f'<Account id="{c["id"]}" name="{c["name"]}"/>' for c in self.contas
            )
            return ElementTree.fromstring(f'<MediaContainer>{corpo}</MediaContainer>')

        raise AssertionError(f"caminho inesperado: {key}")

    def _historico(self, key, headers):
        # O filtro por conta é do SERVIDOR: se o painel mandar o id errado, o
        # resultado tem de vir vazio — é justamente o caso do dono.
        conta = key.split('accountID=')[1].split('&')[0]
        minhas = [e for e in self.entradas if str(e.get('accountID')) == conta]

        inicio = int(headers['X-Plex-Container-Start'])
        tamanho = int(headers['X-Plex-Container-Size'])
        pagina = minhas[inicio:inicio + tamanho]

        corpo = ''.join(_elemento(e) for e in pagina)
        return ElementTree.fromstring(
            f'<MediaContainer size="{len(pagina)}" totalSize="{len(minhas)}">{corpo}</MediaContainer>'
        )


class LigacaoFalsa:
    def __init__(self, plex, account=None):
        self.plex = plex
        self.account = account


class DonoFalso:
    """A conta plex.tv do dono, como a `plexapi` a devolve."""

    def __init__(self, identificador, username):
        self.id = identificador
        self.username = username
        self.title = username


@pytest.fixture(autouse=True)
def cache_limpa(app_context):
    """A cache das contas e dos aparelhos é de ficheiro: sobrevive aos testes."""
    from app.extensions import cache

    cache.clear()
    yield
    cache.clear()


def _filme(**extra):
    base = {'type': 'movie', 'title': 'Duna', 'year': '2021', 'viewedAt': '1700000000',
            'accountID': '77', 'deviceID': '5', 'thumb': '/library/metadata/10/thumb/1'}
    base.update(extra)
    return base


def _gestor(entradas=(), aparelhos=(), contas=(), dono=None):
    contas = contas or [{'id': '77', 'name': 'ana'}]
    return PlexHistoryManager(LigacaoFalsa(ServidorFalso(entradas, aparelhos, contas), dono))


class TestHistorico:
    def test_traduz_um_filme_para_a_forma_que_a_tabela_consome(self):
        gestor = _gestor([_filme()], aparelhos=[{'id': '5', 'name': 'Sala', 'platform': 'Android'}])

        resposta = gestor.get_watch_history('77')

        assert resposta['success'] is True
        linha = resposta['history'][0]
        assert linha['title'] == 'Duna'
        assert linha['subtitle'] == '2021'
        assert linha['date'] == '14/11/2023 22:13'
        assert linha['player'] == 'Sala'
        # A entrada existir já quer dizer que foi visto até ao fim: o Plex não
        # regista o que ficou a meio.
        assert linha['percent_complete'] == 100
        # A capa vai pelo proxy de imagens, com o prefixo do backend — é ele
        # que sabe que aquilo se pede ao Plex com o token.
        assert _fonte(linha['poster_url']) == 'plex:/library/metadata/10/thumb/1'

    def test_um_episodio_traz_a_serie_no_titulo_e_a_capa_da_serie(self):
        gestor = _gestor([_filme(
            type='episode', title='O Início', grandparentTitle='Arcane',
            parentIndex='2', index='3', thumb='/library/metadata/1/thumb/1',
            grandparentThumb='/library/metadata/9/thumb/2',
        )])

        linha = gestor.get_watch_history('77')['history'][0]

        assert linha['title'] == 'Arcane'
        assert linha['subtitle'] == 'S02 · E03 - O Início'
        # A miniatura de um episódio é um fotograma; numa lista não diz nada.
        assert 'metadata/9' in _fonte(linha['poster_url'])

    def test_a_pagina_pedida_e_a_que_vai_nos_cabecalhos(self):
        entradas = [_filme(title=f'Filme {n}', viewedAt=str(1700000000 - n)) for n in range(10)]
        gestor = _gestor(entradas)

        resposta = gestor.get_watch_history('77', page=2, length=3)

        assert [linha['title'] for linha in resposta['history']] == ['Filme 3', 'Filme 4', 'Filme 5']
        assert resposta['pagination'] == {
            'current_page': 2, 'total_pages': 4, 'total_records': 10,
        }

    def test_o_historico_de_outra_pessoa_nao_aparece(self):
        gestor = _gestor(
            [_filme(title='Meu'), _filme(title='Dela', accountID='99')],
            contas=[{'id': '77', 'name': 'ana'}, {'id': '99', 'name': 'rita'}],
        )

        assert [l['title'] for l in gestor.get_watch_history('77')['history']] == ['Meu']

    def test_sem_aparelhos_conhecidos_a_linha_fica_sem_reprodutor_mas_aparece(self):
        gestor = _gestor([_filme()], aparelhos=[])

        linha = gestor.get_watch_history('77')['history'][0]

        assert linha['player'] == ''
        assert linha['title'] == 'Duna'

    def test_uma_falha_do_servidor_nao_passa_por_lista_vazia(self):
        gestor = _gestor([_filme()])
        gestor.conn.plex.falhas.add('historico')

        resposta = gestor.get_watch_history('77')

        # "Não consegui" e "não viu nada" são coisas diferentes, e a página
        # mostra-as de maneira diferente.
        assert resposta['success'] is False


class TestPesquisa:
    def test_filtra_pelo_titulo_e_pelo_subtitulo(self):
        gestor = _gestor([
            _filme(title='Duna'),
            _filme(title='Arrival'),
            _filme(type='episode', title='Piloto', grandparentTitle='Severance',
                   parentIndex='1', index='1'),
        ])

        resposta = gestor.get_watch_history('77', search='duna')

        assert [l['title'] for l in resposta['history']] == ['Duna']
        assert resposta['pagination']['total_records'] == 1

    def test_o_subtitulo_do_episodio_tambem_conta(self):
        gestor = _gestor([_filme(type='episode', title='Piloto', grandparentTitle='Severance',
                                 parentIndex='1', index='1')])

        assert gestor.get_watch_history('77', search='piloto')['history'][0]['title'] == 'Severance'

    def test_a_pesquisa_traz_uma_janela_de_reproducoes(self):
        # O `/status/sessions/history/all` não aceita filtro por título (só
        # accountID, viewedAt, librarySectionID, metadataItemID e sort), por
        # isso a pesquisa é feita aqui — sobre as mais recentes.
        gestor = _gestor([_filme()])

        gestor.get_watch_history('77', search='duna')

        historico = [p for p in gestor.conn.plex.pedidos if p.startswith('/status')]
        assert len(historico) == 1


class TestAparelhos:
    def test_deduz_os_aparelhos_do_historico(self):
        gestor = _gestor(
            [
                _filme(deviceID='5', viewedAt='1700000000'),
                _filme(deviceID='5', viewedAt='1600000000'),
                _filme(deviceID='6', viewedAt='1650000000'),
            ],
            aparelhos=[
                {'id': '5', 'name': 'Sala', 'platform': 'Android'},
                {'id': '6', 'name': 'Portátil', 'platform': 'Chrome'},
            ],
        )

        aparelhos = gestor.get_user_devices('77')['devices']

        # Um aparelho por linha, com a reprodução MAIS RECENTE, e do mais
        # recente para o mais antigo.
        assert [a['player'] for a in aparelhos] == ['Sala', 'Portátil']
        assert aparelhos[0]['last_seen'] == 1700000000.0
        assert aparelhos[0]['platform'] == 'Android'

    def test_um_aparelho_que_o_servidor_ja_nao_lista_nao_desaparece(self):
        gestor = _gestor([_filme(deviceID='5')], aparelhos=[])

        aparelhos = gestor.get_user_devices('77')['devices']

        assert len(aparelhos) == 1
        assert aparelhos[0]['platform'] == ''

    def test_os_aparelhos_de_outra_pessoa_nao_aparecem(self):
        gestor = _gestor(
            [_filme(deviceID='5'), _filme(deviceID='9', accountID='99')],
            aparelhos=[{'id': '5', 'name': 'Sala', 'platform': 'Android'},
                       {'id': '9', 'name': 'Dela', 'platform': 'iOS'}],
            contas=[{'id': '77', 'name': 'ana'}, {'id': '99', 'name': 'rita'}],
        )

        assert [a['player'] for a in gestor.get_user_devices('77')['devices']] == ['Sala']

    def test_uma_falha_a_ler_os_aparelhos_nao_e_uma_lista_vazia(self):
        gestor = _gestor([_filme()])
        gestor.conn.plex.falhas.add('historico')

        assert gestor.get_user_devices('77')['success'] is False


class TestOIdDoDono:
    """
    🐛 No `/accounts` do servidor o dono é a conta 1; o id de plex.tv que o
    painel guarda só aparece nas contas PARTILHADAS. Filtrar o histórico do
    administrador pelo id dele devolvia sempre uma lista vazia, sem erro.
    """

    def test_o_dono_e_traduzido_para_a_conta_do_servidor(self):
        gestor = _gestor(
            [_filme(accountID='1', title='Do dono')],
            contas=[{'id': '1', 'name': 'dono'}, {'id': '77', 'name': 'ana'}],
            dono=DonoFalso('123456789', 'dono'),
        )

        resposta = gestor.get_watch_history('123456789')

        assert [l['title'] for l in resposta['history']] == ['Do dono']

    def test_uma_conta_partilhada_usa_o_proprio_id(self):
        gestor = _gestor([_filme(accountID='77')],
                         contas=[{'id': '1', 'name': 'dono'}, {'id': '77', 'name': 'ana'}])

        assert len(gestor.get_watch_history('77')['history']) == 1

    def test_um_id_que_o_servidor_nao_conhece_nao_inventa_historico(self):
        gestor = _gestor([_filme()], contas=[{'id': '77', 'name': 'ana'}])

        resposta = gestor.get_watch_history('404')

        assert resposta['success'] is True
        assert resposta['history'] == []
        # E nem se chega a perguntar ao servidor pelo histórico.
        assert not [p for p in gestor.conn.plex.pedidos if p.startswith('/status')]

    def test_sem_a_lista_de_contas_usa_se_o_id_do_painel(self):
        # O servidor não respondeu ao `/accounts`: recusar o histórico por isso
        # seria pior do que tentar com o que o painel tem, que serve para toda
        # a gente menos o dono.
        gestor = _gestor([_filme()])
        gestor.conn.plex.falhas.add('accounts')

        assert len(gestor.get_watch_history('77')['history']) == 1


class TestCache:
    def test_as_contas_e_os_aparelhos_nao_se_pedem_a_cada_pagina(self):
        gestor = _gestor([_filme()], aparelhos=[{'id': '5', 'name': 'Sala', 'platform': 'Android'}])

        gestor.get_watch_history('77')
        gestor.get_watch_history('77', page=2)

        assert gestor.conn.plex.pedidos.count('/devices') == 1
        assert gestor.conn.plex.pedidos.count('/accounts') == 1

    def test_uma_falha_de_rede_nao_fica_guardada(self):
        # ⚠️ Não saber não é o mesmo que não existir: gravar o "não" de uma
        # falha deixava o histórico cinco minutos sem nomes de aparelhos.
        gestor = _gestor([_filme()], aparelhos=[{'id': '5', 'name': 'Sala', 'platform': 'Android'}])
        gestor.conn.plex.falhas.add('devices')

        assert gestor.get_watch_history('77')['history'][0]['player'] == ''

        gestor.conn.plex.falhas.clear()
        assert gestor.get_watch_history('77')['history'][0]['player'] == 'Sala'


class TestSemLigacao:
    def test_sem_servidor_devolve_vazio_em_vez_de_rebentar(self):
        gestor = PlexHistoryManager(LigacaoFalsa(None))

        assert gestor.get_watch_history('77') == {
            "success": True, "history": [],
            "pagination": {"current_page": 1, "total_pages": 0, "total_records": 0},
        }
        assert gestor.get_user_devices('77') == {"success": True, "devices": []}


class TestJanela:
    """Nem uma página de cada vez, nem o histórico inteiro."""

    def test_os_aparelhos_saem_de_uma_janela_limitada(self):
        gestor = _gestor([_filme()])

        gestor.get_user_devices('77')

        assert gestor.conn.plex.cabecalhos[0]['X-Plex-Container-Size'] == str(JANELA)

    def test_a_pesquisa_usa_a_mesma_janela(self):
        gestor = _gestor([_filme()])

        gestor.get_watch_history('77', search='duna')

        assert gestor.conn.plex.cabecalhos[0]['X-Plex-Container-Size'] == str(JANELA)

    def test_uma_pagina_normal_pede_so_o_que_mostra(self):
        gestor = _gestor([_filme()])

        gestor.get_watch_history('77', page=3, length=15)

        assert gestor.conn.plex.cabecalhos[0] == {
            'X-Plex-Container-Start': '30', 'X-Plex-Container-Size': '15',
        }


class _Duplo:
    """Substituto inofensivo para as dependências da fachada (não faz rede)."""

    def __getattr__(self, nome):
        return _Duplo()

    def __call__(self, *args, **kwargs):
        return None


class TautulliFalso:
    def __init__(self, configurado):
        self.api_client = type('Cliente', (), {'is_configured': configurado})()

    def get_user_watch_history(self, user_id, page=1, length=15, search=""):
        return {"success": True, "history": ["do tautulli"], "pagination": {}}

    def get_user_devices(self, user_id):
        return {"success": True, "devices": ["do tautulli"]}


class HistoricoFalso:
    def get_watch_history(self, user_id, page=1, length=15, search=""):
        return {"success": True, "history": ["do plex"], "pagination": {}}

    def get_user_devices(self, user_id):
        return {"success": True, "devices": ["do plex"]}


def _fachada(tautulli_configurado):
    from app.services.media_server.plex.backend import PlexManager

    gestor = PlexManager(
        data_manager=_Duplo(),
        stats_manager=TautulliFalso(tautulli_configurado),
        notifier_manager=_Duplo(),
        overseerr_manager=_Duplo(),
    )
    gestor.history = HistoricoFalso()
    return gestor


class TestFachada:
    """Quem responde é decidido num sítio só: a fachada."""

    def test_com_tautulli_e_o_tautulli_que_responde(self):
        gestor = _fachada(True)

        assert gestor.get_watch_history('77')['history'] == ["do tautulli"]
        assert gestor.get_user_devices('77')['devices'] == ["do tautulli"]

    def test_sem_tautulli_responde_o_proprio_plex(self):
        gestor = _fachada(False)

        assert gestor.get_watch_history('77')['history'] == ["do plex"]
        assert gestor.get_user_devices('77')['devices'] == ["do plex"]

    def test_as_estatisticas_dependem_do_tautulli_estar_configurado(self):
        # A CAPACIDADE continua verdadeira — é ela que mantém o cartão do
        # Tautulli nas Conexões, sem o qual não haveria onde o configurar.
        gestor = _fachada(False)

        assert gestor.capabilities.estatisticas is True
        assert gestor.estatisticas_disponiveis() is False

        assert _fachada(True).estatisticas_disponiveis() is True

    def test_um_tautulli_em_falta_nao_rebenta(self):
        from app.services.media_server.plex.backend import PlexManager

        gestor = PlexManager(_Duplo(), None, _Duplo(), _Duplo())
        gestor.history = HistoricoFalso()

        assert gestor.estatisticas_disponiveis() is False
        assert gestor.get_watch_history('77')['history'] == ["do plex"]
