# tests/test_jellyfin_stream_gate_log.py

"""Os cortes do plugin StreamLimiter na Auditoria de Cortes.

Quem bloqueia é o plugin, dentro do processo do Jellyfin: recusa o pedido da
mídia antes de servir um byte, e o painel não participa. O resultado era um
limite a ser cumprido sem rasto nenhum no painel — a auditoria mostrava só os
cortes que o próprio painel dava.

Como o plugin não tem rota de eventos, a única fonte é a linha que ele escreve
no log do servidor. Estes testes guardam o que custa a acertar: o fuso horário
da linha, a marca de água que evita importar duas vezes, e o ponto onde a
leitura seguinte recomeça.
"""

from datetime import datetime, timezone

import pytest

from app.services.media_server.jellyfin.stream_gate_log import (
    PRIMEIRA_LEITURA_BYTES, RAZAO_DO_CORTE,
)
from tests.conftest import FakeDataManager
from tests.test_jellyfin_backend import GUID, montar

pytestmark = pytest.mark.integration

ID_DO_PLUGIN = 'd98fbe02-daf3-4c09-a832-4b4e1d07326c'
LOG = 'log_20260912.log'
APARELHO = '7e0fa1c864325ad644874bdd-d626-4037-b45f-671fa20ca85b'

# A linha tal como o plugin a escreve, com o GUID em hífenes e a hora no fuso
# do servidor.
COM_HIFENES = '38c3a1f0-e4b2-4d7f-9c1a-0b5e6d7f8a90'
BLOQUEIO = (
    '[2026-09-12 20:23:28.756 -03:00] [INF] [97] '
    'Jellyfin.Plugin.StreamLimit.Gate.StreamGateFilter: Stream gate denied playback '
    f'negotiation. User: {COM_HIFENES}, device: "{APARELHO}", limit: 1'
)
RUIDO = (
    '[2026-09-12 20:23:00.000 -03:00] [INF] [1] '
    'Emby.Server.Implementations.Library.LibraryManager: A fazer outra coisa qualquer'
)


@pytest.fixture(autouse=True)
def cache_limpa(app_context):
    """A posição de leitura e a deteção do plugin vivem em cache de ficheiro."""
    from app.extensions import cache

    cache.clear()
    yield cache
    cache.clear()


def _backend(linhas=(), tamanho=None, instalado=True, perfis=None, utilizadores=None,
             aparelhos=None):
    conteudo = ('\n'.join(linhas) + '\n').encode('utf-8') if linhas else b''
    backend = montar(
        {
            '/Plugins': [{"Id": ID_DO_PLUGIN, "Name": "StreamLimiter"}] if instalado else [],
            '/System/Logs': [{"Name": LOG, "DateModified": "2026-09-12T23:30:00Z",
                              "Size": tamanho if tamanho is not None else len(conteudo)}],
            '/Devices': {"Items": aparelhos if aparelhos is not None else [
                {"Id": APARELHO, "Name": "M23", "AppName": "Jellyfin Android"},
            ]},
            '/Users': utilizadores if utilizadores is not None else [
                {"Id": GUID, "Name": "ana"},
            ],
        },
        data_manager=FakeDataManager(profiles=perfis or {}),
    )
    backend.conn.api.ficheiros[LOG] = conteudo
    return backend


