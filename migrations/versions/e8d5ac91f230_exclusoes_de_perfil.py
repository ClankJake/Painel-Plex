"""Recupera as exclusões permanentes para a elegibilidade de testes."""
import json

from alembic import op
import sqlalchemy as sa

revision = 'e8d5ac91f230'
down_revision = 'dcd27a43e891'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('exclusoes_de_perfil'):
        op.create_table('exclusoes_de_perfil',
                        sa.Column('media_user_id', sa.String(64), primary_key=True),
                        sa.Column('username', sa.String),
                        sa.Column('excluido_em', sa.DateTime, nullable=False))
        op.create_index('ix_exclusoes_de_perfil_username', 'exclusoes_de_perfil', ['username'])
    # Um registro antigo só serve como prova de exclusão quando o perfil já saiu.
    eventos = bind.execute(sa.text("""
        SELECT alvo_id, timestamp, detalhes FROM audit_logs
        WHERE acao = 'utilizador.apagar_permanentemente' AND alvo_tipo = 'utilizador'
          AND alvo_id IS NOT NULL AND timestamp IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM user_profiles WHERE media_user_id = alvo_id)
        ORDER BY timestamp DESC
    """)).mappings()
    vistos = set()
    for evento in eventos:
        uid = evento['alvo_id']
        if uid in vistos:
            continue
        vistos.add(uid)
        try:
            nome = json.loads(evento['detalhes'])['perfil']['username']
        except (ValueError, TypeError, KeyError):
            nome = None
        bind.execute(sa.text("""
            INSERT INTO exclusoes_de_perfil (media_user_id, username, excluido_em)
            SELECT :uid, :nome, :data WHERE NOT EXISTS
            (SELECT 1 FROM exclusoes_de_perfil WHERE media_user_id = :uid)
        """), dict(uid=uid, nome=nome, data=evento['timestamp']))


def downgrade():
    op.drop_table('exclusoes_de_perfil')
