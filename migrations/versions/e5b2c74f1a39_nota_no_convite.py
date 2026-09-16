"""Uma nota para identificar o convite

O painel já guardava para QUEM um convite era, mas só num caso: quando havia
Telegram. Todos os outros — o link gerado para alguém que pediu no WhatsApp,
o código dado numa promoção, o convite de teste de uma pessoa concreta —
ficavam a ser uma linha com um código aleatório e mais nada.

Três meses depois, a pergunta "de quem era este?" não tinha resposta enquanto
ninguém o resgatasse, e um convite gasto só dizia o nome de quem o usou — não
o de quem o devia ter usado.

Revision ID: e5b2c74f1a39
Revises: c3f9a1d47b82
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5b2c74f1a39'
down_revision = 'c3f9a1d47b82'
branch_labels = None
depends_on = None


def _colunas(bind, tabela):
    return {c['name'] for c in sa.inspect(bind).get_columns(tabela)}


def upgrade():
    bind = op.get_bind()
    if 'note' not in _colunas(bind, 'invitations'):
        op.add_column('invitations', sa.Column('note', sa.String(length=200), nullable=True))


def downgrade():
    bind = op.get_bind()
    if 'note' in _colunas(bind, 'invitations'):
        op.drop_column('invitations', 'note')
