# tests/test_migracao_identidade_do_plex.py

"""A migração histórica `a0b1c2d3e4f5`, que troca a chave por o ID do Plex.

🛡️ **Ela APAGA o que não conseguir mapear.** A chave primária de
`user_profiles` passa de `username` para o id do Plex, e o mapeamento vem de uma
chamada ao servidor VIVO. Quem não estiver no mapa não é copiado para a tabela
nova — por isso, sem mapeamento nenhum, a migração deitava fora todos os
perfis, todos os pagamentos e todo o histórico **sem uma única linha de erro**.

Acontecia sempre que o Plex estivesse inacessível no arranque, que é
precisamente quando alguém corre um restauro. Recusar é muito melhor: a base de
dados fica intacta e a migração corre outra vez assim que o Plex responder.

⚠️ Mas zero perfis é outra coisa: é uma instalação nova, e aí não há nada a
perder — tem de passar, ou o painel não arranca de todo. Foi esse o engano da
outra vez, quando o `plex_manager` mudou de nome e isto abortava em toda a
gente.

Cada cenário corre num PROCESSO à parte, com a sua própria pasta de
configuração: a fixture `app` da suíte é de âmbito *session* e correr migrações
por cima dela estragava a base de dados partilhada.
"""

import json
import subprocess
import sys
import tempfile

import pytest

pytestmark = pytest.mark.integration

ANTES_DA_MIGRACAO = 'e5f6a7b8c9d0'

# O guião corre num processo à parte e recebe o cenário em JSON, por argv: o
# texto fica literal, sem interpolação a disputar as chavetas do f-string.
GUIAO = r"""
import json, sys
from app.services.media_server.plex.backend import PlexManager

cenario = json.loads(sys.argv[1])
PlexManager.reload_connections = lambda self, from_job=False: (True, 'ok')
PlexManager.is_connected = lambda self: cenario['ligado']
PlexManager.get_all_users = lambda self, force_refresh=False: cenario['contas']

from app import create_app
from app.extensions import db
from flask_migrate import upgrade
from sqlalchemy import inspect, text

with create_app().app_context():
    upgrade(revision=cenario['antes'])
    for nome in cenario['perfis']:
        db.session.execute(text(
            "INSERT INTO user_profiles (username, screen_limit, payment_token) "
            "VALUES (:n, 2, :t)"
        ).bindparams(n=nome, t='tok-' + nome))
    db.session.commit()

    try:
        upgrade()
        recusou = False
    except Exception as e:
        recusou = True
        print('MOTIVO:' + str(e), file=sys.stderr)

    # Depois de uma RECUSA a tabela ainda e a antiga (chave = username, sem
    # coluna media_user_id): a consulta tem de servir os dois estados.
    colunas = [c['name'] for c in inspect(db.engine).get_columns('user_profiles')]
    chave = 'media_user_id' if 'media_user_id' in colunas else "''"
    linhas = db.session.execute(text(
        "SELECT " + chave + ", username FROM user_profiles ORDER BY username")).fetchall()
    versao = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()

print('RESULTADO:' + json.dumps({
    'recusou': recusou,
    'perfis': [[a, b] for a, b in linhas],
    'versao': versao,
}))
"""


def _correr(perfis, contas, ligado=True):
    """Corre a migração num processo e numa base de dados só dele."""
    cenario = json.dumps({
        'perfis': perfis, 'contas': contas, 'ligado': ligado, 'antes': ANTES_DA_MIGRACAO,
    })
    with tempfile.TemporaryDirectory() as pasta:
        saida = subprocess.run(
            [sys.executable, '-c', GUIAO, cenario],
            capture_output=True, text=True, timeout=300,
            env={'PATH': '/usr/bin:/bin:/usr/local/bin', 'PAINEL_PLEX_CONFIG_DIR': pasta,
                 'HOME': pasta, 'TZ': 'UTC'},
        )
    linha = next((l for l in saida.stdout.splitlines() if l.startswith('RESULTADO:')), None)
    assert linha, f"o processo nao chegou ao fim:\n{saida.stdout[-2000:]}\n{saida.stderr[-2000:]}"
    return json.loads(linha[len('RESULTADO:'):]), saida.stderr


class TestQuandoOPlexNaoResponde:
    def test_com_perfis_na_base_de_dados_RECUSA(self):
        # 🛡️ O caso que apagava tudo em silêncio.
        resultado, _erro = _correr(perfis=['ana', 'bruno'], contas=[], ligado=False)

        assert resultado['recusou'] is True

    def test_e_a_base_de_dados_fica_intacta(self):
        resultado, _erro = _correr(perfis=['ana', 'bruno'], contas=[], ligado=False)

        assert [p[1] for p in resultado['perfis']] == ['ana', 'bruno']
        assert resultado['versao'] == ANTES_DA_MIGRACAO

    def test_a_mensagem_diz_o_que_fazer(self):
        _resultado, erro = _correr(perfis=['ana'], contas=[], ligado=False)

        assert 'perda de dados' in erro
        assert 'Plex está acessível' in erro

    def test_uma_instalacao_NOVA_passa(self):
        # ⚠️ Sem perfis não há nada a perder, e abortar aqui impedia o painel de
        # arrancar de todo — foi o que aconteceu quando o manager mudou de nome.
        resultado, _erro = _correr(perfis=[], contas=[], ligado=False)

        assert resultado['recusou'] is False
        assert resultado['versao'] != ANTES_DA_MIGRACAO


class TestQuandoOPlexResponde:
    def test_os_perfis_atravessam_com_o_id_do_plex(self):
        resultado, _erro = _correr(
            perfis=['ana', 'bruno'],
            contas=[{'username': 'ana', 'id': 111}, {'username': 'bruno', 'id': 222}],
        )

        assert resultado['recusou'] is False
        assert resultado['perfis'] == [['111', 'ana'], ['222', 'bruno']]

    def test_quem_ja_nao_esta_no_plex_e_descartado_MAS_com_aviso(self):
        # Descartar é a única saída (a chave nova não existe para ele), mas o
        # administrador tem de ficar a saber quem foi — em vez de dar pela
        # falta meses depois.
        resultado, erro = _correr(
            perfis=['ana', 'fantasma'],
            contas=[{'username': 'ana', 'id': 111}],
        )

        assert [p[1] for p in resultado['perfis']] == ['ana']
        assert 'DESCARTADOS' in erro
        assert 'fantasma' in erro

    def test_um_servidor_sem_partilhas_e_com_perfis_tambem_recusa(self):
        # Ligado, mas sem utilizadores: é indistinguível de um erro, e a
        # consequência seria a mesma. Na dúvida não se apaga.
        resultado, _erro = _correr(perfis=['ana'], contas=[], ligado=True)

        assert resultado['recusou'] is True
