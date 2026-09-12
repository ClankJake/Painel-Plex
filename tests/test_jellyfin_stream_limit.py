# tests/test_jellyfin_stream_limit.py
"""Limite de telas imposto pelo SERVIDOR, via o plugin StreamLimiter.

Foi a peça que faltou durante toda a integração do Jellyfin. O núcleo não sabe
limitar reproduções (o `MaxActiveSessions` limita autenticações), e a ordem de
parar depende de o cliente obedecer — o leitor integrado da aplicação Android
não obedece. O plugin recusa o PEDIDO HTTP da mídia antes de servir um byte,
e isso nenhum cliente pode ignorar.

O painel continua a decidir o limite; o plugin passa a fazê-lo cumprir.
"""

import pytest

from tests.test_jellyfin_backend import GUID, OUTRO, montar
from app.services.media_server.jellyfin.api_client import JellyfinApiError

pytestmark = pytest.mark.integration

DEFINIR = '/StreamLimit/SetUserStreamLimit'
LISTAR = '/StreamLimit/GetAllStreamLimits'
ID_DO_PLUGIN = 'd98fbe02-daf3-4c09-a832-4b4e1d07326c'


@pytest.fixture()
def cache_limpa(app_context):
    """A disponibilidade do plugin fica em cache (partilhada, em disco)."""
    from app.extensions import cache

    cache.clear()
    yield cache
    cache.clear()


def _backend(instalado=True, limites=None, padrao=0, data_manager=None, **extra):
    respostas = {
        '/Plugins': [{"Id": ID_DO_PLUGIN, "Name": "StreamLimiter"}] if instalado else [],
        LISTAR: {"defaultMaxStreams": padrao, "limits": limites if limites is not None else {}},
        DEFINIR: None,
    }
    respostas.update(extra)
    return montar(respostas, data_manager=data_manager)


class TestDeteccao:
    def test_reconhece_o_plugin_pelo_identificador(self, cache_limpa):
        # O GUID é estável; o nome é o que se lê no log de quem depurar isto.
        backend = montar({'/Plugins': [{"Id": ID_DO_PLUGIN, "Name": "Outro Nome"}]})

        assert backend.stream_limit.esta_disponivel() is True

    def test_reconhece_o_plugin_pelo_nome(self, cache_limpa):
        backend = montar({'/Plugins': [{"Id": "outro-id", "Name": "StreamLimiter"}]})

        assert backend.stream_limit.esta_disponivel() is True

    def test_outro_plugin_qualquer_nao_conta(self, cache_limpa):
        backend = montar({'/Plugins': [{"Id": "x", "Name": "Playback Reporting"}]})

        assert backend.stream_limit.esta_disponivel() is False

    def test_a_pergunta_e_feita_uma_vez_so(self, cache_limpa):
        backend = _backend()

        backend.stream_limit.esta_disponivel()
        backend.stream_limit.esta_disponivel()

        assert len([e for e in backend.conn.api.enviados if e[1] == '/Plugins']) == 1

    def test_uma_falha_de_rede_nao_fica_em_cache(self, cache_limpa):
        """
        🐛 Não saber não é o mesmo que não existir: gravar o "não" de uma falha
        deixava o limite dez minutos sem ninguém a impô-lo.
        """
        backend = _backend()
        backend.conn.api.erros['/Plugins'] = JellyfinApiError("boom", status_code=500)
        assert backend.stream_limit.esta_disponivel() is False

        del backend.conn.api.erros['/Plugins']
        assert backend.stream_limit.esta_disponivel() is True


