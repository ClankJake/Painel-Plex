"""Add discord_user_id to user_profiles table

Revision ID: 6c2a1b3d4e5f
Revises: 5b1a2c3d4e5f
Create Date: 2025-07-22 15:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6c2a1b3d4e5f'
down_revision = '5b1a2c3d4e5f'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('discord_user_id', sa.String(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user_profiles', schema=None) as batch_op:
        batch_op.drop_column('discord_user_id')
    # ### end Alembic commands ###
