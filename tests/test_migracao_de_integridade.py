# tests/test_migracao_de_integridade.py
"""
As migrações de integridade correm sobre bases de dados SUJAS.

⚠️ **É o único cenário que interessa.** Numa instalação nova não há órfãos, não
há `status` inventados e não há telefones com parênteses — a migração passa e
não prova nada. O que tem de correr bem é o painel de quem já usa isto há
meses, com anos de dados acumulados sem nenhuma das restrições que agora
existem.

E o que corre mal aqui corre mal da pior maneira: um `CHECK` criado sobre
linhas que já o violam faz o `flask db upgrade` rebentar, e esse comando é a
primeira coisa que o contentor corre. Um painel que não arranca é muito pior do
que os dados desarrumados que ele tinha.

Cada cenário corre num PROCESSO à parte, com a sua própria base de dados.
"""

import json
import subprocess
import sys
import tempfile

import pytest

pytestmark = pytest.mark.integration

# A revisão imediatamente anterior a esta série: é aqui que se semeia a sujidade.
ANTES = 'd8f2b4e91c37'

GUIAO = r"""
import json, sqlite3, sys
from app import create_app
from app.extensions import db
from flask_migrate import upgrade
from sqlalchemy import text

app = create_app()
with app.app_context():
    upgrade(revision='d8f2b4e91c37')
    caminho = db.engine.url.database
    db.session.remove()
    db.engine.dispose()

# ⚠️ A sujidade é semeada por uma ligação com as chaves estrangeiras
# DESLIGADAS, que é como ela apareceu: o painel só as passou a impor nesta
# série de migrações. Pela sessão da aplicação não dava — ela já as impõe, e o
# teste não conseguiria construir o cenário que precisa de testar.
crua = sqlite3.connect(caminho)
crua.execute('PRAGMA foreign_keys=OFF')
crua.executescript('''
INSERT INTO user_profiles (media_user_id, username, screen_limit, status,
  xp, lifetime_xp, referral_rewarded, referral_credit, hide_from_leaderboard,
  allow_downloads, phone_number, billing_day)
VALUES ('1', 'ana', 2, 'active', 0, 0, 0, 0, 0, 0, '+55 (11) 99999-9999', 40);

INSERT INTO user_profiles (media_user_id, username, screen_limit, status,
  xp, lifetime_xp, referral_rewarded, referral_credit, hide_from_leaderboard,
  allow_downloads)
VALUES ('2', 'bruno', -3, 'estado-inventado', -5, 0, 0, -1, 0, 0);

-- Órfãos: apontam para perfis que já não existem.
INSERT INTO blocked_users (media_user_id, username, blocked_at)
VALUES ('999', 'fantasma', '2026-01-01');

INSERT INTO notifications (media_user_id, message, category, is_read)
VALUES ('999', 'olá', 'desconhecida', 0);

-- Um pagamento órfão: HISTÓRICO, tem de sobreviver.
INSERT INTO pix_payments (txid, media_user_id, username, value, status,
  created_at, referral_credit_used, is_proration)
VALUES ('t-orfao', '999', 'fantasma', 25.0, 'CONCLUIDA', '2026-01-01', 0, 0);

-- Um pagamento com valor negativo e estado inventado.
INSERT INTO pix_payments (txid, media_user_id, username, value, status,
  created_at, referral_credit_used, is_proration)
VALUES ('t-mau', '1', 'ana', -10.0, 'SEI-LA', '2026-01-02', 0, 0);

INSERT INTO coupons (id, code, discount_type, value, max_uses, use_count, is_active)
VALUES (1, 'PROMO', 'gratis-total', -5, 10, 0, 1);

-- Duas utilizações do MESMO cupão pela mesma pessoa: violam o UNIQUE reposto.
INSERT INTO coupon_usages (media_user_id, coupon_id, used_at) VALUES ('1', 1, '2026-01-01');
INSERT INTO coupon_usages (media_user_id, coupon_id, used_at) VALUES ('1', 1, '2026-01-02');
''')
crua.commit()
crua.close()

with app.app_context():
    try:
        upgrade()
        erro = None
    except Exception as e:
        erro = f'{type(e).__name__}: {e}'

    def uma(sql):
        return db.session.execute(text(sql)).scalar()

    print('RESULTADO:' + json.dumps({
        'erro': erro,
        'versao': uma('SELECT version_num FROM alembic_version'),
        'bloqueio_orfao': uma("SELECT COUNT(*) FROM blocked_users WHERE media_user_id='999'"),
        'notificacao_orfa': uma("SELECT COUNT(*) FROM notifications WHERE media_user_id='999'"),
        'pagamento_orfao': uma("SELECT COUNT(*) FROM pix_payments WHERE txid='t-orfao'"),
        'status_do_bruno': uma("SELECT status FROM user_profiles WHERE media_user_id='2'"),
        'telas_do_bruno': uma("SELECT screen_limit FROM user_profiles WHERE media_user_id='2'"),
        'credito_do_bruno': uma("SELECT referral_credit FROM user_profiles WHERE media_user_id='2'"),
        'telefone_da_ana': uma("SELECT phone_number FROM user_profiles WHERE media_user_id='1'"),
        'dia_da_ana': uma("SELECT billing_day FROM user_profiles WHERE media_user_id='1'"),
        'valor_mau': uma("SELECT value FROM pix_payments WHERE txid='t-mau'"),
        'status_mau': uma("SELECT status FROM pix_payments WHERE txid='t-mau'"),
        'tipo_do_cupao': uma("SELECT discount_type FROM coupons WHERE id=1"),
        'usos_do_cupao': uma("SELECT COUNT(*) FROM coupon_usages WHERE coupon_id=1"),
        'violacoes': uma("SELECT COUNT(*) FROM pragma_foreign_key_check"),
    }))

"""


