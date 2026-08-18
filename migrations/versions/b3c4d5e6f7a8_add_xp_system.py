"""Adiciona xp_last_sync_at a user_profiles

Revision ID: b3c4d5e6f7a8
Revises: a7b8c9d0e1f2
Create Date: 2026-08-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7a8'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    # 🐛 CORREÇÃO: a coluna 'xp' já é adicionada pela migration 'c92625823728'
    # (mais antiga, ver 'add_tickets_referral_and_gamification'). Tentar
    # adicioná-la de novo aqui quebra o 'flask db upgrade' com erro de
    # "coluna duplicada". Esta migration agora só adiciona o que falta:
    # 'xp_last_sync_at', usado para sincronização incremental de XP.
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('xp_last_sync_at', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_column('xp_last_sync_at')
