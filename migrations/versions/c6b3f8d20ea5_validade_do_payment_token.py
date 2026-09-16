"""Validade do link de pagamento e marcas de remoção suave

Duas colunas novas que andam juntas por serem a mesma família de decisão: o que
deixa de valer, e o que deixa de estar à vista sem deixar de existir.

🛡️ **`user_profiles.payment_token_expires_at`** — o `payment_token` era uma
credencial PORTADORA eterna. Quem tivesse o link `/pay/<token>` via o nome e o
vencimento de quem lá está e podia gerar cobranças; ele viaja por Telegram,
Discord e WhatsApp, fica no histórico dessas conversas para sempre, e um dump da
base de dados entregava o de toda a gente em texto puro. Agora tem validade,
renovada a cada link enviado e trocada depois de um pagamento confirmado.

⚠️ As linhas que já existem ficam com a coluna a NULL, que quer dizer "sem
validade". É de propósito: invalidar de repente os links que estão no telemóvel
de toda a gente seria uma migração a cortar o acesso a quem quer pagar. Cada um
passa a ter validade no primeiro aviso de vencimento que receber.

🛡️ **`deleted_at` em `pix_payments`, `coupons` e `stream_termination_logs`** —
as três tinham um DELETE direto e sem volta, e são as três que mais custam a
perder: o histórico financeiro, o registo de quem usou cada cupão, e a auditoria
de cortes. Apagar por engano não tinha recuperação nenhuma tirando o backup da
noite anterior.

Revision ID: c6b3f8d20ea5
Revises: b9e4a7c15fd2
"""
from alembic import op
import sqlalchemy as sa


revision = 'c6b3f8d20ea5'
down_revision = 'b9e4a7c15fd2'
branch_labels = None
depends_on = None


TABELAS_COM_REMOCAO_SUAVE = ('pix_payments', 'coupons', 'stream_termination_logs')


def _colunas(bind, tabela):
    inspector = sa.inspect(bind)
    if tabela not in inspector.get_table_names():
        return set()
    return {c['name'] for c in inspector.get_columns(tabela)}


def upgrade():
    bind = op.get_bind()

    if 'payment_token_expires_at' not in _colunas(bind, 'user_profiles'):
        op.add_column('user_profiles',
                      sa.Column('payment_token_expires_at', sa.DateTime(), nullable=True))

    for tabela in TABELAS_COM_REMOCAO_SUAVE:
        if not _colunas(bind, tabela):
            continue
        if 'deleted_at' in _colunas(bind, tabela):
            continue
        op.add_column(tabela, sa.Column('deleted_at', sa.DateTime(), nullable=True))
        # Um índice PARCIAL: quase todas as linhas têm `deleted_at` a NULL, e a
        # pergunta que todas as consultas fazem é exatamente essa. Um índice
        # completo indexaria sobretudo nada e o SQLite continuaria a varrer.
        op.execute(
            f'CREATE INDEX IF NOT EXISTS ix_{tabela}_deleted_at '
            f'ON "{tabela}" (deleted_at) WHERE deleted_at IS NOT NULL'
        )


def downgrade():
    bind = op.get_bind()

    for tabela in TABELAS_COM_REMOCAO_SUAVE:
        if 'deleted_at' not in _colunas(bind, tabela):
            continue
        op.execute(f'DROP INDEX IF EXISTS ix_{tabela}_deleted_at')
        with op.batch_alter_table(tabela) as batch_op:
            batch_op.drop_column('deleted_at')

    if 'payment_token_expires_at' in _colunas(bind, 'user_profiles'):
        with op.batch_alter_table('user_profiles') as batch_op:
            batch_op.drop_column('payment_token_expires_at')
