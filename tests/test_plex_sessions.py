# tests/test_plex_sessions.py
"""Tradução do vocabulário do Plex para `MediaSession`.

Estes testes vieram de `test_stream_manager.py`: seguiram a lógica quando ela
mudou de sítio. É aqui que vive tudo o que sabe como o Plex representa uma
sessão — o estado do leitor, a capa, a plataforma, o formato das notificações
do websocket.
"""

import pytest

from app.services.media_server.base import MediaSession
from app.services.media_server.plex.sessions import PlexSessionsProvider, thumb_source


class PlayerFalso:
    def __init__(self, platform="", product="", title="", state=None):
        self.platform = platform
        self.product = product
        self.title = title
        if state is not None:
            self.state = state


class UtilizadorFalso:
    def __init__(self, id=1, title="ana", email="ana@exemplo.com"):
        self.id = id
        self.title = title
        self.email = email


class SessaoFalsa:
    """Imita o suficiente de uma sessão do plexapi para os testes."""

    def __init__(self, session_key="1", user_id=1, titulo="Matrix", tipo="movie",
                 plataforma="", view_offset=0, estado=None, **extra):
        self.sessionKey = session_key
        self.user = UtilizadorFalso(id=user_id)
        self.users = [self.user]
        self.title = titulo
        self.type = tipo
        self.players = [PlayerFalso(platform=plataforma, product=plataforma, state=estado)]
        self.viewOffset = view_offset
        self.parou_com = None
        for chave, valor in extra.items():
            setattr(self, chave, valor)

    def stop(self, reason=None):
        self.parou_com = reason


@pytest.fixture()
def provider():
    return PlexSessionsProvider(connection=None)


class TestHasStateChanged:
    def test_sessao_nova_conta_como_mudanca(self, provider):
        assert provider.has_state_changed([{"sessionKey": "1", "state": "playing"}]) is True

    def test_ping_de_progresso_e_ignorado(self, provider):
        provider.has_state_changed([{"sessionKey": "1", "state": "playing"}])

        # O Plex reenvia 'playing' de poucos em poucos segundos: nada mudou.
        assert provider.has_state_changed([{"sessionKey": "1", "state": "playing"}]) is False

    def test_transicao_de_estado_conta(self, provider):
        provider.has_state_changed([{"sessionKey": "1", "state": "playing"}])

        assert provider.has_state_changed([{"sessionKey": "1", "state": "paused"}]) is True

    def test_fim_de_sessao_conta_e_esquece_a_sessao(self, provider):
        provider.has_state_changed([{"sessionKey": "1", "state": "playing"}])

        assert provider.has_state_changed([{"sessionKey": "1", "state": "stopped"}]) is True
        assert "1" not in provider._last_session_states
        # A mesma chave a voltar é uma sessão nova.
        assert provider.has_state_changed([{"sessionKey": "1", "state": "playing"}]) is True

    def test_estados_irrelevantes_sao_ignorados(self, provider):
        assert provider.has_state_changed([{"sessionKey": "1", "state": "progress"}]) is False

    def test_evento_sem_identificador_conta_por_precaucao(self, provider):
        # Sem sessionKey não há como comparar: nunca se perde um evento real.
        assert provider.has_state_changed([{"state": "playing"}]) is True

    def test_entradas_invalidas_sao_ignoradas(self, provider):
        assert provider.has_state_changed(["texto solto", None, 42]) is False

    def test_varias_sessoes_sao_seguidas_em_separado(self, provider):
        provider.has_state_changed([
            {"sessionKey": "1", "state": "playing"},
            {"sessionKey": "2", "state": "playing"},
        ])

        assert provider.has_state_changed([{"sessionKey": "1", "state": "playing"}]) is False
        assert provider.has_state_changed([{"sessionKey": "2", "state": "paused"}]) is True

    def test_sessoes_sem_sinal_de_vida_sao_esquecidas(self, provider, monkeypatch):
        import time as _time

        from app.services.media_server.plex import sessions as sessions_module

        provider.has_state_changed([{"sessionKey": "1", "state": "playing"}])

        agora = _time.monotonic()
        monkeypatch.setattr(
            sessions_module.time, "monotonic",
            lambda: agora + provider.SESSION_STATE_TTL_SECONDS + 1,
        )

        # Um cliente que se desliga sem enviar 'stopped' não pode ficar memorizado.
        provider.has_state_changed([])
        assert provider._last_session_states == {}

    def test_lista_vazia(self, provider):
        assert provider.has_state_changed([]) is False

    def test_so_avisa_quem_pediu_quando_algo_muda(self, provider):
        avisos = []
        provider._on_change = lambda: avisos.append(1)

        evento = {"type": "playing", "PlaySessionStateNotification": [{"sessionKey": "1", "state": "playing"}]}
        provider._on_plex_event(evento)
        provider._on_plex_event(evento)  # ping de progresso: não avisa

        assert avisos == [1]

    def test_eventos_de_outro_tipo_sao_ignorados(self, provider):
        avisos = []
        provider._on_change = lambda: avisos.append(1)

        provider._on_plex_event({"type": "timeline"})
        provider._on_plex_event("nem sequer é um dicionário")

        assert avisos == []


