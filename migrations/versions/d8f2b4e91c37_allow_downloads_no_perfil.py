"""A permissão de download passa a ficar no perfil

🐛 `allow_downloads` era escrito e descartado em silêncio. O convite guarda-o
(`invitations.allow_downloads`) e o resgate mandava-o para `set_user_profile`,
mas `user_profiles` não tinha a coluna — o SQLAlchemy ignorava a chave e ninguém
dava por isso.

O sintoma aparecia meses depois: `restaurar_acesso` repõe o acesso a partir do
PERFIL, e lia `profile.get('allow_downloads', False)` — sempre False. Quem
tinha download e era bloqueado por falta de pagamento voltava, depois de pagar,
sem poder descarregar nada. Valia nos dois backends.

Fica ao lado de `libraries` porque é a mesma decisão: o que a pessoa pode ver, e
se pode levar consigo.

⚠️ O valor de partida é 0 (não pode descarregar) para toda a gente que já cá
está. Adivinhar o contrário seria conceder uma permissão que ninguém pediu; o
administrador acerta o que for preciso na página de utilizadores, e a partir daí
o valor sobrevive às reativações.

Revision ID: d8f2b4e91c37
Revises: c5e1a9d3f742
"""
from alembic import op
import sqlalchemy as sa


revision = 'd8f2b4e91c37'
down_revision = 'c5e1a9d3f742'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'allow_downloads', sa.Boolean(), nullable=False, server_default=sa.text('0')
        ))


def downgrade():
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_column('allow_downloads')
