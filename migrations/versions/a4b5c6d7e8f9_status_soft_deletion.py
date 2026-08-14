"""Adds status column to user_profiles for soft deletion

Revision ID: a4b5c6d7e8f9
Revises: 1c2b3a4d5e6f
Create Date: 2025-10-16 12:35:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4b5c6d7e8f9'
down_revision = '1c2b3a4d5e6f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False, server_default='active'))
        batch_op.create_index(batch_op.f('ix_user_profiles_status'), ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_profiles_status'))
        batch_op.drop_column('status')
