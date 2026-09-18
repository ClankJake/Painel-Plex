# tests/test_cache_das_recomendacoes.py
"""⚡ O índice de recomendações: uma construção de cada vez, e nunca no pedido.

Construir o índice é a chamada mais cara das estatísticas — o histórico do
servidor inteiro, mais os metadados dos títulos mais vistos. Enquanto isso era
um `@cache.memoize`, não havia tranca nenhuma: à hora a que ele expirava, toda
a gente que tivesse a página aberta o reconstruía ao mesmo tempo, cada um com a
sua leitura do histórico completo, num painel que corre com UM worker de
propósito.
"""

import threading

import pytest

from app.extensions import cache
from app.services.stats_manager import StatsManager
from tests.conftest import FakeDataManager


class FonteFalsa:
    """Uma fonte de estatísticas que conta quantas vezes foi lida."""

    is_configured = True

    def __init__(self):
        self.construcoes = 0


class GestorDeTeste(StatsManager):
    """Um `StatsManager` cujo `build_index` é contado em vez de ir à rede."""

    def __init__(self, ao_construir=None):
        super().__init__(FakeDataManager(), api_client=FonteFalsa())
        self.construcoes = 0
        self._ao_construir = ao_construir
        self.recommendations.build_index = self._build_index

    def _build_index(self, days=None):
        self.construcoes += 1
        if self._ao_construir:
            self._ao_construir(self)
        return {"catalog": {}, "user_items": {}, "item_users": {},
                "genre_index": {}, "days": days, "volta": self.construcoes}


@pytest.fixture()
def gestor(app_context):
    cache.clear()
    criado = GestorDeTeste()
    yield criado
    criado._apagar_o_indice()


class TestConstrucaoUnica:
    def test_a_segunda_leitura_vem_da_cache(self, gestor):
        gestor.get_recommendation_index()
        gestor.get_recommendation_index()

        assert gestor.construcoes == 1

    def test_dias_diferentes_sao_indices_diferentes(self, gestor):
        gestor.get_recommendation_index(days=30)
        gestor.get_recommendation_index(days=60)
        gestor.get_recommendation_index(days=30)

        assert gestor.construcoes == 2

    def test_none_nao_e_um_numero_qualquer(self, gestor):
        """`days=None` quer dizer "o que estiver no config", e é o que a rota pede."""
        gestor.get_recommendation_index()
        gestor.get_recommendation_index(days=0)

        assert gestor.construcoes == 2


class TestQuemChegaAMeioDaConstrucao:
    """⚡ Levar a cópia anterior em vez de ficar à espera.

    Esperar seria correto e seria péssimo: o custo é uma leitura do histórico do
    servidor inteiro, e a alternativa a recomendações de há meia hora é uma
    página parada. Os testes correm noutra thread de propósito — com o lock
    tomado, um caminho que espere quando não devia BLOQUEIA, e é isso que se
    quer ver falhar.
    """

    @staticmethod
    def _noutra_thread(app, gestor, **kwargs):
        """Chama o índice noutra thread e devolve (resultado, terminou_a_tempo)."""
        resultado = {}
        terminou = threading.Event()

        def _correr():
            with app.app_context():
                try:
                    resultado["indice"] = gestor.get_recommendation_index(**kwargs)
                finally:
                    terminou.set()

        thread = threading.Thread(target=_correr, daemon=True)
        thread.start()
        return resultado, terminou, thread

    def test_recebe_a_copia_anterior(self, app, gestor):
        gestor.get_recommendation_index()             # volta 1: enche a cópia
        cache.delete(gestor._chave_do_indice(None))   # o índice expirou

        with gestor._indice_em_construcao:            # alguém já está a construir
            resultado, terminou, thread = self._noutra_thread(app, gestor)
            assert terminou.wait(timeout=5), "ficou à espera em vez de servir a cópia"

        thread.join(timeout=5)
        assert resultado["indice"]["volta"] == 1
        assert gestor.construcoes == 1

    def test_a_tarefa_de_aquecimento_recusa_a_copia(self, app, gestor):
        """⚠️ Ela existe para CONSTRUIR.

        Aceitar a cópia anterior fazia-a devolver o que já lá estava e deixar a
        cache expirar na mesma, na cara de quem abrisse a página a seguir.
        """
        gestor.get_recommendation_index()
        cache.delete(gestor._chave_do_indice(None))

        with gestor._indice_em_construcao:
            resultado, terminou, thread = self._noutra_thread(
                app, gestor, permitir_copia_anterior=False
            )
            assert not terminou.wait(timeout=1), "levou a cópia em vez de construir"

        thread.join(timeout=5)
        assert resultado["indice"]["volta"] == 2
        assert gestor.construcoes == 2

    def test_quem_espera_nao_reconstroi_o_que_ja_ficou_pronto(self, app, gestor):
        """Uma construção, não uma por pedido: é para isto que o lock existe."""
        with gestor._indice_em_construcao:
            resultado, terminou, thread = self._noutra_thread(app, gestor)
            assert not terminou.wait(timeout=1)
            # O dono do lock termina a construção antes de o largar.
            indice = gestor.recommendations.build_index()
            cache.set(gestor._chave_do_indice(None), indice, timeout=60)

        thread.join(timeout=5)
        assert resultado["indice"]["volta"] == 1
        assert gestor.construcoes == 1


