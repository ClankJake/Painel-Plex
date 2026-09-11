"""Guarda o contrato da camada de servidores de média.

Estes testes não verificam comportamento do Plex — verificam que a *forma* do
backend se mantém. São eles que falham no dia em que alguém acrescentar um
método à fachada do Plex sem o declarar no contrato, deixando o backend
seguinte a faltar-lhe uma peça sem ninguém dar por isso.
"""

import dataclasses
import logging

import pytest

from app.services.media_server import (
    DEFAULT_MEDIA_SERVER_TYPE,
    AccountProvisioning,
    ConnectionBackend,
    MediaServerBackend,
    MediaServerCapabilities,
    SubscriptionScheduler,
    UserDirectory,
    create_media_server,
    resolve_media_server_type,
    tipos_suportados,
)
from app.services.media_server.plex import PlexManager


class _Duplo:
    """Substituto inofensivo para as dependências que o backend recebe.

    Nenhum teste deste ficheiro faz rede: construir o backend não liga a nada
    (a ligação só acontece no `init_app`, e só se a app estiver configurada).
    """

    def __getattr__(self, nome):
        return _Duplo()

    def __call__(self, *args, **kwargs):
        return None


@pytest.fixture()
def backend():
    return create_media_server(
        'plex',
        data_manager=_Duplo(),
        stats_manager=_Duplo(),
        notifier_manager=_Duplo(),
        requests_manager=_Duplo(),
    )


class TestFabrica:
    def test_o_plex_e_o_backend_padrao(self, backend):
        assert isinstance(backend, PlexManager)
        assert backend.SERVER_TYPE == DEFAULT_MEDIA_SERVER_TYPE

    @pytest.mark.parametrize('valor', ['plex', 'PLEX', '  Plex  ', ''])
    def test_o_tipo_e_normalizado(self, valor):
        assert resolve_media_server_type(valor) == 'plex'

    def test_tipo_ausente_assume_o_padrao(self):
        # Todas as instalações existentes têm o config.json sem esta chave.
        assert resolve_media_server_type(None) == DEFAULT_MEDIA_SERVER_TYPE

    def test_tipo_desconhecido_nao_impede_o_arranque(self, caplog):
        # 🛡️ Sem backend não há painel, e sem painel o administrador não tem
        # como corrigir a configuração que causou o problema. Uma gralha tem de
        # dar erro no log, não um arranque falhado.
        with caplog.at_level(logging.ERROR):
            tipo = resolve_media_server_type('jellyfim')

        assert tipo == DEFAULT_MEDIA_SERVER_TYPE
        assert 'jellyfim' in caplog.text

    def test_os_tipos_suportados_sao_anunciados(self):
        assert 'plex' in tipos_suportados()


class TestContrato:
    def test_a_fachada_do_plex_cumpre_o_contrato(self, backend):
        assert isinstance(backend, MediaServerBackend)

    def test_os_submanagers_cumprem_os_respetivos_contratos(self, backend):
        assert isinstance(backend.conn, ConnectionBackend)
        assert isinstance(backend.users, UserDirectory)
        assert isinstance(backend.invites, AccountProvisioning)
        assert isinstance(backend.subscriptions, SubscriptionScheduler)

    def test_o_backend_anuncia_as_suas_capacidades(self, backend):
        capacidades = backend.capabilities

        assert isinstance(capacidades, MediaServerCapabilities)
        # O Plex convida contas que já existem; nunca as cria.
        assert capacidades.convites_nativos is True
        assert capacidades.cria_contas is False
        # E não sabe suspender uma conta que não é dele: bloquear é retirar
        # as partilhas.
        assert capacidades.desativa_conta is False

    def test_as_capacidades_sao_imutaveis(self, backend):
        # As bandeiras descrevem o servidor, não o estado da aplicação: nada
        # no painel deve poder ligá-las ou desligá-las em tempo de execução.
        with pytest.raises(dataclasses.FrozenInstanceError):
            backend.capabilities.cria_contas = True


