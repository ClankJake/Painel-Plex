# tests/test_fuso_do_vencimento.py
"""A data de vencimento é o instante que a PESSOA escolheu, não o do servidor.

🐛 REGRESSÃO REAL: o formulário mandava a hora de parede (`2026-09-05T23:59`) e
o painel fazia `datetime.fromisoformat(...)`, que devolve uma data INGÉNUA. O
`astimezone(timezone.utc)` a seguir assume o fuso do SISTEMA — e num contentor
sem `TZ` definido, que é o padrão do Docker, isso é UTC.

Um administrador no Brasil que marcasse as 23:59 ficava com o vencimento às
20:59 dele. E deslizava a cada gravação, sempre no mesmo sentido, porque o
modal reabre com `new Date(expiration_date)` e mostra a hora JÁ convertida para
o fuso de quem olha:

    volta 1: guardado 23:59Z  ->  o campo mostra 20:59
    volta 2: guardado 20:59Z  ->  o campo mostra 17:59
    volta 3: guardado 17:59Z  ->  o campo mostra 14:59

⚠️ Não era um erro incondicional, e é por isso que ninguém lhe pegou: com
`TZ=America/Sao_Paulo` no contentor, o servidor e o navegador concordavam e a
ida e volta era estável. O painel ASSUMIA isso (`get_app_timezone` diz "lê o
fuso forçado no docker-compose") sem nada o obrigar.

Hoje o navegador manda o deslocamento (`comDeslocamentoLocal`, em `utils.js`) e
o instante deixa de ser ambíguo.

⚠️ O `conftest` fixa `TZ=UTC`, por isso o fuso do painel nestes testes é UTC —
que é exatamente o caso que partia.
"""

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import extensions

# ⚠️ **A aplicação tem de existir ANTES de este módulo ser importado**, e isso
# não é estilo: `app/blueprints/auth.py` faz
# `from ..extensions import media_server, data_manager`, capturando os managers
# **por valor** — o padrão que o CLAUDE.md descreve. Em produção a ordem está
# garantida, porque o `create_app()` instancia-os e só depois importa os
# blueprints.
#
# Um `from app.blueprints... import` no topo de um teste inverte essa ordem: a
# cadeia é importada na RECOLHA do pytest, antes de a fixture `app` correr, e o
# `auth.py` fica com `data_manager = None` **para o resto do processo** — a
# fixture `app` é de âmbito *session* e não volta a importá-lo. Os testes deste
# ficheiro passavam à mesma; foram os do LOGIN, a correr a seguir, que
# rebentaram com `'NoneType' object has no attribute 'get_user_profile'` sem
# terem nada a ver com isto.
#
# A saída é pedir a `app` (que corre o `create_app`) antes de qualquer teste, e
# só então importar a função.
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _com_a_aplicacao_de_pe(app):
    """Garante que o `create_app()` correu antes do primeiro import daqui."""
    return app


def _momento_do_vencimento(*args, **kwargs):
    """Importa a função quando ela é CHAMADA — ver a nota acima."""
    from app.blueprints.api.users import _momento_do_vencimento as real

    return real(*args, **kwargs)

RAIZ = Path(__file__).resolve().parent.parent
UTILS_JS = RAIZ / 'app' / 'static' / 'js' / 'utils.js'

# O fuso de quem está a clicar, diferente do do painel (UTC, pelo conftest).
BRASIL = ZoneInfo('America/Sao_Paulo')

SEM_UNIVERSAL = {"UNIVERSAL_EXPIRATION_ENABLED": False}


def _o_que_o_navegador_mostra(guardado, fuso_de_quem_olha):
    """O `new Date(expiration_date)` + getters locais do modal, em Python."""
    return datetime.fromisoformat(guardado).astimezone(fuso_de_quem_olha)


