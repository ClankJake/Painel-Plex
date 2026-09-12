# tests/test_jellyfin_playback_reporting.py
"""Histórico por reprodução, vindo do plugin Playback Reporting.

O núcleo do Jellyfin só guarda, por item, a data da última vez que foi visto.
Com o plugin instalado há um registo de cada REPRODUÇÃO — três vezes o mesmo
episódio dá três linhas, e cada uma diz em que aparelho foi vista, que é a
coluna que o núcleo deixava vazia.

⚠️ O plugin não tem rota de listagem paginada: a única forma de ler a tabela é
`submit_custom_query`, que corre SQL cru e NÃO aceita parâmetros ligados. Por
isso metade destes testes é sobre o que acontece ao texto que vem de fora.
"""

import pytest

from app.services.media_server.jellyfin.api_client import JellyfinApiError
from app.services.media_server.jellyfin.playback_reporting import _literal_sql
from tests.test_jellyfin_backend import GUID, OUTRO, ApiFalsa, montar

pytestmark = pytest.mark.integration

CONSULTA = '/user_usage_stats/submit_custom_query'


@pytest.fixture()
def cache_limpa(app_context):
    """A disponibilidade do plugin fica em cache (partilhada, em disco)."""
    from app.extensions import cache

    cache.clear()
    yield cache
    cache.clear()


def _linha(data="2026-09-12 08:42:29", item_id="item-1", tipo="Movie",
           nome="Duna", cliente="Jellyfin Web", aparelho="Chrome", duracao=3600):
    """Uma linha da PlaybackActivity, na ordem em que a consulta a pede."""
    return [data, item_id, tipo, nome, cliente, aparelho, duracao]


def _responder(linhas, total=None):
    """O plugin responde à contagem e às linhas no MESMO endpoint."""
    def responde(corpo):
        sql = (corpo or {}).get('CustomQueryString', '')
        if 'COUNT(*)' in sql:
            return {"colums": ["COUNT(*)"], "results": [[total if total is not None else len(linhas)]], "message": ""}
        return {"colums": [], "results": [list(l) for l in linhas], "message": ""}
    return responde


def _backend(linhas=None, total=None, itens=None, instalado=True, **extra):
    respostas = {
        '/Plugins': [{"Name": "Playback Reporting", "Id": "x"}] if instalado else [],
        CONSULTA: _responder(linhas if linhas is not None else [], total),
        '/Items': {"Items": itens or [], "TotalRecordCount": len(itens or [])},
    }
    respostas.update(extra)
    return montar(respostas)


def _sql_das_linhas(backend):
    """A consulta que pediu as LINHAS (não a contagem)."""
    envios = [c['CustomQueryString'] for c in backend.conn.api.corpos_enviados(CONSULTA)]
    return next(s for s in envios if 'COUNT(*)' not in s)


