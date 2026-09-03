# tests/test_online_media.py
"""Fontes de Mídia Online do Plex: catálogo, sanitização e aplicação no aceite."""

import pytest

from app.services.plex import online_media
from app.services.plex.online_media import PlexOnlineMediaManager, sanitize_source_keys


class FakeSource:
    """Substituto de plexapi.myplex.AccountOptOut."""

    def __init__(self, key, value="opt_in", fail=False):
        self.key = key
        self.value = value
        self.fail = fail
        self.opt_out_calls = 0

    def optOut(self):
        self.opt_out_calls += 1
        if self.fail:
            raise RuntimeError("a Plex recusou o pedido")
        self.value = "opt_out"


class FakeAccount:
    def __init__(self, sources, raises=False):
        self.username = "convidado"
        self._sources = sources
        self._raises = raises

    def onlineMediaSources(self):
        if self._raises:
            raise RuntimeError("token inválido")
        return self._sources


class FakeConnection:
    def __init__(self, account=None):
        self.account = account


@pytest.fixture()
def manager():
    return PlexOnlineMediaManager(FakeConnection())


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


class TestCatalogo:
    def test_usa_o_catalogo_conhecido_sem_ligacao_ao_plex(self, manager, config_file):
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.epg"])

        catalogo = manager.get_catalog()
        chaves = [item["key"] for item in catalogo]

        assert chaves == online_media.KNOWN_SOURCE_KEYS
        selecionadas = [item["key"] for item in catalogo if item["selected"]]
        assert selecionadas == ["tv.plex.provider.epg"]

    def test_le_as_chaves_da_conta_do_admin_quando_ha_ligacao(self, app_context, config_file):
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=[])
        conta = FakeAccount([FakeSource("tv.plex.provider.vod"), FakeSource("tv.plex.provider.novidade")])
        gestor = PlexOnlineMediaManager(FakeConnection(conta))

        chaves = [item["key"] for item in gestor.get_catalog()]

        # As conhecidas vêm primeiro, na ordem do catálogo; as novas ficam no fim.
        assert chaves == ["tv.plex.provider.vod", "tv.plex.provider.novidade"]

    def test_uma_fonte_ja_escolhida_nunca_desaparece_da_lista(self, app_context, config_file):
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.antiga"])
        conta = FakeAccount([FakeSource("tv.plex.provider.vod")])
        gestor = PlexOnlineMediaManager(FakeConnection(conta))

        catalogo = gestor.get_catalog()
        antiga = next(item for item in catalogo if item["key"] == "tv.plex.provider.antiga")

        assert antiga["selected"] is True

    def test_chave_desconhecida_usa_a_propria_chave_como_nome(self, app_context):
        assert online_media.source_label("tv.plex.provider.novidade") == "tv.plex.provider.novidade"

    def test_marca_como_indisponivel_a_chave_que_a_conta_nao_reconhece(self, app_context, config_file):
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.renomeada"])
        conta = FakeAccount([FakeSource("tv.plex.provider.vod")])
        gestor = PlexOnlineMediaManager(FakeConnection(conta))

        catalogo = {item["key"]: item for item in gestor.get_catalog()}

        assert catalogo["tv.plex.provider.vod"]["available"] is True
        assert catalogo["tv.plex.provider.renomeada"]["available"] is False

    def test_sem_ligacao_ao_plex_nada_e_acusado_de_indisponivel(self, manager, config_file):
        # Sem resposta da conta não sabemos o que existe: acusar aqui seria
        # apontar o dedo a chaves perfeitamente válidas.
        config_file(ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.renomeada"])

        assert all(item["available"] for item in manager.get_catalog())


class TestAplicacaoNoAceite:
    def test_desativa_apenas_as_fontes_configuradas(self, manager, config_file):
        config_file(
            DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM=True,
            ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.vod", "tv.plex.provider.epg"],
        )
        vod = FakeSource("tv.plex.provider.vod")
        epg = FakeSource("tv.plex.provider.epg")
        musica = FakeSource("tv.plex.provider.music")

        resultado = manager.apply_to_account(FakeAccount([vod, epg, musica]))

        assert resultado["success"] is True
        assert sorted(resultado["disabled"]) == ["tv.plex.provider.epg", "tv.plex.provider.vod"]
        assert vod.value == "opt_out" and epg.value == "opt_out"
        assert musica.opt_out_calls == 0
        assert musica.value == "opt_in"

    def test_nao_faz_nada_quando_a_opcao_esta_desligada(self, manager, config_file):
        config_file(
            DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM=False,
            ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.vod"],
        )
        vod = FakeSource("tv.plex.provider.vod")

        resultado = manager.apply_to_account(FakeAccount([vod]))

        assert resultado["skipped"] is True
        assert vod.opt_out_calls == 0

    def test_nao_repete_o_pedido_para_fontes_ja_desativadas(self, manager, config_file):
        config_file(
            DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM=True,
            ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.vod"],
        )
        vod = FakeSource("tv.plex.provider.vod", value="opt_out")

        resultado = manager.apply_to_account(FakeAccount([vod]))

        assert vod.opt_out_calls == 0
        assert resultado["already_disabled"] == ["tv.plex.provider.vod"]
        assert resultado["disabled"] == []

    def test_uma_fonte_que_falha_nao_impede_as_restantes(self, manager, config_file):
        config_file(
            DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM=True,
            ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.vod", "tv.plex.provider.epg"],
        )
        vod = FakeSource("tv.plex.provider.vod", fail=True)
        epg = FakeSource("tv.plex.provider.epg")

        resultado = manager.apply_to_account(FakeAccount([vod, epg]))

        assert resultado["success"] is False
        assert resultado["failed"] == ["tv.plex.provider.vod"]
        assert resultado["disabled"] == ["tv.plex.provider.epg"]

    def test_erro_de_leitura_devolve_falha_sem_levantar_excecao(self, manager, config_file, app_context):
        config_file(
            DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM=True,
            ONLINE_MEDIA_SOURCES_TO_DISABLE=["tv.plex.provider.vod"],
        )

        resultado = manager.apply_to_account(FakeAccount([], raises=True))

        assert resultado["success"] is False
        assert resultado["disabled"] == []

    def test_chaves_explicitas_ignoram_a_configuracao(self, manager, config_file):
        config_file(DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM=False)
        vod = FakeSource("tv.plex.provider.vod")

        resultado = manager.apply_to_account(FakeAccount([vod]), keys=["tv.plex.provider.vod"])

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

        gestor._apply_online_media_preferences(FakeAccount([]))  # não deve levantar

    def test_sem_gestor_configurado_nao_faz_nada(self):
        class FacadeSemGestor:
            pass

        from app.services.plex.invite_manager import PlexInviteManager

        gestor = PlexInviteManager(None, None, None, FacadeSemGestor(), None, None)

        gestor._apply_online_media_preferences(FakeAccount([]))  # não deve levantar