class TestLeituraDaLinha:
    def test_reconhece_a_linha_que_o_plugin_escreve(self):
        backend = _backend([RUIDO, BLOQUEIO])

        bloqueios = backend.gate_log.ler_bloqueios()

        assert len(bloqueios) == 1
        assert bloqueios[0].limite == 1
        assert bloqueios[0].aparelho == APARELHO

    def test_o_guid_fica_na_forma_que_o_painel_usa(self):
        # ⚠️ O log escreve-o COM hífenes; o painel guarda-o sem. Comparar os
        # dois com `==` dava sempre falso, e o sintoma era o corte a ficar sem
        # dono — sem erro nenhum.
        bloqueio = _backend([BLOQUEIO]).gate_log.ler_bloqueios()[0]

        assert bloqueio.user_id == GUID

    def test_a_hora_e_convertida_para_utc(self):
        # ⚠️ A linha vem no fuso do SERVIDOR (-03:00). Guardar o "20:23" punha
        # o corte três horas no futuro — e à frente da marca de água, que faria
        # a importação seguinte ignorar tudo o que viesse depois.
        bloqueio = _backend([BLOQUEIO]).gate_log.ler_bloqueios()[0]

        assert bloqueio.quando == datetime(2026, 9, 12, 23, 23, 28, 756000, tzinfo=timezone.utc)

    def test_o_resto_do_log_e_ignorado(self):
        assert _backend([RUIDO, RUIDO]).gate_log.ler_bloqueios() == []

    def test_uma_linha_do_filtro_que_nao_e_recusa_nao_conta(self):
        # `denied` é exigido de propósito: se o filtro passar a registar também
        # o que DEIXA passar, tomar uma coisa pela outra seria inventar cortes.
        permitida = BLOQUEIO.replace('denied', 'allowed')

        assert _backend([permitida]).gate_log.ler_bloqueios() == []

    def test_uma_linha_do_filtro_ilegivel_nao_rebenta(self):
        estranha = ('[2026-09-12 20:23:28.756 -03:00] [INF] [97] '
                    'Jellyfin.Plugin.StreamLimit.Gate.StreamGateFilter: denied, e mais nada')

        assert _backend([estranha]).gate_log.ler_bloqueios() == []

    def test_sem_limite_na_linha_continua_a_ser_um_corte(self):
        sem_limite = BLOQUEIO.replace(', limit: 1', '')

        bloqueios = _backend([sem_limite]).gate_log.ler_bloqueios()

        assert len(bloqueios) == 1 and bloqueios[0].limite == 0


