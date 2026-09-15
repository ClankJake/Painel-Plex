"""Tabela de auditoria: quem mudou o quê, quando e de onde

Não existia nada. O único rasto de uma ação administrativa era uma linha de
texto no `app.log` — um ficheiro que roda ao fim de alguns megabytes e que as
Configurações têm um botão para truncar. Uma trilha que a própria pessoa
auditada apaga com um clique não é uma trilha.

A tabela não tem chave estrangeira para `user_profiles` de propósito: a
auditoria de uma conta tem de continuar a responder depois de a conta deixar de
existir — que é, precisamente, quando alguém vai perguntar.

Revision ID: a4e91c72bd38
Revises: f2d8b5a13ce7
"""
from alembic import op
import sqlalchemy as sa


revision = 'a4e91c72bd38'
down_revision = 'f2d8b5a13ce7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('ator', sa.String(length=255), nullable=True),
        sa.Column('ator_id', sa.String(length=64), nullable=True),
        sa.Column('acao', sa.String(length=64), nullable=False),
        sa.Column('alvo_tipo', sa.String(length=32), nullable=True),
        sa.Column('alvo_id', sa.String(length=255), nullable=True),
        sa.Column('detalhes', sa.Text(), nullable=True),
        sa.Column('endereco_ip', sa.String(length=45), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_logs_ator_id', 'audit_logs', ['ator_id'])
    op.create_index('ix_audit_logs_acao', 'audit_logs', ['acao'])
    # Em SQL cru por causa do DESC, que a API de índices do Alembic não recebe.
    op.execute('CREATE INDEX ix_audit_logs_timestamp ON audit_logs (timestamp DESC)')
    op.execute('CREATE INDEX ix_audit_logs_acao_timestamp ON audit_logs (acao, timestamp DESC)')


def downgrade():
    op.execute('DROP INDEX IF EXISTS ix_audit_logs_acao_timestamp')
    op.execute('DROP INDEX IF EXISTS ix_audit_logs_timestamp')
    op.drop_index('ix_audit_logs_acao', table_name='audit_logs')
    op.drop_index('ix_audit_logs_ator_id', table_name='audit_logs')
    op.drop_table('audit_logs')
