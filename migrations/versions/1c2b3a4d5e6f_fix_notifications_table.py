"""Fix notifications table schema

Revision ID: 1c2b3a4d5e6f
Revises: a0b1c2d3e4f5
Create Date: 2025-09-18 19:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
import logging

# Configuração do logger
logger = logging.getLogger('alembic.env')

# revision identifiers, used by Alembic.
revision = '1c2b3a4d5e6f'
down_revision = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade():
    """
    Corrige o esquema da tabela 'notifications' para garantir que as notificações do sistema funcionem.
    """
    logger.info("A iniciar a correção do esquema da tabela 'notifications'...")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    
    # CORREÇÃO: Adiciona a limpeza da tabela temporária para tornar a migração mais robusta
    logger.info("A limpar tabelas temporárias de migrações anteriores falhadas...")
    op.execute('DROP TABLE IF EXISTS _alembic_tmp_notifications')

    with op.batch_alter_table('notifications', schema=None) as batch_op:
        # Passo 1: Tornar a coluna user_plex_id anulável novamente.
        # A migração anterior a definiu incorretamente como NOT NULL.
        logger.info("A alterar a coluna 'user_plex_id' para permitir valores nulos...")
        batch_op.alter_column('user_plex_id',
               existing_type=sa.INTEGER(),
               nullable=True)
        
        # Passo 2: Remover a coluna 'username', o seu índice e a sua chave estrangeira, que são redundantes.
        
        # CORREÇÃO: Remover explicitamente o índice antes de remover a coluna.
        indexes = inspector.get_indexes('notifications')
        if any(idx['name'] == 'ix_notifications_username' for idx in indexes):
            logger.info("A remover o índice redundante 'ix_notifications_username'...")
            batch_op.drop_index('ix_notifications_username')

        foreign_keys = inspector.get_foreign_keys('notifications')
        fk_name = next((fk['name'] for fk in foreign_keys if fk['referred_table'] == 'user_profiles' and 'username' in fk['constrained_columns']), None)
        if fk_name:
            logger.info(f"A remover a chave estrangeira redundante '{fk_name}'...")
            batch_op.drop_constraint(fk_name, type_='foreignkey')
        
        # Verifica se a coluna 'username' existe antes de a remover.
        columns = [c['name'] for c in inspector.get_columns('notifications')]
        if 'username' in columns:
            logger.info("A remover a coluna redundante 'username'...")
            batch_op.drop_column('username')

    logger.info("Correção do esquema da tabela 'notifications' concluída com sucesso.")


def downgrade():
    """
    O downgrade desta migração não é totalmente suportado e pode deixar o esquema inconsistente.
    """
    logger.warning("O downgrade desta migração não é totalmente suportado e pode deixar o esquema inconsistente.")
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        # Apenas tenta reverter a nulidade da coluna, que é a alteração mais crítica.
        batch_op.alter_column('user_plex_id',
               existing_type=sa.INTEGER(),
               nullable=False)

