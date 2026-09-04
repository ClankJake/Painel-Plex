# tests/test_online_media.py
"""Fontes de Mídia Online do Plex: leitura, catálogo e aplicação no aceite."""

import pytest

from app.services.plex import online_media
from app.services.plex.online_media import (
    PlexOnlineMediaManager,
    parse_opt_outs,
    sanitize_source_keys,
)


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Substituto do `requests` que grava os pedidos feitos."""

    def __init__(self, get_response=None, post_response=None, post_fails=()):
        self._get_response = get_response if get_response is not None else FakeResponse("[]")
        self._post_response = post_response or FakeResponse("{}")
        self._post_fails = set(post_fails)
        self.gets = []
        self.posts = []

    def get(self, url, headers=None, timeout=None, **kwargs):
        self.gets.append({"url": url, "headers": headers or {}})
        if isinstance(self._get_response, Exception):
            raise self._get_response
        return self._get_response

    def post(self, url, headers=None, params=None, timeout=None, **kwargs):
        self.posts.append({"url": url, "params": params or {}})
        if (params or {}).get("key") in self._post_fails:
            raise RuntimeError("a Plex recusou o pedido")
        return self._post_response


class FakeAccount:
    def __init__(self, uuid="uuid-123", token="token-abc", username="convidado"):
        self.uuid = uuid
        self.authToken = token
        self.username = username


class FakeConnection:
    def __init__(self, account=None):
        self.account = account


# Resposta real da Plex, copiada do log de produção em 2026-09-04. Fica aqui
# como referência: foi a forma que ninguém tinha — um objeto plano, sem
# `key`/`value` — que fez a funcionalidade não desativar nada em silêncio.
REAL_RESPONSE = (
    '{"tv.plex.provider.music":"opt_out","tv.plex.provider.podcasts":"opt_out",'
    '"tv.plex.provider.webshows":"opt_out","tv.plex.provider.vod":"opt_out",'
    '"tv.plex.provider.epg":"opt_out","scrobbling":"opt_in",'
    '"includeAvailabilities":"opt_out","includeDiscoverSource":"opt_out_managed",'
    '"includeMetadataInSearch":"opt_out_managed","includeSocialProof":"opt_out"}'
)

XML_SOURCES = """<?xml version="1.0" encoding="UTF-8"?>
<optOuts size="2">
  <optOut key="tv.plex.provider.vod" value="opt_in"/>
  <optOut key="tv.plex.provider.music" value="opt_out"/>