class TestDeteccao:
    def test_sem_o_plugin_cai_para_o_historico_do_nucleo(self, cache_limpa):
        # Cair para o núcleo é sempre melhor do que uma página vazia.
        backend = _backend(instalado=False, itens=[{
            "Id": "item-1", "Name": "Duna", "Type": "Movie", "ProductionYear": 2021,
            "UserData": {"LastPlayedDate": "2026-09-12T08:42:29Z", "Played": True},
        }])

        resultado = backend.get_watch_history(GUID)

        assert resultado["history"][0]["title"] == "Duna"
        # O do núcleo não sabe o aparelho.
        assert resultado["history"][0]["player"] == ""
        assert backend.conn.api.corpos_enviados(CONSULTA) == []

    def test_com_o_plugin_o_historico_vem_dele(self, cache_limpa):
        backend = _backend([_linha()])

        assert backend.get_watch_history(GUID)["history"][0]["player"] == "Jellyfin Web (Chrome)"

    def test_a_deteccao_e_perguntada_uma_vez_so(self, cache_limpa):
        # Sem cache, cada página do histórico pagava uma chamada extra.
        backend = _backend([_linha()])

        backend.get_watch_history(GUID)
        backend.get_watch_history(GUID)

        assert [e for e in backend.conn.api.enviados if e[1] == '/Plugins'].__len__() == 1

    def test_uma_falha_a_listar_plugins_nao_rebenta(self, cache_limpa):
        backend = _backend([_linha()], **{'/Plugins': None})
        backend.conn.api.erros['/Plugins'] = JellyfinApiError("boom", status_code=500)

        assert backend.get_watch_history(GUID)["success"] is True

    def test_uma_falha_a_listar_plugins_nao_fica_em_cache(self, cache_limpa):
        """
        🐛 Não saber não é o mesmo que não existir. Gravar o "não" de uma falha
        de rede deixava o histórico dez minutos no registo do núcleo — sem
        aparelho na coluna do reprodutor, e sem razão nenhuma.
        """
        backend = _backend([_linha()], **{'/Plugins': None})
        backend.conn.api.erros['/Plugins'] = JellyfinApiError("boom", status_code=500)
        backend.get_watch_history(GUID)

        # O servidor volta: a pergunta é feita de novo e o plugin é usado.
        del backend.conn.api.erros['/Plugins']
        backend.conn.api.respostas['/Plugins'] = [{"Name": "Playback Reporting"}]

        assert backend.get_watch_history(GUID)["history"][0]["player"] == "Jellyfin Web (Chrome)"

    def test_o_plugin_a_falhar_a_meio_cai_para_o_nucleo(self, cache_limpa):
        # 🐛 Devolver a página vazia seria dizer que a pessoa nunca viu nada.
        backend = _backend([], itens=[{
            "Id": "item-1", "Name": "Duna", "Type": "Movie",
            "UserData": {"LastPlayedDate": "2026-09-12T08:42:29Z", "Played": True},
        }])
        backend.conn.api.erros[CONSULTA] = JellyfinApiError("sem tabela", status_code=500)

        resultado = backend.get_watch_history(GUID)

        assert resultado["success"] is True
        assert resultado["history"][0]["title"] == "Duna"


class TestSegurancaDaConsulta:
    """
    🛡️ `submit_custom_query` corre SQL CRU e não aceita parâmetros ligados. O
    id do utilizador e o texto de pesquisa chegam de fora — um vem da sessão,
    o outro de uma caixa de texto. Nada disto entra na consulta em bruto.
    """

    @pytest.mark.parametrize("ataque", [
        "'; DROP TABLE PlaybackActivity;--",
        "' OR 1=1 --",
        "'; ATTACH DATABASE '/tmp/x.db' AS x;--",
        "duna' UNION SELECT 1,2,3,4,5,6,7--",
    ])
    def test_a_pesquisa_nao_consegue_sair_do_literal(self, cache_limpa, ataque):
        """Corre o SQL gerado num SQLite de verdade e vê o que ele faz.

        Contar plicas ou procurar 'DROP TABLE' no texto não prova nada: a
        palavra ESTÁ lá, dentro do literal, e é inofensiva. O que se quer saber
        é se o SQLite a executa — e para isso é preciso executá-la.
        """
        import sqlite3

        backend = _backend([])
        backend.get_watch_history(GUID, search=ataque)
        sql = _sql_das_linhas(backend)

        ligacao = sqlite3.connect(":memory:")
        ligacao.execute(
            "create table PlaybackActivity (DateCreated DATETIME NOT NULL, UserId TEXT, "
            "ItemId TEXT, ItemType TEXT, ItemName TEXT, PlaybackMethod TEXT, "
            "ClientName TEXT, DeviceName TEXT, PlayDuration INT)"
        )
        ligacao.execute(
            "insert into PlaybackActivity values ('2026-09-12', ?, 'i', 'Movie', 'Duna', 'd', 'c', 'd', 1)",
            (OUTRO,),
        )

        # `execute` recusa mais do que um comando: se o ataque tivesse fechado
        # a string, isto levantaria `Warning: You can only execute one statement`.
        linhas = ligacao.execute(sql).fetchall()

        # A tabela continua de pé e a linha de OUTRO não vazou para este.
        assert ligacao.execute("select count(*) from PlaybackActivity").fetchone()[0] == 1
        assert linhas == []

    def test_um_id_que_nao_parece_um_guid_e_recusado_antes_do_sql(self, cache_limpa):
        # Não é um engano de escrita: é um ataque. Nem se tenta a consulta.
        backend = _backend([], itens=[])

        backend.get_watch_history("1' OR '1'='1")

        assert backend.conn.api.corpos_enviados(CONSULTA) == []

    def test_um_id_recusado_cai_para_o_nucleo_em_vez_de_dar_erro(self, cache_limpa):
        backend = _backend([], itens=[])

        assert backend.get_watch_history("' OR 1=1 --")["success"] is True

    @pytest.mark.parametrize("entrada,esperado", [
        ("duna", "duna"),
        ("d'una", "d''una"),
        ("x" * 300, "x" * 100),
        ("linha\nnova", "linhanova"),
        (None, ""),
    ])
    def test_o_escape_do_texto(self, entrada, esperado):
        assert _literal_sql(entrada) == esperado

    def test_a_consulta_filtra_pelo_utilizador_pedido(self, cache_limpa):
        # O histórico é privado: sem isto, a paginação de um mostrava o do outro.
        backend = _backend([])

        backend.get_watch_history(OUTRO)

        assert f"UserId = '{OUTRO}'" in _sql_das_linhas(backend)


