"""Identidade do utilizador passa a texto (suporte a servidores não-Plex)

O Plex identifica as contas por um inteiro; o Jellyfin por um GUID. Para que o
painel possa administrar qualquer um dos dois, a chave primária dos perfis — e
todas as chaves estrangeiras que a citam — passam de INTEGER para VARCHAR(64).

Os IDs que já existem (todos numéricos, porque todas as instalações até aqui
são Plex) são convertidos para a sua forma em texto: 123456 -> '123456'. A
conversão é feita pelo próprio SQLite ao copiar para uma coluna com afinidade
TEXT, e reforçada com um CAST explícito a seguir.

Revision ID: a9f3c17b2e04
Revises: d4f1a7c93b25
"""
from alembic import op
import sqlalchemy as sa


revision = 'a9f3c17b2e04'
down_revision = 'd4f1a7c93b25'
branch_labels = None
depends_on = None


# A tabela dos perfis e as que lhe apontam, com o nome da coluna de identidade
# em cada uma.
TABELAS = (
    ('user_profiles', 'plex_user_id'),
    ('coupon_usages', 'user_plex_id'),
    ('blocked_users', 'user_plex_id'),
    ('pix_payments', 'user_plex_id'),
    ('notifications', 'user_plex_id'),
    ('unlocked_achievements', 'user_plex_id'),
    ('stream_termination_logs', 'user_plex_id'),
)

# Colunas anuláveis: o CAST tem de preservar o NULL em vez de o transformar
# na string 'None'.
ANULAVEIS = {('notifications', 'user_plex_id'), ('user_profiles', 'referred_by')}


def _e_sqlite(bind):
    return bind.dialect.name == 'sqlite'


def _converter(tabela, coluna, tipo_novo, anulavel):
    with op.batch_alter_table(tabela, schema=None) as batch_op:
        batch_op.alter_column(coluna, existing_type=sa.Integer(), type_=tipo_novo, existing_nullable=anulavel)

    # Reforço explícito: no SQLite a cópia para uma coluna VARCHAR já converte
    # (afinidade TEXT), mas noutros motores — e em linhas que tenham escapado —
    # o CAST garante que não fica nenhum inteiro guardado como inteiro. Sem
    # isto, uma consulta feita com '123' não encontrava a linha com 123.
    condicao = f'WHERE "{coluna}" IS NOT NULL' if anulavel else ''
    op.execute(f'UPDATE "{tabela}" SET "{coluna}" = CAST("{coluna}" AS VARCHAR) {condicao}')


def upgrade():
    bind = op.get_bind()

    # 🛡️ No SQLite moderno, o RENAME que o batch_alter_table faz no fim reescreve
    # as referências de chave estrangeira das OUTRAS tabelas para o nome temporário
    # da tabela em reconstrução — e as tabelas filhas ficariam a apontar para uma
    # tabela que deixa de existir. O modo legado desliga essa reescrita.
    if _e_sqlite(bind):
        op.execute('PRAGMA legacy_alter_table=ON')

    try:
        for tabela, coluna in TABELAS:
            _converter(tabela, coluna, sa.String(length=64), (tabela, coluna) in ANULAVEIS)

        # 'referred_by' guarda a identidade de quem indicou: tem de acompanhar.
        _converter('user_profiles', 'referred_by', sa.String(length=64), True)

        # Que servidor de média criou este perfil. Um painel que troque de
        # servidor não pode confundir o histórico de um ID Plex com o de um
        # GUID do Jellyfin que por acaso coincida.
        with op.batch_alter_table('user_profiles', schema=None) as batch_op:
            batch_op.add_column(sa.Column('media_server_type', sa.String(length=20), nullable=True))
        op.execute("UPDATE user_profiles SET media_server_type = 'plex' WHERE media_server_type IS NULL")
    finally:
        if _e_sqlite(bind):
            op.execute('PRAGMA legacy_alter_table=OFF')


def downgrade():
    """Volta a INTEGER.

    ⚠️ Só é seguro numa instalação que nunca tenha guardado um ID não numérico
    (ou seja: que nunca tenha sido ligada a um Jellyfin). Um GUID não cabe num
    inteiro e o CAST do SQLite transformá-lo-ia em 0, colando todos os perfis
    não-Plex na mesma chave. Por isso recusamos em vez de corromper.
    """
    bind = op.get_bind()

    for tabela, coluna in TABELAS:
        nao_numericos = bind.execute(
            sa.text(f'SELECT COUNT(*) FROM "{tabela}" WHERE "{coluna}" IS NOT NULL AND CAST("{coluna}" AS VARCHAR) GLOB \'*[^0-9]*\'')
        ).scalar()
        if nao_numericos:
            raise RuntimeError(
                f"Não é possível reverter: '{tabela}.{coluna}' tem {nao_numericos} identificador(es) "
                "não numérico(s) (perfis de um servidor que não é o Plex). "
                "Reverter truncaria esses IDs para 0 e juntaria perfis distintos."
            )

    if _e_sqlite(bind):
        op.execute('PRAGMA legacy_alter_table=ON')

    try:
        with op.batch_alter_table('user_profiles', schema=None) as batch_op:
            batch_op.drop_column('media_server_type')

        with op.batch_alter_table('user_profiles', schema=None) as batch_op:
            batch_op.alter_column('referred_by', existing_type=sa.String(length=64), type_=sa.Integer(), existing_nullable=True)

        for tabela, coluna in reversed(TABELAS):
            with op.batch_alter_table(tabela, schema=None) as batch_op:
                batch_op.alter_column(
                    coluna,
                    existing_type=sa.String(length=64),
                    type_=sa.Integer(),
                    existing_nullable=(tabela, coluna) in ANULAVEIS,
                )
    finally:
        if _e_sqlite(bind):
            op.execute('PRAGMA legacy_alter_table=OFF')
