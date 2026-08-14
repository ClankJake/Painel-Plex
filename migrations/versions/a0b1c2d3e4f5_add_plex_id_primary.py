"""Usa o ID do Plex como chave primária para os utilizadores (versão robusta v4)

Revision ID: a0b1c2d3e4f5
Revises: e5f6a7b8c9d0
Create Date: 2025-09-17 11:10:00.000000

"""
import logging
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, inspect
import json

# Configuração do logger
logger = logging.getLogger('alembic.env')

# revision identifiers, used by Alembic.
revision = 'a0b1c2d3e4f5'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None

def get_plex_user_id_map():
    """Busca os dados dos utilizadores do Plex para mapear username para ID."""
    try:
        from app import create_app
        from app.extensions import plex_manager
        app = create_app()
        with app.app_context():
            plex_manager.reload_connections(from_job=True)
            all_users = plex_manager.get_all_plex_users(force_refresh=True)
            if not all_users:
                logger.warning("Nenhum utilizador encontrado no Plex. O mapeamento de ID estará vazio.")
                return {}
            return {user['username']: int(user['id']) for user in all_users}
    except Exception as e:
        logger.error(f"FALHA CRÍTICA ao conectar ao Plex durante a migração: {e}")
        logger.error("A migração foi abortada. Por favor, garanta que o Plex está acessível e tente novamente.")
        return None

def column_exists(inspector, table_name, column_name):
    """Verifica se uma coluna já existe numa tabela."""
    try:
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        return column_name in columns
    except Exception:
        return False