class TestLinhas:
    def test_a_mesma_midia_vista_duas_vezes_da_duas_linhas(self, cache_limpa):
        # É a razão de ser do plugin: o núcleo dava uma linha só.
        backend = _backend([
            _linha(data="2026-09-12 20:00:00", aparelho="Chrome"),
            _linha(data="2026-09-11 20:00:00", aparelho="Android TV"),
        ], total=2)

        historico = backend.get_watch_history(GUID)["history"]

        assert len(historico) == 2
        assert [l["player"] for l in historico] == ["Jellyfin Web (Chrome)", "Jellyfin Web (Android TV)"]

    def test_o_cliente_sozinho_quando_o_aparelho_repete_o_nome(self, cache_limpa):
        backend = _backend([_linha(cliente="Kodi", aparelho="Kodi")])

        assert backend.get_watch_history(GUID)["history"][0]["player"] == "Kodi"

    @pytest.mark.parametrize("cliente,aparelho,esperado", [
        ("Jellyfin Web", "", "Jellyfin Web"),
        ("", "Chrome", "Chrome"),
        (None, None, ""),
    ])
    def test_o_reprodutor_com_campos_em_falta(self, cache_limpa, cliente, aparelho, esperado):
        backend = _backend([_linha(cliente=cliente, aparelho=aparelho)])

        assert backend.get_watch_history(GUID)["history"][0]["player"] == esperado

    def test_o_episodio_separa_a_serie_do_resto(self, cache_limpa):
        # O plugin grava "Série - S01E02 - Título" numa só coluna.
        backend = _backend([_linha(tipo="Episode", nome="Dark - S02E05 - Segredos")])

        linha = backend.get_watch_history(GUID)["history"][0]

        assert linha["title"] == "Dark"
        assert linha["subtitle"] == "S02E05 - Segredos"

    def test_a_data_do_plugin_e_legivel(self, cache_limpa):
        backend = _backend([_linha(data="2026-09-12 08:42:29")])

        assert backend.get_watch_history(GUID)["history"][0]["date"] == "12/09/2026 08:42"


class TestPercentagem:
    """
    O plugin guarda os SEGUNDOS vistos, não a percentagem — e é melhor assim:
    a do núcleo é a do item, e seria a mesma nas três vezes que se viu o mesmo
    episódio. A duração vem numa segunda chamada, uma por página.
    """

    def _com_duracao(self, duracao_s, ticks=72_000_000_000):
        # 72 000 000 000 ticks = 7200 s = 2 horas.
        return _backend(
            [_linha(duracao=duracao_s)],
            itens=[{"Id": "item-1", "Name": "Duna", "Type": "Movie", "RunTimeTicks": ticks}],
        )

    def test_metade_do_filme(self, cache_limpa):
        assert self._com_duracao(3600).get_watch_history(GUID)["history"][0]["percent_complete"] == 50

    def test_nunca_passa_dos_cem(self, cache_limpa):
        # Rever uma parte faz o tempo visto ultrapassar a duração.
        assert self._com_duracao(9000).get_watch_history(GUID)["history"][0]["percent_complete"] == 100

    def test_sem_a_duracao_do_item_nao_se_inventa(self, cache_limpa):
        # O item foi apagado da biblioteca: a linha fica, a percentagem não.
        backend = _backend([_linha(duracao=3600)], itens=[])

        assert backend.get_watch_history(GUID)["history"][0]["percent_complete"] == 0

    @pytest.mark.parametrize("duracao", [None, 0, "", "texto"])
    def test_uma_duracao_ilegivel_nao_rebenta(self, cache_limpa, duracao):
        assert self._com_duracao(duracao).get_watch_history(GUID)["history"][0]["percent_complete"] == 0