</optOuts>"""


def manager_with(session, account=None):
    return PlexOnlineMediaManager(FakeConnection(account), session=session)


@pytest.fixture()
def manager():
    """Gestor sem conta ligada (o catálogo cai para a lista conhecida)."""
    return PlexOnlineMediaManager(FakeConnection(), session=FakeSession())


class TestSanitizacao:
    def test_mantem_chaves_validas_e_remove_duplicados(self):
        chaves = ["tv.plex.provider.vod", "tv.plex.provider.vod", "tv.plex.provider.epg"]

        assert sanitize_source_keys(chaves) == [
            "tv.plex.provider.vod",
            "tv.plex.provider.epg",
        ]

    def test_descarta_valores_nao_textuais_e_caracteres_estranhos(self):
        chaves = ["tv.plex.provider.vod", 42, None, "chave com espaco", "a/b", ""]

        assert sanitize_source_keys(chaves) == ["tv.plex.provider.vod"]

    def test_ignora_entradas_que_nao_sao_lista(self):
        assert sanitize_source_keys("tv.plex.provider.vod") == []
        assert sanitize_source_keys(None) == []

    def test_aplica_um_teto_de_seguranca(self):
        assert len(sanitize_source_keys([f"chave.{i}" for i in range(100)])) == 25


class TestLeituraDaResposta:
    """A forma desta resposta já mudou uma vez; o leitor tem de aguentar variações."""

    def test_le_a_resposta_real_da_plex(self):
        lidas = {e["key"]: e["value"] for e in parse_opt_outs(REAL_RESPONSE)}

        assert len(lidas) == 10
        # As duas que o painel desativa por omissão têm mesmo de estar lá.
        assert lidas["tv.plex.provider.vod"] == "opt_out"
        assert lidas["tv.plex.provider.epg"] == "opt_out"
        assert lidas["scrobbling"] == "opt_in"

    def test_le_objeto_plano_chave_valor(self):
        body = '{"tv.plex.provider.vod": "opt_in", "scrobbling": "opt_out"}'

        assert parse_opt_outs(body) == [
            {"key": "tv.plex.provider.vod", "value": "opt_in"},
            {"key": "scrobbling", "value": "opt_out"},
        ]

    def test_le_xml_plano(self):
        assert parse_opt_outs(XML_SOURCES) == [
            {"key": "tv.plex.provider.vod", "value": "opt_in"},
            {"key": "tv.plex.provider.music", "value": "opt_out"},
        ]

    def test_le_xml_com_as_fontes_aninhadas(self):
        # É exatamente esta forma que o leitor do plexapi deixa passar em branco,
        # por só olhar para os filhos diretos da raiz.
        xml = """<MediaContainer>
          <Settings>
            <optOut key="tv.plex.provider.vod" value="opt_in"/>
          </Settings>
        </MediaContainer>"""

        assert parse_opt_outs(xml) == [{"key": "tv.plex.provider.vod", "value": "opt_in"}]

    def test_le_json_em_lista(self):
        body = '[{"key": "tv.plex.provider.vod", "value": "opt_in"}]'

        assert parse_opt_outs(body) == [{"key": "tv.plex.provider.vod", "value": "opt_in"}]

    def test_le_json_aninhado(self):
        body = '{"MediaContainer": {"optOut": [{"key": "tv.plex.provider.epg", "value": "opt_out"}]}}'

        assert parse_opt_outs(body) == [{"key": "tv.plex.provider.epg", "value": "opt_out"}]

    def test_corpo_vazio_ou_ilegivel_devolve_lista_vazia(self):
        assert parse_opt_outs("") == []
        assert parse_opt_outs("   ") == []
        assert parse_opt_outs("isto não é nem XML nem JSON") == []
        assert parse_opt_outs("{isto tampouco}") == []

    def test_a_mesma_chave_repetida_conta_uma_vez(self):
        body = '{"a": {"key": "x", "value": "opt_in"}, "b": [{"key": "x", "value": "opt_out"}]}'

        assert parse_opt_outs(body) == [{"key": "x", "value": "opt_in"}]


class TestPedidoHttp:
    def test_usa_o_uuid_e_o_token_da_conta(self):
        sessao = FakeSession(FakeResponse(XML_SOURCES))
        gestor = manager_with(sessao)

        gestor.read_sources(FakeAccount(uuid="abc", token="tok"))

        pedido = sessao.gets[0]
        assert pedido["url"] == "https://plex.tv/api/v2/user/abc/settings/opt_outs"
        assert pedido["headers"]["X-Plex-Token"] == "tok"

    def test_conta_sem_uuid_ou_token_falha_de_imediato(self):
        gestor = manager_with(FakeSession())

        with pytest.raises(ValueError):
            gestor.read_sources(FakeAccount(uuid=None))
        with pytest.raises(ValueError):
            gestor.read_sources(FakeAccount(token=None))

    def test_desativar_envia_a_chave_e_o_valor(self):
        sessao = FakeSession(FakeResponse(XML_SOURCES))
        gestor = manager_with(sessao)

        gestor._disable_source(FakeAccount(), "tv.plex.provider.vod")

        assert sessao.posts[0]["params"] == {
            "key": "tv.plex.provider.vod",
            "value": "opt_out",
        }


class TestCatalogo:
    def test_usa_o_catalogo_conhecido_sem_ligacao_ao_plex(self, manager, config_file):
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.epg"])

        catalogo = manager.get_catalog()

        assert catalogo["account_read"] is False
        assert [item["key"] for item in catalogo["sources"]] == online_media.KNOWN_SOURCE_KEYS
        selecionadas = [item["key"] for item in catalogo["sources"] if item["selected"]]
        assert selecionadas == ["tv.plex.provider.epg"]

    def test_le_as_chaves_da_conta_do_admin_quando_ha_ligacao(self, app_context, config_file):
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=[])
        xml = """<optOuts>
          <optOut key="tv.plex.provider.vod" value="opt_in"/>
          <optOut key="tv.plex.provider.novidade" value="opt_in"/>
        </optOuts>"""
        gestor = manager_with(FakeSession(FakeResponse(xml)), FakeAccount())

        catalogo = gestor.get_catalog()

        assert catalogo["account_read"] is True
        # As conhecidas vêm primeiro, na ordem do catálogo; as novas ficam no fim.
        assert [i["key"] for i in catalogo["sources"]] == [
            "tv.plex.provider.vod",
            "tv.plex.provider.novidade",
        ]

    def test_uma_fonte_ja_escolhida_nunca_desaparece_da_lista(self, app_context, config_file):
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.antiga"])
        gestor = manager_with(FakeSession(FakeResponse(XML_SOURCES)), FakeAccount())

        catalogo = {i["key"]: i for i in gestor.get_catalog()["sources"]}

        assert catalogo["tv.plex.provider.antiga"]["selected"] is True
        assert catalogo["tv.plex.provider.antiga"]["available"] is False
        assert catalogo["tv.plex.provider.vod"]["available"] is True

    def test_conta_que_nao_devolve_nada_e_sinalizada(self, app_context, config_file):
        # O caso real observado em produção: a Plex responde 200 mas sem fontes.
        # A interface tem de dizer que a lista mostrada é só o catálogo conhecido.
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=[])
        gestor = manager_with(FakeSession(FakeResponse("<optOuts/>")), FakeAccount())

        catalogo = gestor.get_catalog()

        assert catalogo["account_read"] is False
        assert all(item["available"] for item in catalogo["sources"])

    def test_erro_no_pedido_nao_rebenta_a_pagina(self, app_context, config_file):
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=[])
        gestor = manager_with(FakeSession(RuntimeError("rede em baixo")), FakeAccount())

        catalogo = gestor.get_catalog()

        assert catalogo["account_read"] is False
        assert [i["key"] for i in catalogo["sources"]] == online_media.KNOWN_SOURCE_KEYS

    def test_chave_desconhecida_usa_a_propria_chave_como_nome(self, app_context):
        assert online_media.source_label("tv.plex.provider.novidade") == "tv.plex.provider.novidade"


class TestAplicacaoNoAceite:
    def _config(self, config_file, chaves):
        config_file(
            DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM=True,
            ONLINE_MEDIA_SOURCES_TO_DISABLE=chaves,
        )

    def test_desativa_apenas_as_fontes_configuradas(self, config_file):
        self._config(config_file, ["tv.plex.provider.vod"])
        sessao = FakeSession(FakeResponse(XML_SOURCES))

        resultado = manager_with(sessao).apply_to_account(FakeAccount())

        assert resultado["success"] is True
        assert resultado["disabled"] == ["tv.plex.provider.vod"]
        assert [p["params"]["key"] for p in sessao.posts] == ["tv.plex.provider.vod"]

    def test_nao_faz_nada_quando_a_opcao_esta_desligada(self, config_file):
        config_file(
            DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM=False,
            ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.vod"],
        )
        sessao = FakeSession(FakeResponse(XML_SOURCES))

        resultado = manager_with(sessao).apply_to_account(FakeAccount())

        assert resultado["skipped"] is True
        assert sessao.gets == [] and sessao.posts == []

    def test_nao_repete_o_pedido_para_fontes_ja_desativadas(self, config_file):
        self._config(config_file, ["tv.plex.provider.music"])
        sessao = FakeSession(FakeResponse(XML_SOURCES))

        resultado = manager_with(sessao).apply_to_account(FakeAccount())

        assert resultado["already_disabled"] == ["tv.plex.provider.music"]
        assert sessao.posts == []

    def test_uma_fonte_que_falha_nao_impede_as_restantes(self, config_file):
        self._config(config_file, ["tv.plex.provider.vod", "tv.plex.provider.news"])
        xml = """<optOuts>
          <optOut key="tv.plex.provider.vod" value="opt_in"/>
          <optOut key="tv.plex.provider.news" value="opt_in"/>
        </optOuts>"""
        sessao = FakeSession(FakeResponse(xml), post_fails=["tv.plex.provider.vod"])

        resultado = manager_with(sessao).apply_to_account(FakeAccount())

        assert resultado["success"] is False
        assert resultado["failed"] == ["tv.plex.provider.vod"]
        assert resultado["disabled"] == ["tv.plex.provider.news"]

    def test_a_resposta_real_reconhece_as_chaves_por_omissao(self, config_file):
        # O caso que falhou em produção: com a resposta real, nem 'vod' nem
        # 'epg' podem voltar a ser dadas como inexistentes na conta.
        self._config(config_file, ["tv.plex.provider.vod", "tv.plex.provider.epg"])
        sessao = FakeSession(FakeResponse(REAL_RESPONSE))

        resultado = manager_with(sessao).apply_to_account(FakeAccount())

        assert resultado["missing"] == []
        assert sorted(resultado["already_disabled"]) == [
            "tv.plex.provider.epg",
            "tv.plex.provider.vod",
        ]

    def test_chave_inexistente_na_conta_e_reportada(self, config_file):
        self._config(config_file, ["tv.plex.provider.renomeada"])
        sessao = FakeSession(FakeResponse(XML_SOURCES))

        resultado = manager_with(sessao).apply_to_account(FakeAccount())

        assert resultado["missing"] == ["tv.plex.provider.renomeada"]
        assert resultado["disabled"] == []
        assert sessao.posts == []

    def test_erro_de_leitura_devolve_falha_sem_levantar_excecao(self, config_file, app_context):
        self._config(config_file, ["tv.plex.provider.vod"])
        sessao = FakeSession(RuntimeError("token inválido"))

        resultado = manager_with(sessao).apply_to_account(FakeAccount())

        assert resultado["success"] is False
        assert resultado["disabled"] == []

    def test_chaves_explicitas_ignoram_a_configuracao(self, config_file):
        config_file(DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM=False)
        sessao = FakeSession(FakeResponse(XML_SOURCES))

        resultado = manager_with(sessao).apply_to_account(
            FakeAccount(), keys=["tv.plex.provider.vod"]
        )

        assert resultado["disabled"] == ["tv.plex.provider.vod"]


class TestGanchoNoConvite:
    """O aceite do convite nunca pode falhar por causa desta preferência."""

    def _invite_manager(self, online_media_manager):
        from app.services.plex.invite_manager import PlexInviteManager

        class FakeFacade:
            online_media = online_media_manager

        return PlexInviteManager(None, None, None, FakeFacade(), None, None)

    def test_uma_excecao_do_gestor_e_engolida(self):
        class Explosivo:
            def apply_to_account(self, user_account):
                raise RuntimeError("boom")

        gestor = self._invite_manager(Explosivo())

        gestor._apply_online_media_preferences(FakeAccount())  # não deve levantar

    def test_sem_gestor_configurado_nao_faz_nada(self):
        from app.services.plex.invite_manager import PlexInviteManager

        class FacadeSemGestor:
            pass

        gestor = PlexInviteManager(None, None, None, FacadeSemGestor(), None, None)

        gestor._apply_online_media_preferences(FakeAccount())  # não deve levantar
