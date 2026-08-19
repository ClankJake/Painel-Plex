"""Adiciona lifetime_xp a user_profiles

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-08-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lifetime_xp', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_column('lifetime_xp')