class TestOInstanteEscolhido:

    def test_a_hora_escolhida_no_brasil_e_a_hora_gravada(self):
        escolhido = _momento_do_vencimento('2026-09-05T23:59:00-03:00', SEM_UNIVERSAL)
        assert escolhido.astimezone(timezone.utc).isoformat() == '2026-09-06T02:59:00+00:00'

        # E é isso que o administrador volta a ver: as 23:59 dele.
        mostrado = _o_que_o_navegador_mostra(escolhido.astimezone(timezone.utc).isoformat(), BRASIL)
        assert (mostrado.hour, mostrado.minute) == (23, 59)
        assert mostrado.date().isoformat() == '2026-09-05'

    def test_abrir_e_gravar_sem_tocar_em_nada_nao_move_o_vencimento(self):
        """A ida e volta que deslizava três horas de cada vez."""
        escrito = '2026-09-05T23:59:00-03:00'
        guardados = []

        for _ in range(3):
            guardado = _momento_do_vencimento(escrito, SEM_UNIVERSAL).astimezone(timezone.utc).isoformat()
            guardados.append(guardado)
            # O modal reabre, mostra a hora no fuso de quem olha, e o
            # `comDeslocamentoLocal` volta a juntar-lhe o deslocamento.
            mostrado = _o_que_o_navegador_mostra(guardado, BRASIL)
            escrito = mostrado.strftime('%Y-%m-%dT%H:%M:%S%z')
            escrito = f"{escrito[:-2]}:{escrito[-2:]}"

        assert guardados == ['2026-09-06T02:59:00+00:00'] * 3, (
            "O vencimento moveu-se entre gravações: " + " -> ".join(guardados)
        )

    def test_uma_data_sem_deslocamento_continua_a_ser_lida_no_fuso_do_painel(self):
        """O navegador com o JavaScript antigo em cache não pode dar erro."""
        escolhido = _momento_do_vencimento('2026-09-05T23:59', SEM_UNIVERSAL)
        assert escolhido.utcoffset() == timedelta(0), "o painel corre em UTC nos testes"
        assert escolhido.astimezone(timezone.utc).isoformat() == '2026-09-05T23:59:00+00:00'

    def test_um_fuso_a_leste_tambem_e_respeitado(self):
        """Não é só o Brasil: o deslocamento positivo tem de ir no sentido certo."""
        escolhido = _momento_do_vencimento('2026-09-05T09:00:00+09:00', SEM_UNIVERSAL)
        assert escolhido.astimezone(timezone.utc).isoformat() == '2026-09-05T00:00:00+00:00'


class TestOHorarioUniversal:
    """A hora universal é do PAINEL; o dia é de quem escolheu."""

    def test_a_hora_vem_do_painel_e_o_dia_de_quem_escolheu(self):
        config = {"UNIVERSAL_EXPIRATION_ENABLED": True, "UNIVERSAL_EXPIRATION_TIME": "23:59"}
        # O administrador no Brasil escolhe o DIA 5 (o campo da hora está
        # desativado quando esta definição está ligada).
        escolhido = _momento_do_vencimento('2026-09-05T00:00:00-03:00', config)

        assert escolhido.utcoffset() == timedelta(0), "a hora universal é a do painel"
        assert (escolhido.hour, escolhido.minute) == (23, 59)
        assert escolhido.day == 5, "o dia é o que a pessoa escolheu"

    def test_uma_hora_universal_ilegivel_nao_derruba_a_gravacao(self):
        config = {"UNIVERSAL_EXPIRATION_ENABLED": True, "UNIVERSAL_EXPIRATION_TIME": "meia-noite"}
        escolhido = _momento_do_vencimento('2026-09-05T23:59:00-03:00', config)
        assert escolhido.astimezone(timezone.utc).isoformat() == '2026-09-06T02:59:00+00:00'


class TestODiaDeFaturacao:

    def test_a_ancora_e_o_dia_que_a_pessoa_escolheu(self):
        """⚠️ 05/09 às 23:59 no Brasil é 06/09 em UTC.

        Ancorar a faturação no 6 mudava o dia da cobrança de toda a gente que
        marcasse uma hora depois das 21:00.
        """
        escolhido = _momento_do_vencimento('2026-09-05T23:59:00-03:00', SEM_UNIVERSAL)
        assert escolhido.day == 5
        assert escolhido.astimezone(timezone.utc).day == 6


