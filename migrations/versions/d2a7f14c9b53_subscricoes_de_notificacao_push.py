"""Tabela dos aparelhos subscritos para notificações push

🔔 O painel passa a poder avisar no celular (ou no navegador do computador) o
que até agora só aparecia no sino de quem tivesse a página aberta: um pagamento
confirmado e um pedido de conteúdo novo à espera de aprovação.

Cada linha é um APARELHO, não uma pessoa: o navegador do computador, o celular
e o painel instalado na tela de início têm subscrições diferentes, com chaves
próprias. O `endpoint` é o endereço que o serviço de push (Google, Mozilla,
Apple) deu àquele aparelho, e é ÚNICO — um navegador que volte a subscrever
devolve o mesmo, e sem a restrição ficavam linhas repetidas a entregar a mesma
notificação duas e três vezes.

⚠️ **`media_user_id` a NULL quer dizer ADMINISTRADOR**, a mesma convenção que
`notifications` já usava. Isso é também o que permite ao dono do painel
subscrever antes de ter perfil local — ele só passa a ter um no primeiro login
depois da versão que o cria.

A chave estrangeira é da família do ESTADO: `ON DELETE CASCADE` (um aparelho
subscrito não faz sentido sem o perfil) e `ON UPDATE CASCADE`, que é o que
permite ao `migrar_identidade` trocar a chave primária de um perfil cuja conta
foi recriada no servidor.

Revision ID: d2a7f14c9b53
Revises: c6b3f8d20ea5
"""
from alembic import op
import sqlalchemy as sa


revision = 'd2a7f14c9b53'
down_revision = 'c6b3f8d20ea5'
branch_labels = None
depends_on = None


def _tabelas(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade():
    bind = op.get_bind()
    if 'push_subscriptions' in _tabelas(bind):
        return

    op.create_table(
        'push_subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('media_user_id', sa.String(length=64), nullable=True),
        sa.Column('endpoint', sa.String(length=512), nullable=False),
        sa.Column('p256dh', sa.String(length=255), nullable=False),
        sa.Column('auth', sa.String(length=64), nullable=False),
        sa.Column('device_label', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_success_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['media_user_id'], ['user_profiles.media_user_id'],
            ondelete='CASCADE', onupdate='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        # Uma RESTRIÇÃO e não um índice único: é assim que o modelo a declara
        # (`unique=True` na coluna), e o esquema das migrações tem de ser o
        # mesmo que o `create_all` constrói — há um teste que os compara.
        sa.UniqueConstraint('endpoint'),
    )
    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_push_subscriptions_media_user_id'), ['media_user_id'], unique=False)


def downgrade():
    bind = op.get_bind()
    if 'push_subscriptions' not in _tabelas(bind):
        return

    with op.batch_alter_table('push_subscriptions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_push_subscriptions_media_user_id'))
    op.drop_table('push_subscriptions')