@pytest.fixture(scope='module')
def resultado():
    with tempfile.TemporaryDirectory() as pasta:
        saida = subprocess.run(
            [sys.executable, '-c', GUIAO],
            capture_output=True, text=True, timeout=300,
            env={'PATH': '/usr/bin:/bin:/usr/local/bin',
                 'PAINEL_PLEX_CONFIG_DIR': pasta, 'HOME': pasta, 'TZ': 'UTC'},
        )
    linha = next((l for l in saida.stdout.splitlines() if l.startswith('RESULTADO:')), None)
    assert linha, (
        f"o processo não chegou ao fim:\n{saida.stdout[-2000:]}\n{saida.stderr[-3000:]}"
    )
    return json.loads(linha[len('RESULTADO:'):])


def test_as_migracoes_correm_ate_ao_fim(resultado):
    """Um `flask db upgrade` que rebenta é, em Docker, um painel que não arranca."""
    assert resultado['erro'] is None, resultado['erro']
    # A revisão de topo. Muda sempre que entra uma migração nova — e é isso
    # que garante que a nova também correu até ao fim, e não só as anteriores.
    assert resultado['versao'] == 'b8e1f45c92d7'


def test_nao_fica_nenhuma_violacao_de_integridade(resultado):
    assert resultado['violacoes'] == 0


class TestOsOrfaos:
    def test_o_que_e_ESTADO_e_apagado(self, resultado):
        assert resultado['bloqueio_orfao'] == 0
        assert resultado['notificacao_orfa'] == 0

    def test_o_que_e_HISTORICO_fica(self, resultado):
        """🛡️ Um pagamento recebido aconteceu. Apagá-lo porque o perfil já não
        existe falsifica o relatório financeiro do mês em que entrou."""
        assert resultado['pagamento_orfao'] == 1


class TestAArrumacaoDosValores:
    """⚠️ Nenhuma destas instruções APAGA linhas: levam o valor inválido para o
    mais próximo que faça sentido. Apagar o perfil de alguém porque o
    `screen_limit` estava a -3 seria a migração a resolver um problema criando
    outro maior."""

    def test_um_estado_inventado_passa_a_inactive(self, resultado):
        assert resultado['status_do_bruno'] == 'inactive'

    def test_os_contadores_negativos_vao_a_zero(self, resultado):
        assert resultado['telas_do_bruno'] == 0
        assert resultado['credito_do_bruno'] == 0

    def test_um_dia_de_cobranca_impossivel_fica_a_NULL(self, resultado):
        assert resultado['dia_da_ana'] is None

    def test_um_pagamento_negativo_vai_a_zero_e_muda_de_estado(self, resultado):
        assert resultado['valor_mau'] == 0
        assert resultado['status_mau'] == 'FALHOU'

    def test_um_tipo_de_desconto_desconhecido_passa_a_percentage(self, resultado):
        assert resultado['tipo_do_cupao'] == 'percentage'


def test_o_telefone_e_normalizado_para_so_digitos(resultado):
    """🐛 No formato antigo, o destinatário `{phone_number}@s.whatsapp.net` era
    inválido e a notificação não chegava a ninguém — sem erro nenhum."""
    assert resultado['telefone_da_ana'] == '5511999999999'


def test_os_usos_duplicados_do_mesmo_cupao_sao_reduzidos_a_um(resultado):
    """⚠️ Criar o UNIQUE por cima de linhas que já o violam faz a migração
    rebentar a meio. Mantém-se o registo mais antigo, que é o que responde à
    pergunta 'esta pessoa já usou este cupão?'."""
    assert resultado['usos_do_cupao'] == 1
