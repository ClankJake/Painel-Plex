"""Adiciona telegram_id a tabela de convites

Revision ID: w9s4e5a8f6j2
Revises: b5c6d7e8f9a0
Create Date: 2025-10-20 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'w9s4e5a8f6j2'
down_revision = 'b5c6d7e8f9a0' # Certifique-se que este é o ID da sua última migração (ex: b5c6d7e8f9a0)
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('telegram_id', sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.drop_column('telegram_id')