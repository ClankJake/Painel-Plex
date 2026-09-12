# tests/test_jellyfin_backend.py
"""Backend do Jellyfin: ligação, utilizadores, bibliotecas, bloqueio e sessões.

Nenhum teste fala com um Jellyfin real: o `ApiFalsa` responde com o que a API
devolveria e regista o que foi enviado, que é onde estão os erros que
interessam (uma política gravada pela metade, um comando de paragem sem aviso).
"""

import pytest

from app.services.media_server.base import (
    AccountProvisioning, ConnectionBackend, MediaServerBackend,
    MediaSession, SessionsProvider, UserDirectory,
)
from app.services.media_server.jellyfin import JellyfinManager
from app.services.media_server.jellyfin.api_client import JellyfinApiError
from app.services.media_server.jellyfin.sessions import (
    JellyfinSessionsProvider, plataforma_de, ticks_para_ms,
)
from tests.conftest import FakeDataManager

def _fonte_da_imagem(url):
    """O payload `<prefixo>:<caminho>` por trás de um URL do proxy."""
    import base64
    from urllib.parse import parse_qs, urlparse

    origem = parse_qs(urlparse(url).query)['source'][0]
    return base64.urlsafe_b64decode(origem).decode('utf-8')


GUID = "38c3a1f0e4b24d7f9c1a0b5e6d7f8a90"
OUTRO = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


class ApiFalsa:
    """Responde como o Jellyfin e guarda tudo o que lhe foi enviado."""

    def __init__(self, respostas=None, erros=None):
        self.respostas = respostas or {}
        self.erros = erros or {}
        self.enviados = []
        # Os DELETE com parâmetros de query (a revogação de um aparelho leva o
        # id no query string, não no caminho).
        self.apagados = []
        # (endpoint, token) de cada pedido feito com um token diferente da
        # chave de API — é assim que o `/Sessions/Logout` fecha a sessão certa.
        self.tokens_usados = []
        # Os parâmetros de query do último GET a cada endpoint — é assim que se
        # verifica a consulta que foi mesmo pedida ao servidor.
        self.ultimos_params = {}
        self.base_url = "http://jellyfin.local:8096"
        self.api_key = "chave"
        self.is_configured = True

    def _resolver(self, metodo, endpoint):
        self.enviados.append((metodo, endpoint))
        # Um erro pode ser registado só para um método — o mesmo endpoint
        # responde ao GET e ao DELETE com resultados diferentes.
        if (metodo, endpoint) in self.erros:
            raise self.erros[(metodo, endpoint)]
        if endpoint in self.erros:
            raise self.erros[endpoint]
        # A correspondência exata vem primeiro: sem isso, um pedido a
        # '/Users/<id>' era servido pela resposta registada para '/Users' e o
        # teste passava a exercitar outra coisa.
        if endpoint in self.respostas:
            return self.respostas[endpoint]
        for chave, valor in self.respostas.items():
            if endpoint.startswith(chave):
                return valor
        return None

    def get(self, endpoint, **kwargs):
        self.ultimos_params[endpoint] = kwargs.get('params') or {}
        return self._resolver('GET', endpoint)

    def post(self, endpoint, json=None, **kwargs):
        self.enviados.append(('POST', endpoint, json))
        if endpoint in self.erros:
            raise self.erros[endpoint]
        resposta = self.respostas.get(endpoint)
        # Uma resposta que depende do CORPO enviado — o histórico do plugin faz
        # duas consultas ao mesmo endpoint (a contagem e as linhas).
        return resposta(json) if callable(resposta) else resposta

    def request(self, method, endpoint, *, params=None, json=None, timeout=None, token=None):
        self.enviados.append((method.upper(), endpoint, json))
        self.tokens_usados.append((endpoint, token))
        if endpoint in self.erros:
            raise self.erros[endpoint]
        return self.respostas.get(endpoint)

    def delete(self, endpoint, **kwargs):
        self.apagados.append((endpoint, kwargs.get('params')))
        return self._resolver('DELETE', endpoint)

    def reload_config(self):
        pass

    def corpos_enviados(self, endpoint):
        return [e[2] for e in self.enviados if e[0] == 'POST' and e[1] == endpoint]


BIBLIOTECAS = [
    {"Name": "Filmes", "ItemId": "lib-filmes"},
    {"Name": "Séries", "ItemId": "lib-series"},
]

POLITICA_BASE = {
    "IsAdministrator": False,
    "IsDisabled": False,
    "EnableAllFolders": False,
    "EnabledFolders": ["lib-filmes"],
    "EnableContentDownloading": False,
    "MaxActiveSessions": 0,
    "EnableRemoteAccess": True,
}


def montar(respostas=None, erros=None, data_manager=None):
    """Um backend Jellyfin ligado a uma API falsa.

    Quem toca na base de dados (bloqueios, perfis, convites) passa o
    `DataManager` real: são exatamente os caminhos onde a identidade em texto
    tem de funcionar de ponta a ponta, e um duplo esconderia isso.
    """
    backend = JellyfinManager(data_manager=data_manager or FakeDataManager())
    backend.conn.api = ApiFalsa(respostas, erros)
    backend.conn.server_info = {"Id": "servidor-1", "ServerName": "Casa"}
    return backend


@pytest.fixture()
def cache_limpa(app_context):
    from app.extensions import cache
    cache.clear()
    yield cache
    cache.clear()


class TestContrato:
    def test_a_fachada_cumpre_o_contrato(self):
        assert isinstance(JellyfinManager(data_manager=None), MediaServerBackend)

    def test_os_submanagers_cumprem_os_respetivos_contratos(self):
        backend = JellyfinManager(data_manager=None)

        assert isinstance(backend.conn, ConnectionBackend)
        assert isinstance(backend.users, UserDirectory)
        assert isinstance(backend.invites, AccountProvisioning)
        assert isinstance(backend.sessions, SessionsProvider)

    def test_as_capacidades_sao_o_oposto_das_do_plex(self):
        from app.services.media_server.plex import PlexManager

        jellyfin = JellyfinManager(data_manager=None).capabilities
        plex = PlexManager(None, None, None, None).capabilities

        # É esta diferença que a interface deve consultar, em vez de comparar
        # o tipo de servidor.
        assert jellyfin.cria_contas is True and plex.cria_contas is False
        assert jellyfin.convites_nativos is False and plex.convites_nativos is True
        assert jellyfin.login_delegado is False and plex.login_delegado is True
        # O Jellyfin sabe suspender uma conta; o Plex tem de retirar partilhas.
        assert jellyfin.desativa_conta is True and plex.desativa_conta is False

    def test_a_fabrica_conhece_o_jellyfin(self):
        from app.services.media_server import create_media_server, tipos_suportados

        assert 'jellyfin' in tipos_suportados()
        backend = create_media_server(
            'jellyfin', data_manager=None, stats_manager=None,
            notifier_manager=None, requests_manager=None,
        )
        assert isinstance(backend, JellyfinManager)


