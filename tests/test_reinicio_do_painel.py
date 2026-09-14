# tests/test_reinicio_do_painel.py

"""O reinício que o painel pede a si próprio — e o que o navegador vê entretanto.

🐛 REGRESSÃO REPORTADA: depois de concluir o assistente a escolher **Jellyfin**,
o painel voltava com a página de login do **Plex**. O log do contentor mostrava
tudo certo do lado do servidor — a ligação ao Jellyfin validada, o config
gravado, o aviso de que o tipo de servidor tinha mudado — e ainda assim o que
aparecia era o servidor antigo.

Eram duas causas somadas, e as duas estão aqui:

1. **O `--preload` do gunicorn.** Com ele, o `create_app()` corre no processo
   MESTRE e cada worker é um `fork` dessa memória. O reinício do painel mata o
   WORKER (`os.kill(os.getpid(), SIGTERM)`), e o mestre levanta outro a partir
   da mesma memória pré-carregada — com o config ANTIGO lá dentro. O painel
   ficava a falar com o Plex até alguém reiniciar o contentor à mão.

2. **A espera de oito segundos no navegador.** Um `setTimeout` fixo, repetido em
   três sítios. O processo antigo não morre quando lhe mandam o sinal: sob
   gunicorn ele só sai quando não houver ligações a ser servidas, e um separador
   aberto chega para o segurar durante todo o tempo de cortesia (30 segundos por
   omissão). A página recarregava a tempo de apanhar o processo a morrer.

A correção da segunda é uma marca por arranque (`BOOT_ID`) e uma rota que a
diz: espera-se até ela MUDAR, em vez de contar o tempo.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


class TestOComandoDoGunicorn:
    """O que o contentor executa. É onde a primeira causa vivia."""

    @pytest.fixture()
    def cmd(self):
        dockerfile = (RAIZ / 'Dockerfile').read_text(encoding='utf-8')
        linhas = [l for l in dockerfile.splitlines() if l.startswith('CMD ')]
        assert len(linhas) == 1, "Esperava um único CMD no Dockerfile."
        return linhas[0]

    def test_sem_preload(self, cmd):
        # ⚠️ Com `--preload` a aplicação é lida UMA vez, no arranque do
        # contentor: o worker que nasce depois de um reinício pedido pelo painel
        # traz o config antigo, e trocar de servidor de média deixa de resultar.
        assert '--preload' not in cmd

    def test_um_worker_apenas(self, cmd):
        # O motivo de não haver `--preload` é este: ele serve para poupar
        # memória entre VÁRIOS workers, e aqui há um só de propósito (gevent +
        # SocketIO sem `message_queue`). Se um dia forem mais, esta troca volta
        # a ter de ser pensada.
        assert re.search(r'-w\s+1\b', cmd)

    def test_o_socket_de_gestao_esta_desligado(self, cmd):
        # 🔇 A partir da 25.1.0 o gunicorn abre por omissão um socket para o
        # `gunicornc`, em `$HOME/.gunicorn/`. Num contentor lançado com
        # `--user`, o Docker põe `HOME=/` e criá-lo é proibido, por isso ficava
        # um ERROR por cada worker que nascia:
        #
        #     Control server error: [Errno 13] Permission denied: '/.gunicorn'
        #
        # O painel não usa o `gunicornc` — reinicia-se por SIGTERM.
        assert '--no-control-socket' in cmd

    def test_a_flag_obriga_a_fixar_a_versao(self, cmd):
        # ⚠️ As duas pontas andam juntas: nas versões anteriores à 25.1.0 a flag
        # não existe e o gunicorn RECUSA-SE A ARRANCAR ("unrecognized
        # arguments"). O contentor morria no arranque — o oposto de calar um
        # aviso.
        import re

        if '--no-control-socket' not in cmd:
            pytest.skip('A flag saiu do CMD; a versão mínima deixa de ser exigida.')

        requisitos = (RAIZ / 'requirements.txt').read_text(encoding='utf-8')
        linha = [l for l in requisitos.splitlines()
                 if l.strip().lower().startswith('gunicorn')]

        assert linha, 'O gunicorn tem de estar no requirements.txt.'
        encontrado = re.search(r'gunicorn\s*>=\s*(\d+)\.(\d+)', linha[0])
        assert encontrado, (
            f"O gunicorn tem de ficar fixado em >=25.1 por causa do "
            f"--no-control-socket; está '{linha[0].strip()}'."
        )
        assert (int(encontrado.group(1)), int(encontrado.group(2))) >= (25, 1)

    def test_tempo_de_cortesia_curto(self, cmd):
        # Sem isto são os 30 segundos por omissão, e o worker gasta-os SEMPRE
        # que há um separador aberto — é o tempo que o painel demorava a voltar.
        encontrado = re.search(r'--graceful-timeout\s+(\d+)', cmd)
        assert encontrado, "O tempo de cortesia tem de ser explícito."
        assert int(encontrado.group(1)) <= 15


class TestAMarcaDoArranque:
    """`BOOT_ID`: como o navegador sabe que já é o painel NOVO a responder."""

    pytestmark = pytest.mark.integration

    def test_a_aplicacao_tem_uma(self, app):
        assert app.config.get('BOOT_ID')

    def test_e_sorteada_a_cada_create_app(self, app):
        # É o que dá sentido à espera: se dois arranques partilhassem a marca,
        # ela nunca mudaria e a página recarregaria cedo demais na mesma.
        #
        # ⚠️ Não se cria aqui uma SEGUNDA aplicação para o comprovar: as
        # extensões globais só podem ser inicializadas uma vez por processo (a
        # fixture `app` é de âmbito *session* por causa disso), e fazê-lo
        # partia a suíte inteira a partir daqui. Prende-se a origem do valor.
        import re

        origem = (RAIZ / 'app/__init__.py').read_text(encoding='utf-8')

        assert re.search(r"app\.config\['BOOT_ID'\]\s*=\s*uuid\.uuid4\(\)\.hex", origem)
        assert re.fullmatch(r'[0-9a-f]{32}', app.config['BOOT_ID'])


@pytest.mark.integration
class TestARotaDeEstado:
    def test_diz_a_marca_e_o_servidor(self, client, app):
        resposta = client.get('/api/system/status')

        assert resposta.status_code == 200
        corpo = resposta.get_json()
        assert corpo['success'] is True
        assert corpo['boot_id'] == app.config['BOOT_ID']
        assert 'media_server_type' in corpo

    def test_responde_sem_sessao(self, client):
        # Quem pergunta está à espera de conseguir entrar — exigir-lhe uma
        # sessão seria exigir exatamente aquilo que ainda não consegue fazer.
        resposta = client.get('/api/system/status')

        assert resposta.status_code == 200

    def test_responde_com_o_painel_por_configurar(self, client, config_file):
        # ⚠️ O `before_request` manda tudo o que não está isento para o
        # assistente. Sem a isenção, a espera do restauro de backup feito DE
        # DENTRO do assistente recebia um 302 para o próprio assistente e nunca
        # via a marca mudar.
        config_file(IS_CONFIGURED=False)

        resposta = client.get('/api/system/status')

        assert resposta.status_code == 200
        assert resposta.get_json()['success'] is True

    def test_esta_fora_do_limitador(self, app):
        # É pedida de segundo a segundo enquanto se espera; com o limitador pela
        # frente, a espera acabava num 429 e a página ficava presa.
        from flask_limiter.util import get_qualified_name

        from app import extensions

        nome = get_qualified_name(app.view_functions['system_api.estado_do_processo'])

        assert nome in extensions.limiter.limit_manager._route_exemptions


@pytest.mark.integration
class TestQuemPedeOReinicioRecebeAMarca:
    """Sem ela na resposta, não há com o que comparar depois."""

    def test_o_fim_do_assistente(self, app_context, monkeypatch):
        from app.blueprints.api import system

        monkeypatch.setattr(system, '_agendar_reinicio', lambda motivo: None)

        class BackendFalso:
            SERVER_TYPE = 'jellyfin'

            def get_owner_account(self):
                return None

        resposta = system._concluir_com_reinicio({'IS_CONFIGURED': True}, BackendFalso())
        corpo = resposta.get_json()

        assert corpo['restarting'] is True
        assert corpo['boot_id'] == app_context.config['BOOT_ID']

    @pytest.mark.parametrize('rota', [
        '/api/system/setup/restore-backup',
        '/api/system/backup/restore',
    ])
    def test_os_restauros_de_backup(self, client, app, monkeypatch, config_file, rota):
        import io

        from app.blueprints.api import system

        configurada = rota.endswith('/backup/restore')
        config_file(IS_CONFIGURED=configurada, ADMIN_USER_ID='1', ADMIN_USER='dono')
        if configurada:
            with client.session_transaction() as sessao:
                sessao['user_details'] = {
                    'id': '1', 'username': 'dono', 'email': 'a@b.test', 'role': 'admin',
                }
                sessao['_user_id'] = '1'
                sessao['_fresh'] = True

        monkeypatch.setattr(system, '_restaurar_backup', lambda ficheiro: None)
        monkeypatch.setattr(system, '_agendar_reinicio', lambda motivo: None)

        resposta = client.post(
            rota,
            data={'file': (io.BytesIO(b'zip'), 'backup.zip')},
            content_type='multipart/form-data',
        )

        assert resposta.status_code == 200, resposta.get_data(as_text=True)
        corpo = resposta.get_json()
        assert corpo['success'] is True
        assert corpo['restarting'] is True
        assert corpo['boot_id'] == app.config['BOOT_ID']


class TestONavegadorEsperaPelaMarca:
    """O JavaScript deixou de contar o tempo."""

    FICHEIROS = [
        'app/static/js/setup.js',
        'app/static/js/settings_modules/handlers.js',
    ]

    @pytest.mark.parametrize('caminho', FICHEIROS)
    def test_nao_recarrega_por_tempo(self, caminho):
        # 🐛 Era `setTimeout(() => window.location.reload(), 8000)`. Oito
        # segundos é menos do que o tempo de cortesia do gunicorn, por isso
        # quem respondia ainda era o processo antigo.
        origem = (RAIZ / caminho).read_text(encoding='utf-8')

        recargas_por_tempo = re.findall(
            r'setTimeout\([^;]*window\.location\.(reload|href)[^;]*\)', origem
        )
        assert not recargas_por_tempo, (
            f"{caminho} volta a recarregar por tempo: {recargas_por_tempo}"
        )

    @pytest.mark.parametrize('caminho', FICHEIROS)
    def test_usa_o_ajudante_da_espera(self, caminho):
        origem = (RAIZ / caminho).read_text(encoding='utf-8')

        assert 'aguardarReinicio' in origem
        assert re.search(r"import \{[^}]*aguardarReinicio[^}]*\} from '.*reinicio\.js'", origem)

    @pytest.mark.parametrize('caminho', FICHEIROS)
    def test_passa_a_marca_que_veio_na_resposta(self, caminho):
        # Sem `bootId` o ajudante não tem o que comparar e volta ao palpite.
        origem = (RAIZ / caminho).read_text(encoding='utf-8')

        for chamada in re.findall(r'aguardarReinicio\(\{(.*?)\}\)', origem, re.S):
            assert 'bootId:' in chamada
            assert 'url:' in chamada

    def test_o_ajudante_espera_pela_marca_a_mudar(self):
        origem = (RAIZ / 'app/static/js/reinicio.js').read_text(encoding='utf-8')

        assert 'boot_id' in origem
        assert '!==' in origem

    def test_as_paginas_sabem_a_rota_de_estado(self):
        # A ponte entre o Jinja e o JavaScript: uma chave `urls.x` que o script
        # procura tem de existir como `data-*` no template.
        setup = (RAIZ / 'app/templates/setup.html').read_text(encoding='utf-8')
        definicoes = (RAIZ / 'app/templates/settings.html').read_text(encoding='utf-8')

        assert 'data-urls-status="{{ url_for(\'system_api.estado_do_processo\') }}"' in setup
        assert 'data-urls-system-status="{{ url_for(\'system_api.estado_do_processo\') }}"' in definicoes
