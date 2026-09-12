# tests/test_stream_manager.py
"""Política de streams: sessões duplicadas, limite de telas e cache do painel.

O motor de streams deixou de falar com o Plex: recebe `MediaSession` já
traduzidas de um `SessionsProvider`. Estes testes exercitam só a política — o
que conta como uma tela, quem é cortado primeiro, quando se reaproveita uma
leitura. A tradução do vocabulário do Plex é testada em
`tests/test_plex_sessions.py`.
"""

import time

import pytest

from app.services.media_server.base import MediaSession
from app.services.stream_manager import StreamManager
from app.utils.identity import normalize_user_id
from tests.conftest import FakeDataManager


def sessao(session_key="1", user_id=1, titulo="Matrix", plataforma="default",
           view_offset=0, terminavel=True, playback_key=None, **extra):
    """Uma sessão já traduzida, como o provider a entrega."""
    campos = dict(
        user_id=normalize_user_id(user_id),
        username_fallback="ana",
        user_email="ana@exemplo.com",
        session_key=session_key,
        playback_key=playback_key,
        media_title=titulo,
        title=titulo,
        subtitle="",
        media_type="movie",
        state="playing",
        platform=plataforma,
        player="Plex Web - Portátil",
        progress=0.0,
        view_offset=view_offset,
        duration=0,
        raw={"terminavel": terminavel},
    )
    campos.update(extra)
    return MediaSession(**campos)


class ProviderFalso:
    """Um servidor de média qualquer: entrega sessões e regista os cortes."""

    def __init__(self, sessoes=None, owner_id=None):
        self._sessoes = sessoes or []
        self.chamadas = 0
        self.terminadas = {}
        # O último recurso, para clientes que ignoram a ordem de parar.
        self.forcadas = {}
        self.forca_funciona = True
        self.ligado = True
        self.owner_id = owner_id
        self.avatares_pedidos = []

    # --- ligação ---
    def is_connected(self):
        return self.ligado

    def reconnect(self):
        return self.ligado, ""

    def get_owner_id(self):
        return self.owner_id

    # --- sessões ---
    def list_sessions(self):
        self.chamadas += 1
        return list(self._sessoes)

    def terminate(self, session, reason):
        # Uma sessão marcada como não terminável imita a do servidor que ainda
        # não tem identificador interno (a carregar).
        if isinstance(session.raw, dict) and not session.raw.get("terminavel", True):
            return False
        self.terminadas[session.session_key] = reason
        return True

    def force_terminate(self, session, reason):
        self.forcadas[session.session_key] = reason
        return self.forca_funciona

    def user_thumb_source(self, raw_thumb):
        self.avatares_pedidos.append(raw_thumb)
        return f"url:{raw_thumb}" if raw_thumb else None

    def deduplicate_sessions(self, sessions):
        # Um servidor que não duplica reproduções — como o Jellyfin. Fundir
        # sessões é conhecimento do servidor, testado no provider de cada um.
        return list(sessions)

    # --- tempo real ---
    def supports_realtime(self):
        return False

    def is_listener_healthy(self):
        return True

    def start_listener(self, on_change):
        self.on_change = on_change

    def stop_listener(self):
        pass


@pytest.fixture()
def manager(app_context):
    return StreamManager(sessions_provider=ProviderFalso(), data_manager=FakeDataManager(), user_manager=None)


@pytest.fixture()
def cache_limpa(app_context):
    """A cache é partilhada entre testes (fica em disco): limpa antes e depois."""
    from app.extensions import cache

    cache.clear()
    yield cache
    cache.clear()


class TestAgrupamento:
    def test_agrupa_por_utilizador(self, manager):
        grupos = manager._group_sessions_by_user([
            sessao(session_key="1", user_id=1),
            sessao(session_key="2", user_id=1),
            sessao(session_key="3", user_id=2),
        ])

        assert len(grupos["1"]) == 2
        assert len(grupos["2"]) == 1

    def test_sessoes_sem_utilizador_sao_descartadas(self, manager):
        assert manager._group_sessions_by_user([sessao(user_id=None)]) == {}