class TestEscritaDoLimite:
    def test_o_limite_vai_para_o_plugin(self, cache_limpa, data_manager):
        backend = _backend(data_manager=data_manager)
        data_manager.set_user_profile(GUID, {"username": "ana"})

        backend.update_screen_limit(GUID, 2)

        assert backend.conn.api.ultimos_params[DEFINIR] == {'userId': GUID, 'streamsAllowed': 2}

    def test_o_perfil_local_continua_a_ser_gravado(self, cache_limpa, data_manager):
        # O painel continua a ser quem decide — e quem corta, que o plugin não
        # sabe nada de assinaturas vencidas nem de bloqueios.
        backend = _backend(data_manager=data_manager)
        data_manager.set_user_profile(GUID, {"username": "ana"})

        backend.update_screen_limit(GUID, 2)

        assert data_manager.get_user_profile(GUID)['screen_limit'] == 2

    def test_sem_o_plugin_grava_o_perfil_e_nao_tenta_o_servidor(self, cache_limpa, data_manager):
        backend = _backend(instalado=False, data_manager=data_manager)
        data_manager.set_user_profile(GUID, {"username": "ana"})

        backend.update_screen_limit(GUID, 2)

        assert data_manager.get_user_profile(GUID)['screen_limit'] == 2
        assert DEFINIR not in backend.conn.api.ultimos_params

    def test_uma_recusa_do_plugin_nao_impede_a_gravacao_local(self, cache_limpa, data_manager):
        # O limite do painel tem de valer mesmo que o plugin esteja com problemas.
        backend = _backend(data_manager=data_manager)
        backend.conn.api.erros[DEFINIR] = JellyfinApiError("boom", status_code=500)
        data_manager.set_user_profile(GUID, {"username": "ana"})

        backend.update_screen_limit(GUID, 2)

        assert data_manager.get_user_profile(GUID)['screen_limit'] == 2

    def test_um_utilizador_que_ja_nao_existe_no_servidor_nao_e_um_erro(self, cache_limpa, data_manager):
        backend = _backend(data_manager=data_manager)
        backend.conn.api.erros[DEFINIR] = JellyfinApiError("não existe", status_code=404)

        assert backend.stream_limit.definir_limite(GUID, 2) is False

    def test_um_identificador_vazio_nao_chega_ao_plugin(self, cache_limpa):
        backend = _backend()

        assert backend.stream_limit.definir_limite(None, 2) is False
        assert DEFINIR not in backend.conn.api.ultimos_params

    @pytest.mark.parametrize("entrada,esperado", [(None, 0), (0, 0), (-3, 0), ("4", 4)])
    def test_o_valor_enviado_e_sempre_um_inteiro_nao_negativo(self, cache_limpa, entrada, esperado):
        backend = _backend()

        backend.stream_limit.definir_limite(GUID, entrada)

        assert backend.conn.api.ultimos_params[DEFINIR]['streamsAllowed'] == esperado


class TestOZeroQuerDizerCoisasDiferentes:
    """
    ⚠️ No painel, 0 é ILIMITADO. No plugin, 0 apaga o limite próprio e passa a
    valer o `DefaultMaxStreams` do servidor — que, existindo, é um limite e não
    a ausência dele. Não há como dizer "sem limite para esta pessoa" enquanto o
    padrão existir, por isso avisa-se em vez de fingir que ficou aplicado.
    """

    def test_sem_padrao_no_plugin_o_zero_e_mesmo_ilimitado(self, cache_limpa, caplog):
        backend = _backend(padrao=0)

        with caplog.at_level("WARNING"):
            backend.stream_limit.definir_limite(GUID, 0)

        assert "limite padrão" not in caplog.text

    def test_com_padrao_no_plugin_o_ilimitado_do_painel_e_avisado(self, cache_limpa, caplog):
        backend = _backend(padrao=2)

        with caplog.at_level("WARNING"):
            backend.stream_limit.definir_limite(GUID, 0)

        assert "limite padrão" in caplog.text
        # E o valor vai à mesma: é o mais perto de "ilimitado" que dá para dizer.
        assert backend.conn.api.ultimos_params[DEFINIR]['streamsAllowed'] == 0

    def test_um_limite_normal_nao_gera_aviso(self, cache_limpa, caplog):
        backend = _backend(padrao=2)

        with caplog.at_level("WARNING"):
            backend.stream_limit.definir_limite(GUID, 3)

        assert "limite padrão" not in caplog.text


