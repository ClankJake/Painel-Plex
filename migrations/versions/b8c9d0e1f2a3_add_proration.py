"""Adiciona is_proration a pix_payments

Revision ID: b8c9d0e1f2a3
Revises: f7a8b9c0d1e2
Create Date: 2026-08-22 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'b8c9d0e1f2a3'
down_revision = 'f7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pix_payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_proration', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('pix_payments', schema=None) as batch_op:
        batch_op.drop_column('is_proration')
