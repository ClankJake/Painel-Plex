"""Pedidos de reposição de palavra-passe

Só servem onde as contas são LOCAIS (o painel cria-as e é responsável pelas
credenciais). Num painel Plex a tabela fica vazia: a palavra-passe vive no
plex.tv e o painel não tem nada que a repor.

🛡️ A chave primária é o RESUMO (sha256) do token, não o token. Quem lesse a
base de dados — ou um ZIP de backup, que é só um ficheiro — ficava com uma porta
aberta por cada pedido ainda válido.

Revision ID: c5e1a9d3f742
Revises: b7d4e82a16c9
"""
from alembic import op
import sqlalchemy as sa


revision = 'c5e1a9d3f742'
down_revision = 'b7d4e82a16c9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'password_resets',
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('media_user_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['media_user_id'], ['user_profiles.media_user_id'],
                                name='fk_password_resets_user_profiles'),
        sa.PrimaryKeyConstraint('token_hash'),
    )
    with op.batch_alter_table('password_resets', schema=None) as batch_op:
        batch_op.create_index('ix_password_resets_media_user_id', ['media_user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('password_resets', schema=None) as batch_op:
        batch_op.drop_index('ix_password_resets_media_user_id')
    op.drop_table('password_resets')