class TestLigacao:
    def test_bibliotecas_mapeiam_nome_para_item_id(self, cache_limpa):
        # A política de um utilizador guarda o ItemId, não o nome — é por isso
        # que a 'key' tem de ser o ItemId.
        backend = montar({'/Library/VirtualFolders': BIBLIOTECAS})

        assert backend.get_libraries() == [
            {"title": "Filmes", "key": "lib-filmes"},
            {"title": "Séries", "key": "lib-series"},
        ]

    def test_bibliotecas_sem_ligacao(self, cache_limpa):
        backend = JellyfinManager(data_manager=None)
        backend.conn.api = ApiFalsa()

        assert backend.get_libraries() == []

    def test_chave_recusada_da_uma_mensagem_acionavel(self, cache_limpa):
        backend = JellyfinManager(data_manager=None)
        backend.conn.api = ApiFalsa(erros={'/System/Info': JellyfinApiError("nope", status_code=401)})

        sucesso, mensagem = backend.conn.reload()

        assert sucesso is False
        assert "chave de API" in mensagem

    def test_o_identificador_do_servidor(self, cache_limpa):
        assert montar().get_server_identifier() == "servidor-1"


class TestUtilizadores:
    def _backend(self):
        return montar({
            '/Users': [
                {"Id": GUID, "Name": "ana", "PrimaryImageTag": "abc", "Policy": {"IsDisabled": False}},
                {"Id": OUTRO, "Name": "bruno", "Policy": {"IsDisabled": True}},
            ],
            '/Library/VirtualFolders': BIBLIOTECAS,
        })

    def test_lista_traduzida(self, cache_limpa):
        utilizadores = self._backend().users.list_users()

        assert [u['username'] for u in utilizadores] == ["ana", "bruno"]
        assert utilizadores[0]['id'] == GUID
        assert utilizadores[1]['is_disabled'] is True

    def test_o_jellyfin_nao_tem_email_nas_contas(self, cache_limpa):
        # O painel recolhe o email no registo; o servidor não o guarda.
        assert self._backend().users.list_users()[0]['email'] is None

    def test_procura_por_id(self, cache_limpa):
        assert self._backend().get_user_by_id(GUID)['username'] == "ana"

    def test_id_inexistente(self, cache_limpa):
        assert self._backend().get_user_by_id("nao-existe") is None


class TestBibliotecasDoUtilizador:
    def _backend(self, policy):
        return montar({
            f'/Users/{GUID}': {"Id": GUID, "Name": "ana", "Policy": dict(policy)},
            '/Users': [{"Id": GUID, "Name": "ana", "Policy": dict(policy)}],
            '/Library/VirtualFolders': BIBLIOTECAS,
        })

    def test_le_as_bibliotecas_permitidas(self, cache_limpa):
        resultado = self._backend(POLITICA_BASE).get_user_libraries(GUID)

        assert resultado['libraries'] == ["Filmes"]
        assert resultado['allow_sync'] is False

    def test_acesso_a_tudo_lista_todas(self, cache_limpa):
        politica = {**POLITICA_BASE, "EnableAllFolders": True, "EnabledFolders": []}

        assert self._backend(politica).get_user_libraries(GUID)['libraries'] == ["Filmes", "Séries"]

    def test_gravar_converte_nomes_em_item_ids(self, cache_limpa):
        backend = self._backend(POLITICA_BASE)

        backend.update_user_libraries(GUID, ["Séries"], allow_sync=True)

        gravada = backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy')[0]
        assert gravada['EnabledFolders'] == ["lib-series"]
        assert gravada['EnableContentDownloading'] is True

    def test_selecionar_todas_marca_o_acesso_total(self, cache_limpa):
        # Sem isto, uma biblioteca criada depois ficaria invisível para quem
        # devia ver tudo — e ninguém perceberia porquê.
        backend = self._backend(POLITICA_BASE)

        backend.update_user_libraries(GUID, ["Filmes", "Séries"])

        gravada = backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy')[0]
        assert gravada['EnableAllFolders'] is True
        assert gravada['EnabledFolders'] == []

    def test_a_politica_e_gravada_inteira(self, cache_limpa):
        # 🛡️ O Jellyfin SUBSTITUI a política toda neste endpoint. Enviar só os
        # campos alterados repunha os restantes nos valores por omissão — um
        # administrador perdia a flag de administrador ao mudar de biblioteca.
        backend = self._backend(POLITICA_BASE)

        backend.update_user_libraries(GUID, ["Filmes"])

        gravada = backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy')[0]
        assert gravada['EnableRemoteAccess'] is True
        assert 'IsAdministrator' in gravada


@pytest.mark.integration
class TestBloqueio:
    def _backend(self, data_manager):
        return montar({
            f'/Users/{GUID}': {"Id": GUID, "Name": "ana", "Policy": dict(POLITICA_BASE)},
            '/Users': [{"Id": GUID, "Name": "ana", "Policy": dict(POLITICA_BASE)}],
            '/Library/VirtualFolders': BIBLIOTECAS,
        }, data_manager=data_manager)

    def test_bloquear_suspende_a_conta_sem_mexer_nas_bibliotecas(self, cache_limpa, data_manager):
        # É a vantagem face ao Plex: não é preciso guardar e repor partilhas.
        backend = self._backend(data_manager)

        resultado = backend.block_user(GUID, reason='expired')

        gravada = backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy')[0]
        assert resultado['success'] is True
        assert gravada['IsDisabled'] is True
        assert gravada['EnabledFolders'] == ["lib-filmes"]

    def test_bloquear_regista_o_motivo(self, cache_limpa, data_manager):
        backend = self._backend(data_manager)

        backend.block_user(GUID, reason='trial_expired')

        assert backend.data_manager.get_blocked_user(GUID)['block_reason'] == 'trial_expired'

    def test_desbloquear_reativa_a_conta(self, cache_limpa, data_manager):
        backend = self._backend(data_manager)
        backend.block_user(GUID)

        backend.unblock_user(GUID)

        ultima = backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy')[-1]
        assert ultima['IsDisabled'] is False
        assert backend.data_manager.get_blocked_user(GUID) is None

    def test_o_limite_de_telas_nao_toca_na_politica_do_servidor(self, cache_limpa, data_manager):
        """
        🐛 REGRESSÃO: isto escrevia `Policy.MaxActiveSessions`, a pensar que era
        um limite de telas. Não é — limita AUTENTICAÇÕES — e saía caro: não
        cortava quem já estava a ver (só recusa entradas novas) e trancava a
        pessoa fora do PAINEL, porque entrar no painel autentica-se contra o
        servidor e ocupa uma sessão.
        """
        backend = self._backend(data_manager)
        backend.data_manager.set_user_profile(GUID, {"username": "ana"})

        backend.update_screen_limit(GUID, 3)

        assert backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy') == []
        assert backend.data_manager.get_user_profile(GUID)['screen_limit'] == 3


