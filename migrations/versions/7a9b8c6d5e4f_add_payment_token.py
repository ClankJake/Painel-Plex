"""Adiciona payment_token aos perfis de utilizador

Revision ID: 7a9b8c6d5e4f
Revises: 6c2a1b3d4e5f
Create Date: 2025-07-23 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import secrets

# revision identifiers, used by Alembic.
revision = '7a9b8c6d5e4f'
down_revision = '6c2a1b3d4e5f'
branch_labels = None
depends_on = None


def upgrade():
    # Adiciona a coluna permitindo nulos temporariamente
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_token', sa.String(), nullable=True))
        batch_op.create_unique_constraint('uq_payment_token', ['payment_token'])

    # Popula a nova coluna com tokens únicos para todos os utilizadores existentes
    connection = op.get_bind()
    user_profiles = connection.execute(sa.text("SELECT username FROM user_profiles")).fetchall()
    
    for user in user_profiles:
        token = secrets.token_urlsafe(16)
        connection.execute(
            sa.text("UPDATE user_profiles SET payment_token = :token WHERE username = :username"),
            {'token': token, 'username': user[0]}
        )

    # Altera a coluna para não permitir mais nulos
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.alter_column('payment_token', nullable=False)


def downgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_constraint('uq_payment_token', type_='unique')
        batch_op.drop_column('payment_token')
