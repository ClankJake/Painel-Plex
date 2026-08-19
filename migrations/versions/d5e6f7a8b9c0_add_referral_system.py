"""Adiciona sistema de referencia (indique e ganhe)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-08-19 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd5e6f7a8b9c0'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade():
    # ⚠️ NOTA: a coluna 'referred_by' NÃO é criada aqui — já foi adicionada pela
    # migração 'c92625823728' (add_tickets_referral_and_gamification), onde ficou
    # órfã (sem modelo ORM nem código a usá-la). Recriá-la faria o upgrade falhar
    # com erro de coluna duplicada. Aqui só acrescentamos o que falta.
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('referral_code', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('referral_rewarded', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('referral_credit', sa.Float(), nullable=False, server_default='0'))
        batch_op.create_index(batch_op.f('ix_user_profiles_referral_code'), ['referral_code'], unique=True)


def downgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_profiles_referral_code'))
        batch_op.drop_column('referral_credit')
        batch_op.drop_column('referral_rewarded')
        batch_op.drop_column('referral_code')
