"""Índices para as consultas que varriam tabelas inteiras

Seis índices, escolhidos por consultas que existem e correm sozinhas — não por
precaução. Duas famílias:

**As colunas que ninguém tinha indexado.** O resumo financeiro e a limpeza de
cobranças abandonadas filtram `pix_payments` por estado e data; as varreduras da
madrugada procuram os perfis com vencimento ou fim de teste; e a marca de água
da importação de cortes (`get_last_termination_timestamp`) filtra por razão e
ordena por data, de cinco em cinco minutos, sobre a única tabela que nunca
encolhe.

**Os índices que existiam e não eram usados.** `user_profiles.username` e
`coupons.code` estão indexados desde sempre, mas todas as consultas os comparam
em minúsculas ou maiúsculas — `func.lower(username)`, `func.upper(code)` — e o
SQLite não usa o índice de uma coluna quando a comparação é sobre uma EXPRESSÃO
dela. O índice existia, ficava por usar, e ninguém reparava porque o plano de
consulta não aparece em lado nenhum. A correção é um índice sobre a mesma
expressão.

Revision ID: f2d8b5a13ce7
Revises: e1c7a4f92db6
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2d8b5a13ce7'
down_revision = 'e1c7a4f92db6'
branch_labels = None
depends_on = None


# (nome, tabela, expressão SQL das colunas)
#
# Vão em SQL cru, e não pelo `op.create_index`, porque dois deles são sobre uma
# FUNÇÃO das colunas — que é precisamente o ponto — e a API de índices do
# Alembic só recebe nomes de colunas.
INDICES = (
    ('ix_stream_termination_logs_reason_timestamp', 'stream_termination_logs',
     '(reason, timestamp DESC)'),
    ('ix_pix_payments_status_created_at', 'pix_payments', '(status, created_at)'),
    # ⚠️ PARCIAIS, com a mesma condição das consultas que os vão usar. Um
    # índice completo sobre uma coluna maioritariamente NULL indexa sobretudo
    # nada — e o SQLite, estimando que teria de ler quase a tabela toda,
    # continuava a varrê-la. Ficava um índice a custar escritas e a não servir
    # ninguém. Confirmado com `EXPLAIN QUERY PLAN` nos dois formatos.
    ('ix_user_profiles_expiration_date', 'user_profiles',
     "(expiration_date) WHERE expiration_date IS NOT NULL AND expiration_date != ''"),
    ('ix_user_profiles_trial_end_date', 'user_profiles',
     "(trial_end_date) WHERE trial_end_date IS NOT NULL AND trial_end_date != ''"),
    ('ix_user_profiles_username_lower', 'user_profiles', '(lower(username))'),
    ('ix_coupons_code_upper', 'coupons', '(upper(code))'),
)


# Índices cujo NOME ficou preso à grafia antiga da coluna. A coluna passou a
# chamar-se `media_user_id` na migração `b7d4e82a16c9`, mas o índice manteve o
# nome `..._user_plex_id` — cobre a coluna certa, chama-se pelo nome errado.
#
# ⚠️ Não é cosmética: o `create_all` (que é o que os testes usam) cria-os com o
# nome do modelo, e o `flask db upgrade` deixava-os com o nome antigo. As duas
# bases de dados ficavam diferentes, e qualquer verificação de paridade entre
# elas tinha de levar uma exceção à frente — que é onde as divergências a sério
# se escondem. Renomear um índice no SQLite é apagá-lo e criá-lo: não há cópia
# de tabela nenhuma.
RENOMEADOS = (
    ('ix_coupon_usages_user_plex_id', 'ix_coupon_usages_media_user_id',
     'coupon_usages', '(media_user_id)'),
    ('ix_notifications_user_plex_id', 'ix_notifications_media_user_id',
     'notifications', '(media_user_id)'),
    ('ix_pix_payments_user_plex_id', 'ix_pix_payments_media_user_id',
     'pix_payments', '(media_user_id)'),
    ('ix_stream_termination_logs_user_plex_id', 'ix_stream_termination_logs_media_user_id',
     'stream_termination_logs', '(media_user_id)'),
    ('ix_unlocked_achievements_user_plex_id', 'ix_unlocked_achievements_media_user_id',
     'unlocked_achievements', '(media_user_id)'),
)


def _existe(bind, tabela):
    return tabela in sa.inspect(bind).get_table_names()


def _renomear(antigo, novo, tabela, colunas):
    bind = op.get_bind()
    if not _existe(bind, tabela):
        return
    op.execute(f'DROP INDEX IF EXISTS {antigo}')
    op.execute(f'CREATE INDEX IF NOT EXISTS {novo} ON "{tabela}" {colunas}')


def upgrade():
    bind = op.get_bind()
    for nome, tabela, colunas in INDICES:
        if not _existe(bind, tabela):
            continue
        op.execute(f'CREATE INDEX IF NOT EXISTS {nome} ON "{tabela}" {colunas}')

    for antigo, novo, tabela, colunas in RENOMEADOS:
        _renomear(antigo, novo, tabela, colunas)


def downgrade():
    for nome, _tabela, _colunas in INDICES:
        op.execute(f'DROP INDEX IF EXISTS {nome}')

    for antigo, novo, tabela, colunas in RENOMEADOS:
        _renomear(novo, antigo, tabela, colunas)