@pytest.mark.skipif(shutil.which('node') is None, reason="precisa do Node")
class TestOLadoDoNavegador:
    """Corre o `comDeslocamentoLocal` REAL, com o Node no fuso do Brasil."""

    def _correr(self, entradas, tz):
        fonte = UTILS_JS.read_text(encoding='utf-8')
        inicio = fonte.index('export function comDeslocamentoLocal')
        fim = fonte.index('export function escapeHTML')
        script = fonte[inicio:fim].replace('export function', 'function', 1) + """
console.log(JSON.stringify(JSON.parse(process.argv[1]).map(comDeslocamentoLocal)));
"""
        r = subprocess.run(
            ['node', '--input-type=module', '-e', script, '--', json.dumps(entradas)],
            capture_output=True, text=True, check=True, cwd=RAIZ,
            env={'PATH': '/usr/bin:/bin:/usr/local/bin', 'TZ': tz},
        )
        return json.loads(r.stdout)

    def test_acrescenta_o_deslocamento_de_quem_esta_a_ver(self):
        assert self._correr(['2026-09-05T23:59'], 'America/Sao_Paulo') == ['2026-09-05T23:59:00-03:00']
        assert self._correr(['2026-09-05T23:59'], 'UTC') == ['2026-09-05T23:59:00+00:00']
        assert self._correr(['2026-09-05T23:59'], 'Asia/Tokyo') == ['2026-09-05T23:59:00+09:00']

    def test_usa_o_deslocamento_DAQUELA_data_e_nao_o_de_hoje(self):
        """Onde há horário de verão, os dois não são o mesmo.

        Lisboa está em UTC+1 no verão e em UTC+0 no inverno: perguntar ao
        `new Date()` de agora dava a metade errada do ano.
        """
        verao, inverno = self._correr(['2026-07-15T12:00', '2026-01-15T12:00'], 'Europe/Lisbon')
        assert verao.endswith('+01:00'), verao
        assert inverno.endswith('+00:00'), inverno

    def test_o_que_o_navegador_manda_e_o_que_o_painel_le(self):
        """As duas metades têm de concordar, que é o ponto de tudo isto."""
        enviado, = self._correr(['2026-09-05T23:59'], 'America/Sao_Paulo')
        escolhido = _momento_do_vencimento(enviado, SEM_UNIVERSAL)
        mostrado = _o_que_o_navegador_mostra(
            escolhido.astimezone(timezone.utc).isoformat(), BRASIL)
        assert mostrado.strftime('%Y-%m-%dT%H:%M') == '2026-09-05T23:59'

    def test_nao_estraga_o_que_nao_percebe(self):
        assert self._correr(['', 'nao-e-uma-data'], 'UTC') == ['', 'nao-e-uma-data']


ADMIN = '99001'
ALVO = '55001'


class ServidorFalso:
    """O mínimo que a rota do perfil pede ao servidor de média."""

    def get_user_by_id(self, media_user_id):
        return {'id': str(media_user_id), 'username': 'ana', 'email': 'ana@b.test'}

    def block_user(self, media_user_id, reason=None):
        return True

    def unblock_user(self, media_user_id):
        return True


class TestPelaRotaReal:
    """A rota inteira, para cobrir o agendador e o `billing_day`.

    A função pura acima é onde vive a decisão; isto confirma que o resto do
    caminho — o `run_date` do APScheduler e o que fica gravado no perfil —
    concorda com ela.
    """

    def _autenticar(self, client):
        with client.session_transaction() as sessao:
            sessao['user_details'] = {'id': ADMIN, 'username': 'dono',
                                      'email': 'a@b.test', 'role': 'admin'}
            sessao['_user_id'] = ADMIN
            sessao['_fresh'] = True

    def test_o_vencimento_gravado_e_o_instante_escolhido(
            self, app, client, db_session, config_file, monkeypatch):
        config_file(IS_CONFIGURED=True, ADMIN_USER='dono', ADMIN_USER_ID=ADMIN,
                    UNIVERSAL_EXPIRATION_ENABLED=False)
        monkeypatch.setattr(extensions, 'media_server', ServidorFalso())
        self._autenticar(client)

        with app.app_context():
            extensions.data_manager.set_user_profile(
                ALVO, {'media_user_id': ALVO, 'username': 'ana'})

        resposta = client.post(f'/api/users/profile/{ALVO}', json={
            'name': 'Ana',
            # 23:59 do dia 5, no Brasil. ⚠️ Uma data FUTURA de propósito:
            # no passado a rota bloqueia a conta e o agendador dispara a
            # tarefa na hora — ruído num teste que é sobre o fuso.
            'expiration_datetime_local': '2030-09-05T23:59:00-03:00',
        })
        assert resposta.status_code == 200, resposta.get_data(as_text=True)

        with app.app_context():
            perfil = extensions.data_manager.get_user_profile(ALVO)

        assert perfil['expiration_date'] == '2030-09-06T02:59:00+00:00'
        # ⚠️ E a âncora de faturação é o dia 5 — o que a pessoa escolheu —,
        # não o 6 em que aquele instante cai em UTC.
        assert perfil['billing_day'] == 5

        # O que o modal volta a mostrar a quem gravou: as 23:59 dele.
        mostrado = _o_que_o_navegador_mostra(perfil['expiration_date'], BRASIL)
        assert mostrado.strftime('%Y-%m-%dT%H:%M') == '2030-09-05T23:59'
