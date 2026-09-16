"""Guarda o contrato da camada de servidores de média.

Estes testes não verificam comportamento do Plex — verificam que a *forma* do
backend se mantém. São eles que falham no dia em que alguém acrescentar um
método à fachada do Plex sem o declarar no contrato, deixando o backend
seguinte a faltar-lhe uma peça sem ninguém dar por isso.
"""

import dataclasses
import logging
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

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

    # `app_context`: `get_all_users` lê a cache do Flask, que precisa de
    # contexto. Sem ele este teste só passava quando outro ficheiro já tinha
    # criado a aplicação — e falhava a correr sozinho.
    def test_a_fachada_trata_a_lista_crua_do_diretorio(self, backend, monkeypatch, app_context):
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


class TestAsAssinaturasNaoDivergem:
    """Os dois backends têm de aceitar os mesmos ARGUMENTOS, não só os mesmos nomes.

    🐛 `send_invite` tinha um terceiro parâmetro chamado `media_user_id` no Plex
    (como no contrato) e `plex_user_id` no Jellyfin — o nome antigo, de quando
    só havia um servidor. Quem chamava pelo nome, que é como o contrato manda,
    levava com `TypeError: send_invite() got an unexpected keyword argument`.
    Acontecia nas duas reativações, a paga e a manual, e o que ficava no log não
    dizia nada sobre um parâmetro mal chamado.

    Um `isinstance(backend, Protocol)` não apanha isto: os Protocols verificam
    os nomes dos métodos, não as suas assinaturas.
    """

    def _parametros(self, funcao):
        import inspect

        return [
            nome for nome, p in inspect.signature(funcao).parameters.items()
            if nome != 'self' and p.kind is not inspect.Parameter.VAR_KEYWORD
        ]

    @pytest.mark.parametrize('metodo', ['send_invite', 'create_account', 'claim_invitation',
                                       'conta_a_partir_de_credenciais'])
    def test_o_provisionamento_fala_a_mesma_lingua(self, metodo):
        from app.services.media_server.jellyfin.account_manager import JellyfinAccountManager
        from app.services.media_server.plex.invite_manager import PlexInviteManager

        do_plex = getattr(PlexInviteManager, metodo, None)
        do_jellyfin = getattr(JellyfinAccountManager, metodo, None)
        if do_plex is None or do_jellyfin is None:
            pytest.skip(f"'{metodo}' não existe nos dois backends.")

        assert self._parametros(do_jellyfin) == self._parametros(do_plex)

    def test_nenhum_parametro_tem_a_marca_de_um_servidor_no_nome(self):
        # A fachada já não deixa passar métodos com 'plex' no nome; os
        # PARÂMETROS escaparam a essa rede e foi por aí que o bug entrou.
        from app.services.media_server.jellyfin.account_manager import JellyfinAccountManager
        from app.services.media_server.jellyfin.backend import JellyfinManager

        for classe in (JellyfinManager, JellyfinAccountManager):
            for nome in dir(classe):
                if nome.startswith('_'):
                    continue
                atributo = getattr(classe, nome, None)
                if not callable(atributo):
                    continue
                com_marca = [p for p in self._parametros(atributo) if 'plex' in p.lower()]
                assert com_marca == [], f"{classe.__name__}.{nome}{com_marca}"


class TestOUltimoRecursoDosCortes:
    """⚠️ Nem todos os servidores têm alguma coisa mais forte do que pedir.

    🐛 A definição "Forçar o encerramento em aparelhos que ignoram o comando"
    aparecia num painel PLEX, onde `force_terminate()` devolve sempre False:
    prometia um comportamento que nunca acontecia, e o log ainda aconselhava a
    ligá-la. Quem decide é a capacidade — uma capacidade em falta esconde a
    funcionalidade.
    """

    def test_o_plex_nao_tem_nada_mais_forte(self):
        assert PlexManager.CAPABILITIES.corte_forcado is False

    def test_o_jellyfin_tem(self):
        from app.services.media_server.jellyfin.backend import JellyfinManager

        assert JellyfinManager.CAPABILITIES.corte_forcado is True

    @pytest.mark.parametrize('tipo', ['plex', 'jellyfin'])
    def test_a_capacidade_e_o_provider_dizem_o_mesmo(self, tipo):
        # São duas declarações do mesmo facto: uma para a interface (o cartão
        # nas Configurações) e outra para o motor de streams, que fala com o
        # provider e não com o backend. Divergirem seria a definição a aparecer
        # sem fazer nada, ou a desaparecer onde faz.
        backend = create_media_server(
            tipo, data_manager=_Duplo(), stats_manager=_Duplo(),
            notifier_manager=_Duplo(), requests_manager=_Duplo(),
        )

        assert backend.sessions.suporta_corte_forcado() is backend.CAPABILITIES.corte_forcado


