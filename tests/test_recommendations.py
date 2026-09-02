# tests/test_recommendations.py
"""Motor de recomendações: "Porque assistiu X, pode gostar de Y"."""

import pytest

from app.services.tautulli import recommendations_handler as rec_module
from app.services.tautulli.recommendations_handler import (
    DEFAULTS,
    RecommendationsHandler,
    _item_identity,
    _percent_complete,
    build_poster_url,
)
from tests.conftest import FakeDataManager


class FakeApiClient:
    """Cliente do Tautulli falso: devolve o histórico e os metadados do teste."""

    def __init__(self, history=None, metadata=None):
        self.history = history if history is not None else []
        self.metadata = metadata or {}
        self.chamadas_historico = []
        self.chamadas_metadata = []

    def get_history(self, **kwargs):
        self.chamadas_historico.append(kwargs)
        return {"data": self.history}

    def get_metadata(self, rating_key):
        self.chamadas_metadata.append(str(rating_key))
        return self.metadata.get(str(rating_key))


@pytest.fixture()
def configurar(monkeypatch):
    """Substitui a leitura do config.json por valores controlados pelo teste."""

    def _configurar(**valores):
        config = dict(DEFAULTS)
        config.update(valores)
        monkeypatch.setattr(rec_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


def filme(user_id, rating_key, titulo, percent=100, ano=2020):
    return {
        "user_id": user_id,
        "media_type": "movie",
        "rating_key": rating_key,
        "title": titulo,
        "year": ano,
        "percent_complete": percent,
        "thumb": f"/library/metadata/{rating_key}/thumb",
    }


def episodio(user_id, serie_key, serie, titulo="Ep 1", percent=100):
    return {
        "user_id": user_id,
        "media_type": "episode",
        "rating_key": f"{serie_key}001",
        "grandparent_rating_key": serie_key,
        "grandparent_title": serie,
        "title": titulo,
        "percent_complete": percent,
        "grandparent_thumb": f"/library/metadata/{serie_key}/thumb",
    }


class TestItemIdentity:
    def test_filme_usa_o_rating_key(self):
        identidade = _item_identity(filme(1, "10", "Duna"))

        assert identidade["key"] == "movie:10"
        assert identidade["media_type"] == "movie"
        assert identidade["title"] == "Duna"
        assert identidade["year"] == 2020

    def test_filme_sem_rating_key_cai_para_titulo_e_ano(self):
        identidade = _item_identity({"media_type": "movie", "title": "Duna", "year": 2021})

        assert identidade["key"] == "movie:duna|2021"

    def test_episodio_e_agrupado_pela_serie(self):
        identidade = _item_identity(episodio(1, "77", "Dark"))

        assert identidade["key"] == "show:77"
        assert identidade["media_type"] == "show"
        assert identidade["title"] == "Dark"

    def test_o_ano_do_episodio_nao_vira_ano_da_serie(self):
        item = episodio(1, "77", "Dark")
        item["year"] = 2017

        assert _item_identity(item)["year"] is None

    def test_musica_e_ignorada(self):
        assert _item_identity({"media_type": "track", "title": "Song"}) is None

    def test_item_sem_titulo_nem_chave_e_ignorado(self):
        assert _item_identity({"media_type": "movie"}) is None


class TestPercentComplete:
    def test_le_a_percentagem(self):
        assert _percent_complete({"percent_complete": 42}) == 42

    @pytest.mark.parametrize("valor", [None, "", "abc"])
    def test_sem_percentagem_valida_assume_visto(self, valor):
        assert _percent_complete({"percent_complete": valor}) == 100.0

    def test_cai_para_o_watched_status(self):
        assert _percent_complete({"watched_status": 0.5}) == 50.0


class TestBuildPosterUrl:
    def test_sem_thumb_nao_ha_poster(self):
        assert build_poster_url(None) is None

    def test_gera_url_do_proxy_interno(self, app_context):
        url = build_poster_url("/library/metadata/1/thumb")

        assert "/image/?source=" in url
        # O URL do Tautulli nunca pode aparecer em claro para o browser.
        assert "pms_image_proxy" not in url


class TestBuildIndex:
    def _handler(self, history, metadata=None, profiles=None):
        dados = FakeDataManager(profiles=profiles or {})
        return RecommendationsHandler(FakeApiClient(history=history, metadata=metadata), data_manager=dados)

    def test_agrega_utilizadores_e_itens(self, app_context, configurar):
        configurar(RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=0)
        handler = self._handler([
            filme(1, "10", "Duna"),
            filme(1, "10", "Duna"),
            filme(2, "10", "Duna"),
            episodio(2, "77", "Dark"),
        ])

        index = handler.build_index()

        assert index["user_items"]["1"]["movie:10"] == 2
        assert index["item_users"]["movie:10"] == {"1", "2"}
        assert index["catalog"]["show:77"]["title"] == "Dark"

    def test_sessoes_muito_curtas_nao_contam(self, app_context, configurar):
        configurar(RECOMMENDATIONS_MIN_PERCENT_WATCHED=25, RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=0)
        handler = self._handler([filme(1, "10", "Duna", percent=5)])

        assert handler.build_index()["user_items"] == {}

    def test_a_janela_de_dias_e_enviada_ao_tautulli(self, app_context, configurar):
        configurar(RECOMMENDATIONS_HISTORY_DAYS=30, RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=0)
        handler = self._handler([])

        handler.build_index()

        assert "after" in handler.api.chamadas_historico[0]

    def test_quem_pediu_privacidade_nao_influencia_os_outros(self, app_context, configurar):
        configurar(RECOMMENDATIONS_RESPECT_PRIVACY=True, RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=0)
        handler = self._handler(
            [filme(1, "10", "Duna"), filme(2, "10", "Duna")],
            profiles={2: {"plex_user_id": 2, "hide_from_leaderboard": True}},
        )

        index = handler.build_index()

        # O histórico do utilizador privado continua a existir (ele precisa dele
        # para receber recomendações), mas não entra na matriz de vizinhos.
        assert "2" in index["user_items"]
        assert index["item_users"]["movie:10"] == {"1"}

    def test_a_privacidade_pode_ser_desligada(self, app_context, configurar):
        configurar(RECOMMENDATIONS_RESPECT_PRIVACY=False, RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=0)
        handler = self._handler(
            [filme(1, "10", "Duna"), filme(2, "10", "Duna")],
            profiles={2: {"plex_user_id": 2, "hide_from_leaderboard": True}},
        )

        assert handler.build_index()["item_users"]["movie:10"] == {"1", "2"}

    def test_generos_do_historico_sao_aproveitados_sem_pedir_metadados(self, app_context, configurar):
        configurar()
        item = filme(1, "10", "Duna")
        item["genres"] = ["Ficção Científica", "Aventura"]
        handler = self._handler([item])

        index = handler.build_index()

        assert index["catalog"]["movie:10"]["genres"] == ["Ficção Científica", "Aventura"]
        assert handler.api.chamadas_metadata == []

    def test_generos_em_falta_sao_procurados_nos_metadados(self, app_context, configurar):
        configurar(RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=10)
        handler = self._handler(
            [filme(1, "10", "Duna")],
            metadata={"10": {"genres": ["Drama"]}},
        )

        assert handler.build_index()["catalog"]["movie:10"]["genres"] == ["Drama"]

    def test_o_limite_de_consultas_de_metadados_e_respeitado(self, app_context, configurar):
        configurar(RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=1)
        handler = self._handler([filme(1, "10", "Duna"), filme(1, "20", "Arrival")])

        handler.build_index()

        assert len(handler.api.chamadas_metadata) == 1

    def test_limite_zero_desliga_as_consultas(self, app_context, configurar):
        configurar(RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=0)
        handler = self._handler([filme(1, "10", "Duna")])

        handler.build_index()

        assert handler.api.chamadas_metadata == []

    def test_falha_nos_metadados_nao_quebra_o_indice(self, app_context, configurar):
        configurar(RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=5)
        handler = self._handler([filme(1, "10", "Duna")])
        handler.api.get_metadata = lambda rating_key: (_ for _ in ()).throw(RuntimeError("boom"))

        assert handler.build_index()["catalog"]["movie:10"]["genres"] == []


class TestRecomendacoesColaborativas:
    def _index(self, history, configurar, **config):
        config.setdefault("RECOMMENDATIONS_GENRE_LOOKUP_LIMIT", 0)
        configurar(**config)
        handler = RecommendationsHandler(FakeApiClient(history=history), data_manager=FakeDataManager())
        return handler, handler.build_index()

    def test_sem_historico_nao_ha_recomendacoes(self, app_context, configurar):
        handler, index = self._index([filme(2, "10", "Duna")], configurar)

        resultado = handler.recommend(index, "999")

        assert resultado["sections"] == []
        assert resultado["reason"] == "no_history"

    def test_recomenda_o_que_os_vizinhos_tambem_viram(self, app_context, configurar):
        # A ana viu Duna. A beatriz e o carlos viram Duna E Arrival.
        historico = [
            filme(1, "10", "Duna"),
            filme(2, "10", "Duna"), filme(2, "20", "Arrival"),
            filme(3, "10", "Duna"), filme(3, "20", "Arrival"),
        ]
        handler, index = self._index(historico, configurar, RECOMMENDATIONS_MIN_CO_OCCURRENCE=2)

        resultado = handler.recommend(index, "1")

        assert resultado["reason"] == "ok"
        assert len(resultado["sections"]) == 1
        seccao = resultado["sections"][0]
        assert seccao["seed"]["title"] == "Duna"
        assert [item["title"] for item in seccao["items"]] == ["Arrival"]
        assert seccao["items"][0]["match_type"] == "viewers"
        assert seccao["items"][0]["shared_viewers"] == 2

    def test_nunca_recomenda_o_que_o_utilizador_ja_viu(self, app_context, configurar):
        historico = [
            filme(1, "10", "Duna"), filme(1, "20", "Arrival"),
            filme(2, "10", "Duna"), filme(2, "20", "Arrival"),
            filme(3, "10", "Duna"), filme(3, "20", "Arrival"),
        ]
        handler, index = self._index(historico, configurar)

        resultado = handler.recommend(index, "1")

        sugeridos = {item["key"] for s in resultado["sections"] for item in s["items"]}
        assert sugeridos == set()

    def test_o_minimo_de_utilizadores_em_comum_e_respeitado(self, app_context, configurar):
        historico = [
            filme(1, "10", "Duna"),
            filme(2, "10", "Duna"), filme(2, "20", "Arrival"),
            filme(3, "10", "Duna"),
        ]
        handler, index = self._index(historico, configurar, RECOMMENDATIONS_MIN_CO_OCCURRENCE=2)

        assert handler.recommend(index, "1")["sections"] == []

        handler2, index2 = self._index(historico, configurar, RECOMMENDATIONS_MIN_CO_OCCURRENCE=1)
        assert handler2.recommend(index2, "1")["sections"][0]["items"][0]["title"] == "Arrival"

    def test_um_unico_espectador_nao_gera_recomendacao(self, app_context, configurar):
        # Só a beatriz viu os dois: uma pessoa é coincidência, não é sinal.
        historico = [filme(1, "10", "Duna"), filme(2, "10", "Duna"), filme(2, "20", "Arrival")]
        handler, index = self._index(historico, configurar, RECOMMENDATIONS_MIN_CO_OCCURRENCE=1)

        # 'Duna' tem 2 espectadores (ana e beatriz), por isso passa o mínimo...
        assert handler.recommend(index, "1")["sections"][0]["items"][0]["title"] == "Arrival"

        # ...mas se só a ana tiver visto a semente, não há com quem cruzar.
        handler2, index2 = self._index(
            [filme(1, "10", "Duna"), filme(2, "20", "Arrival")], configurar,
            RECOMMENDATIONS_MIN_CO_OCCURRENCE=1,
        )
        assert handler2.recommend(index2, "1")["sections"] == []

    def test_a_popularidade_bruta_nao_ganha_da_afinidade(self, app_context, configurar):
        """
        'Popular' foi visto por toda a gente; 'Nicho' só por quem viu a semente.
        O cosseno tem de premiar o 'Nicho' — é isso que distingue uma
        recomendação de uma lista dos mais vistos.
        """
        historico = [filme(1, "10", "Semente")]
        # 4 utilizadores viram a semente e o nicho; 20 viram o popular.
        for user_id in range(2, 6):
            historico += [filme(user_id, "10", "Semente"), filme(user_id, "30", "Nicho"), filme(user_id, "20", "Popular")]
        for user_id in range(100, 120):
            historico.append(filme(user_id, "20", "Popular"))

        handler, index = self._index(historico, configurar, RECOMMENDATIONS_MIN_CO_OCCURRENCE=2)
        items = handler.recommend(index, "1")["sections"][0]["items"]

        assert [item["title"] for item in items] == ["Nicho", "Popular"]

    def test_o_mesmo_titulo_nao_se_repete_entre_faixas(self, app_context, configurar):
        historico = [filme(1, "10", "A"), filme(1, "11", "B")]
        for user_id in (2, 3, 4):
            historico += [filme(user_id, "10", "A"), filme(user_id, "11", "B"), filme(user_id, "20", "Comum")]

        handler, index = self._index(historico, configurar, RECOMMENDATIONS_MIN_CO_OCCURRENCE=2)
        seccoes = handler.recommend(index, "1")["sections"]

        chaves = [item["key"] for s in seccoes for item in s["items"]]
        assert len(chaves) == len(set(chaves))

    def test_limites_de_faixas_e_de_itens(self, app_context, configurar):
        historico = [filme(1, "10", "A"), filme(1, "11", "B"), filme(1, "12", "C")]
        for user_id in (2, 3, 4):
            historico += [
                filme(user_id, "10", "A"), filme(user_id, "11", "B"), filme(user_id, "12", "C"),
                filme(user_id, "20", "X"), filme(user_id, "21", "Y"), filme(user_id, "22", "Z"),
            ]

        handler, index = self._index(
            historico, configurar,
            RECOMMENDATIONS_MAX_SECTIONS=2, RECOMMENDATIONS_ITEMS_PER_SECTION=1,
            RECOMMENDATIONS_MIN_CO_OCCURRENCE=2,
        )
        seccoes = handler.recommend(index, "1")["sections"]

        assert len(seccoes) == 2
        assert all(len(s["items"]) == 1 for s in seccoes)

    def test_as_sementes_comecam_pelo_mais_visto(self, app_context, configurar):
        historico = [filme(1, "10", "Pouco"), filme(1, "11", "Muito"), filme(1, "11", "Muito")]
        for user_id in (2, 3):
            historico += [
                filme(user_id, "10", "Pouco"), filme(user_id, "11", "Muito"),
                filme(user_id, "20", "Sugestao"),
            ]

        handler, index = self._index(
            historico, configurar,
            RECOMMENDATIONS_MAX_SECTIONS=1, RECOMMENDATIONS_MIN_CO_OCCURRENCE=2,
        )

        assert handler.recommend(index, "1")["sections"][0]["seed"]["title"] == "Muito"

    def test_nao_expoe_quem_assistiu(self, app_context, configurar):
        historico = [
            filme(1, "10", "Duna"),
            filme(2, "10", "Duna"), filme(2, "20", "Arrival"),
            filme(3, "10", "Duna"), filme(3, "20", "Arrival"),
        ]
        handler, index = self._index(historico, configurar, RECOMMENDATIONS_MIN_CO_OCCURRENCE=2)

        item = handler.recommend(index, "1")["sections"][0]["items"][0]

        # Apenas QUANTOS, nunca QUEM.
        assert item["shared_viewers"] == 2
        assert not any("user" in chave for chave in item)


class TestRecomendacoesPorGenero:
    """Plano B para servidores pequenos, onde não há cruzamento suficiente."""

    def _handler(self, history, metadata):
        return RecommendationsHandler(
            FakeApiClient(history=history, metadata=metadata),
            data_manager=FakeDataManager(),
        )

    def test_completa_com_titulos_do_mesmo_genero(self, app_context, configurar):
        configurar(RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=10, RECOMMENDATIONS_MIN_CO_OCCURRENCE=2)
        handler = self._handler(
            [filme(1, "10", "Duna"), filme(2, "20", "Arrival"), filme(2, "30", "Comédia")],
            metadata={
                "10": {"genres": ["Ficção Científica", "Aventura"]},
                "20": {"genres": ["Ficção Científica", "Aventura"]},
                "30": {"genres": ["Comédia", "Romance"]},
            },
        )
        index = handler.build_index()

        seccao = handler.recommend(index, "1")["sections"][0]

        assert seccao["seed"]["title"] == "Duna"
        assert [item["title"] for item in seccao["items"]] == ["Arrival"]
        assert seccao["items"][0]["match_type"] == "genre"
        assert seccao["items"][0]["shared_genres"] == ["Aventura", "Ficção Científica"]

    def test_um_unico_genero_partilhado_nao_chega(self, app_context, configurar):
        # 'Drama' sozinho descreve metade do catálogo: não é semelhança.
        configurar(RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=10)
        handler = self._handler(
            [filme(1, "10", "Duna"), filme(2, "20", "Outro")],
            metadata={
                "10": {"genres": ["Drama", "Ficção Científica"]},
                "20": {"genres": ["Drama", "Romance"]},
            },
        )

        assert handler.recommend(handler.build_index(), "1")["sections"] == []

    def test_sem_generos_nao_ha_plano_b(self, app_context, configurar):
        configurar(RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=0)
        handler = self._handler([filme(1, "10", "Duna"), filme(2, "20", "Arrival")], metadata={})

        resultado = handler.recommend(handler.build_index(), "1")

        assert resultado["sections"] == []
        assert resultado["reason"] == "not_enough_data"

    def test_o_mesmo_tipo_de_media_vem_primeiro(self, app_context, configurar):
        configurar(RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=10, RECOMMENDATIONS_MIN_CO_OCCURRENCE=99)
        handler = self._handler(
            [
                filme(1, "10", "Duna"),
                filme(2, "20", "Arrival"),
                episodio(2, "77", "Dark"),
            ],
            metadata={
                "10": {"genres": ["Ficção Científica", "Aventura"]},
                "20": {"genres": ["Ficção Científica", "Aventura"]},
                "77": {"genres": ["Ficção Científica", "Aventura"]},
            },
        )

        items = handler.recommend(handler.build_index(), "1")["sections"][0]["items"]

        assert items[0]["media_type"] == "movie"
