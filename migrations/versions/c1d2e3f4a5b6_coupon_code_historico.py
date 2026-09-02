"""Torna pix_payments.coupon_code um registo historico (sem chave estrangeira)

Revision ID: c1d2e3f4a5b6
Revises: b8c9d0e1f2a3
Create Date: 2026-09-02 10:00:00.000000

O codigo do cupao guardado numa cobranca e uma FOTOGRAFIA do que foi usado, nao
uma referencia viva ao catalogo de cupoes: apagar uma promocao antiga nao pode
invalidar nem apagar o historico financeiro que ja a citou, e o relatorio CSV
precisa de continuar a mostrar o codigo. A chave estrangeira para 'coupons.code'
tambem nunca chegou a ser imposta pelo SQLite (o PRAGMA foreign_keys nao e
ligado), pelo que so existia como uma inconsistencia a espera de dar problemas
caso alguem a ligasse.

Nota sobre o SQLite: a chave estrangeira original nao tem nome, por isso nao ha
'drop_constraint' possivel. O caminho suportado e recriar a tabela a partir de
uma definicao explicita ('copy_from'), que e o que esta migracao faz.

"""
from alembic import op
import sqlalchemy as sa

revision = 'c1d2e3f4a5b6'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def _pix_payments(com_fk_de_cupao):
    """
    Definicao completa de 'pix_payments'. Tem de listar TODAS as colunas: e a
    partir daqui que a tabela e recriada e os dados copiados, coluna a coluna.
    """
    constraints = [
        sa.PrimaryKeyConstraint('txid'),
        sa.ForeignKeyConstraint(['user_plex_id'], ['user_profiles.plex_user_id']),
        sa.UniqueConstraint('external_reference'),
    ]
    if com_fk_de_cupao:
        constraints.append(sa.ForeignKeyConstraint(['coupon_code'], ['coupons.code']))

    return sa.Table(
        'pix_payments',
        sa.MetaData(),
        sa.Column('txid', sa.String(), nullable=False),
        sa.Column('user_plex_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('provider', sa.String()),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('screens', sa.Integer(), nullable=True),
        sa.Column('external_reference', sa.String(), nullable=True),
        sa.Column('description', sa.String(length=100), nullable=True),
        sa.Column('coupon_code', sa.String(), nullable=True),
        sa.Column('referral_credit_used', sa.Float(), nullable=False, server_default='0'),
        sa.Column('is_proration', sa.Boolean(), nullable=False, server_default='0'),
        *constraints,
    )


def upgrade():
    # 'copy_from' sem a chave estrangeira do cupao: a tabela e recriada com esta
    # estrutura e os dados sao copiados para la.
    with op.batch_alter_table(
        'pix_payments',
        copy_from=_pix_payments(com_fk_de_cupao=False),
        recreate='always',
    ) as batch_op:
        # ⚠️ Recriar a tabela apaga os indices que nao forem declarados aqui.
        # 'ix_pix_payments_user_plex_id' ja existia e serve quase todas as
        # consultas de pagamentos — tem de ser reposto.
        batch_op.create_index('ix_pix_payments_user_plex_id', ['user_plex_id'])
        # Novo: 'coupon_code' passou a ser consultado (auditoria de cupoes e
        # relatorios), pelo que ganha indice proprio.
        batch_op.create_index('ix_pix_payments_coupon_code', ['coupon_code'])


def downgrade():
    # Fora do batch: o indice nao consta da definicao passada em 'copy_from',
    # por isso o batch nao sabe da existencia dele para o poder remover.
    op.drop_index('ix_pix_payments_coupon_code', table_name='pix_payments')
    with op.batch_alter_table(
        'pix_payments',
        copy_from=_pix_payments(com_fk_de_cupao=False),
        recreate='always',
    ) as batch_op:
        batch_op.create_index('ix_pix_payments_user_plex_id', ['user_plex_id'])
        batch_op.create_foreign_key(
            'fk_pix_payments_coupon_code', 'coupons', ['coupon_code'], ['code']
        )