@pytest.mark.integration
class TestLimpezaDoLimiteDeSessoes:
    """
    Reparação de uma vez. Quem já corria o painel tem no servidor um
    `MaxActiveSessions` que o painel lá pôs por engano, e que o tranca fora do
    próprio painel. A limpeza tira-o; depois disso o painel não volta a mexer
    neste campo — repeti-la todos os dias desfaria, às escondidas, um limite
    que o administrador tenha posto de propósito no Jellyfin.
    """

    def _montar(self, data_manager, politicas):
        utilizadores = [
            {"Id": uid, "Name": nome, "Policy": dict(POLITICA_BASE, MaxActiveSessions=maximo)}
            for uid, nome, maximo in politicas
        ]
        return montar({'/Users': utilizadores}, data_manager=data_manager)

    def test_tira_o_limite_que_o_painel_pos(self, cache_limpa, data_manager):
        backend = self._montar(data_manager, [(GUID, "ana", 2)])
        data_manager.set_user_profile(GUID, {"username": "ana", "screen_limit": 2})

        resultado = backend.clear_session_limits()

        assert resultado == {"success": True, "limpos": 1}
        assert backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy')[-1]['MaxActiveSessions'] == 0

    def test_quem_ja_esta_sem_limite_nao_e_reescrito(self, cache_limpa, data_manager):
        backend = self._montar(data_manager, [(GUID, "ana", 0)])
        data_manager.set_user_profile(GUID, {"username": "ana", "screen_limit": 2})

        assert backend.clear_session_limits()["limpos"] == 0
        assert backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy') == []

    def test_quem_nao_tem_perfil_no_painel_nao_e_tocado(self, cache_limpa, data_manager):
        # Noutra conta, o limite é de quem o pôs — o painel não o administra.
        backend = self._montar(data_manager, [(OUTRO, "bruno", 5)])

        assert backend.clear_session_limits()["limpos"] == 0
        assert backend.conn.api.corpos_enviados(f'/Users/{OUTRO}/Policy') == []

    def test_a_politica_vai_inteira(self, cache_limpa, data_manager):
        # A armadilha de sempre: `POST /Users/{id}/Policy` substitui a política
        # toda. Enviar só o campo alterado apagaria os restantes.
        backend = self._montar(data_manager, [(GUID, "ana", 2)])
        data_manager.set_user_profile(GUID, {"username": "ana", "screen_limit": 2})

        backend.clear_session_limits()

        gravada = backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy')[-1]
        assert gravada['EnabledFolders'] == POLITICA_BASE['EnabledFolders']
        assert 'IsAdministrator' in gravada

    def test_sem_ligacao_nao_escreve_nada_e_nao_se_da_por_feita(self, cache_limpa, data_manager):
        # Se desse `success`, a reparação ficava marcada como concluída sem ter
        # corrido, e quem estivesse trancado ficava trancado para sempre.
        backend = self._montar(data_manager, [(GUID, "ana", 2)])
        data_manager.set_user_profile(GUID, {"username": "ana", "screen_limit": 2})
        backend.conn.server_info = None

        assert backend.clear_session_limits()["success"] is False
        assert backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy') == []

    def test_o_plex_nao_tem_nada_a_limpar(self):
        from app.services.media_server.plex.backend import PlexManager

        backend = PlexManager.__new__(PlexManager)
        assert backend.clear_session_limits() == {"success": True, "limpos": 0}


@pytest.mark.integration
class TestRemocao:
    def test_remover_apaga_no_servidor_e_desativa_o_perfil(self, cache_limpa, data_manager):
        backend = montar({'/Users': [{"Id": GUID, "Name": "ana", "Policy": {}}]}, data_manager=data_manager)
        backend.data_manager.set_user_profile(GUID, {"username": "ana", "status": "active"})

        resultado = backend.remove_user(GUID)

        assert resultado['success'] is True
        assert ('DELETE', f'/Users/{GUID}') in backend.conn.api.enviados
        assert backend.data_manager.get_user_profile(GUID)['status'] == 'inactive'

    def test_ja_nao_existir_no_servidor_nao_e_erro(self, cache_limpa, data_manager):
        # O objetivo — deixar de ter acesso — está cumprido, e é preciso
        # continuar para limpar o perfil local.
        backend = montar(
            {'/Users': [{"Id": GUID, "Name": "ana", "Policy": {}}]},
            erros={f'/Users/{GUID}': JellyfinApiError("não existe", status_code=404)},
            data_manager=data_manager,
        )
        backend.data_manager.set_user_profile(GUID, {"username": "ana", "status": "active"})

        assert backend.remove_user(GUID)['success'] is True
        assert backend.data_manager.get_user_profile(GUID)['status'] == 'inactive'


class TestSessoes:
    def _sessao_bruta(self, **extra):
        base = {
            "Id": "sess-1",
            "UserId": GUID,
            "UserName": "ana",
            "Client": "Jellyfin Web",
            "DeviceName": "Chrome",
            "PlayState": {"PositionTicks": 300_000_000, "IsPaused": False},
            "NowPlayingItem": {
                "Id": "item-1", "Name": "Duna", "Type": "Movie",
                "RunTimeTicks": 1_200_000_000, "ProductionYear": 2021,
                "ImageTags": {"Primary": "tag1"},
            },
        }
        base.update(extra)
        return base

    def _provider(self, sessoes):
        backend = montar({'/Sessions': sessoes})
        return backend.sessions

    def test_ticks_viram_milissegundos(self):
        # 10 000 000 ticks = 1 segundo = 1000 ms.
        assert ticks_para_ms(10_000_000) == 1000
        assert ticks_para_ms(None) == 0

    def test_traducao_de_uma_sessao(self, cache_limpa):
        sessao = self._provider([self._sessao_bruta()]).list_sessions()[0]

        assert isinstance(sessao, MediaSession)
        assert sessao.user_id == GUID
        assert sessao.session_key == "sess-1"
        assert sessao.title == "Duna"
        assert sessao.state == "playing"
        assert sessao.view_offset == 30_000
        assert sessao.duration == 120_000
        assert sessao.progress == 25.0
        assert sessao.platform == "chrome"

    def test_pausa(self, cache_limpa):
        bruta = self._sessao_bruta()
        bruta['PlayState']['IsPaused'] = True

        assert self._provider([bruta]).list_sessions()[0].state == "paused"

    def test_quem_esta_ligado_sem_ver_nada_nao_conta(self, cache_limpa):
        # O Jellyfin devolve também as sessões apenas ligadas.
        ociosa = {"Id": "sess-2", "UserId": OUTRO, "UserName": "bruno"}

        assert len(self._provider([self._sessao_bruta(), ociosa]).list_sessions()) == 1

    def test_episodio_traz_serie_e_numeracao(self, cache_limpa):
        bruta = self._sessao_bruta(NowPlayingItem={
            "Id": "ep-1", "Name": "Segredos", "Type": "Episode",
            "SeriesName": "Dark", "ParentIndexNumber": 2, "IndexNumber": 5,
            "RunTimeTicks": 1_200_000_000,
        })

        sessao = self._provider([bruta]).list_sessions()[0]

        assert sessao.title == "Dark"
        assert sessao.subtitle == "S02 · E05 - Segredos"
        assert sessao.media_title == "Dark S02E05 - Segredos"

    def test_encerrar_avisa_antes_de_cortar(self, cache_limpa):
        # O Stop do Jellyfin não leva motivo: a mensagem é o que mais se
        # aproxima do 'reason' do Plex, e vai primeiro.
        provider = self._provider([self._sessao_bruta()])
        sessao = provider.list_sessions()[0]

        assert provider.terminate(sessao, "excedeu o limite") is True

        enviados = [e[1] for e in provider.conn.api.enviados if e[0] == 'POST']
        assert enviados == ['/Sessions/sess-1/Message', '/Sessions/sess-1/Playing/Stop']
        aviso = provider.conn.api.corpos_enviados('/Sessions/sess-1/Message')[0]
        assert aviso['Text'] == "excedeu o limite"

    def test_um_aviso_que_falha_nao_impede_o_corte(self, cache_limpa):
        backend = montar(
            {'/Sessions': [self._sessao_bruta()]},
            erros={'/Sessions/sess-1/Message': JellyfinApiError("cliente mudo")},
        )
        sessao = backend.sessions.list_sessions()[0]

        assert backend.sessions.terminate(sessao, "motivo") is True
        assert ('POST', '/Sessions/sess-1/Playing/Stop', None) in backend.conn.api.enviados

    def test_uma_recusa_do_servidor_nao_e_dada_por_feita(self, cache_limpa):
        """
        🐛 O `Stop` devolvia True em TODOS os casos. Quem chama trata True como
        "encerrada" e não volta a tentar, por isso uma recusa do servidor
        desaparecia sem deixar rasto — e o stream seguia.
        """
        backend = montar(
            {'/Sessions': [self._sessao_bruta()]},
            erros={'/Sessions/sess-1/Playing/Stop': JellyfinApiError("recusado", status_code=500)},
        )
        sessao = backend.sessions.list_sessions()[0]

        assert backend.sessions.terminate(sessao, "motivo") is False

    def test_a_sessao_que_ja_desapareceu_conta_como_encerrada(self, cache_limpa):
        backend = montar(
            {'/Sessions': [self._sessao_bruta()]},
            erros={'/Sessions/sess-1/Playing/Stop': JellyfinApiError("não existe", status_code=404)},
        )
        sessao = backend.sessions.list_sessions()[0]

        assert backend.sessions.terminate(sessao, "motivo") is True