class TestOndeSeRetoma:
    def test_a_segunda_leitura_nao_repete_a_primeira(self):
        backend = _backend([BLOQUEIO])

        assert len(backend.gate_log.ler_bloqueios()) == 1
        assert backend.gate_log.ler_bloqueios() == []

    def test_le_o_que_apareceu_entretanto(self):
        backend = _backend([BLOQUEIO])
        backend.gate_log.ler_bloqueios()

        segundo = BLOQUEIO.replace('20:23:28', '20:25:00')
        backend.conn.api.ficheiros[LOG] += (segundo + '\n').encode('utf-8')
        backend.conn.api.respostas['/System/Logs'][0]['Size'] = len(backend.conn.api.ficheiros[LOG])

        bloqueios = backend.gate_log.ler_bloqueios()

        assert len(bloqueios) == 1
        assert bloqueios[0].quando.minute == 25

    def test_uma_linha_a_meio_fica_para_a_leitura_seguinte(self):
        # O log é escrito enquanto o lemos: retomar a meio de uma linha dava
        # uma linha partida, que nunca seria reconhecida.
        backend = _backend([RUIDO])
        backend.conn.api.ficheiros[LOG] += BLOQUEIO[:60].encode('utf-8')
        backend.conn.api.respostas['/System/Logs'][0]['Size'] = len(backend.conn.api.ficheiros[LOG])

        assert backend.gate_log.ler_bloqueios() == []

        # Quando a linha ficar completa, é lida inteira.
        backend.conn.api.ficheiros[LOG] = (RUIDO + '\n' + BLOQUEIO + '\n').encode('utf-8')
        backend.conn.api.respostas['/System/Logs'][0]['Size'] = len(backend.conn.api.ficheiros[LOG])

        assert len(backend.gate_log.ler_bloqueios()) == 1

    def test_pede_so_a_parte_nova(self):
        backend = _backend([BLOQUEIO])
        backend.gate_log.ler_bloqueios()
        backend.conn.api.ficheiros[LOG] += (RUIDO + '\n').encode('utf-8')
        backend.conn.api.respostas['/System/Logs'][0]['Size'] = len(backend.conn.api.ficheiros[LOG])

        backend.gate_log.ler_bloqueios()

        assert backend.conn.api.pedidos_range[-1].startswith('bytes=')

    def test_um_servidor_que_ignora_o_pedido_parcial_da_o_mesmo_resultado(self):
        # O corte passa a ser feito aqui em vez de lá: muda o que atravessa a
        # rede, não o resultado.
        backend = _backend([BLOQUEIO])
        backend.conn.api.honra_range = False
        backend.gate_log.ler_bloqueios()

        segundo = BLOQUEIO.replace('20:23:28', '20:25:00')
        backend.conn.api.ficheiros[LOG] += (segundo + '\n').encode('utf-8')
        backend.conn.api.respostas['/System/Logs'][0]['Size'] = len(backend.conn.api.ficheiros[LOG])

        bloqueios = backend.gate_log.ler_bloqueios()

        assert [b.quando.minute for b in bloqueios] == [25]

    def test_um_ficheiro_que_encolheu_recomeca_do_inicio(self):
        backend = _backend([BLOQUEIO])
        backend.gate_log.ler_bloqueios()

        # Rodou por baixo de nós, com o mesmo nome.
        backend.conn.api.ficheiros[LOG] = (RUIDO + '\n' + BLOQUEIO + '\n').encode('utf-8')
        backend.conn.api.respostas['/System/Logs'][0]['Size'] = 10

        assert len(backend.gate_log.ler_bloqueios()) == 1

    def test_a_primeira_leitura_de_um_log_enorme_so_ve_o_fim(self):
        # Importar o dia inteiro de uma vez encheria a auditoria de cortes
        # antigos no momento em que o plugin fosse instalado.
        backend = _backend([BLOQUEIO], tamanho=PRIMEIRA_LEITURA_BYTES * 4)

        backend.gate_log.ler_bloqueios()

        inicio = int(backend.conn.api.pedidos_range[-1].split('=')[1].split('-')[0])
        assert inicio == PRIMEIRA_LEITURA_BYTES * 3

    def test_um_ficheiro_novo_comeca_do_zero(self):
        backend = _backend([RUIDO])
        backend.gate_log.ler_bloqueios()

        # O Jellyfin roda o log todos os dias: o alvo muda de nome.
        novo = 'log_20260913.log'
        backend.conn.api.ficheiros[novo] = (BLOQUEIO + '\n').encode('utf-8')
        backend.conn.api.respostas['/System/Logs'].append(
            {"Name": novo, "DateModified": "2026-09-13T01:00:00Z",
             "Size": len(backend.conn.api.ficheiros[novo])}
        )

        assert len(backend.gate_log.ler_bloqueios()) == 1


class TestFalhas:
    def test_um_log_inacessivel_nao_parte_o_job(self):
        from app.services.media_server.jellyfin.api_client import JellyfinApiError

        backend = _backend([BLOQUEIO])
        backend.conn.api.erros['/System/Logs/Log'] = JellyfinApiError("403", status_code=403)

        assert backend.gate_log.ler_bloqueios() == []

    def test_sem_ligacao_nao_se_tenta_nada(self):
        backend = _backend([BLOQUEIO])
        backend.conn.server_info = None

        assert backend.gate_log.ler_bloqueios() == []


