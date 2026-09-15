"""CHECK nas colunas de domínio fechado, e telefones só com dígitos

⚠️ **Uma coluna `db.String(20)` não impunha nada.** O SQLite **não verifica o
comprimento de um VARCHAR** — um `String(20)` aceita 200 caracteres sem se
queixar — e nunca houve um único `CHECK` no esquema. Gravar
`user_profiles.status = 'qualquer-coisa'` passava, e a partir daí a pessoa não
era nem ativa nem inativa: não aparecia nas listagens, não era bloqueada, não
era removida. Um estado inventado não dá erro — dá um utilizador invisível.

O mesmo valia para um pagamento de valor NEGATIVO, que entrava no somatório do
relatório financeiro e subtraía da receita do mês, e para um `billing_day` a 0
ou a 32, a partir do qual todos os vencimentos seguintes eram calculados.

🐛 **E os telefones passam a ser guardados só com dígitos.** O destinatário do
WhatsApp é montado como `{phone_number}@s.whatsapp.net`: um número escrito da
forma natural — `(11) 99999-9999` — produzia um identificador inválido e a
mensagem não chegava a ninguém, sem erro nenhum. A normalização das linhas que
já existem é feita aqui.

⚠️ Os dados são arrumados ANTES de a restrição entrar. Criar um `CHECK` sobre
linhas que já o violam faz o `flask db upgrade` rebentar — e, em Docker, isso é
um painel que não arranca.

Revision ID: b9e4a7c15fd2
Revises: a4e91c72bd38
"""
from alembic import op
import sqlalchemy as sa


revision = 'b9e4a7c15fd2'
down_revision = 'a4e91c72bd38'
branch_labels = None
depends_on = None


# ⚠️ Os valores são repetidos aqui em vez de virem de `app/dominios.py` de
# propósito: uma migração é uma FOTOGRAFIA do esquema no momento em que foi
# escrita. Se importasse a lista viva, acrescentar um estado novo daqui a um ano
# mudava, retroativamente, o que esta migração faz em quem a correr depois.
ESTADOS_DO_PERFIL = ('active', 'inactive')
ESTADOS_DE_PAGAMENTO = ('ATIVA', 'PROCESSANDO', 'CONCLUIDA', 'FALHOU', 'REVERTIDO')
TIPOS_DE_DESCONTO = ('percentage', 'fixed')
CATEGORIAS_DE_NOTIFICACAO = ('info', 'success', 'warning', 'error')
ESTADOS_DE_TAREFA = ('pending', 'running', 'completed', 'failed')


def _lista(valores):
    return ', '.join(f"'{v}'" for v in valores)


# tabela -> ((nome da restrição, condição SQL), ...)
RESTRICOES = {
    'user_profiles': (
        ('ck_user_profiles_status', f'"status" IN ({_lista(ESTADOS_DO_PERFIL)})'),
        ('ck_user_profiles_billing_day',
         'billing_day IS NULL OR (billing_day >= 1 AND billing_day <= 31)'),
        ('ck_user_profiles_screen_limit', 'screen_limit >= 0'),
        ('ck_user_profiles_xp', 'xp >= 0'),
        ('ck_user_profiles_lifetime_xp', 'lifetime_xp >= 0'),
        ('ck_user_profiles_referral_credit', 'referral_credit >= 0'),
    ),
    'pix_payments': (
        ('ck_pix_payments_status', f'"status" IN ({_lista(ESTADOS_DE_PAGAMENTO)})'),
        ('ck_pix_payments_value', 'value >= 0'),
        ('ck_pix_payments_referral_credit_used', 'referral_credit_used >= 0'),
        ('ck_pix_payments_screens', 'screens IS NULL OR screens >= 0'),
    ),
    'coupons': (
        ('ck_coupons_discount_type', f'"discount_type" IN ({_lista(TIPOS_DE_DESCONTO)})'),
        ('ck_coupons_value', 'value >= 0'),
        ('ck_coupons_max_uses', 'max_uses >= 0'),
        ('ck_coupons_use_count', 'use_count >= 0'),
    ),
    'notifications': (
        ('ck_notifications_category', f'"category" IN ({_lista(CATEGORIAS_DE_NOTIFICACAO)})'),
    ),
    'tasks': (
        ('ck_tasks_status', f'"status" IN ({_lista(ESTADOS_DE_TAREFA)})'),
        ('ck_tasks_progress_current', 'progress_current >= 0'),
        ('ck_tasks_progress_total', 'progress_total >= 0'),
    ),
}

