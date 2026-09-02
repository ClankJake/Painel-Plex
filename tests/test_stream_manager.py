# tests/test_stream_manager.py
"""Controlo de streams: deteção de mudanças, sessões duplicadas e limite de telas."""

import pytest

from app.services.stream_manager import StreamManager
from tests.conftest import FakeDataManager


class PlayerFalso:
    def __init__(self, platform="", product="", title=""):
        self.platform = platform
        self.product = product
        self.title = title


class UtilizadorFalso:
    def __init__(self, id=1, title="ana", email="ana@exemplo.com"):
        self.id = id
        self.title = title
        self.email = email


class SessaoFalsa:
    """Imita o suficiente de uma sessão do plexapi para os testes."""

    def __init__(self, session_key="1", user_id=1, titulo="Matrix", tipo="movie",
                 plataforma="", view_offset=0, **extra):
        self.sessionKey = session_key
        self.user = UtilizadorFalso(id=user_id)
        self.users = [self.user]
        self.title = titulo
        self.type = tipo
        self.players = [PlayerFalso(platform=plataforma, product=plataforma)]
        self.viewOffset = view_offset
        self.parou_com = None
        for chave, valor in extra.items():
            setattr(self, chave, valor)

    def stop(self, reason=None):
        self.parou_com = reason


@pytest.fixture()
def manager(app_context):
    return StreamManager(plex_connection=None, data_manager=FakeDataManager(), user_manager=None)


@pytest.fixture()
def cache_limpa(app_context):
    """A cache é partilhada entre testes (fica em disco): limpa antes e depois."""
    from app.extensions import cache

    cache.clear()
    yield cache
    cache.clear()


class TestHasStateChanged:
    def test_sessao_nova_conta_como_mudanca(self, manager):
        assert manager._has_state_changed([{"sessionKey": "1", "state": "playing"}]) is True

    def test_ping_de_progresso_e_ignorado(self, manager):
        manager._has_state_changed([{"sessionKey": "1", "state": "playing"}])

        # O Plex reenvia 'playing' de poucos em poucos segundos: nada mudou.
        assert manager._has_state_changed([{"sessionKey": "1", "state": "playing"}]) is False

    def test_transicao_de_estado_conta(self, manager):
        manager._has_state_changed([{"sessionKey": "1", "state": "playing"}])

        assert manager._has_state_changed([{"sessionKey": "1", "state": "paused"}]) is True

    def test_fim_de_sessao_conta_e_esquece_a_sessao(self, manager):
        manager._has_state_changed([{"sessionKey": "1", "state": "playing"}])

        assert manager._has_state_changed([{"sessionKey": "1", "state": "stopped"}]) is True
        assert "1" not in manager._last_session_states
        # A mesma chave a voltar é uma sessão nova.
        assert manager._has_state_changed([{"sessionKey": "1", "state": "playing"}]) is True

    def test_estados_irrelevantes_sao_ignorados(self, manager):
        assert manager._has_state_changed([{"sessionKey": "1", "state": "progress"}]) is False

    def test_evento_sem_identificador_conta_por_precaucao(self, manager):
        # Sem sessionKey não há como comparar: nunca se perde um evento real.
        assert manager._has_state_changed([{"state": "playing"}]) is True

    def test_entradas_invalidas_sao_ignoradas(self, manager):
        assert manager._has_state_changed(["texto solto", None, 42]) is False

    def test_varias_sessoes_sao_seguidas_em_separado(self, manager):
        manager._has_state_changed([
            {"sessionKey": "1", "state": "playing"},
            {"sessionKey": "2", "state": "playing"},
        ])

        assert manager._has_state_changed([{"sessionKey": "1", "state": "playing"}]) is False
        assert manager._has_state_changed([{"sessionKey": "2", "state": "paused"}]) is True

    def test_sessoes_sem_sinal_de_vida_sao_esquecidas(self, manager, monkeypatch):
        import time as _time

        from app.services import stream_manager as stream_module

        manager._has_state_changed([{"sessionKey": "1", "state": "playing"}])

        agora = _time.monotonic()
        monkeypatch.setattr(
            stream_module.time, "monotonic",
            lambda: agora + manager.SESSION_STATE_TTL_SECONDS + 1,
        )

        # Um cliente que se desliga sem enviar 'stopped' não pode ficar memorizado.
        manager._has_state_changed([])
        assert manager._last_session_states == {}

    def test_lista_vazia(self, manager):
        assert manager._has_state_changed([]) is False