class TestPlataforma:
    @pytest.mark.parametrize("texto,esperado", [
        ("Chrome", "chrome"),
        ("Safari", "safari"),
        ("Firefox", "firefox"),
        ("Microsoft Edge", "msedge"),
        ("Brave", "chrome"),
        ("Android", "android"),
        ("Roku", "roku"),
        ("Apple TV", "atv"),
        ("iOS", "ios"),
        ("PlayStation 5", "playstation"),
        ("Xbox One", "xbox"),
        ("Samsung Tizen", "samsung"),
        ("webOS", "lg"),
        ("Kodi", "kodi"),
        ("Chromecast", "chromecast"),
        ("Plexamp", "plexamp"),
        ("Windows", "windows"),
        ("Linux", "linux"),
        ("Plex Media Player", "plex"),
    ])
    def test_reconhece_as_plataformas(self, provider, texto, esperado):
        assert provider._plataforma(SessaoFalsa(plataforma=texto)) == esperado

    @pytest.mark.parametrize("texto", [
        "Chromecast",
        "Chromecast Ultra",
        "Android Chromecast built-in",  # Chromecast com Google TV
    ])
    def test_um_chromecast_nao_e_confundido_com_o_browser_chrome(self, provider, texto):
        # 'chrome' está contido em 'chromecast': a verificação do Chromecast tem
        # de vir primeiro, senão o filtro de sessões duplicadas de Cast nunca atua.
        assert provider._plataforma(SessaoFalsa(plataforma=texto)) == "chromecast"

    def test_o_browser_chrome_continua_a_ser_reconhecido(self, provider):
        assert provider._plataforma(SessaoFalsa(plataforma="Chrome")) == "chrome"

    def test_plataforma_desconhecida(self, provider):
        assert provider._plataforma(SessaoFalsa(plataforma="AparelhoEstranho")) == "default"

    def test_sessao_sem_leitor(self, provider):
        sessao = SessaoFalsa()
        sessao.players = []

        assert provider._plataforma(sessao) == "default"


class TestTituloParaRegisto:
    def test_filme(self, provider):
        assert provider._titulo_para_registo(SessaoFalsa(titulo="Duna")) == "Duna"

    def test_episodio_com_temporada_e_numero(self, provider):
        sessao = SessaoFalsa(
            titulo="Segredos", tipo="episode",
            grandparentTitle="Dark", parentIndex=2, index=5,
        )

        assert provider._titulo_para_registo(sessao) == "Dark S02E05 - Segredos"

    def test_episodio_sem_numeracao(self, provider):
        sessao = SessaoFalsa(titulo="Piloto", tipo="episode", grandparentTitle="Dark")

        assert provider._titulo_para_registo(sessao) == "Dark - Piloto"

    def test_episodio_com_numeracao_invalida(self, provider):
        sessao = SessaoFalsa(
            titulo="Piloto", tipo="episode",
            grandparentTitle="Dark", parentIndex="abc", index="x",
        )

        assert provider._titulo_para_registo(sessao) == "Dark - Piloto"


class TestIdDoUtilizador:
    def test_vem_normalizado(self, provider):
        # O Plex devolve um inteiro, a base de dados guarda texto. O motor de
        # streams cruza os dois (perfis, bloqueios), por isso normaliza à
        # entrada — ver o comentário 🐛 em _id_do_utilizador.
        assert provider._id_do_utilizador(SessaoFalsa(user_id=42)) == "42"

    def test_aceita_um_guid(self, provider):
        assert provider._id_do_utilizador(SessaoFalsa(user_id="38c3a1f0")) == "38c3a1f0"

    def test_sessao_sem_utilizador(self, provider):
        sessao = SessaoFalsa()
        sessao.user = None
        sessao.users = []

        assert provider._id_do_utilizador(sessao) is None


