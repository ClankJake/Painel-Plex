# tests/test_esquema_migrado.py
"""
O esquema que as MIGRAÇÕES constroem tem de ser o mesmo que os MODELOS
descrevem.

⚠️ **Os dois caminhos existem mesmo e são diferentes**: em produção a base de
dados é construída pelo `flask db upgrade` (no `run.py` e no `CMD` do
Dockerfile), e nos testes pelo `db.create_all()` a partir dos modelos. Quando
divergem, a suíte fica verde sobre um esquema que ninguém tem — foi assim que
`coupon_usages` andou anos sem a restrição de unicidade que impede o mesmo
cupão de ser usado duas vezes pela mesma pessoa: nos testes existia, em
produção não.

🐛 **E foi assim que os índices de expressão desapareceram sem ninguém dar por
isso.** O `batch_alter_table` reconstrói a tabela a partir do que consegue
REFLETIR, e um índice sobre `lower(username)` não é reflectido — o SQLAlchemy
avisa com um `SAWarning` e segue. Uma migração que reconstruísse a tabela
levava o índice consigo, as consultas voltavam a varrer tudo, e nada em lado
nenhum dizia porquê.

O teste corre num PROCESSO à parte, com a sua própria pasta de configuração: a
fixture `app` da suíte é de âmbito *session* e correr migrações por cima dela
estragava a base de dados partilhada.
"""

import json
import subprocess
import sys
import tempfile

import pytest

pytestmark = pytest.mark.integration


GUIAO = r"""
import json, sqlite3, sys, os

from app import create_app
from app.extensions import db

modo = sys.argv[1]
with create_app().app_context():
    if modo == 'migracoes':
        from flask_migrate import upgrade
        upgrade()
    else:
        db.create_all()
    caminho = db.engine.url.database

c = sqlite3.connect(caminho)
esquema = {}
for (tabela,) in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
    colunas = sorted(l[1] for l in c.execute('PRAGMA table_info("%s")' % tabela))
    chaves = sorted(
        '%s.%s->%s.%s on_delete=%s on_update=%s' % (tabela, l[3], l[2], l[4], l[6], l[5])
        for l in c.execute('PRAGMA foreign_key_list("%s")' % tabela)
    )
    indices = sorted(
        l[0] for l in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? "
            "AND sql IS NOT NULL", (tabela,))
    )
    definicao = c.execute(
        "SELECT sql FROM sqlite_master WHERE name=?", (tabela,)).fetchone()[0] or ''
    esquema[tabela] = {
        'colunas': colunas,
        'chaves': chaves,
        'indices': indices,
        'checks': definicao.count('CHECK'),
    }

print('ESQUEMA:' + json.dumps(esquema))
"""

# Tabelas e colunas que só existem em bases de dados vindas de versões antigas:
# não têm modelo nem código por trás, e as migrações não as apagam porque
# apagar dados de alguém é pior do que deixar uma tabela morta a ocupar espaço.
LEGADAS = {'tickets', 'alembic_version'}
COLUNAS_LEGADAS = {
    'invitations': {'owner_plex_id'},
    'user_profiles': {'level'},
}
# Índices que só existem na base de dados migrada: cobrem colunas legadas
# (`owner_plex_id`, a tabela `tickets`) ou colunas que os modelos nunca pediram
# para indexar. Não são uma divergência — o teste só falha sobre o que os
# MODELOS declaram e as migrações não criam.
def _e_indice_so_do_migrado(nome):
    return nome.endswith(('_owner_plex_id', '_username')) or nome.startswith('ix_tickets_')


def _esquema(modo):
    with tempfile.TemporaryDirectory() as pasta:
        saida = subprocess.run(
            [sys.executable, '-c', GUIAO, modo],
            capture_output=True, text=True, timeout=300,
            env={'PATH': '/usr/bin:/bin:/usr/local/bin',
                 'PAINEL_PLEX_CONFIG_DIR': pasta, 'HOME': pasta, 'TZ': 'UTC'},
        )
    linha = next((l for l in saida.stdout.splitlines() if l.startswith('ESQUEMA:')), None)
    assert linha, (
        f"o processo '{modo}' não chegou ao fim:\n"
        f"{saida.stdout[-2000:]}\n{saida.stderr[-3000:]}"
    )
    return json.loads(linha[len('ESQUEMA:'):])


@pytest.fixture(scope='module')
def esquemas():
    return _esquema('migracoes'), _esquema('modelos')


def test_as_migracoes_criam_as_mesmas_tabelas(esquemas):
    migrado, modelos = esquemas
    assert set(modelos) - set(migrado) == set(), "tabelas que as migrações não criam"
    assert set(migrado) - set(modelos) <= LEGADAS


def test_as_colunas_coincidem(esquemas):
    migrado, modelos = esquemas
    for tabela in sorted(set(modelos)):
        de_mais = set(migrado[tabela]['colunas']) - set(modelos[tabela]['colunas'])
        em_falta = set(modelos[tabela]['colunas']) - set(migrado[tabela]['colunas'])
        assert not em_falta, f"{tabela}: colunas que as migrações não criam: {em_falta}"
        assert de_mais <= COLUNAS_LEGADAS.get(tabela, set()), f"{tabela}: {de_mais}"


def test_as_chaves_estrangeiras_coincidem(esquemas):
    """Inclui o ON DELETE e o ON UPDATE: uma chave sem cascade parece igual e
    comporta-se ao contrário."""
    migrado, modelos = esquemas
    for tabela in sorted(set(modelos)):
        assert migrado[tabela]['chaves'] == modelos[tabela]['chaves'], tabela


def test_os_indices_coincidem(esquemas):
    """🐛 É este que apanha um índice de expressão deitado fora por uma
    reconstrução de tabela."""
    migrado, modelos = esquemas
    for tabela in sorted(set(modelos)):
        de_migrado = set(migrado[tabela]['indices'])
        de_modelos = {i for i in modelos[tabela]['indices']
                      if not _e_indice_so_do_migrado(i)}
        assert de_modelos - de_migrado == set(), (
            f"{tabela}: índices que as migrações não criam (ou que uma "
            f"reconstrução de tabela deitou fora): {de_modelos - de_migrado}"
        )


def test_as_restricoes_de_dominio_coincidem(esquemas):
    migrado, modelos = esquemas
    for tabela in sorted(set(modelos)):
        assert migrado[tabela]['checks'] == modelos[tabela]['checks'], (
            f"{tabela}: {migrado[tabela]['checks']} CHECK nas migrações contra "
            f"{modelos[tabela]['checks']} nos modelos"
        )