# Como arrumar cada coluna ANTES de a restrição entrar. Nenhuma destas
# instruções apaga linhas: levam o valor inválido para o mais próximo que faça
# sentido, e dizem quantas mexeram.
ARRUMACOES = (
    ('user_profiles', 'status fora do domínio → inactive',
     f"UPDATE user_profiles SET status = 'inactive' "
     f"WHERE status IS NULL OR status NOT IN ({_lista(ESTADOS_DO_PERFIL)})"),
    ('user_profiles', 'billing_day fora de 1-31 → NULL',
     'UPDATE user_profiles SET billing_day = NULL '
     'WHERE billing_day IS NOT NULL AND (billing_day < 1 OR billing_day > 31)'),
    ('user_profiles', 'contadores negativos → 0',
     'UPDATE user_profiles SET screen_limit = MAX(screen_limit, 0), '
     'xp = MAX(xp, 0), lifetime_xp = MAX(lifetime_xp, 0), '
     'referral_credit = MAX(referral_credit, 0) '
     'WHERE screen_limit < 0 OR xp < 0 OR lifetime_xp < 0 OR referral_credit < 0'),
    ('pix_payments', 'status fora do domínio → FALHOU',
     f"UPDATE pix_payments SET status = 'FALHOU' "
     f"WHERE status IS NULL OR status NOT IN ({_lista(ESTADOS_DE_PAGAMENTO)})"),
    ('pix_payments', 'valores negativos → 0',
     'UPDATE pix_payments SET value = MAX(value, 0), '
     'referral_credit_used = MAX(referral_credit_used, 0), '
     'screens = CASE WHEN screens < 0 THEN 0 ELSE screens END '
     'WHERE value < 0 OR referral_credit_used < 0 OR screens < 0'),
    ('coupons', 'tipo de desconto desconhecido → percentage',
     f"UPDATE coupons SET discount_type = 'percentage' "
     f"WHERE discount_type IS NULL OR discount_type NOT IN ({_lista(TIPOS_DE_DESCONTO)})"),
    ('coupons', 'valores negativos → 0',
     'UPDATE coupons SET value = MAX(value, 0), max_uses = MAX(max_uses, 0), '
     'use_count = MAX(use_count, 0) '
     'WHERE value < 0 OR max_uses < 0 OR use_count < 0'),
    ('notifications', 'categoria desconhecida → info',
     f"UPDATE notifications SET category = 'info' "
     f"WHERE category IS NULL OR category NOT IN ({_lista(CATEGORIAS_DE_NOTIFICACAO)})"),
    ('tasks', 'estado desconhecido → failed',
     f"UPDATE tasks SET status = 'failed' "
     f"WHERE status IS NULL OR status NOT IN ({_lista(ESTADOS_DE_TAREFA)})"),
    ('tasks', 'progresso negativo → 0',
     'UPDATE tasks SET progress_current = MAX(progress_current, 0), '
     'progress_total = MAX(progress_total, 0) '
     'WHERE progress_current < 0 OR progress_total < 0'),
    # 🐛 O telefone: tudo o que não for dígito sai, e o que ficar vazio vira NULL.
    ('user_profiles', 'telefones normalizados para só dígitos',
     "UPDATE user_profiles SET phone_number = NULL "
     "WHERE phone_number IS NOT NULL AND TRIM(phone_number) = ''"),
)


def _existe(bind, tabela):
    return tabela in sa.inspect(bind).get_table_names()