class TestTraducao:
    def test_uma_sessao_do_plex_vira_uma_media_session(self, provider):
        sessao = SessaoFalsa(
            session_key="7", user_id=42, titulo="Duna", plataforma="Chrome",
            view_offset=30000, duration=120000, estado="paused",
            thumb="/library/metadata/1/thumb", year=2021,
        )

        traduzida = provider._traduzir(sessao)

        assert isinstance(traduzida, MediaSession)
        assert traduzida.user_id == "42"
        assert traduzida.session_key == "7"
        assert traduzida.title == "Duna"
        assert traduzida.platform == "chrome"
        assert traduzida.state == "paused"
        assert traduzida.progress == 25.0
        assert traduzida.artwork_source == "plex:/library/metadata/1/thumb"
        assert traduzida.user_email == "ana@exemplo.com"
        # O objeto original segue junto, para o provider o poder encerrar depois.
        assert traduzida.raw is sessao

    def test_um_episodio_traz_serie_e_numeracao(self, provider):
        sessao = SessaoFalsa(
            titulo="Segredos", tipo="episode",
            grandparentTitle="Dark", parentIndex=2, index=5,
        )

        traduzida = provider._traduzir(sessao)

        assert traduzida.title == "Dark"
        assert traduzida.subtitle == "S02 · E05 - Segredos"
        # O título de registo é o composto, que vai para a auditoria.
        assert traduzida.media_title == "Dark S02E05 - Segredos"

    @pytest.mark.parametrize("bruto,esperado", [
        ("playing", "playing"),
        ("paused", "paused"),
        ("buffering", "buffering"),
        ("qualquer-outra-coisa", "stopped"),
    ])
    def test_estados_do_leitor(self, provider, bruto, esperado):
        assert provider._traduzir(SessaoFalsa(estado=bruto)).state == esperado

    def test_capa_absoluta_perde_o_token(self, provider):
        sessao = SessaoFalsa(thumb="https://servidor.local/foto.png?X-Plex-Token=segredo&w=100")

        fonte = provider._traduzir(sessao).artwork_source

        assert fonte.startswith("url:")
        assert "segredo" not in fonte
        assert "w=100" in fonte

    def test_sem_capa_nao_inventa_uma(self, provider):
        assert provider._traduzir(SessaoFalsa()).artwork_source is None


class TestTerminar:
    def test_encerra_a_sessao_com_o_motivo(self, provider):
        sessao = SessaoFalsa(session_key="1", session=type("S", (), {"id": "interno"})())

        assert provider.terminate(provider._traduzir(sessao), "excedeu o limite") is True
        assert sessao.parou_com == "excedeu o limite"

    def test_sessao_sem_identificador_interno_ainda_nao_pode_ser_encerrada(self, provider):
        # Uma reprodução a carregar: quem chama tem de voltar a tentar, em vez
        # de dar o corte por feito.
        sessao = SessaoFalsa(session_key="1")

        assert provider.terminate(provider._traduzir(sessao), "motivo") is False
        assert sessao.parou_com is None


class TestAvatares:
    def test_caminho_relativo_vai_pela_conta_plex(self):
        assert thumb_source("/users/avatar.png") == "plex_account:/users/avatar.png"

    def test_url_do_plex_tv_vai_pela_conta_plex(self):
        assert thumb_source("https://plex.tv/users/a.png") == "plex_account:https://plex.tv/users/a.png"

    def test_url_de_terceiros_vai_como_url_externo(self):
        assert thumb_source("https://gravatar.com/a.png") == "url:https://gravatar.com/a.png"

    def test_o_token_e_removido(self):
        fonte = thumb_source("https://plex.tv/a.png?X-Plex-Token=segredo")

        assert "segredo" not in fonte

    def test_avatar_ja_encaminhado_nao_e_reencaminhado(self):
        # Sem isto, um avatar já passado pelo proxy era embrulhado outra vez.
        assert thumb_source("/image/?source=abc") is None

    def test_sem_avatar(self):
        assert thumb_source(None) is None
