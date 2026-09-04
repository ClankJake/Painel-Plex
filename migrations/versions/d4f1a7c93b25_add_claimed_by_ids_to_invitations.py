"""Regista os IDs do Plex de quem resgatou cada convite

O username do Plex pode ser alterado pelo próprio utilizador (o painel tem
inclusive sincronização para o acompanhar), por isso 'claimed_by_users' não
serve como identidade estável: bastava mudar de nome para voltar a resgatar um
convite de teste. Esta coluna guarda, em paralelo, o ID numérico do Plex, que
nunca muda.

Os convites já existentes ficam com a coluna a NULL — a verificação continua a
usar o username como recurso para esses registos antigos.

Revision ID: d4f1a7c93b25
Revises: c1d2e3f4a5b6
Create Date: 2026-09-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd4f1a7c93b25'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('claimed_by_ids', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.drop_column('claimed_by_ids')
