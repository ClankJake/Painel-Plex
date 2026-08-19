"""Adiciona referral_credit_used a pix_payments

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-08-19 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'e6f7a8b9c0d1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pix_payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('referral_credit_used', sa.Float(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('pix_payments', schema=None) as batch_op:
        batch_op.drop_column('referral_credit_used')
