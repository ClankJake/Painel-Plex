"""O convite passa a poder trazer um Discord ID, além do Telegram

O painel notifica por Telegram, Discord, WhatsApp e webhook, mas só o Telegram
podia ser pré-atribuído a um convite. Quem administra um servidor pelo Discord
— que é a maioria de quem tem um bot — gerava o convite e depois tinha de
vincular a conta à mão, olhando para a lista de utilizadores à procura de quem
acabou de entrar.

A coluna é irmã da `telegram_id` e não a substitui: os convites que já existem
continuam a valer, e as integrações que mandam `telegram_id` também.

Revision ID: b8e1f45c92d7
Revises: f7d3b9e24a16
"""
from alembic import op
import sqlalchemy as sa


revision = 'b8e1f45c92d7'
down_revision = 'f7d3b9e24a16'
branch_labels = None
depends_on = None


def _colunas(bind, tabela):
    return {c['name'] for c in sa.inspect(bind).get_columns(tabela)}


def upgrade():
    bind = op.get_bind()
    if 'discord_id' not in _colunas(bind, 'invitations'):
        op.add_column('invitations', sa.Column('discord_id', sa.String(), nullable=True))


def downgrade():
    bind = op.get_bind()
    if 'discord_id' in _colunas(bind, 'invitations'):
        op.drop_column('invitations', 'discord_id')