class TestFusaoDeSessoes:
    """O motor não decide o que é a mesma reprodução — pergunta ao servidor.

    Reconhecer o par (o telemóvel que comanda um Chromecast) é conhecimento do
    servidor e vive no provider; ver `tests/test_plex_sessions.py`.
    """

    def test_respeita_o_que_o_provider_devolve(self, manager):
        a = sessao(session_key="1", titulo="Duna")
        b = sessao(session_key="2", titulo="Duna")
        manager.sessions.deduplicate_sessions = lambda sessoes: [a]

        assert manager._filter_duplicate_cast_sessions([a, b]) == [a]

    def test_um_servidor_que_nao_duplica_mantem_tudo(self, manager):
        # 🐛 É a regressão reportada: com o Jellyfin, duas reproduções da MESMA
        # mídia em aparelhos diferentes contavam como uma tela só, porque o
        # motor aplicava a todos o filtro de Cast do Plex.
        sessoes = [
            sessao(session_key="1", titulo="Duna", plataforma="android"),
            sessao(session_key="2", titulo="Duna", plataforma="chrome"),
        ]

        assert len(manager._filter_duplicate_cast_sessions(sessoes)) == 2

    def test_lista_vazia(self, manager):
        assert manager._filter_duplicate_cast_sessions([]) == []


class TestBuildPlaceholders:
    def test_marcadores_da_mensagem_de_corte(self, manager):
        perfil = {"name": "Ana Silva", "telegram_user": "@ana", "phone_number": "5511988887777"}

        marcadores = manager._build_placeholders(1, "ana", perfil, sessao(), context={"limit": 2})

        assert marcadores["username"] == "ana"
        assert marcadores["name"] == "Ana Silva"
        assert marcadores["email"] == "ana@exemplo.com"
        assert marcadores["limit"] == 2
        assert marcadores["greeting"]

    def test_sem_nome_usa_o_username(self, manager):
        assert manager._build_placeholders(1, "ana", {}, sessao())["name"] == "ana"


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
        sessoes = [sessao(session_key="1"), sessao(session_key="2")]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 2}, self._config())

        assert manager.sessions.terminadas == {}

    def test_limite_zero_significa_sem_limite(self, manager, cache_limpa):
        sessoes = [sessao(session_key=str(i)) for i in range(5)]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 0}, self._config())

        assert manager.sessions.terminadas == {}

    def test_corta_o_excesso_comecando_pela_sessao_mais_antiga(self, manager, cache_limpa):
        # Por omissão ("oldest"), o maior view_offset é o que está a correr há mais tempo.
        antiga = sessao(session_key="1", view_offset=9000)
        recente = sessao(session_key="2", view_offset=10)

        manager._enforce_screen_limits(1, "ana", [recente, antiga], {"screen_limit": 1}, self._config())

        assert set(manager.sessions.terminadas) == {"1"}

    def test_estrategia_newest_preserva_quem_ja_estava_a_ver(self, manager, cache_limpa):
        antiga = sessao(session_key="1", view_offset=9000)
        recente = sessao(session_key="2", view_offset=10)

        manager._enforce_screen_limits(
            1, "ana", [antiga, recente], {"screen_limit": 1},
            self._config(SCREEN_LIMIT_TERMINATION_STRATEGY="newest"),
        )

        assert set(manager.sessions.terminadas) == {"2"}

    def test_a_mensagem_de_corte_e_personalizada(self, manager, cache_limpa):
        cortada = sessao(session_key="1", view_offset=999)
        mantida = sessao(session_key="2", view_offset=1)

        manager._enforce_screen_limits(1, "ana", [cortada, mantida], {"screen_limit": 1}, self._config())

        assert manager.sessions.terminadas["1"] == "ana, excedeu o limite de 1 tela(s)."

    def test_o_corte_fica_registado_na_auditoria(self, manager, cache_limpa):
        registos = []
        manager.data_manager.log_stream_termination = lambda **kwargs: registos.append(kwargs)
        sessoes = [
            sessao(session_key="1", titulo="Duna", view_offset=100),
            sessao(session_key="2", titulo="Matrix", view_offset=10),
        ]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 1}, self._config())

        assert len(registos) == 1
        assert registos[0]["reason"] == "limit_exceeded"
        assert registos[0]["media_title"] == "Duna"

    def test_sessoes_ja_cortadas_nao_contam_de_novo(self, manager, cache_limpa):
        # Sem o anti-spam, a mesma sessão seria cortada em cada verificação.
        cache_limpa.set("kill_spam_1", True, timeout=60)
        sessoes = [
            sessao(session_key="1", view_offset=100),
            sessao(session_key="2", view_offset=10),
        ]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 1}, self._config())

        assert manager.sessions.terminadas == {}

    def test_a_mesma_midia_em_dois_aparelhos_conta_duas_telas(self, manager, cache_limpa):
        """
        🐛 REGRESSÃO REPORTADA: um utilizador com limite de telas a reproduzir a
        MESMA mídia em dois aparelhos não era cortado. O motor aplicava a todos
        os servidores o filtro de Cast do Plex, que funde duas sessões do mesmo
        utilizador com o mesmo título — e assim as duas contavam como uma.
        """
        primeiro = sessao(session_key="1", titulo="Duna", plataforma="android", view_offset=100)
        segundo = sessao(session_key="2", titulo="Duna", plataforma="chrome", view_offset=5000)

        # Mesma sequência usada em 'check_and_enforce_streams'.
        unicas = manager._filter_duplicate_cast_sessions([primeiro, segundo])
        manager._enforce_screen_limits(1, "ana", unicas, {"screen_limit": 1}, self._config())

        assert len(manager.sessions.terminadas) == 1

    def test_corta_varias_sessoes_de_uma_vez(self, manager, cache_limpa):
        sessoes = [sessao(session_key=str(i), view_offset=i * 100) for i in range(4)]

        manager._enforce_screen_limits(1, "ana", sessoes, {"screen_limit": 1}, self._config())

        assert len(manager.sessions.terminadas) == 3