class TestItensApagados:
    def test_a_linha_sobrevive_ao_item_deixar_de_existir(self, cache_limpa):
        # O plugin guarda o nome em texto; é o que resta quando o ficheiro sai.
        backend = _backend([_linha(nome="Filme Antigo")], itens=[])

        linha = backend.get_watch_history(GUID)["history"][0]

        assert linha["title"] == "Filme Antigo"
        assert linha["poster_url"] is None

    def test_o_nome_atual_da_biblioteca_ganha_ao_que_o_plugin_gravou(self, cache_limpa):
        # Um item renomeado na biblioteca mostra o nome novo.
        backend = _backend(
            [_linha(nome="Nome Antigo")],
            itens=[{"Id": "item-1", "Name": "Duna", "Type": "Movie", "ProductionYear": 2021}],
        )

        assert backend.get_watch_history(GUID)["history"][0]["title"] == "Duna"

    def test_os_detalhes_sao_pedidos_numa_chamada_so(self, cache_limpa):
        # Uma chamada por linha seria 15 pedidos por página.
        backend = _backend([_linha(item_id="a"), _linha(item_id="b"), _linha(item_id="a")], total=3)

        backend.get_watch_history(GUID)

        pedidos = [e for e in backend.conn.api.enviados if e[1] == '/Items']
        assert len(pedidos) == 1
        assert backend.conn.api.ultimos_params['/Items']['ids'] == ['a', 'b']


class TestPaginacao:
    def test_a_pagina_vira_limit_e_offset(self, cache_limpa):
        backend = _backend([], total=31)

        backend.get_watch_history(GUID, page=3, length=15)

        sql = _sql_das_linhas(backend)
        assert "LIMIT 15 OFFSET 30" in sql

    def test_o_total_vem_da_contagem_do_plugin(self, cache_limpa):
        backend = _backend([_linha()], total=31)

        assert backend.get_watch_history(GUID, page=2, length=15)["pagination"] == {
            "current_page": 2, "total_pages": 3, "total_records": 31,
        }

    def test_as_linhas_vem_da_mais_recente_para_a_mais_antiga(self, cache_limpa):
        backend = _backend([])

        backend.get_watch_history(GUID)

        assert "ORDER BY DateCreated DESC" in _sql_das_linhas(backend)


class TestRespostasMalFormadas:
    def test_um_erro_de_sqlite_dentro_de_um_200_nao_passa_por_lista_vazia(self, cache_limpa):
        """
        🐛 O plugin devolve o erro do SQLite na chave `message` de uma resposta
        200. Sem olhar para ela, uma consulta inválida passava por "este
        utilizador nunca viu nada" — e ninguém dava por isso.
        """
        backend = montar({
            '/Plugins': [{"Name": "Playback Reporting"}],
            CONSULTA: {"colums": [], "results": [], "message": "SQL error: no such table"},
            '/Items': {"Items": [], "TotalRecordCount": 0},
        })

        # Cai para o núcleo, que responde honestamente com uma lista vazia.
        resultado = backend.get_watch_history(GUID)

        assert resultado["success"] is True
        assert resultado["pagination"]["total_records"] == 0

    def test_uma_contagem_que_nao_se_percebe_nao_vira_zero(self, cache_limpa):
        backend = montar({
            '/Plugins': [{"Name": "Playback Reporting"}],
            CONSULTA: {"colums": [], "results": "não é uma lista", "message": ""},
            '/Items': {"Items": [], "TotalRecordCount": 0},
        })

        assert backend.get_watch_history(GUID)["success"] is True

    def test_uma_linha_mais_curta_do_que_o_esperado_nao_rebenta(self, cache_limpa):
        backend = _backend([["2026-09-12 08:42:29", "item-1"]])

        assert backend.get_watch_history(GUID)["history"][0]["date"] == "12/09/2026 08:42"
