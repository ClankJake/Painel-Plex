"""Remoção suave dos convites

🛡️ Apagar um convite apagava também o "membro desde" de toda a gente que
entrou por ele.

`get_user_claim_date` — a única resposta do painel à pergunta "desde quando é
que esta pessoa está aqui" — procura o username dentro de
`invitations.claimed_by_users`. Não há outra fonte: a data não está no perfil.
Por isso o botão de apagar, que existe para arrumar a lista de convites gastos,
destruía em silêncio o histórico de entrada de cada pessoa que tinha resgatado
aquele código, e a "Minha Conta" dela passava a dizer "Não disponível" para
sempre.

É a mesma decisão que `pix_payments`, `coupons` e `stream_termination_logs` já
tinham tomado: o que custa perder sai das leituras e fica na tabela.

O índice é PARCIAL (`WHERE deleted_at IS NOT NULL`) pelo mesmo motivo dos
outros três: a esmagadora maioria das linhas tem a coluna a NULL, e um índice
completo sobre isso indexa sobretudo nada — o SQLite estima que teria de ler
quase a tabela toda e continua a varrê-la, ficando um índice a custar escritas
sem servir ninguém.

Revision ID: c3f9a1d47b82
Revises: d2a7f14c9b53
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3f9a1d47b82'
down_revision = 'd2a7f14c9b53'
branch_labels = None
depends_on = None


def _colunas(bind, tabela):
    return {c['name'] for c in sa.inspect(bind).get_columns(tabela)}


def _indices(bind, tabela):
    return {i['name'] for i in sa.inspect(bind).get_indexes(tabela)}


def upgrade():
    bind = op.get_bind()

    # ADD COLUMN é nativo no SQLite: não há aqui `batch_alter_table`, e por
    # isso também não há o risco de a reconstrução da tabela deitar fora um
    # índice que o Alembic não consiga reflectir.
    if 'deleted_at' not in _colunas(bind, 'invitations'):
        op.add_column('invitations', sa.Column('deleted_at', sa.DateTime(), nullable=True))

    if 'ix_invitations_deleted_at' not in _indices(bind, 'invitations'):
        op.create_index('ix_invitations_deleted_at', 'invitations', ['deleted_at'],
                        sqlite_where=sa.text('deleted_at IS NOT NULL'))


def downgrade():
    bind = op.get_bind()

    if 'ix_invitations_deleted_at' in _indices(bind, 'invitations'):
        op.drop_index('ix_invitations_deleted_at', table_name='invitations')

    if 'deleted_at' in _colunas(bind, 'invitations'):
        op.drop_column('invitations', 'deleted_at')
