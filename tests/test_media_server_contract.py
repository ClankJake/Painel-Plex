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
    """Os nomes sem marca têm de apontar para o mesmo comportamento dos antigos."""

    def test_is_connected_reflete_a_ligacao(self, backend):
        assert backend.is_connected() is False

        backend.conn.plex = object()
        assert backend.is_connected() is True

    def test_get_server_identifier_delega_no_nome_antigo(self, backend, monkeypatch):
        monkeypatch.setattr(backend, 'get_machine_identifier', lambda: 'abc123')
        assert backend.get_server_identifier() == 'abc123'

    def test_get_all_users_delega_no_nome_antigo(self, backend, monkeypatch):
        chamadas = []
        monkeypatch.setattr(
            backend, 'get_all_plex_users',
            lambda force_refresh=False: chamadas.append(force_refresh) or ['ana'],
        )

        assert backend.get_all_users() == ['ana']
        assert backend.get_all_users(force_refresh=True) == ['ana']
        assert chamadas == [False, True]


@pytest.mark.integration
class TestLigacaoNaAplicacao:
    def test_o_alias_herdado_aponta_para_o_mesmo_objeto(self, app):
        # 'plex_manager' existe só para os pontos de chamada ainda não migrados.
        # No dia em que deixarem de ser o mesmo objeto, metade do painel passa a
        # falar com um backend e a outra metade com outro.
        from app import extensions

        assert extensions.media_server is not None
        assert extensions.plex_manager is extensions.media_server

    def test_a_configuracao_declara_o_tipo_de_servidor(self, app):
        assert app.config.get('MEDIA_SERVER_TYPE') == 'plex'