class TestClienteQueIgnoraOComando:
    """
    🐛 REGRESSÃO REPORTADA: com o leitor integrado da aplicação Android
    (ExoPlayer), o painel mandava parar a cada volta, o Jellyfin aceitava — e a
    reprodução continuava. Pelo navegador o mesmo corte funcionava.

    Para esses clientes existe um último recurso: revogar o acesso do APARELHO.
    Isso invalida as credenciais dele, e o pedido seguinte do leitor recebe 401
    — a reprodução morre mesmo que o cliente não escute comandos.
    """

    def _sessao(self, backend):
        return backend.sessions.list_sessions()[0]

    def _bruta(self, **extra):
        return TestSessoes()._sessao_bruta(**extra)

    def _montar(self, aparelhos=None, **respostas):
        base = {'/Devices': {"Items": aparelhos if aparelhos is not None else []}}
        base.update(respostas)
        return base

    def test_revogar_o_aparelho_leva_o_id_do_aparelho(self, cache_limpa):
        backend = montar(self._montar(
            aparelhos=[{"Id": "aparelho-abc", "Name": "M23"}],
            **{'/Sessions': [self._bruta(DeviceId="aparelho-abc")]},
        ))
        sessao = self._sessao(backend)

        assert backend.sessions.force_terminate(sessao, "limite") is True
        assert ('/Devices', {'id': 'aparelho-abc'}) in backend.conn.api.apagados

    def test_usa_a_grafia_do_servidor_e_nao_a_da_sessao(self, cache_limpa):
        # O id vem do CLIENTE. Se o servidor o lista com outra caixa, é a dele
        # que vale — mandar a outra é pedir o 400 de volta.
        backend = montar(self._montar(
            aparelhos=[{"Id": "Aparelho-ABC", "Name": "M23"}],
            **{'/Sessions': [self._bruta(DeviceId="aparelho-abc")]},
        ))

        assert backend.sessions.force_terminate(self._sessao(backend), "limite") is True
        assert ('/Devices', {'id': 'Aparelho-ABC'}) in backend.conn.api.apagados

    def test_aparelho_que_o_servidor_nao_conhece_nao_e_pedido(self, cache_limpa):
        """
        🐛 REGRESSÃO REPORTADA: o `DeviceId` da sessão é o que o CLIENTE diz
        ser, e nem sempre corresponde a um aparelho registado. O
        `DELETE /Devices` respondia 400 com um corpo vazio — a cada volta da
        verificação, para sempre, sem dizer nada a quem lia o log.
        """
        backend = montar(self._montar(
            aparelhos=[{"Id": "outro-aparelho"}],
            **{'/Sessions': [self._bruta(DeviceId="aparelho-abc")]},
        ))

        assert backend.sessions.force_terminate(self._sessao(backend), "limite") is False
        assert backend.conn.api.apagados == []

    def test_sem_aparelho_conhecido_nao_se_apaga_nada(self, cache_limpa):
        # Nunca adivinhar: apagar o aparelho errado tira o acesso a quem não fez
        # nada. Sem o DeviceId, o painel assume que não tem como forçar.
        backend = montar(self._montar(**{'/Sessions': [self._bruta()]}))
        sessao = self._sessao(backend)

        assert backend.sessions.force_terminate(sessao, "limite") is False
        assert backend.conn.api.apagados == []

    def test_uma_recusa_do_servidor_nao_e_dada_por_feita(self, cache_limpa):
        # O aparelho EXISTE na lista; é o apagar que o servidor recusa.
        backend = montar(
            self._montar(
                aparelhos=[{"Id": "aparelho-abc"}],
                **{'/Sessions': [self._bruta(DeviceId="aparelho-abc")]},
            ),
            erros={('DELETE', '/Devices'): JellyfinApiError("sem permissão", status_code=403)},
        )
        sessao = self._sessao(backend)

        assert backend.sessions.force_terminate(sessao, "limite") is False
        assert ('/Devices', {'id': 'aparelho-abc'}) in backend.conn.api.apagados

    def test_o_cliente_que_avisa_que_nao_aceita_comandos_e_terminado_na_mesma(self, cache_limpa):
        # O `SupportsMediaControl` só serve para o log ficar a explicar porquê:
        # a ordem vai sempre, porque nem todos os clientes que ignoram o
        # comando declaram que o fazem.
        backend = montar({'/Sessions': [self._bruta(SupportsMediaControl=False)]})
        sessao = self._sessao(backend)

        assert backend.sessions.terminate(sessao, "motivo") is True
        assert ('POST', '/Sessions/sess-1/Playing/Stop', None) in backend.conn.api.enviados

    def test_o_plex_nao_tem_nada_mais_forte_a_oferecer(self):
        # O contrato exige a resposta honesta: sem um último recurso, False.
        from app.services.media_server.plex.sessions import PlexSessionsProvider

        provider = PlexSessionsProvider(None)
        assert provider.force_terminate(MediaSession(
            user_id="1", username_fallback="ana", user_email="", session_key="1",
            media_title="Duna", title="Duna", subtitle="", media_type="movie",
            state="playing", platform="chrome", player="Plex Web", progress=0.0,
            view_offset=0, duration=0,
        ), "limite") is False