class TestGetPlatformInfo:
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
    def test_reconhece_as_plataformas(self, manager, texto, esperado):
        assert manager._get_platform_info(SessaoFalsa(plataforma=texto)) == esperado

    @pytest.mark.parametrize("texto", [
        "Chromecast",
        "Chromecast Ultra",
        "Android Chromecast built-in",  # Chromecast com Google TV
    ])
    def test_um_chromecast_nao_e_confundido_com_o_browser_chrome(self, manager, texto):
        # 'chrome' está contido em 'chromecast': a verificação do Chromecast tem
        # de vir primeiro, senão o filtro de sessões duplicadas de Cast nunca atua.
        assert manager._get_platform_info(SessaoFalsa(plataforma=texto)) == "chromecast"

    def test_o_browser_chrome_continua_a_ser_reconhecido(self, manager):
        assert manager._get_platform_info(SessaoFalsa(plataforma="Chrome")) == "chrome"

    def test_plataforma_desconhecida(self, manager):
        assert manager._get_platform_info(SessaoFalsa(plataforma="AparelhoEstranho")) == "default"

    def test_sessao_sem_leitor(self, manager):
        sessao = SessaoFalsa()
        sessao.players = []

        assert manager._get_platform_info(sessao) == "default"


class TestGetMediaTitle:
    def test_filme(self, manager):
        assert manager._get_media_title(SessaoFalsa(titulo="Duna")) == "Duna"

    def test_episodio_com_temporada_e_numero(self, manager):
        sessao = SessaoFalsa(
            titulo="Segredos", tipo="episode",
            grandparentTitle="Dark", parentIndex=2, index=5,
        )

        assert manager._get_media_title(sessao) == "Dark S02E05 - Segredos"

    def test_episodio_sem_numeracao(self, manager):
        sessao = SessaoFalsa(titulo="Piloto", tipo="episode", grandparentTitle="Dark")

        assert manager._get_media_title(sessao) == "Dark - Piloto"

    def test_episodio_com_numeracao_invalida(self, manager):
        sessao = SessaoFalsa(
            titulo="Piloto", tipo="episode",
            grandparentTitle="Dark", parentIndex="abc", index="x",
        )

        assert manager._get_media_title(sessao) == "Dark - Piloto"


class TestSessionHelpers:
    def test_id_do_utilizador(self, manager):
        assert manager._get_session_user_id(SessaoFalsa(user_id=42)) == 42

    def test_sessao_sem_utilizador(self, manager):
        sessao = SessaoFalsa()
        sessao.user = None
        sessao.users = []

        assert manager._get_session_user_id(sessao) is None

    def test_agrupa_por_utilizador(self, manager):
        sessoes = [
            SessaoFalsa(session_key="1", user_id=1),
            SessaoFalsa(session_key="2", user_id=1),
            SessaoFalsa(session_key="3", user_id=2),
        ]

        grupos = manager._group_sessions_by_user(sessoes)

        assert len(grupos[1]) == 2
        assert len(grupos[2]) == 1

    def test_sessoes_sem_utilizador_sao_descartadas(self, manager):
        sem_utilizador = SessaoFalsa()
        sem_utilizador.user = None
        sem_utilizador.users = []

        assert manager._group_sessions_by_user([sem_utilizador]) == {}


class TestFilterDuplicateCastSessions:
    def test_remove_o_telemovel_que_comanda_o_chromecast(self, manager):
        chromecast = SessaoFalsa(session_key="1", titulo="Duna", plataforma="Chromecast")
        telemovel = SessaoFalsa(session_key="2", titulo="Duna", plataforma="Android")

        restantes = manager._filter_duplicate_cast_sessions([chromecast, telemovel])

        assert restantes == [chromecast]

    def test_conteudos_diferentes_contam_as_duas(self, manager):
        chromecast = SessaoFalsa(session_key="1", titulo="Duna", plataforma="Chromecast")
        telemovel = SessaoFalsa(session_key="2", titulo="Matrix", plataforma="Android")

        assert len(manager._filter_duplicate_cast_sessions([chromecast, telemovel])) == 2

    def test_sem_chromecast_nada_e_removido(self, manager):
        sessoes = [
            SessaoFalsa(session_key="1", titulo="Duna", plataforma="Android"),
            SessaoFalsa(session_key="2", titulo="Duna", plataforma="Chrome"),
        ]

        assert len(manager._filter_duplicate_cast_sessions(sessoes)) == 2

    def test_lista_vazia(self, manager):
        assert manager._filter_duplicate_cast_sessions([]) == []


class TestBuildPlaceholders:
    def test_marcadores_da_mensagem_de_corte(self, manager):
        perfil = {"name": "Ana Silva", "telegram_user": "@ana", "phone_number": "5511988887777"}

        marcadores = manager._build_placeholders(1, "ana", perfil, SessaoFalsa(), context={"limit": 2})

        assert marcadores["username"] == "ana"
        assert marcadores["name"] == "Ana Silva"
        assert marcadores["email"] == "ana@exemplo.com"
        assert marcadores["limit"] == 2
        assert marcadores["greeting"]

    def test_sem_nome_usa_o_username(self, manager):
        assert manager._build_placeholders(1, "ana", {}, SessaoFalsa())["name"] == "ana"