def upgrade():
    logger.info("A iniciar migração robusta v4 para usar o ID do Plex como chave primária...")
    bind = op.get_bind()
    inspector = inspect(bind)
    user_id_map = get_plex_user_id_map()

    if user_id_map is None:
        raise Exception("Não foi possível obter o mapeamento de utilizadores do Plex. A migração foi abortada para evitar a perda de dados.")

    # --- Passo 0: Limpeza de tentativas anteriores falhadas ---
    logger.info("A limpar tabelas temporárias de migrações anteriores falhadas...")
    op.execute('DROP TABLE IF EXISTS user_profiles_new')
    op.execute('DROP TABLE IF EXISTS blocked_users_new')

    # --- Passo 1: Lidar com a tabela 'user_profiles' ---
    logger.info("A processar a tabela 'user_profiles'...")
    op.create_table('user_profiles_new',
        sa.Column('plex_user_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('telegram_user', sa.String(), nullable=True),
        sa.Column('discord_user_id', sa.String(), nullable=True),
        sa.Column('phone_number', sa.String(), nullable=True),
        sa.Column('expiration_date', sa.String(), nullable=True),
        sa.Column('last_notification_sent', sa.String(), nullable=True),
        sa.Column('trial_end_date', sa.String(), nullable=True),
        sa.Column('trial_job_id', sa.String(), nullable=True),
        sa.Column('expiration_job_id', sa.String(), nullable=True),
        sa.Column('overseerr_access', sa.Boolean(), nullable=True),
        sa.Column('screen_limit', sa.Integer(), nullable=False),
        sa.Column('hide_from_leaderboard', sa.Boolean(), nullable=False),
        sa.Column('libraries', sa.Text(), nullable=True),
        sa.Column('payment_token', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('plex_user_id'),
        sa.UniqueConstraint('payment_token'),
        sa.UniqueConstraint('username')
    )
    profiles = bind.execute(text("SELECT * FROM user_profiles")).mappings().all()
    for profile in profiles:
        plex_id = user_id_map.get(profile['username'])
        if plex_id:
            insert_data = {
                'plex_user_id': plex_id,
                'username': profile.get('username'), 'name': profile.get('name'),
                'telegram_user': profile.get('telegram_user'), 'discord_user_id': profile.get('discord_user_id'),
                'phone_number': profile.get('phone_number'), 'expiration_date': profile.get('expiration_date'),
                'last_notification_sent': profile.get('last_notification_sent'), 'trial_end_date': profile.get('trial_end_date'),
                'trial_job_id': profile.get('trial_job_id'), 'expiration_job_id': profile.get('expiration_job_id'),
                'overseerr_access': profile.get('overseerr_access', False), 'screen_limit': profile.get('screen_limit', 0),
                'hide_from_leaderboard': profile.get('hide_from_leaderboard', False), 'libraries': profile.get('libraries'),
                'payment_token': profile.get('payment_token')
            }
            op.execute(text("""INSERT INTO user_profiles_new (plex_user_id, username, name, telegram_user, discord_user_id, phone_number, expiration_date, last_notification_sent, trial_end_date, trial_job_id, expiration_job_id, overseerr_access, screen_limit, hide_from_leaderboard, libraries, payment_token) VALUES (:plex_user_id, :username, :name, :telegram_user, :discord_user_id, :phone_number, :expiration_date, :last_notification_sent, :trial_end_date, :trial_job_id, :expiration_job_id, :overseerr_access, :screen_limit, :hide_from_leaderboard, :libraries, :payment_token)""").bindparams(**insert_data))
    op.drop_table('user_profiles')
    op.rename_table('user_profiles_new', 'user_profiles')
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_profiles_username'), ['username'], unique=True)
    
    # --- Passo 2: Lidar com a tabela 'blocked_users' ---
    logger.info("A processar a tabela 'blocked_users'...")
    op.create_table('blocked_users_new',
        sa.Column('user_plex_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('blocked_at', sa.String(), nullable=True),
        sa.Column('block_reason', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['user_plex_id'], ['user_profiles.plex_user_id']),
        sa.PrimaryKeyConstraint('user_plex_id')
    )
    blocked_users = bind.execute(text("SELECT * FROM blocked_users")).mappings().all()
    for user in blocked_users:
        plex_id = user_id_map.get(user['username'])
        if plex_id:
            op.execute(text("INSERT INTO blocked_users_new (user_plex_id, username, blocked_at, block_reason) VALUES (:plex_id, :username, :blocked_at, :block_reason)").bindparams(plex_id=plex_id, **user))
    op.drop_table('blocked_users')
    op.rename_table('blocked_users_new', 'blocked_users')

    # --- Passo 3: Lidar com as restantes tabelas ---
    related_tables = {'coupon_usages': 'user_username', 'notifications': 'username', 'pix_payments': 'username', 'stream_termination_logs': 'username', 'unlocked_achievements': 'username'}
    for table_name, old_username_col in related_tables.items():
        logger.info(f"A processar a tabela '{table_name}'...")
        if not column_exists(inspector, table_name, 'user_plex_id'):
            with op.batch_alter_table(table_name, schema=None) as batch_op:
                batch_op.add_column(sa.Column('user_plex_id', sa.Integer(), nullable=True))
        
        records = bind.execute(text(f"SELECT DISTINCT {old_username_col} FROM {table_name}")).mappings().all()
        for record in records:
            username = record[old_username_col]
            if username:
                plex_id = user_id_map.get(username)
                if plex_id:
                    bind.execute(text(f"UPDATE {table_name} SET user_plex_id = :plex_id WHERE {old_username_col} = :username"), {'plex_id': plex_id, 'username': username})
        
        bind.execute(text(f"DELETE FROM {table_name} WHERE user_plex_id IS NULL"))
        
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.alter_column('user_plex_id', nullable=False)
            batch_op.create_foreign_key(f'fk_{table_name}_user_plex_id', 'user_profiles', ['user_plex_id'], ['plex_user_id'])
            if table_name not in ['pix_payments', 'stream_termination_logs', 'unlocked_achievements', 'notifications']:
                batch_op.drop_column(old_username_col)
            if not any(idx['name'] == f'ix_{table_name}_user_plex_id' for idx in inspector.get_indexes(table_name)):
                batch_op.create_index(f'ix_{table_name}_user_plex_id', ['user_plex_id'], unique=False)

    logger.info("Migração para ID do Plex concluída com sucesso.")

def downgrade():
    logger.warning("O downgrade desta migração não é totalmente suportado e pode resultar em perda de dados.")
    pass

