"""Centraliza o limite de telas na tabela de perfis.

Revision ID: e1c2a3b4d5f6
Revises: d3a4b5c6d7e8
Create Date: 2025-06-29 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e1c2a3b4d5f6'
down_revision = 'd3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    # Adiciona a nova coluna 'screen_limit' à tabela 'user_profiles'
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('screen_limit', sa.Integer(), nullable=False, server_default='0'))

    # Dropa a tabela 'limited_users' que já não é necessária
    op.drop_table('limited_users')


def downgrade():
    # Recria a tabela 'limited_users'
    op.create_table('limited_users',
    sa.Column('username', sa.VARCHAR(), nullable=False),
    sa.Column('screen_limit', sa.INTEGER(), nullable=False),
    sa.PrimaryKeyConstraint('username')
    )

    # Remove a coluna 'screen_limit' da tabela 'user_profiles'
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_column('screen_limit')