def _so_digitos(bind):
    """Reescreve os telefones com caracteres a mais, um a um.

    Em SQL cru não há como tirar "tudo o que não for dígito" — o SQLite não tem
    expressões regulares — e encadear `replace()` para cada símbolo possível
    seria adivinhar. São poucas linhas e corre uma vez.
    """
    if not _existe(bind, 'user_profiles'):
        return

    linhas = bind.execute(sa.text(
        'SELECT media_user_id, phone_number FROM user_profiles '
        'WHERE phone_number IS NOT NULL AND phone_number != ""'
    )).fetchall()

    mexidas = 0
    for identificador, telefone in linhas:
        limpo = ''.join(c for c in str(telefone) if c.isdigit())
        if limpo == telefone:
            continue
        bind.execute(
            sa.text('UPDATE user_profiles SET phone_number = :novo WHERE media_user_id = :id'),
            {'novo': limpo or None, 'id': identificador},
        )
        mexidas += 1

    if mexidas:
        print(
            f"[b9e4a7c15fd2] {mexidas} telefone(s) normalizado(s) para só dígitos: "
            "no formato antigo, a notificação de WhatsApp não chegava a ninguém."
        )


def _tabela_sem_restricoes(bind, tabela):
    """A tabela refletida, sem os `CHECK` que esta migração vai (re)criar.

    O `copy_from` do `batch_alter_table` reconstrói a tabela a partir do que lhe
    dermos. Tirar os nossos daqui torna a migração repetível: correr o upgrade
    depois de um downgrade não tenta criar duas vezes a mesma restrição.
    """
    refletida = sa.Table(tabela, sa.MetaData(), autoload_with=bind)
    nossos = {nome for nome, _condicao in RESTRICOES.get(tabela, ())}
    refletida.constraints = {
        c for c in refletida.constraints
        if not (isinstance(c, sa.CheckConstraint) and c.name in nossos)
    }
    return refletida


# ⚠️ **O `batch_alter_table` deita fora os índices que não sabe reflectir.**
# Um índice sobre uma EXPRESSÃO (`lower(username)`, `upper(code)`) não é
# reflectido pelo SQLAlchemy — ele avisa com um `SAWarning` e segue —, por isso
# a tabela reconstruída nasce sem ele. Sem esta reposição, esta migração
# desfazia em silêncio metade da `f2d8b5a13ce7`: os índices desapareciam, as
# consultas voltavam a varrer as tabelas e nada, em lado nenhum, dizia porquê.
#
# Os índices PARCIAIS (com `WHERE`) sobrevivem; estes dois não. Repõem-se com
# `IF NOT EXISTS` para a migração poder correr mais do que uma vez.
INDICES_DE_EXPRESSAO = (
    ('ix_user_profiles_username_lower', 'user_profiles', '(lower(username))'),
    ('ix_coupons_code_upper', 'coupons', '(upper(code))'),
)


def _repor_indices_de_expressao(bind):
    for nome, tabela, colunas in INDICES_DE_EXPRESSAO:
        if not _existe(bind, tabela):
            continue
        op.execute(f'CREATE INDEX IF NOT EXISTS {nome} ON "{tabela}" {colunas}')


def _aplicar(bind, criar):
    # 🛡️ Sem o modo legado, o RENAME com que o `batch_alter_table` termina
    # reescreve as referências das tabelas filhas para o nome TEMPORÁRIO da
    # tabela em reconstrução — e `user_profiles` é o pai de cinco delas. É o
    # mesmo detalhe que a migração `b7d4e82a16c9` documenta, e que ali custou
    # uma tabela sem chaves.
    e_sqlite = bind.dialect.name == 'sqlite'
    if e_sqlite:
        op.execute('PRAGMA legacy_alter_table=ON')
    try:
        for tabela, restricoes in RESTRICOES.items():
            if not _existe(bind, tabela):
                continue
            with op.batch_alter_table(
                tabela,
                copy_from=_tabela_sem_restricoes(bind, tabela),
                recreate='always',
            ) as batch_op:
                if criar:
                    for nome, condicao in restricoes:
                        batch_op.create_check_constraint(nome, sa.text(condicao))
    finally:
        if e_sqlite:
            op.execute('PRAGMA legacy_alter_table=OFF')

    _repor_indices_de_expressao(bind)


def upgrade():
    bind = op.get_bind()

    for tabela, descricao, instrucao in ARRUMACOES:
        if not _existe(bind, tabela):
            continue
        mexidas = bind.execute(sa.text(instrucao)).rowcount
        if mexidas:
            print(f"[b9e4a7c15fd2] {tabela}: {mexidas} linha(s) arrumada(s) ({descricao}).")

    _so_digitos(bind)
    _aplicar(bind, criar=True)


def downgrade():
    _aplicar(op.get_bind(), criar=False)