class TestSincronizacao:
    """
    Quem instala o plugin DEPOIS de já ter os limites no painel não tinha quem
    os escrevesse: o painel só os empurra quando alguém os altera. E o painel é
    a fonte da verdade — o que for mexido na página do plugin é reposto.
    """

    def _perfis(self, *pares):
        return [{"media_user_id": uid, "screen_limit": telas} for uid, telas in pares]

    def test_repoe_o_que_diverge(self, cache_limpa):
        backend = _backend(limites={GUID: 1})

        resultado = backend.stream_limit.sincronizar(self._perfis((GUID, 3)))

        assert resultado == {"success": True, "corrigidos": 1}
        assert backend.conn.api.ultimos_params[DEFINIR] == {'userId': GUID, 'streamsAllowed': 3}

    def test_quem_ja_esta_certo_nao_e_reescrito(self, cache_limpa):
        backend = _backend(limites={GUID: 3})

        assert backend.stream_limit.sincronizar(self._perfis((GUID, 3)))["corrigidos"] == 0
        assert DEFINIR not in backend.conn.api.ultimos_params

    def test_quem_nunca_teve_limite_no_plugin_passa_a_ter(self, cache_limpa):
        # É exatamente o caso de quem instalou o plugin agora.
        backend = _backend(limites={})

        assert backend.stream_limit.sincronizar(self._perfis((GUID, 2)))["corrigidos"] == 1

    def test_o_identificador_com_hifenes_do_plugin_nao_conta_como_divergencia(self, cache_limpa):
        # O plugin devolve a chave sem hífenes, mas isto não pode depender disso.
        com_hifenes = "38c3a1f0-e4b2-4d7f-9c1a-0b5e6d7f8a90"
        backend = _backend(limites={com_hifenes: 3})

        assert backend.stream_limit.sincronizar(self._perfis((GUID, 3)))["corrigidos"] == 0

    def test_um_perfil_sem_identificador_e_ignorado(self, cache_limpa):
        backend = _backend(limites={})

        assert backend.stream_limit.sincronizar([{"screen_limit": 2}])["corrigidos"] == 0

    def test_sem_o_plugin_nao_ha_nada_a_sincronizar(self, cache_limpa):
        backend = _backend(instalado=False)

        assert backend.stream_limit.sincronizar(self._perfis((GUID, 3))) == {"success": True, "corrigidos": 0}

    def test_o_plugin_a_falhar_nao_se_da_por_sincronizado(self, cache_limpa):
        # Se desse `success`, o log diria que está tudo em dia sem estar.
        backend = _backend()
        backend.conn.api.erros[LISTAR] = JellyfinApiError("boom", status_code=500)

        assert backend.stream_limit.sincronizar(self._perfis((GUID, 3)))["success"] is False

    def test_uma_resposta_sem_limites_trata_se_como_vazia(self, cache_limpa):
        backend = _backend(**{LISTAR: {"defaultMaxStreams": 0}})

        assert backend.stream_limit.sincronizar(self._perfis((GUID, 2)))["corrigidos"] == 1

    def test_um_limite_ilegivel_nao_rebenta_a_sincronizacao(self, cache_limpa):
        backend = _backend(limites={GUID: "muitos", OUTRO: 2})

        assert backend.stream_limit.sincronizar(self._perfis((OUTRO, 2)))["corrigidos"] == 0

    def test_a_fachada_sincroniza_a_partir_dos_perfis_locais(self, cache_limpa, data_manager):
        backend = _backend(limites={}, data_manager=data_manager)
        data_manager.set_user_profile(GUID, {"username": "ana", "screen_limit": 2})

        assert backend.sync_screen_limits()["corrigidos"] == 1

    def test_o_plex_nao_tem_nada_a_sincronizar(self):
        from app.services.media_server.plex.backend import PlexManager

        backend = PlexManager.__new__(PlexManager)
        assert backend.sync_screen_limits() == {"success": True, "corrigidos": 0}