class TestPlataformas:
    @pytest.mark.parametrize("cliente,esperado", [
        ("Jellyfin Web", "chrome"),
        ("Findroid", "android"),
        ("Jellyfin for Roku", "roku"),
        ("Swiftfin", "ios"),
        # 🐛 Isto esperava "plex": um cliente do Jellyfin ficava com o
        # logótipo do PLEX ao lado do nome, na lista de aparelhos e no
        # "Reproduzindo Agora".
        ("Jellyfin Media Player", "jellyfin"),
        ("Swiftfin para Apple TV", "atv"),
        ("AparelhoEstranho", "default"),
    ])
    def test_plataformas(self, cliente, esperado):
        dispositivo = {"Jellyfin Web": "Chrome", "Findroid": "Android", "Swiftfin": "iPhone"}.get(cliente, "")
        assert plataforma_de(cliente, dispositivo, "") == esperado

    def test_o_chromecast_nao_e_confundido_com_o_chrome(self):
        assert plataforma_de("Jellyfin Web", "Chromecast", "") == "chromecast"

    def test_o_tempo_real_ainda_nao_esta_disponivel(self):
        # Enquanto for False, o motor de streams usa a verificação periódica.
        assert JellyfinSessionsProvider(None).supports_realtime() is False


@pytest.mark.integration
class TestCriacaoDeContas:
    class Registo:
        def __init__(self, username, password, email=""):
            self.username = username
            self.password = password
            self.email = email

    def _backend(self, utilizadores=None, data_manager=None):
        return montar({
            '/Users': utilizadores if utilizadores is not None else [],
            '/Users/New': {"Id": GUID, "Name": "ana"},
            '/Library/VirtualFolders': BIBLIOTECAS,
            f'/Users/{GUID}': {"Id": GUID, "Name": "ana", "Policy": dict(POLITICA_BASE)},
        }, data_manager=data_manager)

    def test_cria_a_conta_no_servidor(self, cache_limpa):
        backend = self._backend()

        resultado = backend.invites.create_account("ana", "segredo")

        assert resultado['success'] is True
        assert resultado['user_id'] == GUID
        assert backend.conn.api.corpos_enviados('/Users/New')[0] == {"Name": "ana", "Password": "segredo"}

    def test_nome_repetido_da_uma_mensagem_clara(self, cache_limpa):
        # O Jellyfin recusa com um 400 pouco explícito; vale a pena antecipar.
        backend = self._backend([{"Id": OUTRO, "Name": "Ana", "Policy": {}}])

        resultado = backend.invites.create_account("ana", "segredo")

        assert resultado['success'] is False
        assert "já existe" in resultado['message'].lower()

    def test_sem_palavra_passe_nao_cria(self, cache_limpa):
        assert self._backend().invites.create_account("ana", "")['success'] is False

    def test_resgatar_um_convite_cria_a_conta_e_aplica_as_bibliotecas(self, cache_limpa, data_manager):
        backend = self._backend(data_manager=data_manager)
        backend.create_invitation(library_titles=["Séries"], screens=2)
        codigo = backend.list_invitations()[0]['code']

        resultado = backend.claim_invitation(codigo, self.Registo("ana", "segredo", "ana@exemplo.com"))

        assert resultado['success'] is True
        politica = backend.conn.api.corpos_enviados(f'/Users/{GUID}/Policy')[0]
        assert politica['EnabledFolders'] == ["lib-series"]
        perfil = backend.data_manager.get_user_profile(GUID)
        assert perfil['username'] == "ana"
        assert perfil['media_server_type'] == "jellyfin"

    def test_sem_credenciais_nao_gasta_a_vaga(self, cache_limpa, data_manager):
        backend = self._backend(data_manager=data_manager)
        backend.create_invitation(library_titles=["Filmes"])
        codigo = backend.list_invitations()[0]['code']

        resultado = backend.claim_invitation(codigo, self.Registo("", ""))

        assert resultado['success'] is False
        assert backend.list_invitations()[0]['use_count'] == 0

    def test_uma_falha_a_criar_devolve_a_vaga(self, cache_limpa, data_manager):
        # Sem isto, uma tentativa falhada queimava uma utilização do convite.
        backend = self._backend([{"Id": OUTRO, "Name": "ana", "Policy": {}}], data_manager=data_manager)
        backend.create_invitation(library_titles=["Filmes"])
        codigo = backend.list_invitations()[0]['code']

        resultado = backend.claim_invitation(codigo, self.Registo("ana", "segredo"))

        assert resultado['success'] is False
        assert backend.list_invitations()[0]['use_count'] == 0

    def test_este_servidor_nao_tem_convites_pendentes(self):
        assert JellyfinManager(data_manager=None).invites.accept_invite_via_token("x")['success'] is False


class TestImagens:
    def test_o_url_da_imagem_leva_a_chave_de_api(self, cache_limpa):
        backend = montar()

        url, params = backend.authorize_image_url('jellyfin', '/Items/1/Images/Primary')

        assert url == "http://jellyfin.local:8096/Items/1/Images/Primary"
        assert params == {'ApiKey': 'chave'}

    def test_fonte_de_outro_backend_nao_gera_url(self, cache_limpa):
        assert montar().authorize_image_url('plex', '/x') == (None, {})

    def test_o_backend_anuncia_as_suas_fontes(self):
        assert JellyfinManager(data_manager=None).IMAGE_SOURCES == ('jellyfin',)