class TestSessaoQueAindaNaoPodeSerCortada:
    """Uma reprodução a carregar ainda não tem como ser encerrada."""

    def test_o_corte_e_reagendado_em_vez_de_dado_por_feito(self, manager, cache_limpa, monkeypatch):
        reagendamentos = []
        monkeypatch.setattr(manager, '_schedule_delayed_check', lambda: reagendamentos.append(1))

        manager._terminate_session(sessao(session_key="1", terminavel=False), "motivo")

        assert manager.sessions.terminadas == {}
        assert reagendamentos == [1]

    def test_nao_se_reagenda_a_mesma_sessao_sem_parar(self, manager, cache_limpa, monkeypatch):
        # Sem a trava, cada ciclo agendava uma nova verificação para a mesma sessão.
        reagendamentos = []
        monkeypatch.setattr(manager, '_schedule_delayed_check', lambda: reagendamentos.append(1))

        manager._terminate_session(sessao(session_key="1", terminavel=False), "motivo")
        manager._terminate_session(sessao(session_key="1", terminavel=False), "motivo")

        assert reagendamentos == [1]


class TestGetActiveStreamCount:
    """
    O resumo do dashboard só precisa do número de streams. Contar aqui evita
    montar o payload visual inteiro (avatares, capas, detalhes de transcode).
    """

    def _manager(self, sessoes):
        provider = ProviderFalso(sessoes)
        gestor = StreamManager(sessions_provider=provider, data_manager=FakeDataManager(), user_manager=None)
        return gestor, provider

    def test_sem_ligacao_devolve_zero(self, app_context):
        gestor, provider = self._manager([])
        provider.ligado = False

        assert gestor.get_active_stream_count() == 0
        assert provider.chamadas == 0

    def test_conta_as_sessoes_ativas(self, app_context):
        gestor, provider = self._manager([
            sessao(session_key="1", user_id=1, titulo="Duna"),
            sessao(session_key="2", user_id=2, titulo="Matrix"),
        ])

        assert gestor.get_active_stream_count(use_cache=False) == 2
        assert provider.chamadas == 1

    def test_a_contagem_usa_a_fusao_do_servidor(self, app_context):
        """A contagem e a lista têm de usar o mesmo critério, senão discordam."""
        gestor, provider = self._manager([
            sessao(session_key="1", user_id=1, titulo="Duna"),
            sessao(session_key="2", user_id=1, titulo="Duna"),
        ])
        provider.deduplicate_sessions = lambda sessoes: sessoes[:1]

        assert gestor.get_active_stream_count(use_cache=False) == 1

    def test_a_mesma_midia_em_dois_aparelhos_conta_duas(self, app_context):
        # 🐛 Num servidor que não duplica reproduções, as duas contam.
        gestor, _ = self._manager([
            sessao(session_key="1", user_id=1, titulo="Duna", plataforma="android"),
            sessao(session_key="2", user_id=1, titulo="Duna", plataforma="chrome"),
        ])

        assert gestor.get_active_stream_count(use_cache=False) == 2

    def test_falha_de_rede_devolve_zero(self, app_context):
        gestor, provider = self._manager([])

        def rebenta():
            raise ConnectionError("servidor inacessível")

        provider.list_sessions = rebenta
        assert gestor.get_active_stream_count(use_cache=False) == 0

    def test_reaproveita_a_contagem_ja_guardada(self, app_context):
        """Com uma leitura recente em cache, não se volta a falar com o servidor."""
        gestor, provider = self._manager([])
        gestor._now_playing_cache = {"success": True, "stream_count": 3, "sessions": []}
        gestor._now_playing_cached_at = time.monotonic()

        assert gestor.get_active_stream_count() == 3
        assert provider.chamadas == 0