class TestEnforceScreenLimits:
    def _config(self, **extra):
        config = {
            "STREAM_CHECK_INTERVAL_SECONDS": 15,
            "SCREEN_LIMIT_TERMINATION_STRATEGY": "oldest",
            "TERMINATION_MSG_SCREEN_LIMIT": "{username}, excedeu o limite de {limit} tela(s).",
        }
        config.update(extra)
        return config

    def test_dentro_do_limite_nao_corta_nada(self, manager, cache_limpa):
        sessoes = [SessaoFalsa(session_key="1"), SessaoFalsa(session_key="2")]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 2}, self._config())

        assert all(s.parou_com is None for s in sessoes)

    def test_limite_zero_significa_sem_limite(self, manager, cache_limpa):
        sessoes = [SessaoFalsa(session_key=str(i)) for i in range(5)]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 0}, self._config())

        assert all(s.parou_com is None for s in sessoes)

    def test_corta_o_excesso_comecando_pela_sessao_mais_antiga(self, manager, cache_limpa):
        # Por omissão ("oldest"), o maior viewOffset é o que está a correr há mais tempo.
        antiga = SessaoFalsa(session_key="1", view_offset=9000, session=type("S", (), {"id": "a"})())
        recente = SessaoFalsa(session_key="2", view_offset=10, session=type("S", (), {"id": "b"})())

        manager._enforce_screen_limits(1, "ana", [recente, antiga], {"screen_limit": 1}, self._config())

        assert antiga.parou_com is not None
        assert recente.parou_com is None

    def test_estrategia_newest_preserva_quem_ja_estava_a_ver(self, manager, cache_limpa):
        antiga = SessaoFalsa(session_key="1", view_offset=9000, session=type("S", (), {"id": "a"})())
        recente = SessaoFalsa(session_key="2", view_offset=10, session=type("S", (), {"id": "b"})())

        manager._enforce_screen_limits(
            1, "ana", [antiga, recente], {"screen_limit": 1},
            self._config(SCREEN_LIMIT_TERMINATION_STRATEGY="newest"),
        )

        assert recente.parou_com is not None
        assert antiga.parou_com is None

    def test_a_mensagem_de_corte_e_personalizada(self, manager, cache_limpa):
        cortada = SessaoFalsa(session_key="1", view_offset=999, session=type("S", (), {"id": "a"})())
        mantida = SessaoFalsa(session_key="2", view_offset=1, session=type("S", (), {"id": "b"})())

        manager._enforce_screen_limits(1, "ana", [cortada, mantida], {"screen_limit": 1}, self._config())

        assert cortada.parou_com == "ana, excedeu o limite de 1 tela(s)."

    def test_o_corte_fica_registado_na_auditoria(self, manager, cache_limpa):
        registos = []
        manager.data_manager.log_stream_termination = lambda **kwargs: registos.append(kwargs)
        sessoes = [
            SessaoFalsa(session_key="1", titulo="Duna", view_offset=100, session=type("S", (), {"id": "a"})()),
            SessaoFalsa(session_key="2", titulo="Matrix", view_offset=10, session=type("S", (), {"id": "b"})()),
        ]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 1}, self._config())

        assert len(registos) == 1
        assert registos[0]["reason"] == "limit_exceeded"
        assert registos[0]["media_title"] == "Duna"

    def test_sessoes_ja_cortadas_nao_contam_de_novo(self, manager, cache_limpa):
        # Sem o anti-spam, a mesma sessão seria cortada em cada verificação.
        cache_limpa.set("kill_spam_1", True, timeout=60)
        sessoes = [
            SessaoFalsa(session_key="1", view_offset=100, session=type("S", (), {"id": "a"})()),
            SessaoFalsa(session_key="2", view_offset=10, session=type("S", (), {"id": "b"})()),
        ]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 1}, self._config())

        assert all(s.parou_com is None for s in sessoes)

    def test_cast_a_partir_do_telemovel_conta_como_uma_tela(self, manager, cache_limpa):
        """
        Regressão do bug do Chromecast: o telemóvel que apenas comanda o Cast
        aparece como uma segunda sessão no Plex. Se não for filtrado, um
        utilizador com limite de 1 tela era cortado ao usar o Chromecast.
        """
        chromecast = SessaoFalsa(
            session_key="1", titulo="Duna", plataforma="Chromecast",
            view_offset=100, session=type("S", (), {"id": "a"})(),
        )
        telemovel = SessaoFalsa(
            session_key="2", titulo="Duna", plataforma="Android",
            view_offset=100, session=type("S", (), {"id": "b"})(),
        )

        # Mesma sequência usada em 'check_and_enforce_streams'.
        unicas = manager._filter_duplicate_cast_sessions([chromecast, telemovel])
        manager._enforce_screen_limits(1, "ana", unicas, {"screen_limit": 1}, self._config())

        assert chromecast.parou_com is None
        assert telemovel.parou_com is None

    def test_corta_varias_sessoes_de_uma_vez(self, manager, cache_limpa):
        sessoes = [
            SessaoFalsa(session_key=str(i), view_offset=i * 100, session=type("S", (), {"id": str(i)})())
            for i in range(4)
        ]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 1}, self._config())

        assert sum(1 for s in sessoes if s.parou_com) == 3
