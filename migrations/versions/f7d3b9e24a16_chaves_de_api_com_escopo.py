"""Chaves de API com nome, escopo e revogação

⚠️ Havia UMA chave para tudo (`INTERNAL_TRIGGER_KEY`, no config.json),
partilhada pelo endpoint de convites para bots e pelo webhook do Seerr. Duas
consequências que só se notam no pior dia: regenerá-la porque um bot foi
comprometido derrubava também o Seerr, e a chave dada a um bot de Telegram
podia aceitar webhooks em nome do painel.

🛡️ O que fica gravado é o RESUMO da chave, não a chave — a mesma decisão de
`password_resets`. O que está na tabela (e dentro do ZIP de backup, que é só um
ficheiro) não serve para nada: só quem a copiou no momento em que foi criada a
tem.

⚠️ **A chave antiga continua a funcionar, com todos os escopos.** Invalidá-la
nesta migração seria cortar, de uma vez e sem aviso, todos os bots e
integrações que já existem lá fora — onde este repositório não chega. Ela
deixa de ser a única, não deixa de ser.

Revision ID: f7d3b9e24a16
Revises: e5b2c74f1a39
"""
from alembic import op
import sqlalchemy as sa


revision = 'f7d3b9e24a16'
down_revision = 'e5b2c74f1a39'
branch_labels = None
depends_on = None


def _tabelas(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade():
    bind = op.get_bind()
    if 'api_keys' in _tabelas(bind):
        return

    op.create_table(
        'api_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=80), nullable=False),
        sa.Column('prefixo', sa.String(length=16), nullable=False),
        sa.Column('resumo', sa.String(length=64), nullable=False),
        sa.Column('escopos', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('prefixo'),
    )
    op.create_index('ix_api_keys_prefixo', 'api_keys', ['prefixo'], unique=True)
    op.create_index('ix_api_keys_revoked_at', 'api_keys', ['revoked_at'],
                    sqlite_where=sa.text('revoked_at IS NOT NULL'))


def downgrade():
    bind = op.get_bind()
    if 'api_keys' not in _tabelas(bind):
        return
    op.drop_index('ix_api_keys_revoked_at', table_name='api_keys')
    op.drop_index('ix_api_keys_prefixo', table_name='api_keys')
    op.drop_table('api_keys')
