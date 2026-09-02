# tests/test_stats_handler.py
"""Gamificação: níveis, XP, temporadas, conquistas e processamento do histórico."""

from datetime import datetime, timedelta, timezone

import pytest
from requests.exceptions import RequestException

from app.services.tautulli import stats_handler as stats_module
from app.services.tautulli.stats_handler import (
    StatsHandler,
    _safe_int_float,
    get_default_level_table,
    get_level_info,
    normalize_level_table,
)
from tests.conftest import FakeDataManager

UTC = timezone.utc


class FakeApiClient:
    """Cliente do Tautulli falso: devolve o histórico que o teste definir."""

    def __init__(self, history=None, metadata=None, erro=None):
        self.history = history or []
        self.metadata = metadata or {}
        self.erro = erro
        self.chamadas = []

    def get_history(self, **kwargs):
        self.chamadas.append(kwargs)
        if self.erro:
            raise self.erro
        return {"data": self.history}

    def get_metadata(self, rating_key):
        return self.metadata.get(str(rating_key))


@pytest.fixture()
def configurar(monkeypatch):
    def _configurar(**valores):
        config = {
            "XP_PER_MINUTE_WATCHED": 1,
            "XP_BONUS_PER_COMPLETED_ITEM": 20,
            "XP_COMPLETION_THRESHOLD_PERCENT": 90,
            "XP_LEVEL_TABLE": [],
            "XP_RESET_ENABLED": False,
            "XP_RESET_MONTHS": [],
        }
        config.update(valores)
        monkeypatch.setattr(stats_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


class TestSafeIntFloat:
    @pytest.mark.parametrize("valor,esperado", [
        ("42", 42), (42.9, 42), ("42.9", 42), (0, 0), ("-3", -3),
    ])
    def test_converte(self, valor, esperado):
        assert _safe_int_float(valor) == esperado

    @pytest.mark.parametrize("valor", [None, "", "abc", [], {}])
    def test_valores_invalidos_usam_o_padrao(self, valor):
        assert _safe_int_float(valor) == 0

    def test_padrao_personalizado(self):
        assert _safe_int_float(None, default=7) == 7


class TestNormalizeLevelTable:
    def test_tabela_vazia_usa_a_padrao(self):
        assert normalize_level_table([]) == get_default_level_table()
        assert normalize_level_table(None) == get_default_level_table()

    def test_ordena_por_xp_crescente(self):
        tabela = normalize_level_table([
            {"threshold": 500, "name": "B"},
            {"threshold": 0, "name": "A"},
        ])

        assert [n["threshold"] for n in tabela] == [0, 500]

    def test_remove_thresholds_duplicados(self):
        tabela = normalize_level_table([
            {"threshold": 0, "name": "A"},
            {"threshold": 0, "name": "A duplicado"},
        ])

        assert len(tabela) == 1

    def test_ignora_entradas_invalidas(self):
        tabela = normalize_level_table([
            {"threshold": 0, "name": "A"},
            {"threshold": "abc", "name": "B"},
            {"threshold": 100, "name": ""},
            {"threshold": -5, "name": "C"},
            "não é um dicionário",
        ])

        assert [n["name"] for n in tabela] == ["A"]

    def test_garante_um_nivel_a_partir_de_zero_xp(self):
        # Sem isto, um utilizador novo (0 XP) ficava sem nível nenhum.
        tabela = normalize_level_table([{"threshold": 500, "name": "B"}])

        assert tabela[0]["threshold"] == 0

    def test_icone_padrao(self):
        assert normalize_level_table([{"threshold": 0, "name": "A"}])[0]["icon"] == "⭐"

    def test_tabela_so_com_lixo_cai_para_a_padrao(self):
        assert normalize_level_table([{"nada": 1}]) == get_default_level_table()


class TestGetLevelInfo:
    def test_utilizador_novo(self):
        info = get_level_info(0)

        assert info["level_number"] == 1
        assert info["progress_percent"] == 0
        assert info["is_max_level"] is False

    def test_progresso_a_meio_do_nivel(self):
        info = get_level_info(250)  # metade de 0 -> 500

        assert info["level_number"] == 1
        assert info["progress_percent"] == 50.0
        assert info["xp_for_next_level"] == 250

    def test_exatamente_no_limiar_sobe_de_nivel(self):
        assert get_level_info(500)["level_number"] == 2

    def test_nivel_maximo(self):
        info = get_level_info(999999)

        assert info["is_max_level"] is True
        assert info["progress_percent"] == 100
        assert info["xp_next_level_threshold"] is None
        assert info["xp_for_next_level"] == 0

    @pytest.mark.parametrize("xp", [-100, None])
    def test_xp_invalido_conta_como_zero(self, xp):
        assert get_level_info(xp)["xp"] == 0

    def test_tabela_personalizada(self):
        tabela = normalize_level_table([
            {"threshold": 0, "name": "Início", "icon": "🌱"},
            {"threshold": 100, "name": "Fim", "icon": "🏁"},
        ])

        info = get_level_info(100, tabela)

        assert info["level_name"] == "Fim"
        assert info["total_levels"] == 2
        assert info["is_max_level"] is True


class TestSyncUserXp:
    def _handler(self, history=None, profiles=None, **kwargs):
        dados = FakeDataManager(profiles=profiles or {1: {"plex_user_id": 1, "username": "ana"}})
        return StatsHandler(FakeApiClient(history=history, **kwargs), data_manager=dados), dados

    def test_soma_xp_por_minuto_assistido(self, app_context, configurar):
        configurar()
        handler, dados = self._handler(history=[
            {"date": 1700000000, "duration": 3600, "percent_complete": 10},
        ])

        # 3600s = 60 min * 1 XP = 60
        assert handler.sync_user_xp(1, "ana") == 60
        assert dados.profiles[1]["xp"] == 60

    def test_bonus_por_item_concluido(self, app_context, configurar):
        configurar()
        handler, _dados = self._handler(history=[
            {"date": 1700000000, "duration": 600, "percent_complete": 95},
        ])

        # 10 min = 10 XP + 20 de bónus
        assert handler.sync_user_xp(1, "ana") == 30

    def test_soma_ao_xp_existente_e_ao_lifetime(self, app_context, configurar):
        configurar()
        handler, dados = self._handler(
            history=[{"date": 1700000000, "duration": 600, "percent_complete": 0}],
            profiles={1: {"plex_user_id": 1, "username": "ana", "xp": 100, "lifetime_xp": 500}},
        )

        assert handler.sync_user_xp(1, "ana") == 110
        assert dados.profiles[1]["lifetime_xp"] == 510

    def test_nao_reprocessa_historico_ja_contado(self, app_context, configurar):
        configurar()
        handler, _dados = self._handler(
            history=[{"date": 1700000000, "duration": 600, "percent_complete": 0}],
            profiles={1: {"plex_user_id": 1, "username": "ana", "xp": 50, "xp_last_sync_at": 1700000000}},
        )

        # O item é do próprio instante da última sincronização: não conta de novo.
        assert handler.sync_user_xp(1, "ana") == 50

    def test_guarda_o_instante_do_item_mais_recente(self, app_context, configurar):
        configurar()
        handler, dados = self._handler(history=[
            {"date": 1700000000, "duration": 60, "percent_complete": 0},
            {"date": 1700009999, "duration": 60, "percent_complete": 0},
        ])

        handler.sync_user_xp(1, "ana")

        assert dados.profiles[1]["xp_last_sync_at"] == 1700009999

    def test_historico_vazio_marca_a_sincronizacao(self, app_context, configurar):
        configurar()
        handler, dados = self._handler(history=[])

        assert handler.sync_user_xp(1, "ana") == 0
        assert dados.profiles[1]["xp_last_sync_at"] > 0

    def test_primeira_sincronizacao_pede_todo_o_historico(self, app_context, configurar):
        configurar()
        handler, _dados = self._handler(history=[])

        handler.sync_user_xp(1, "ana")

        assert "after" not in handler.api.chamadas[0]

    def test_sincronizacoes_seguintes_filtram_por_data(self, app_context, configurar):
        configurar()
        handler, _dados = self._handler(
            history=[],
            profiles={1: {"plex_user_id": 1, "username": "ana", "xp_last_sync_at": 1700000000}},
        )

        handler.sync_user_xp(1, "ana")

        assert handler.api.chamadas[0]["after"] == "2023-11-14"

    def test_notifica_quando_sobe_de_nivel(self, app_context, configurar):
        configurar()
        handler, dados = self._handler(
            history=[{"date": 1700000000, "duration": 60000, "percent_complete": 0}],
            profiles={1: {"plex_user_id": 1, "username": "ana", "xp": 0}},
        )

        handler.sync_user_xp(1, "ana")

        assert len(dados.notifications) == 1
        assert dados.notifications[0]["user_plex_id"] == 1

    def test_sem_subida_de_nivel_nao_notifica(self, app_context, configurar):
        configurar()
        handler, dados = self._handler(history=[
            {"date": 1700000000, "duration": 60, "percent_complete": 0},
        ])

        handler.sync_user_xp(1, "ana")

        assert dados.notifications == []

    def test_tautulli_indisponivel_falha_em_silencio(self, app_context, configurar):
        configurar()
        handler, _dados = self._handler(erro=RequestException("offline"))

        # A página de estatísticas tem de continuar a funcionar.
        assert handler.sync_user_xp(1, "ana") is None

    def test_sem_data_manager(self, app_context, configurar):
        configurar()

        assert StatsHandler(FakeApiClient(), data_manager=None).sync_user_xp(1, "ana") is None

    def test_xp_por_minuto_configuravel(self, app_context, configurar):
        configurar(XP_PER_MINUTE_WATCHED=2, XP_BONUS_PER_COMPLETED_ITEM=0)
        handler, _dados = self._handler(history=[
            {"date": 1700000000, "duration": 600, "percent_complete": 0},
        ])

        assert handler.sync_user_xp(1, "ana") == 20


class TestSeasonInfo:
    def test_desativado(self, app_context, configurar):
        configurar(XP_RESET_ENABLED=False)

        assert StatsHandler(FakeApiClient()).get_season_info() is None

    def test_sem_meses_escolhidos(self, app_context, configurar):
        configurar(XP_RESET_ENABLED=True, XP_RESET_MONTHS=[])

        assert StatsHandler(FakeApiClient()).get_season_info() is None

    def test_meses_invalidos_sao_descartados(self, app_context, configurar):
        configurar(XP_RESET_ENABLED=True, XP_RESET_MONTHS=["abc", 0, 13])

        assert StatsHandler(FakeApiClient()).get_season_info() is None

    def test_proximo_reset_e_sempre_no_futuro(self, app_context, configurar):
        configurar(XP_RESET_ENABLED=True, XP_RESET_MONTHS=[1, 7])

        info = StatsHandler(FakeApiClient()).get_season_info()

        assert info["enabled"] is True
        assert info["months"] == [1, 7]
        assert datetime.fromisoformat(info["next_reset_at"]) > datetime.now(UTC)
        assert info["days_remaining"] >= 0

    def test_meses_duplicados_sao_agrupados_e_ordenados(self, app_context, configurar):
        configurar(XP_RESET_ENABLED=True, XP_RESET_MONTHS=[7, 1, 7])

        assert StatsHandler(FakeApiClient()).get_season_info()["months"] == [1, 7]


class TestResetSeason:
    @pytest.fixture()
    def handler(self, app_context, configurar, monkeypatch):
        from app import config as config_module

        monkeypatch.setattr(config_module, "save_app_config", lambda cfg: True)
        dados = FakeDataManager(profiles={
            1: {"plex_user_id": 1, "xp": 500, "lifetime_xp": 500},
            2: {"plex_user_id": 2, "xp": 100, "lifetime_xp": 100},
        })
        return StatsHandler(FakeApiClient(), data_manager=dados)

    def test_desativado_nao_repoe_nada(self, handler, configurar):
        configurar(XP_RESET_ENABLED=False)

        resultado = handler.reset_season_if_due()

        assert resultado["reset"] is False
        assert handler.data_manager.profiles[1]["xp"] == 500

    def test_mes_que_nao_e_de_reset(self, handler, configurar):
        mes_diferente = 12 if datetime.now(UTC).month != 12 else 1
        configurar(XP_RESET_ENABLED=True, XP_RESET_MONTHS=[mes_diferente])

        assert handler.reset_season_if_due()["reset"] is False

    def test_fora_do_dia_1_ou_ja_efetuado(self, handler, configurar):
        agora = datetime.now(UTC)
        configurar(
            XP_RESET_ENABLED=True,
            XP_RESET_MONTHS=[agora.month],
            XP_LAST_RESET_PERIOD=agora.strftime("%Y-%m"),
        )

        # Quer por não ser dia 1, quer por já ter sido feito este mês, não repete.
        assert handler.reset_season_if_due()["reset"] is False

    def test_forcado_repoe_o_xp_da_temporada(self, handler, configurar):
        configurar(XP_RESET_ENABLED=False)

        resultado = handler.reset_season_if_due(force=True)

        assert resultado["reset"] is True
        assert resultado["affected_users"] == 2
        assert handler.data_manager.profiles[1]["xp"] == 0

    def test_o_lifetime_xp_nunca_e_reposto(self, handler, configurar):
        configurar()

        handler.reset_season_if_due(force=True)

        assert handler.data_manager.profiles[1]["lifetime_xp"] == 500

    def test_falha_na_base_de_dados_e_reportada(self, app_context, configurar, monkeypatch):
        configurar()

        class DataManagerQueRebenta(FakeDataManager):
            def reset_all_users_xp(self):
                raise RuntimeError("base de dados bloqueada")

        resultado = StatsHandler(FakeApiClient(), DataManagerQueRebenta()).reset_season_if_due(force=True)

        assert resultado["success"] is False


class TestAchievements:
    def _stats(self, **valores):
        base = {
            "movie_count": 0,
            "episode_count": 0,
            "weekly_activity_python": [0] * 7,
            "unique_decades": set(),
            "favorite_director_count": 0,
            "night_owl_session_count": 0,
            "pioneer_count": 0,
        }
        base.update(valores)
        return base

    @pytest.fixture()
    def handler(self, app_context, configurar):
        configurar(
            ACHIEVEMENT_MOVIE_MARATHON_BRONZE=5,
            ACHIEVEMENT_MOVIE_MARATHON_SILVER=10,
            ACHIEVEMENT_MOVIE_MARATHON_GOLD=20,
        )
        return StatsHandler(FakeApiClient(), data_manager=FakeDataManager())

    def test_desbloqueia_o_nivel_bronze(self, handler):
        conquistas = handler._calculate_achievements(self._stats(movie_count=5), 7, 1, "ana")

        assert "movie_marathon_bronze" in {c["id"] for c in conquistas}

    def test_desbloqueia_sempre_o_nivel_mais_alto_atingido(self, handler):
        conquistas = handler._calculate_achievements(self._stats(movie_count=25), 7, 1, "ana")
        ids = {c["id"] for c in conquistas}

        assert "movie_marathon_gold" in ids
        assert "movie_marathon_bronze" not in ids

    def test_abaixo_da_meta_nao_desbloqueia(self, handler):
        conquistas = handler._calculate_achievements(self._stats(movie_count=4), 7, 1, "ana")

        assert conquistas == []

    def test_conquistas_ja_ganhas_sao_mantidas(self, handler):
        handler._calculate_achievements(self._stats(movie_count=5), 7, 1, "ana")

        # Numa segunda visita, sem filmes novos, a conquista continua a aparecer.
        conquistas = handler._calculate_achievements(self._stats(), 7, 1, "ana")

        assert {c["id"] for c in conquistas} == {"movie_marathon_bronze"}

    def test_notifica_o_dono_da_conquista(self, handler):
        handler._calculate_achievements(self._stats(movie_count=5), 7, 1, "ana")

        assert len(handler.data_manager.notifications) == 1
        assert handler.data_manager.notifications[0]["user_plex_id"] == 1

    def test_nao_notifica_duas_vezes_a_mesma_conquista(self, handler):
        handler._calculate_achievements(self._stats(movie_count=5), 7, 1, "ana")
        handler._calculate_achievements(self._stats(movie_count=6), 7, 1, "ana")

        assert len(handler.data_manager.notifications) == 1

    def test_guerreiro_de_fim_de_semana(self, handler):
        # Índices 5 e 6 são sábado e domingo.
        stats = self._stats(weekly_activity_python=[10, 0, 0, 0, 0, 100, 100])

        ids = {c["id"] for c in handler._calculate_achievements(stats, 7, 1, "ana")}

        assert "weekend_warrior_bronze" in ids

    def test_sem_atividade_nao_ha_divisao_por_zero(self, handler):
        assert handler._calculate_achievements(self._stats(), 7, 1, "ana") == []

    def test_conquistas_variadas(self, handler):
        stats = self._stats(
            episode_count=20,
            unique_decades={"1980s", "1990s", "2000s"},
            night_owl_session_count=3,
            pioneer_count=1,
        )

        ids = {c["id"] for c in handler._calculate_achievements(stats, 7, 1, "ana")}

        assert {"series_binger_bronze", "time_traveler_bronze", "night_owl_bronze", "pioneer_bronze"} <= ids

    def test_sem_data_manager(self, app_context, configurar):
        configurar()

        assert StatsHandler(FakeApiClient(), None)._calculate_achievements({}, 7, 1, "ana") == []


class TestProcessHistoryItem:
    @pytest.fixture()
    def handler(self, app_context, configurar):
        configurar()
        return StatsHandler(FakeApiClient())

    def _item(self, **extra):
        item = {
            "date": int(datetime(2026, 3, 14, 20, 0, tzinfo=UTC).timestamp()),
            "duration": 3600,
            "media_type": "movie",
            "title": "Matrix",
            "year": 1999,
            "platform": "Android",
        }
        item.update(extra)
        return item

    def test_contabiliza_um_filme(self, handler):
        stats = handler._initialize_stats_dict()

        handler._process_history_item(self._item(), stats, {})

        assert stats["plays"] == 1
        assert stats["movie_count"] == 1
        assert stats["total_duration"] == 3600
        assert stats["top_movies"]["Matrix"] == 1
        assert stats["unique_platforms"] == {"Android"}

    def test_decada_do_filme(self, handler):
        stats = handler._initialize_stats_dict()

        handler._process_history_item(self._item(year=1999), stats, {})

        assert stats["unique_decades"] == {"1990s"}

    def test_ano_invalido_nao_gera_decada(self, handler):
        stats = handler._initialize_stats_dict()

        handler._process_history_item(self._item(year=""), stats, {})

        assert stats["unique_decades"] == set()

    def test_contabiliza_um_episodio(self, handler):
        stats = handler._initialize_stats_dict()

        handler._process_history_item(
            self._item(media_type="episode", grandparent_title="Dark", genres=["Drama"]), stats, {}
        )

        assert stats["episode_count"] == 1
        assert stats["top_shows"]["Dark"] == 1
        assert stats["unique_genres"] == {"Drama"}

    def test_generos_da_serie_sao_procurados_uma_so_vez(self, app_context, configurar):
        configurar()
        api = FakeApiClient(metadata={"55": {"genres": ["Ficção"]}})
        handler = StatsHandler(api)
        stats = handler._initialize_stats_dict()
        cache = {}

        for _ in range(2):
            handler._process_history_item(
                self._item(media_type="episode", grandparent_rating_key="55"), stats, cache
            )

        assert cache == {"55": ["Ficção"]}
        assert stats["genre_counts"]["Ficção"] == 2

    def test_conquista_coruja_conta_sessoes_de_madrugada(self, handler):
        stats = handler._initialize_stats_dict()
        madrugada = int(datetime(2026, 3, 14, 3, 0, tzinfo=UTC).timestamp())

        handler._process_history_item(self._item(date=madrugada), stats, {})

        assert stats["night_owl_session_count"] == 1
        assert stats["late_night_plays"] == 1

    def test_sessao_a_meio_da_tarde_nao_conta_como_coruja(self, handler):
        stats = handler._initialize_stats_dict()

        handler._process_history_item(self._item(), stats, {})

        assert stats["night_owl_session_count"] == 0

    def test_pioneiro_dentro_da_janela(self, handler):
        stats = handler._initialize_stats_dict()
        visto = int(datetime(2026, 3, 14, 20, 0, tzinfo=UTC).timestamp())
        adicionado = visto - 3600  # 1 hora antes

        handler._process_history_item(
            self._item(date=visto, rating_key="10"), stats,
            {}, {"10": adicionado, "_window_hours": 48},
        )

        assert stats["pioneer_count"] == 1

    def test_pioneiro_fora_da_janela(self, handler):
        stats = handler._initialize_stats_dict()
        visto = int(datetime(2026, 3, 14, 20, 0, tzinfo=UTC).timestamp())
        adicionado = visto - (72 * 3600)  # 3 dias antes

        handler._process_history_item(
            self._item(date=visto, rating_key="10"), stats,
            {}, {"10": adicionado, "_window_hours": 48},
        )

        assert stats["pioneer_count"] == 0

    def test_atividade_semanal_em_ambos_os_formatos(self, handler):
        stats = handler._initialize_stats_dict()
        # 14/03/2026 é um sábado: índice 5 em Python, 6 em JavaScript.
        handler._process_history_item(self._item(), stats, {})

        assert stats["weekly_activity_python"][5] == 3600
        assert stats["weekly_activity_js"][6] == 3600

    def test_apenas_os_primeiros_nove_itens_recentes(self, handler):
        stats = handler._initialize_stats_dict()

        for i in range(12):
            handler._process_history_item(self._item(title=f"Filme {i}"), stats, {})

        assert len(stats["recent"]) == 9
        assert stats["plays"] == 12


class TestFinalizeStats:
    @pytest.fixture()
    def handler(self, app_context, configurar):
        configurar()
        return StatsHandler(FakeApiClient())

    def test_converte_conjuntos_em_listas_serializaveis(self, handler):
        stats = handler._initialize_stats_dict()
        stats["unique_genres"].add("Drama")
        stats["unique_days"].add(datetime(2026, 3, 14, tzinfo=UTC).date())
        stats["unique_platforms"].add("Android")
        stats["unique_decades"].add("1990s")

        handler._finalize_stats(stats)

        assert stats["unique_genres"] == ["Drama"]
        assert stats["unique_days"] == ["2026-03-14"]
        assert isinstance(stats["unique_platforms"], list)
        assert isinstance(stats["unique_decades"], list)

    def test_genero_e_realizador_favoritos(self, handler):
        stats = handler._initialize_stats_dict()
        stats["genre_counts"].update(["Drama", "Drama", "Ação"])
        stats["director_counts"].update(["Nolan", "Nolan", "Villeneuve"])

        handler._finalize_stats(stats)

        assert stats["favorite_genre"] == "Drama"
        assert stats["favorite_director_count"] == 2

    def test_sem_dados_nao_rebenta(self, handler):
        stats = handler._initialize_stats_dict()

        handler._finalize_stats(stats)

        assert stats["favorite_director_count"] == 0
        assert stats["favorite_genre"]