class TestSuperficieAgnostica:
    """A fachada é a fronteira: daqui para fora ninguém sabe que por baixo é Plex."""

    def test_is_connected_reflete_a_ligacao(self, backend):
        assert backend.is_connected() is False

        backend.conn.plex = object()
        assert backend.is_connected() is True

    def test_get_server_identifier_le_a_ligacao(self, backend, monkeypatch):
        monkeypatch.setattr(backend.conn, 'get_server_identifier', lambda: 'abc123')
        assert backend.get_server_identifier() == 'abc123'

    def test_invalidate_user_cache_delega_no_diretorio(self, backend, monkeypatch):
        chamadas = []
        monkeypatch.setattr(backend.users, 'invalidate_user_cache', lambda: chamadas.append(True))

        backend.invalidate_user_cache()
        assert chamadas == [True]

    def test_a_fachada_trata_a_lista_crua_do_diretorio(self, backend, monkeypatch):
        # A fachada consome `users.list_users()` — a leitura crua — e devolve-a
        # tratada para a interface. São duas camadas com responsabilidades
        # distintas que, durante muito tempo, tiveram o mesmo nome.
        monkeypatch.setattr(backend.users, 'list_users', lambda: [{'id': 1, 'username': 'ana', 'thumb': None}])
        monkeypatch.setattr(backend.users, 'invalidate_user_cache', lambda: None)

        utilizadores = backend.get_all_users(force_refresh=True)

        assert [u['username'] for u in utilizadores] == ['ana']

    def test_nenhum_nome_com_marca_sobra_na_fachada(self, backend):
        # Se um método com 'plex' no nome reaparecer aqui, é sinal de que o
        # painel voltou a ter de saber com que servidor está a falar.
        publicos = [nome for nome in dir(backend) if not nome.startswith('_')]
        com_marca = sorted(nome for nome in publicos if 'plex' in nome.lower())

        # 'plex' e 'account' são os objetos da biblioteca plexapi, ainda lidos
        # pelo proxy de imagens para injetar o X-Plex-Token. Não têm 'plex' no
        # nome do atributo por acaso — ficam documentados como dívida da Fase 1.
        assert com_marca == []


class TestAutorizacaoDeImagensDoPlex:
    """A injeção do X-Plex-Token, agora que vive no backend e não no proxy.

    Estes testes vieram de `test_image_security.py`: seguiram a lógica quando
    ela mudou de sítio. O proxy continua a ser testado lá, mas sobre a
    *delegação* — que é tudo o que ele passa a saber.
    """

    class _PlexFalso:
        _token = 'token-plex'
        _baseurl = 'http://plex.local:32400'

        def url(self, path, includeToken=False):
            return f'http://plex.local:32400{path}'

    class _ContaFalsa:
        _token = 'token-conta'

    def test_fonte_plex_injeta_o_token(self, backend):
        backend.conn.plex = self._PlexFalso()

        url, params = backend.authorize_image_url('plex', '/library/metadata/1/thumb')

        assert url == 'http://plex.local:32400/library/metadata/1/thumb'
        assert params['X-Plex-Token'] == 'token-plex'

    def test_plex_account_aceita_caminho_relativo(self, backend):
        backend.conn.account = self._ContaFalsa()

        url, params = backend.authorize_image_url('plex_account', 'users/avatar.png')

        assert url == 'https://plex.tv/users/avatar.png'
        assert params['X-Plex-Token'] == 'token-conta'

    def test_plex_account_aceita_url_absoluto_do_plex(self, backend):
        backend.conn.account = self._ContaFalsa()

        url, _params = backend.authorize_image_url('plex_account', 'https://plex.tv/users/avatar.png?w=100')

        assert url == 'https://plex.tv/users/avatar.png?w=100'

    def test_plex_account_recusa_dominios_estranhos(self, backend):
        # 🛡️ Sem esta verificação, o token da conta Plex seria enviado a
        # terceiros. `'plex.tv' in netloc` aceitava 'plex.tv.atacante.com'.
        backend.conn.account = self._ContaFalsa()

        with pytest.raises(ValueError):
            backend.authorize_image_url('plex_account', 'https://atacante.exemplo/roubar.png')

    def test_sem_ligacao_nao_ha_url(self, backend):
        assert backend.authorize_image_url('plex', '/x') == (None, {})
        assert backend.authorize_image_url('plex_account', '/x') == (None, {})

    def test_fonte_desconhecida_nao_ha_url(self, backend):
        assert backend.authorize_image_url('outra-coisa', '/x') == (None, {})

    def test_get_base_url_alimenta_a_allowlist_do_proxy(self, backend):
        # 🐛 Ao tirar o objeto plexapi de dentro do proxy, o endereço do servidor
        # quase ficou fora da allowlist — e as capas passariam a ser bloqueadas.
        assert backend.get_base_url() is None

        backend.conn.plex = self._PlexFalso()
        assert backend.get_base_url() == 'http://plex.local:32400'


@pytest.mark.integration
class TestLigacaoNaAplicacao:
    def test_o_painel_expoe_um_unico_nome_para_o_servidor(self, app):
        # O alias 'plex_manager' foi eliminado na Fase 0.5: dois nomes para o
        # mesmo objeto são dois sítios por onde um backend errado pode entrar.
        from app import extensions

        assert extensions.media_server is not None
        assert not hasattr(extensions, 'plex_manager')

    def test_a_configuracao_declara_o_tipo_de_servidor(self, app):
        assert app.config.get('MEDIA_SERVER_TYPE') == 'plex'
