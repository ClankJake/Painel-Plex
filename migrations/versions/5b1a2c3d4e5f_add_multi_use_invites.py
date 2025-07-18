"""Adiciona convites de uso múltiplo e códigos personalizados

Revision ID: 5b1a2c3d4e5f
Revises: 4a5b6c7d8e9f
Create Date: 2025-07-18 18:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
import json


# revision identifiers, used by Alembic.
revision = '5b1a2c3d4e5f'
down_revision = '4a5b6c7d8e9f'
branch_labels = None
depends_on = None


def upgrade():
    # CORREÇÃO: Remove a tabela temporária se ela existir de uma execução anterior falhada.
    # Isto torna a migração mais robusta contra interrupções.
    op.execute('DROP TABLE IF EXISTS _alembic_tmp_invitations')

    # Adiciona as novas colunas à tabela 'invitations'
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_uses', sa.Integer(), nullable=False, server_default='1'))
        batch_op.add_column(sa.Column('use_count', sa.Integer(), nullable=False, server_default='0'))
        # Altera a coluna 'claimed_by' para 'claimed_by_users' para armazenar uma lista JSON de utilizadores
        batch_op.alter_column('claimed_by', new_column_name='claimed_by_users', type_=sa.Text(), existing_type=sa.String())

    # Preenche os dados existentes para garantir a compatibilidade,
    # fazendo a transformação dos dados em Python para evitar dependências de funções do SQLite.
    connection = op.get_bind()
    invitations_to_update = connection.execute(sa.text("""
        SELECT code, claimed_by_users FROM invitations
        WHERE claimed_by_users IS NOT NULL AND claimed_by_users != '';
    """)).fetchall()

    for code, claimed_by_user in invitations_to_update:
        # Verifica se o valor já não é um array JSON
        if claimed_by_user and not claimed_by_user.strip().startswith('['):
            # Cria a string do array JSON manualmente
            json_array_string = json.dumps([claimed_by_user])
            # Executa a atualização para esta linha específica
            connection.execute(
                sa.text("UPDATE invitations SET claimed_by_users = :claimed_users WHERE code = :code"),
                {'claimed_users': json_array_string, 'code': code}
            )


def downgrade():
    # Reverte as alterações na tabela 'invitations'
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.alter_column('claimed_by_users', new_column_name='claimed_by', type_=sa.String(), existing_type=sa.Text())
        batch_op.drop_column('use_count')
        batch_op.drop_column('max_uses')