@pytest.mark.integration
class TestAutenticacao:
    def _backend(self, resultado=None, erro=None, data_manager=None):
        return montar(
            {'/Users/AuthenticateByName': resultado} if resultado else {},
            erros={'/Users/AuthenticateByName': erro} if erro else None,
            data_manager=data_manager,
        )

    def test_credenciais_certas_devolvem_a_conta(self, cache_limpa):
        backend = self._backend({"User": {"Id": GUID, "Name": "ana"}, "AccessToken": "tok"})

        conta = backend.authenticate("ana", "segredo")

        assert conta.id == GUID
        assert conta.username == "ana"
        assert backend.conn.api.corpos_enviados('/Users/AuthenticateByName')[0] == {"Username": "ana", "Pw": "segredo"}

    def test_a_sessao_aberta_para_validar_a_palavra_passe_e_fechada(self, cache_limpa):
        """
        🐛 REGRESSÃO: autenticar ABRE uma sessão no Jellyfin, e deitar o token
        fora não a fecha — fica na lista de sessões do servidor. Cada entrada no
        painel deixava uma sessão órfã, e num servidor com sessões simultâneas
        limitadas a pessoa tinha de sair de um aparelho para entrar no painel.

        `/Sessions/Logout` encerra a sessão de QUEM CHAMA, por isso o pedido tem
        de ir com o token do utilizador — com a chave de API do painel fecharia
        a sessão errada.
        """
        backend = self._backend({"User": {"Id": GUID, "Name": "ana"}, "AccessToken": "tok"})

        backend.authenticate("ana", "segredo")

        assert ('/Sessions/Logout', 'tok') in backend.conn.api.tokens_usados

    def test_uma_falha_a_fechar_a_sessao_nao_impede_a_entrada(self, cache_limpa):
        # No pior caso fica a sessão órfã que existia antes desta correção —
        # recusar o login por causa disso seria muito pior.
        backend = montar(
            {'/Users/AuthenticateByName': {"User": {"Id": GUID, "Name": "ana"}, "AccessToken": "tok"}},
            erros={'/Sessions/Logout': JellyfinApiError("boom", status_code=500)},
        )

        assert backend.authenticate("ana", "segredo").id == GUID

    def test_sem_token_na_resposta_nao_se_tenta_fechar_nada(self, cache_limpa):
        backend = self._backend({"User": {"Id": GUID, "Name": "ana"}})

        backend.authenticate("ana", "segredo")

        assert backend.conn.api.tokens_usados == []

    def test_o_token_de_sessao_do_servidor_nao_e_guardado(self, cache_limpa):
        # O painel fala com o Jellyfin pela chave de API: guardar também o token
        # do utilizador seria mais um segredo de terceiros a proteger sem motivo.
        conta = self._backend({"User": {"Id": GUID, "Name": "ana"}, "AccessToken": "tok"}).authenticate("ana", "segredo")

        assert not hasattr(conta, 'token')
        assert "tok" not in repr(conta)

    def test_credenciais_erradas_devolvem_none_sem_levantar(self, cache_limpa):
        # 401 é o caso NORMAL, não um erro do painel.
        backend = self._backend(erro=JellyfinApiError("unauthorized", status_code=401))

        assert backend.authenticate("ana", "errada") is None

    def test_uma_falha_do_servidor_tambem_devolve_none(self, cache_limpa):
        backend = self._backend(erro=JellyfinApiError("boom", status_code=500))

        assert backend.authenticate("ana", "segredo") is None

    @pytest.mark.parametrize("username,password", [("", "x"), ("ana", ""), (None, None)])
    def test_credenciais_vazias_nao_chegam_ao_servidor(self, cache_limpa, username, password):
        backend = self._backend({"User": {"Id": GUID, "Name": "ana"}})

        assert backend.authenticate(username, password) is None
        assert backend.conn.api.corpos_enviados('/Users/AuthenticateByName') == []

    def test_resposta_sem_utilizador_nao_abre_sessao(self, cache_limpa):
        backend = self._backend({"AccessToken": "tok"})

        assert backend.authenticate("ana", "segredo") is None

    def test_o_endereco_do_servidor_so_vai_no_resgate_concluido(self, cache_limpa, data_manager):
        # 🔒 Estava no HTML da página pública de convite: qualquer pessoa com o
        # código ficava a saber onde está o servidor sem sequer o resgatar.
        backend = montar({
            '/Users': [],
            '/Users/New': {"Id": GUID, "Name": "ana"},
            '/Library/VirtualFolders': BIBLIOTECAS,
            f'/Users/{GUID}': {"Id": GUID, "Name": "ana", "Policy": dict(POLITICA_BASE)},
        }, data_manager=data_manager)
        backend.create_invitation(library_titles=["Filmes"])
        codigo = backend.list_invitations()[0]['code']

        class Registo:
            username, password, email = "ana", "segredo", ""

        resultado = backend.claim_invitation(codigo, Registo())

        assert resultado["user_data"]["server_url"] == "http://jellyfin.local:8096"


@pytest.mark.integration
class TestAutenticacaoNoPlex:
    def test_o_plex_nao_autentica_por_palavra_passe(self):
        # A conta vive no plex.tv e o painel usa o fluxo de PIN. Devolver None
        # faz a página de login mostrar o botão em vez de um formulário inútil.
        from app.services.media_server.plex import PlexManager

        assert PlexManager(None, None, None, None).authenticate("ana", "segredo") is None


class TestFusaoDeSessoes:
    def test_o_jellyfin_nao_funde_nada(self, cache_limpa):
        """
        🐛 REGRESSÃO REPORTADA: um utilizador com limite de telas a ver a MESMA
        mídia em dois aparelhos não era cortado, porque o motor aplicava a todos
        os servidores o filtro de Cast do Plex.

        No Jellyfin quem apenas COMANDA outro aparelho aparece sem
        `NowPlayingItem` e já é descartado antes — o que resta são reproduções
        verdadeiras, cada uma valendo uma tela.
        """
        provider = JellyfinSessionsProvider(None)
        duas = [
            MediaSession(user_id="1", username_fallback="ana", user_email="", session_key="1",
                         media_title="Duna", title="Duna", subtitle="", media_type="movie",
                         state="playing", platform="android", player="", progress=0.0,
                         view_offset=100, duration=1000),
            MediaSession(user_id="1", username_fallback="ana", user_email="", session_key="2",
                         media_title="Duna", title="Duna", subtitle="", media_type="movie",
                         state="playing", platform="chrome", player="", progress=0.0,
                         view_offset=100, duration=1000),
        ]

        assert len(provider.deduplicate_sessions(duas)) == 2

    def test_quem_so_comanda_nao_chega_a_contar(self, cache_limpa):
        # É isto que torna a fusão desnecessária no Jellyfin.
        backend = montar({'/Sessions': [
            {"Id": "a-tocar", "UserId": GUID, "UserName": "ana", "Client": "Jellyfin Android",
             "PlayState": {"PositionTicks": 0, "IsPaused": False},
             "NowPlayingItem": {"Id": "i1", "Name": "Duna", "Type": "Movie", "RunTimeTicks": 10}},
            {"Id": "so-comanda", "UserId": GUID, "UserName": "ana", "Client": "Jellyfin Web"},
        ]})

        sessoes = backend.sessions.list_sessions()

        assert [s.session_key for s in sessoes] == ["a-tocar"]

    @pytest.mark.parametrize("cliente,dispositivo", [
        ("Jellyfin Podcast", ""),
        ("Jellyfin Web", "Cast Room"),
        ("Broadcast Player", ""),
    ])
    def test_nem_tudo_com_cast_no_nome_e_um_chromecast(self, cliente, dispositivo):
        # 🐛 A verificação era por substring de 'cast': um falso Chromecast fazia
        # o filtro de sessões duplicadas descartar a outra sessão do utilizador.
        assert plataforma_de(cliente, dispositivo, "") != "chromecast"

    def test_um_chromecast_a_serio_continua_a_ser_reconhecido(self):
        assert plataforma_de("Jellyfin Web", "Chromecast", "") == "chromecast"
        assert plataforma_de("Google Cast", "", "") == "chromecast"


