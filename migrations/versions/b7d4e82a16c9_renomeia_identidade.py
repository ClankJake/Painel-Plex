"""Renomeia a coluna de identidade para 'media_user_id'

A identidade deixou de ser exclusiva do Plex na migração anterior (passou a
texto, para caber um GUID do Jellyfin). Faltava o nome deixar de mentir:
'plex_user_id' em 'user_profiles' e 'user_plex_id' nas seis tabelas que lhe
apontam passam ambas a 'media_user_id'.

As chaves estrangeiras citam a coluna do pai PELO NOME, por isso todas as
tabelas que apontam para 'user_profiles' têm de ser reconstruídas — incluindo
duas que o painel já não usa mas que continuam na base de dados de quem fez
upgrade desde as versões antigas ('tickets', criada pela migração c92625823728
e sem modelo correspondente, e 'invitations.owner_plex_id'). Deixá-las a
apontar para uma coluna inexistente daria 'foreign key mismatch' a qualquer
verificação de integridade.

Revision ID: b7d4e82a16c9
Revises: a9f3c17b2e04
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7d4e82a16c9'
down_revision = 'a9f3c17b2e04'
branch_labels = None
depends_on = None


IDENTIDADE = sa.String(length=64)

# tabela -> (coluna antiga, coluna nova, é anulável)
FILHAS = (
    ('coupon_usages', 'user_plex_id', 'media_user_id', False),
    ('blocked_users', 'user_plex_id', 'media_user_id', False),
    ('pix_payments', 'user_plex_id', 'media_user_id', False),
    ('notifications', 'user_plex_id', 'media_user_id', True),
    ('unlocked_achievements', 'user_plex_id', 'media_user_id', False),
    ('stream_termination_logs', 'user_plex_id', 'media_user_id', False),
    # Legadas: sem modelo no painel, mas presentes em bases de dados antigas.
    # O nome da coluna fica como está — renomear uma coluna morta é risco sem
    # ganho — mas o tipo e a chave estrangeira têm de acompanhar.
    ('tickets', 'user_plex_id', 'user_plex_id', False),
    ('invitations', 'owner_plex_id', 'owner_plex_id', True),
)


def _e_sqlite(bind):
    return bind.dialect.name == 'sqlite'


def _tem_coluna(bind, tabela, coluna):
    inspector = sa.inspect(bind)
    if tabela not in inspector.get_table_names():
        return False
    return coluna in {c['name'] for c in inspector.get_columns(tabela)}


def _reconstruir(tabela, antigo, novo, anulavel, coluna_pai):
    """Renomeia/converte a coluna e repõe a chave estrangeira para o nome novo.

    `copy_from` descreve a tabela à batch_alter_table sem as chaves
    estrangeiras reflectidas: sem isso, a tabela reconstruída levaria consigo a
    referência antiga e continuaria a apontar para uma coluna que já não
    existe.
    """
    bind = op.get_bind()
    tabela_refletida = sa.Table(tabela, sa.MetaData(), autoload_with=bind)
    tabela_refletida.constraints = {
        c for c in tabela_refletida.constraints
        if not isinstance(c, sa.ForeignKeyConstraint)
    }
    for coluna in tabela_refletida.columns:
        coluna.foreign_keys = set()

    with op.batch_alter_table(tabela, copy_from=tabela_refletida) as batch_op:
        batch_op.alter_column(
            antigo,
            new_column_name=novo,
            type_=IDENTIDADE,
            existing_type=IDENTIDADE,
            existing_nullable=anulavel,
        )

    # A chave estrangeira é reposta numa SEGUNDA passagem, de propósito: criada
    # dentro do mesmo batch da renomeação, referiria uma coluna que ainda não
    # existe com esse nome e desaparecia sem erro nenhum — as seis tabelas
    # filhas ficavam sem chave estrangeira e a base de dados migrada deixava de
    # coincidir com a que o `create_all` constrói de raiz.
    if coluna_pai:
        with op.batch_alter_table(tabela) as batch_op:
            batch_op.create_foreign_key(
                f'fk_{tabela}_{novo}_user_profiles', 'user_profiles', [novo], [coluna_pai]
            )

    # Garante que nenhum valor ficou guardado como inteiro (ver a migração
    # anterior): uma consulta com '123' não encontra a linha com 123.
    condicao = f'WHERE "{novo}" IS NOT NULL' if anulavel else ''
    op.execute(f'UPDATE "{tabela}" SET "{novo}" = CAST("{novo}" AS VARCHAR) {condicao}')


def _mover(de, para):
    bind = op.get_bind()
    if _e_sqlite(bind):
        # 🛡️ Ver a migração anterior: sem o modo legado, o RENAME final do
        # batch_alter_table reescreve as referências das outras tabelas para o
        # nome temporário da tabela em reconstrução.
        op.execute('PRAGMA legacy_alter_table=ON')

    try:
        # A tabela pai primeiro: as filhas precisam do nome novo para lhe apontar.
        _reconstruir('user_profiles', de, para, False, coluna_pai=None)

        for tabela, antigo, novo, anulavel in FILHAS:
            coluna = antigo if de == 'plex_user_id' else novo
            destino = novo if de == 'plex_user_id' else antigo
            if not _tem_coluna(bind, tabela, coluna):
                continue
            _reconstruir(tabela, coluna, destino, anulavel, coluna_pai=para)
    finally:
        if _e_sqlite(bind):
            op.execute('PRAGMA legacy_alter_table=OFF')


def upgrade():
    _mover('plex_user_id', 'media_user_id')


def downgrade():
    _mover('media_user_id', 'plex_user_id')