class TestInvalidacao:
    def test_apaga_tambem_a_copia_anterior(self, gestor):
        """⚠️ A cópia foi construída com os parâmetros ANTIGOS.

        Deixá-la ficar depois de o administrador os mudar era exatamente o bug
        que `invalidate_recommendations_cache` existe para não haver.
        """
        gestor.get_recommendation_index()
        chave = gestor._chave_do_indice(None)
        assert cache.get(f"{chave}:anterior") is not None

        gestor.invalidate_recommendations_cache()

        assert cache.get(chave) is None
        assert cache.get(f"{chave}:anterior") is None

    def test_apaga_todas_as_janelas_construidas(self, gestor):
        """Sem `@cache.memoize` não há `delete_memoized` que as encontre."""
        gestor.get_recommendation_index(days=30)
        gestor.get_recommendation_index(days=60)

        gestor.invalidate_recommendations_cache()
        gestor.get_recommendation_index(days=30)
        gestor.get_recommendation_index(days=60)

        assert gestor.construcoes == 4


class TestOLockExiste:
    def test_e_por_instancia_e_cooperativo(self, gestor):
        """Sob gevent o `monkey.patch_all()` troca isto por um lock que cede a vez."""
        assert isinstance(gestor._indice_em_construcao, type(threading.Lock()))
        assert not gestor._indice_em_construcao.locked()


class TestAquecimentoNoAgendador:
    """⚡ Construir o índice deixou de ser trabalho de quem abre a página.

    Enquanto só acontecia DENTRO de um pedido, quem tivesse o azar de apanhar a
    cache fria esperava pela leitura do histórico do servidor inteiro — e num
    painel com um worker gevent esperava com ele toda a gente que lá batesse ao
    mesmo tempo.
    """

    @pytest.fixture()
    def correr(self, app, monkeypatch):
        """Corre a tarefa com um `stats_manager` espião."""
        def _correr(ativas=True, com_fonte=True, rebenta=False):
            from app import extensions, scheduler as scheduler_module

            class GestorEspiao:
                def __init__(self):
                    self.pedidos = []

                def get_recommendation_index(self, **kwargs):
                    self.pedidos.append(kwargs)
                    if rebenta:
                        raise RuntimeError("a fonte não respondeu")
                    return {"catalog": {"movie:1": {}}, "user_items": {"7": {}}}

            gestor = GestorEspiao()
            monkeypatch.setattr(scheduler_module, "_app", app, raising=False)
            monkeypatch.setattr(extensions, "stats_manager", gestor, raising=False)
            monkeypatch.setattr(
                scheduler_module, "load_or_create_config",
                lambda: {"RECOMMENDATIONS_ENABLED": ativas},
            )
            monkeypatch.setattr(
                "app.utils.estatisticas.estatisticas_disponiveis", lambda: com_fonte
            )

            scheduler_module.recommendations_warmup_job()
            return gestor

        return _correr

    def test_constroi_o_indice(self, correr):
        assert correr().pedidos == [{"permitir_copia_anterior": False}]

    def test_desligadas_nao_ha_nada_a_aquecer(self, correr):
        assert correr(ativas=False).pedidos == []

    def test_sem_fonte_nao_ha_nada_a_aquecer(self, correr):
        """🔇 Um painel Plex sem servidor à mão, um Jellyfin em baixo.

        Tentar dava um WARNING de 25 em 25 minutos, para sempre.
        """
        assert correr(com_fonte=False).pedidos == []

    def test_uma_falha_nao_derruba_a_tarefa(self, correr):
        """O pedido seguinte volta a tentar pelo caminho de sempre."""
        assert correr(rebenta=True).pedidos == [{"permitir_copia_anterior": False}]

    def test_esta_registada_no_agendador(self):
        """Uma tarefa escrita e não registada é a otimização que nunca corre."""
        import inspect

        from app import scheduler as scheduler_module

        fonte = inspect.getsource(scheduler_module.setup_scheduler)
        assert "id='recommendations_warmup_job'" in fonte
        assert "AQUECIMENTO_DAS_RECOMENDACOES_EM_MINUTOS" in fonte
        # Mais vezes do que a cache dura, ou fica sempre uma janela em que quem
        # abre a página é o primeiro a pedir o índice.
        assert (scheduler_module.AQUECIMENTO_DAS_RECOMENDACOES_EM_MINUTOS
                < scheduler_module.VALIDADE_DO_INDICE_EM_MINUTOS)


class TestInvalidacaoDepoisDeUmReinicio:
    """⚠️ A cache vive em disco; o registo de chaves é da memória do processo.

    Sem apagar a chave por omissão incondicionalmente, mudar as definições logo
    a seguir a reiniciar o painel não apagava nada — que é exatamente quando
    alguém está a afinar o motor e a perguntar-se porque é que nada muda.
    """

    def test_apaga_o_indice_deixado_por_um_processo_anterior(self, app_context):
        cache.clear()
        anterior = GestorDeTeste()
        anterior.get_recommendation_index()
        chave = anterior._chave_do_indice(None)
        assert cache.get(chave) is not None

        # O painel reiniciou: outro objeto, registo de chaves vazio, mas a
        # cache em disco continua lá.
        depois_do_reinicio = GestorDeTeste()
        assert depois_do_reinicio._chaves_do_indice == set()

        depois_do_reinicio.invalidate_recommendations_cache()

        assert cache.get(chave) is None
        assert cache.get(f"{chave}:anterior") is None