@pytest.mark.integration
class TestIdentidadeDaReproducao:
    """
    🐛 REGRESSÃO REPORTADA: o `Id` da sessão do Jellyfin é do APARELHO e
    sobrevive a parar e recomeçar. O painel guardava "já cortei esta" por esse
    id, e quem recomeçasse logo a seguir a um corte ficava toda a janela do
    anti-spam sem ser incomodado — no log do servidor, cortes espaçados de 60 a
    120 segundos em vez de a cada volta da verificação.
    """

    def _sessao_bruta(self, **extra):
        base = {
            "Id": "aparelho-android", "UserId": GUID, "UserName": "teste",
            "Client": "Jellyfin for Android", "DeviceName": "Telemóvel",
            "PlayState": {"PositionTicks": 0, "IsPaused": False},
            "NowPlayingItem": {"Id": "filme-1", "Name": "Amor Sem Limites",
                               "Type": "Movie", "RunTimeTicks": 10_000},
        }
        base.update(extra)
        return base

    def test_a_sessao_continua_a_ser_o_que_se_manda_parar(self, cache_limpa):
        backend = montar({'/Sessions': [self._sessao_bruta()]})

        sessao = backend.sessions.list_sessions()[0]

        assert sessao.session_key == "aparelho-android"

    def test_reproducoes_diferentes_no_mesmo_aparelho_tem_chaves_diferentes(self, cache_limpa):
        primeiro = montar({'/Sessions': [self._sessao_bruta()]}).sessions.list_sessions()[0]
        segundo = montar({'/Sessions': [self._sessao_bruta(NowPlayingItem={
            "Id": "filme-2", "Name": "O Recomeço", "Type": "Movie", "RunTimeTicks": 10_000,
        })]}).sessions.list_sessions()[0]

        assert primeiro.session_key == segundo.session_key
        assert primeiro.playback_key != segundo.playback_key

    def test_o_playlist_item_id_separa_dois_arranques_do_mesmo_filme(self, cache_limpa):
        primeiro = montar({'/Sessions': [self._sessao_bruta(PlaylistItemId="playlistItem1")]}).sessions.list_sessions()[0]
        segundo = montar({'/Sessions': [self._sessao_bruta(PlaylistItemId="playlistItem2")]}).sessions.list_sessions()[0]

        assert primeiro.playback_key != segundo.playback_key


@pytest.mark.integration
class TestSessoesVisiveis:
    def test_nao_se_filtra_por_atividade_recente(self, cache_limpa):
        # 🐛 O painel pedia `activeWithinSeconds=60`, um filtro inventado sem
        # nada na API que o justificasse. Uma reprodução cujo cliente demore
        # mais do que isso a dar sinal de vida desaparecia da vista — e o que o
        # painel não vê, não conta nem corta.
        backend = montar({'/Sessions': []})

        backend.sessions.list_sessions()

        pedidos = [e for e in backend.conn.api.enviados if e[1] == '/Sessions']
        assert pedidos, "a listagem de sessões não foi pedida"

    def test_quem_esta_ligado_sem_ver_nada_continua_a_nao_contar(self, cache_limpa):
        backend = montar({'/Sessions': [
            {"Id": "a-tocar", "UserId": GUID, "UserName": "teste", "Client": "Jellyfin for Android",
             "PlayState": {"PositionTicks": 0, "IsPaused": False},
             "NowPlayingItem": {"Id": "i1", "Name": "Duna", "Type": "Movie", "RunTimeTicks": 10}},
            {"Id": "so-ligado", "UserId": GUID, "UserName": "teste", "Client": "Jellyfin Web"},
        ]})

        assert [s.session_key for s in backend.sessions.list_sessions()] == ["a-tocar"]


