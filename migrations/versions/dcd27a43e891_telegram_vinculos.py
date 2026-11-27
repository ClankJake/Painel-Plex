"""Vínculos confirmados do bot de autoatendimento."""
from alembic import op
import sqlalchemy as sa

revision = 'dcd27a43e891'
down_revision = 'c2e7049a82d6'
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('telegram_vinculos'):
        op.create_table('telegram_vinculos',
            sa.Column('media_user_id', sa.String(64), sa.ForeignKey('user_profiles.media_user_id',
                      ondelete='CASCADE', onupdate='CASCADE'), primary_key=True),
            sa.Column('chat_id', sa.String(), unique=True),
            sa.Column('desafio', sa.String(), unique=True),
            sa.Column('validade', sa.DateTime()))


def downgrade():
    op.drop_table('telegram_vinculos')