class TestNowPlayingPayload:
    """O payload que o frontend consome, montado a partir das sessões traduzidas."""

    def test_monta_a_sessao_para_o_painel(self, app_context):
        class DiretorioFalso:
            def list_users(self):
                return [{"id": 1, "username": "ana", "thumb": "https://plex.tv/avatar.png"}]

        provider = ProviderFalso([
            sessao(session_key="7", user_id=1, titulo="Duna", plataforma="chrome",
                   artwork_source="plex:/library/metadata/1/thumb"),
        ])
        gestor = StreamManager(sessions_provider=provider, data_manager=FakeDataManager(),
                               user_manager=DiretorioFalso())

        payload = gestor.get_now_playing(use_cache=False)

        assert payload["success"] is True
        assert payload["stream_count"] == 1
        item = payload["sessions"][0]
        assert item["session_key"] == "7"
        assert item["user"] == "ana"
        assert item["title"] == "Duna"
        assert item["platform"] == "chrome"
        # As imagens passam sempre pelo proxy do painel (o `url_for` prefixa o
        # host quando há contexto de pedido, daí o "in" em vez do "startswith").
        assert "/image/?source=" in item["thumb_url"]
        assert "/image/?source=" in item["user_thumb"]

    def test_utilizador_fora_do_diretorio_usa_o_nome_da_sessao(self, app_context):
        class DiretorioVazio:
            def list_users(self):
                return []

        provider = ProviderFalso([sessao(session_key="1", user_id=99)])
        gestor = StreamManager(sessions_provider=provider, data_manager=FakeDataManager(),
                               user_manager=DiretorioVazio())

        assert gestor.get_now_playing(use_cache=False)["sessions"][0]["user"] == "ana"


class TestNowPlayingCache:
    """Janela curta de reaproveitamento (ver NOW_PLAYING_CACHE_SECONDS)."""

    def _manager(self):
        return StreamManager(sessions_provider=ProviderFalso(), data_manager=FakeDataManager(), user_manager=None)

    def test_pedidos_seguidos_partilham_uma_leitura(self, app_context, monkeypatch):
        gestor = self._manager()
        chamadas = []

        def leitura():
            chamadas.append(1)
            return {"success": True, "stream_count": 1, "sessions": [{"session_key": "1"}]}

        monkeypatch.setattr(gestor, "_build_now_playing", leitura)

        primeiro = gestor.get_now_playing()
        segundo = gestor.get_now_playing()

        assert len(chamadas) == 1
        assert primeiro == segundo

    def test_quem_recebe_o_resultado_nao_estraga_a_cache(self, app_context, monkeypatch):
        gestor = self._manager()
        monkeypatch.setattr(
            gestor, "_build_now_playing",
            lambda: {"success": True, "stream_count": 1, "sessions": [{"session_key": "1"}]}
        )

        primeiro = gestor.get_now_playing()
        primeiro["sessions"].clear()

        assert gestor.get_now_playing()["sessions"] == [{"session_key": "1"}]

    def test_use_cache_false_forca_leitura_fresca(self, app_context, monkeypatch):
        gestor = self._manager()
        chamadas = []
        monkeypatch.setattr(
            gestor, "_build_now_playing",
            lambda: (chamadas.append(1), {"success": True, "stream_count": 0, "sessions": []})[1]
        )

        gestor.get_now_playing()
        gestor.get_now_playing(use_cache=False)

        assert len(chamadas) == 2

    def test_uma_falha_nao_fica_colada_ao_painel(self, app_context, monkeypatch):
        """Só leituras bem-sucedidas são guardadas: uma falha de rede é transitória."""
        gestor = self._manager()
        respostas = [
            {"success": False, "stream_count": 0, "sessions": []},
            {"success": True, "stream_count": 1, "sessions": [{"session_key": "1"}]},
        ]
        monkeypatch.setattr(gestor, "_build_now_playing", lambda: respostas.pop(0))

        assert gestor.get_now_playing()["success"] is False
        assert gestor.get_now_playing()["success"] is True

    def test_mudanca_de_estado_descarta_a_cache(self, app_context, monkeypatch):
        """
        Um play/pausa real tem de chegar ao painel de imediato — a cache não pode
        servir o estado anterior ao pedido que vem logo a seguir ao sinal.
        """
        gestor = self._manager()
        monkeypatch.setattr(
            gestor, "_build_now_playing",
            lambda: {"success": True, "stream_count": 0, "sessions": []}
        )
        gestor.get_now_playing()
        assert gestor._get_cached_now_playing() is not None

        gestor.invalidate_now_playing_cache()
        assert gestor._get_cached_now_playing() is None

    def test_cache_expira_ao_fim_da_janela(self, app_context, monkeypatch):
        gestor = self._manager()
        monkeypatch.setattr(
            gestor, "_build_now_playing",
            lambda: {"success": True, "stream_count": 0, "sessions": []}
        )
        gestor.get_now_playing()

        # Recua o relógio da cache para além da janela.
        gestor._now_playing_cached_at -= (StreamManager.NOW_PLAYING_CACHE_SECONDS + 1)
        assert gestor._get_cached_now_playing() is None


