"""Adiciona billing_day a user_profiles (ancora o dia de renovacao)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-08-20 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('billing_day', sa.Integer(), nullable=True))

    # 🔄 Retrocompatibilidade: preenche o 'billing_day' dos utilizadores que já
    # existem, a partir do dia da sua data de vencimento atual.
    #
    # NOTA IMPORTANTE: quem já sofreu a erosão (ex: contratou a 31 e o vencimento
    # já está a 28) fica ancorado no dia 28. Não há forma fiável de recuperar o dia
    # original a partir dos dados existentes — e "adivinhar" que era 31 poderia dar
    # dias a mais a quem contratou mesmo a 28. Preferimos o valor conservador e
    # correto: a partir daqui a erosão PARA, mas não é retroativamente revertida.
    # O administrador pode ajustar manualmente casos concretos, se quiser.
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE user_profiles
           SET billing_day = CAST(strftime('%d', substr(expiration_date, 1, 10)) AS INTEGER)
         WHERE expiration_date IS NOT NULL
           AND expiration_date != ''
           AND billing_day IS NULL
    """))


def downgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_column('billing_day')