class TestReporOAcesso:
    """Repor o acesso é do BACKEND, porque é diferente em cada servidor."""

    def test_esta_no_contrato(self):
        assert hasattr(MediaServerBackend, 'restaurar_acesso')

    def test_os_dois_backends_respondem(self):
        from app.services.media_server.jellyfin.backend import JellyfinManager

        assert callable(getattr(PlexManager, 'restaurar_acesso'))
        assert callable(getattr(JellyfinManager, 'restaurar_acesso'))


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


class TestONomeDoServidorNaoSaiDaPasta:
    """**A fachada é a fronteira**, e um blueprint é do lado de fora dela.

    🐛 `app/blueprints/api/invites.py` importava `plexapi.myplex.MyPlexAccount`
    para transformar um token do plex.tv numa conta. Duas coisas erradas: uma
    rota a saber que o servidor é o Plex, e um painel Jellyfin a carregar a
    biblioteca do Plex para nada. Hoje é `conta_a_partir_de_credenciais`, do
    contrato, que sabe o que é uma "conta" em cada servidor.
    """

    # ⚠️ Os que ainda faltam, NOMEADOS de propósito. Esta lista existe para
    # encolher e nunca para crescer: um ficheiro novo que importe `plexapi`
    # falha este teste, e tirar um daqui é o trabalho que falta fazer.
    #
    #   • `auth.py` — o fluxo de PIN do plex.tv, que é a metade do login que
    #     MUDA por servidor. Sair daqui é acrescentar ao contrato um passo de
    #     autenticação por PIN, coisa que o Jellyfin não tem.
    #   • `system.py` — o assistente de instalação valida a ligação ao Plex
    #     antes de haver backend construído a quem perguntar.
    CONHECIDOS = {'app/blueprints/auth.py', 'app/blueprints/api/system.py'}

    def _importa_plexapi(self, texto):
        # O `import`, não a palavra: os comentários deste repositório falam de
        # `plexapi` precisamente para explicar porque é que ele não deve estar
        # aqui, e um teste que os apanhasse punha quem escreve a contorná-lo.
        return re.search(r'^\s*(?:from|import)\s+plexapi\b', texto, re.M) is not None

    @pytest.mark.parametrize('ficheiro', sorted(
        str(c.relative_to(RAIZ))
        for c in (RAIZ / 'app/blueprints').rglob('*.py')
    ))
    def test_nenhum_blueprint_novo_importa_a_biblioteca_do_plex(self, ficheiro):
        if ficheiro in self.CONHECIDOS:
            pytest.skip("já era assim antes desta rede; ver CONHECIDOS")

        texto = (RAIZ / ficheiro).read_text(encoding='utf-8')
        assert not self._importa_plexapi(texto), (
            f"{ficheiro} importa a biblioteca do Plex: acrescente um método ao "
            f"contrato em vez de a trazer para fora de media_server/plex/."
        )

    def test_a_lista_dos_que_faltam_esta_certa(self):
        """Um nome que lá fique depois de o problema estar resolvido é ruído.

        E um que saia da lista sem o problema estar resolvido tira a rede ao
        ficheiro sem ninguém dar por isso.
        """
        reais = {
            str(c.relative_to(RAIZ))
            for c in (RAIZ / 'app/blueprints').rglob('*.py')
            if self._importa_plexapi(c.read_text(encoding='utf-8'))
        }
        assert reais == self.CONHECIDOS, (
            f"a lista dos blueprints que ainda importam plexapi está errada: "
            f"faltam {sorted(reais - self.CONHECIDOS)}, sobram {sorted(self.CONHECIDOS - reais)}"
        )

    def test_os_dois_backends_sabem_construir_uma_conta(self):
        from app.services.media_server.jellyfin.backend import JellyfinManager
        from app.services.media_server.plex.backend import PlexManager

        for classe in (JellyfinManager, PlexManager):
            assert hasattr(classe, 'conta_a_partir_de_credenciais'), classe.__name__