class TestGuardaContraCorteRepetido:
    """
    🐛 REGRESSÃO REPORTADA (log de um Jellyfin real): os cortes apareciam
    espaçados de 60 a 120 segundos em vez de a cada volta da verificação. O
    utilizador era cortado, recomeçava o filme, e ficava um minuto inteiro sem
    ser incomodado.

    A guarda "já cortei esta" era gravada com o `session_key`. No Plex isso é a
    REPRODUÇÃO (muda a cada play); no Jellyfin é o APARELHO, e sobrevive a
    parar e recomeçar — por isso a guarda apanhava a reprodução seguinte.
    """

    def _config(self, intervalo=15):
        return {
            "STREAM_CHECK_INTERVAL_SECONDS": intervalo,
            "SCREEN_LIMIT_TERMINATION_STRATEGY": "oldest",
            "TERMINATION_MSG_SCREEN_LIMIT": "limite",
        }

    def test_recomecar_a_reproducao_volta_a_ser_cortado(self, manager, cache_limpa):
        # Mesmo aparelho (mesma sessão), reprodução nova.
        primeira = sessao(session_key="aparelho-1", playback_key="aparelho-1:reproducao-A", view_offset=900)
        outra = sessao(session_key="aparelho-2", playback_key="aparelho-2:reproducao-B", view_offset=10)

        manager._enforce_screen_limits(1, "ana", [primeira, outra], {"screen_limit": 1}, self._config())
        assert set(manager.sessions.terminadas) == {"aparelho-1"}

        # O utilizador recomeça no MESMO aparelho: é uma reprodução nova.
        manager.sessions.terminadas.clear()
        recomecada = sessao(session_key="aparelho-1", playback_key="aparelho-1:reproducao-C", view_offset=900)

        manager._enforce_screen_limits(1, "ana", [recomecada, outra], {"screen_limit": 1}, self._config())

        assert set(manager.sessions.terminadas) == {"aparelho-1"}

    def test_a_mesma_reproducao_nao_e_cortada_duas_vezes_seguidas(self, manager, cache_limpa):
        # A guarda continua a servir para o que existe: não repetir a ordem
        # (nem a mensagem, nem a auditoria) enquanto o cliente obedece.
        a_cortar = sessao(session_key="aparelho-1", playback_key="repro-A", view_offset=900)
        outra = sessao(session_key="aparelho-2", playback_key="repro-B", view_offset=10)

        manager._enforce_screen_limits(1, "ana", [a_cortar, outra], {"screen_limit": 1}, self._config())
        manager.sessions.terminadas.clear()

        manager._enforce_screen_limits(1, "ana", [a_cortar, outra], {"screen_limit": 1}, self._config())

        assert manager.sessions.terminadas == {}

    def test_a_janela_acompanha_o_intervalo_de_verificacao(self, manager):
        # Duas voltas chegam para o cliente obedecer; um minuto fixo dava a
        # quem fosse cortado um minuto de stream livre.
        assert manager._janela_anti_repeticao(self._config(intervalo=15)) == 30
        assert manager._janela_anti_repeticao(self._config(intervalo=30)) == 60
        # Nunca menos de 30s, mesmo com um intervalo muito curto.
        assert manager._janela_anti_repeticao(self._config(intervalo=5)) == 30

    def test_sem_chave_de_reproducao_usa_a_da_sessao(self):
        # Um servidor que não distinga os dois continua a funcionar como antes.
        assert sessao(session_key="abc").playback_key == "abc"