class TestImportacao:
    """O que chega à Auditoria de Cortes."""

    def test_o_corte_do_plugin_fica_na_auditoria(self):
        backend = _backend([BLOQUEIO], perfis={GUID: {"username": "ana"}})

        resultado = backend.importar_bloqueios_do_servidor()

        assert resultado == {"success": True, "importados": 1}
        registo = backend.data_manager.terminations[0]
        assert registo['username'] == 'ana'
        assert registo['reason'] == RAZAO_DO_CORTE
        # Não há título: o plugin recusa ANTES de haver reprodução. O que se
        # sabe é o limite que se atingiu.
        assert 'Limite de 1' in registo['media_title']
        # E o aparelho fica com o nome que a pessoa reconhece, não com o id de
        # cinquenta caracteres que o cliente declara.
        assert registo['platform'] == 'M23'

    def test_a_hora_e_a_do_corte_e_nao_a_da_leitura(self):
        # A leitura acontece minutos depois: gravar "agora" punha todos os
        # cortes empilhados no mesmo instante, fora de ordem com os restantes.
        backend = _backend([BLOQUEIO], perfis={GUID: {"username": "ana"}})

        backend.importar_bloqueios_do_servidor()

        assert backend.data_manager.terminations[0]['timestamp'] == datetime(
            2026, 9, 12, 23, 23, 28, 756000, tzinfo=timezone.utc)

    def test_nao_importa_duas_vezes_o_mesmo_corte(self):
        backend = _backend([BLOQUEIO], perfis={GUID: {"username": "ana"}})
        backend.importar_bloqueios_do_servidor()

        # A posição de leitura já chegava — mas a marca de água é a garantia
        # que sobrevive a perder a cache ou a reler o ficheiro.
        backend.gate_log._guardar_posicao(LOG, 0)

        assert backend.importar_bloqueios_do_servidor()['importados'] == 0
        assert len(backend.data_manager.terminations) == 1

    def test_sem_o_plugin_nao_se_le_o_log(self):
        backend = _backend([BLOQUEIO], instalado=False)

        assert backend.importar_bloqueios_do_servidor()['importados'] == 0
        assert '/System/Logs' not in [e[1] for e in backend.conn.api.enviados]

    def test_quem_ainda_nao_tem_perfil_e_identificado_pelo_servidor(self):
        # Um corte sem nome não diz nada a quem lê a auditoria.
        backend = _backend([BLOQUEIO], perfis={})

        backend.importar_bloqueios_do_servidor()

        assert backend.data_manager.terminations[0]['username'] == 'ana'

    def test_um_utilizador_que_ninguem_conhece_nao_vira_linha(self):
        backend = _backend([BLOQUEIO], perfis={}, utilizadores=[])

        assert backend.importar_bloqueios_do_servidor()['importados'] == 0

    def test_um_aparelho_desconhecido_nao_inventa_nome(self):
        backend = _backend([BLOQUEIO], perfis={GUID: {"username": "ana"}}, aparelhos=[])

        backend.importar_bloqueios_do_servidor()

        assert backend.data_manager.terminations[0]['platform'] == ''

    def test_o_corte_aparece_na_dashboard_sem_recarregar(self, monkeypatch):
        from app import extensions

        anunciados = []
        monkeypatch.setattr(extensions.socketio, 'emit',
                            lambda evento, payload=None, **kw: anunciados.append((evento, payload)))
        backend = _backend([BLOQUEIO], perfis={GUID: {"username": "ana"}})

        backend.importar_bloqueios_do_servidor()

        assert anunciados[0][0] == 'new_termination_log'
        # ⚠️ A interface faz `new Date(timestamp + 'Z')`: um `datetime` cru
        # serializado pelo Flask dava "Invalid Date", sem erro nenhum.
        assert anunciados[0][1]['timestamp'] == '2026-09-12T23:23:28'

    def test_os_cortes_entram_por_ordem(self):
        segundo = BLOQUEIO.replace('20:23:28', '20:25:00')
        backend = _backend([segundo, BLOQUEIO], perfis={GUID: {"username": "ana"}})

        backend.importar_bloqueios_do_servidor()

        momentos = [t['timestamp'] for t in backend.data_manager.terminations]
        assert momentos == sorted(momentos)


class TestOndeNaoSeAplica:
    def test_no_plex_nao_ha_nada_para_importar(self):
        from app.services.media_server.plex import PlexManager

        backend = PlexManager(None, None, None, None)

        assert backend.importar_bloqueios_do_servidor() == {"success": True, "importados": 0}

    def test_o_job_atravessa_a_fachada_sem_saber_de_que_servidor_se_trata(self, app, monkeypatch):
        from app import extensions, scheduler as agendador

        agendador.set_app_for_jobs(app)
        chamadas = []
        falso = type('Backend', (), {
            'importar_bloqueios_do_servidor': lambda self: chamadas.append(True) or {"importados": 0},
        })()
        monkeypatch.setattr(extensions, 'media_server', falso)

        agendador.server_block_import_job()

        assert chamadas == [True]
