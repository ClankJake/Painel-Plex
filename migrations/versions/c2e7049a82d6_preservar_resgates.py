"""Preserva o código nos resgates quando o administrador exclui o cupom."""
from alembic import op
import sqlalchemy as sa

revision = 'c2e7049a82d6'
down_revision = 'b8e1f45c92d7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('coupon_usages') as batch:
        batch.add_column(sa.Column('archived_coupon_code', sa.String(), nullable=True))
        batch.alter_column('coupon_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    if op.get_bind().execute(sa.text(
        'SELECT 1 FROM coupon_usages WHERE coupon_id IS NULL LIMIT 1'
    )).first():
        raise RuntimeError('A versão anterior não suporta resgates de cupons excluídos.')
    with op.batch_alter_table('coupon_usages') as batch:
        batch.alter_column('coupon_id', existing_type=sa.Integer(), nullable=False)
        batch.drop_column('archived_coupon_code')