class TestClienteQueIgnoraAOrdemDeParar:
    """
    🐛 REGRESSÃO REPORTADA (log de um painel real ligado ao Jellyfin): o painel
    via as duas reproduções, mandava parar a cada volta, o servidor aceitava a
    ordem — e o stream seguia à mesma. O leitor integrado da aplicação Android
    (ExoPlayer) recebe o comando e ignora-o; pelo navegador o corte funcionava.

    Sem contagem, o painel pedia educadamente para sempre. Ao fim de algumas
    tentativas assume-se que o cliente não vai obedecer e escala para o último
    recurso do servidor — que é agressivo (no Jellyfin revoga o acesso do
    aparelho), e por isso só acontece com FORCE_STREAM_TERMINATION ativo.
    """

    @pytest.fixture()
    def forcar(self, manager, monkeypatch):
        """Liga/desliga o FORCE_STREAM_TERMINATION visto pelo motor."""
        config = {}

        def definir(ativo):
            config['FORCE_STREAM_TERMINATION'] = ativo

        monkeypatch.setattr(
            "app.services.stream_manager.load_or_create_config", lambda: config
        )
        return definir

    def _insistir(self, manager, sessao_alvo, voltas):
        for _ in range(voltas):
            manager._terminate_session(sessao_alvo, "limite")

    def test_as_primeiras_tentativas_sao_sempre_o_pedido_normal(self, manager, cache_limpa, forcar):
        forcar(True)
        alvo = sessao(session_key="exoplayer", playback_key="exoplayer:repro-A")

        self._insistir(manager, alvo, manager.TENTATIVAS_ANTES_DE_FORCAR)

        assert manager.sessions.terminadas == {"exoplayer": "limite"}
        assert manager.sessions.forcadas == {}

    def test_ao_fim_das_tentativas_escala_para_o_ultimo_recurso(self, manager, cache_limpa, forcar):
        forcar(True)
        alvo = sessao(session_key="exoplayer", playback_key="exoplayer:repro-A")

        self._insistir(manager, alvo, manager.TENTATIVAS_ANTES_DE_FORCAR + 1)

        assert manager.sessions.forcadas == {"exoplayer": "limite"}

    def test_desligado_por_omissao_avisa_mas_nao_forca(self, manager, cache_limpa, forcar):
        # Revogar o acesso de um aparelho não se desfaz a partir do painel: o
        # administrador tem de pedir explicitamente.
        forcar(False)
        alvo = sessao(session_key="exoplayer", playback_key="exoplayer:repro-A")

        self._insistir(manager, alvo, manager.TENTATIVAS_ANTES_DE_FORCAR + 3)

        assert manager.sessions.forcadas == {}
        # E continua a pedir — desistir em silêncio seria pior.
        assert manager.sessions.terminadas == {"exoplayer": "limite"}

    def test_se_o_ultimo_recurso_falhar_volta_a_pedir(self, manager, cache_limpa, forcar):
        # Um servidor sem nada mais forte a oferecer (o Plex, por exemplo)
        # devolve False; o motor não pode dar o corte por feito.
        forcar(True)
        manager.sessions.forca_funciona = False
        alvo = sessao(session_key="exoplayer", playback_key="exoplayer:repro-A")

        self._insistir(manager, alvo, manager.TENTATIVAS_ANTES_DE_FORCAR + 1)

        assert manager.sessions.forcadas == {"exoplayer": "limite"}
        assert manager.sessions.terminadas == {"exoplayer": "limite"}

    def test_a_contagem_e_por_reproducao_e_nao_por_aparelho(self, manager, cache_limpa, forcar):
        # No Jellyfin a sessão é do APARELHO e sobrevive a parar e recomeçar.
        # Quem recomeça merece outra vez o pedido educado.
        forcar(True)
        primeira = sessao(session_key="exoplayer", playback_key="exoplayer:repro-A")
        self._insistir(manager, primeira, manager.TENTATIVAS_ANTES_DE_FORCAR)

        recomecada = sessao(session_key="exoplayer", playback_key="exoplayer:repro-B")
        manager._terminate_session(recomecada, "limite")

        assert manager.sessions.forcadas == {}