@pytest.mark.integration
class TestHistoricoEAparelhos:
    """
    No Plex isto vem do Tautulli, que guarda um registo por REPRODUÇÃO. O
    Jellyfin não tem equivalente no núcleo, mas guarda por utilizador e por
    item o que basta: `UserData` traz `LastPlayedDate`, `PlayedPercentage`,
    `PlayCount` e `Played`.

    ⚠️ É um histórico por ITEM: ver o mesmo episódio três vezes dá uma linha.
    E o servidor não guarda em que aparelho cada item foi visto — daí a coluna
    do reprodutor vir vazia, e a lista de aparelhos vir de `GET /Devices`.
    """

    def _filme(self, **extra):
        base = {
            "Id": "item-1", "Name": "Duna", "Type": "Movie", "ProductionYear": 2021,
            "RunTimeTicks": 1_200_000_000, "ImageTags": {"Primary": "tag1"},
            "UserData": {"LastPlayedDate": "2026-09-12T08:42:29.1234567Z", "Played": True},
        }
        base.update(extra)
        return base

    def _backend(self, itens=None, total=None, aparelhos=None):
        resposta_itens = {
            "Items": itens if itens is not None else [],
            "TotalRecordCount": total if total is not None else len(itens or []),
        }
        return montar({
            '/Items': resposta_itens,
            '/Devices': {"Items": aparelhos or []},
        })

    # --- aparelhos ---

    def test_os_aparelhos_vem_da_lista_do_servidor(self, cache_limpa):
        # É melhor do que a do Plex: são os aparelhos REGISTADOS na conta, e
        # não os que se conseguem adivinhar a partir do histórico.
        backend = self._backend(aparelhos=[
            {"Id": "d1", "Name": "Chrome", "AppName": "Jellyfin Web",
             "DateLastActivity": "2026-09-12T08:00:00Z"},
        ])

        resultado = backend.get_user_devices(GUID)

        assert resultado["success"] is True
        assert resultado["devices"][0]["player"] == "Chrome"
        assert resultado["devices"][0]["platform"] == "Jellyfin Web"
        assert resultado["devices"][0]["last_seen"] > 0

    def test_o_servidor_diz_qual_e_o_icone(self, cache_limpa):
        """
        🐛 REGRESSÃO REPORTADA: todos os aparelhos apareciam com o logótipo do
        PLEX. A interface adivinhava o ícone pela PRIMEIRA palavra do nome da
        aplicação, e com o Jellyfin isso dá sempre "jellyfin" ("Jellyfin Web",
        "Jellyfin Android"...) — que não existia no catálogo de ícones. O
        ícone de recurso, esse, é mesmo o logótipo do Plex.
        """
        backend = self._backend(aparelhos=[
            {"Id": "d1", "Name": "Chrome", "AppName": "Jellyfin Web"},
            {"Id": "d2", "Name": "SM-M236B", "AppName": "Jellyfin Android"},
            {"Id": "d3", "Name": "Sala", "AppName": "Jellyfin Media Player"},
        ])

        chaves = [a["platform_key"] for a in backend.get_user_devices(GUID)["devices"]]

        assert chaves == ["chrome", "android", "jellyfin"]

    def test_o_nome_da_aplicacao_continua_a_ser_mostrado(self, cache_limpa):
        # A chave do ícone é uma coisa, o texto que a pessoa lê é outra:
        # "chrome" por baixo do nome do aparelho não diria nada.
        backend = self._backend(aparelhos=[{"Id": "d1", "Name": "Chrome", "AppName": "Jellyfin Web"}])

        aparelho = backend.get_user_devices(GUID)["devices"][0]

        assert aparelho["platform"] == "Jellyfin Web"
        assert aparelho["platform_key"] == "chrome"

    def test_um_aparelho_que_nao_se_classifica_nao_inventa_chave(self, cache_limpa):
        backend = self._backend(aparelhos=[{"Id": "d1", "Name": "Caixa", "AppName": "AlgoDesconhecido"}])

        assert backend.get_user_devices(GUID)["devices"][0]["platform_key"] == "default"

    def test_o_nome_personalizado_ganha_ao_do_aparelho(self, cache_limpa):
        # É o nome que o administrador deu na interface do Jellyfin, e o que a
        # pessoa reconhece.
        backend = self._backend(aparelhos=[
            {"Id": "d1", "Name": "SM-M236B", "CustomName": "Telemóvel da Ana"},
        ])

        assert backend.get_user_devices(GUID)["devices"][0]["player"] == "Telemóvel da Ana"

    def test_os_aparelhos_vem_do_mais_recente_para_o_mais_antigo(self, cache_limpa):
        backend = self._backend(aparelhos=[
            {"Id": "d1", "Name": "Antigo", "DateLastActivity": "2026-09-01T08:00:00Z"},
            {"Id": "d2", "Name": "Recente", "DateLastActivity": "2026-09-12T08:00:00Z"},
        ])

        nomes = [a["player"] for a in backend.get_user_devices(GUID)["devices"]]

        assert nomes == ["Recente", "Antigo"]

    def test_os_aparelhos_sao_pedidos_so_para_este_utilizador(self, cache_limpa):
        backend = self._backend(aparelhos=[])

        backend.get_user_devices(GUID)

        assert ('GET', '/Devices') in [(m, e) for m, e in backend.conn.api.enviados if m == 'GET']

    def test_sem_ligacao_a_lista_vem_vazia_e_nao_e_um_erro(self, cache_limpa):
        backend = self._backend(aparelhos=[{"Id": "d1", "Name": "Chrome"}])
        backend.conn.server_info = None

        assert backend.get_user_devices(GUID) == {"success": True, "devices": []}

    # --- histórico ---

    def test_um_filme_traz_titulo_ano_e_data(self, cache_limpa):
        linha = self._backend([self._filme()]).get_watch_history(GUID)["history"][0]

        assert linha["title"] == "Duna"
        assert linha["subtitle"] == "2021"
        assert linha["date"] == "12/09/2026 08:42"

    def test_a_fracao_de_sete_casas_do_dotnet_nao_parte_a_data(self, cache_limpa):
        """O Jellyfin manda SETE casas decimais — a precisão dos ticks do .NET.

        O `fromisoformat` do Python 3.11+ tolera-as, mas o painel não pode
        depender disso: em versões anteriores levantava `ValueError`, e o
        sintoma seria a data de cada linha a vir vazia sem erro nenhum.
        """
        item = self._filme(UserData={"LastPlayedDate": "2026-09-12T08:42:29.1234567Z", "Played": True})

        assert self._backend([item]).get_watch_history(GUID)["history"][0]["date"] == "12/09/2026 08:42"

    @pytest.mark.parametrize("data", [None, "", "não é uma data"])
    def test_uma_data_ilegivel_nao_rebenta_a_lista(self, cache_limpa, data):
        item = self._filme(UserData={"LastPlayedDate": data, "Played": True})

        assert self._backend([item]).get_watch_history(GUID)["history"][0]["date"] == ""

    def test_um_episodio_traz_a_serie_e_a_numeracao(self, cache_limpa):
        item = {
            "Id": "ep-1", "Name": "Segredos", "Type": "Episode", "SeriesName": "Dark",
            "SeriesId": "serie-1", "ParentIndexNumber": 2, "IndexNumber": 5,
            "UserData": {"LastPlayedDate": "2026-09-12T08:42:29Z", "Played": True},
        }

        linha = self._backend([item]).get_watch_history(GUID)["history"][0]

        assert linha["title"] == "Dark"
        assert linha["subtitle"] == "S02 · E05 - Segredos"

    def test_um_item_marcado_como_visto_conta_como_cem_por_cento(self, cache_limpa):
        # 🐛 `PlayedPercentage` só vem preenchido enquanto a reprodução está a
        # meio. Um item já visto não a traz — e aí são 100%, não 0.
        item = self._filme(UserData={"LastPlayedDate": "2026-09-12T08:42:29Z", "Played": True})

        assert self._backend([item]).get_watch_history(GUID)["history"][0]["percent_complete"] == 100

    def test_um_item_a_meio_traz_a_percentagem_do_servidor(self, cache_limpa):
        item = self._filme(UserData={
            "LastPlayedDate": "2026-09-12T08:42:29Z", "Played": False, "PlayedPercentage": 37.4,
        })

        assert self._backend([item]).get_watch_history(GUID)["history"][0]["percent_complete"] == 37

    def test_a_capa_de_um_episodio_e_a_da_serie(self, cache_limpa):
        # A miniatura de um episódio é um fotograma, que numa lista não diz
        # nada — é a mesma escolha que o backend do Plex faz.
        item = {
            "Id": "ep-1", "Name": "Segredos", "Type": "Episode", "SeriesName": "Dark",
            "SeriesId": "serie-1", "SeriesPrimaryImageTag": "tagS",
            "ImageTags": {"Primary": "tagE"},
            "UserData": {"LastPlayedDate": "2026-09-12T08:42:29Z"},
        }

        url = self._backend([item]).get_watch_history(GUID)["history"][0]["poster_url"]

        assert _fonte_da_imagem(url) == "jellyfin:/Items/serie-1/Images/Primary?tag=tagS"

    def test_sem_capa_a_linha_continua_a_existir(self, cache_limpa):
        item = self._filme(ImageTags={})

        assert self._backend([item]).get_watch_history(GUID)["history"][0]["poster_url"] is None

    def test_o_reprodutor_vem_vazio_porque_o_servidor_nao_o_guarda(self, cache_limpa):
        # Inventar seria pior: quem lê a coluna fica a achar que sabe.
        assert self._backend([self._filme()]).get_watch_history(GUID)["history"][0]["player"] == ""

    def test_a_paginacao_e_calculada_a_partir_do_total_do_servidor(self, cache_limpa):
        backend = self._backend([self._filme()], total=31)

        paginacao = backend.get_watch_history(GUID, page=2, length=15)["pagination"]

        assert paginacao == {"current_page": 2, "total_pages": 3, "total_records": 31}

    def test_a_pagina_pedida_vira_um_deslocamento(self, cache_limpa):
        backend = self._backend([])

        backend.get_watch_history(GUID, page=3, length=15)

        # O primeiro item da terceira página é o de índice 30.
        assert backend.conn.api.ultimos_params['/Items']['StartIndex'] == 30
        assert backend.conn.api.ultimos_params['/Items']['Limit'] == 15

    def test_so_vem_o_que_ja_foi_visto_e_do_mais_recente_primeiro(self, cache_limpa):
        backend = self._backend([])

        backend.get_watch_history(GUID)

        params = backend.conn.api.ultimos_params['/Items']
        assert params['Filters'] == 'IsPlayed'
        assert params['SortBy'] == 'DatePlayed'
        assert params['SortOrder'] == 'Descending'
        assert params['userId'] == GUID

    def test_a_pesquisa_vai_para_o_servidor(self, cache_limpa):
        # Filtrar no painel só apanharia a página atual.
        backend = self._backend([])

        backend.get_watch_history(GUID, search="duna")

        assert backend.conn.api.ultimos_params['/Items']['SearchTerm'] == "duna"

    def test_sem_pesquisa_nao_se_manda_o_campo(self, cache_limpa):
        backend = self._backend([])

        backend.get_watch_history(GUID, search="")

        assert 'SearchTerm' not in backend.conn.api.ultimos_params['/Items']

    def test_uma_falha_do_servidor_nao_rebenta_a_pagina(self, cache_limpa):
        backend = montar({}, erros={'/Items': JellyfinApiError("boom", status_code=500)})

        assert backend.get_watch_history(GUID)["success"] is False
