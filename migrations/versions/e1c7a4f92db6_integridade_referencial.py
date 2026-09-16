"""Limpa os órfãos e dá um significado a cada chave estrangeira

As chaves estrangeiras existiam no esquema desde sempre e NUNCA foram impostas:
`PRAGMA foreign_keys` fica desligado por omissão no SQLite e ninguém o ligava.
Resultado: apagar um perfil deixava para trás tudo o que lhe apontava, sem erro
nenhum, e era possível gravar um pagamento com um `media_user_id` que nunca
existiu.

Esta migração limpa o que ficou para trás e separa as tabelas em duas famílias,
porque elas não querem a mesma coisa:

* **ESTADO** — um bloqueio, uma notificação, uma conquista, um pedido de
  reposição de palavra-passe, o registo de uso de um cupão. Nada disto faz
  sentido sem o perfil a que pertence, por isso passa a `ON DELETE CASCADE`:
  apagar o perfil leva-os consigo, de propósito e à vista.

* **HISTÓRICO** — os pagamentos e a auditoria de cortes. Estes têm de
  SOBREVIVER ao perfil: um pagamento recebido aconteceu, e apagá-lo porque a
  conta foi removida falsifica o relatório financeiro do mês. Deixam de ter
  chave estrangeira e ficam uma coluna indexada, exatamente pela mesma razão
  que `pix_payments.coupon_code` deixou de apontar para `coupons.code`.

⚠️ Sem esta separação, ligar o PRAGMA obrigava a escolher entre apagar
histórico financeiro ou não ligar nada.

Revision ID: e1c7a4f92db6
Revises: d8f2b4e91c37
"""
from alembic import op
import sqlalchemy as sa


revision = 'e1c7a4f92db6'
down_revision = 'd8f2b4e91c37'
branch_labels = None
depends_on = None


IDENTIDADE = sa.String(length=64)

# Tabelas de ESTADO: (tabela, coluna, é anulável)
ESTADO = (
    ('coupon_usages', 'media_user_id', False),
    ('blocked_users', 'media_user_id', False),
    ('notifications', 'media_user_id', True),
    ('unlocked_achievements', 'media_user_id', False),
    ('password_resets', 'media_user_id', False),
)

# Tabelas de HISTÓRICO: (tabela, coluna, é anulável)
HISTORICO = (
    ('pix_payments', 'media_user_id', False),
    ('stream_termination_logs', 'media_user_id', False),
)

# Colunas LEGADAS: existem em bases de dados que vêm de versões antigas, não têm
# modelo nem código por trás, e apontam para `user_profiles`. Enquanto as chaves
# não eram impostas isso não incomodava ninguém; a partir de agora, um valor que
# lá tenha ficado impediria de apagar o perfil correspondente — por uma coluna
# que nada lê. A chave estrangeira sai; a coluna fica onde está.
LEGADAS = (
    ('invitations', 'owner_plex_id'),
    ('tickets', 'user_plex_id'),
)


def _existe(bind, tabela):
    return tabela in sa.inspect(bind).get_table_names()


def _sem_a_chave_do_perfil(bind, tabela):
    """Reflete a tabela e retira-lhe SÓ a chave estrangeira para `user_profiles`.

    O `copy_from` do `batch_alter_table` reconstrói a tabela a partir do que lhe
    dermos. Sem tirar a chave antiga, a tabela nova nascia com ela de volta e a
    migração não mudava nada — o mesmo detalhe que a `b7d4e82a16c9` documenta.

    ⚠️ **"Só" é a palavra importante.** Tirar todas levava consigo a chave de
    `coupon_usages.coupon_id` para `coupons.id`, que nada tem a ver com o que
    esta migração decide — e ela desaparecia em silêncio, que é exatamente o
    tipo de erro que a migração existe para corrigir.
    """
    refletida = sa.Table(tabela, sa.MetaData(), autoload_with=bind)

    def _e_do_perfil(fk):
        return fk.referred_table.name == 'user_profiles'

    refletida.constraints = {
        c for c in refletida.constraints
        if not (isinstance(c, sa.ForeignKeyConstraint) and _e_do_perfil(c))
    }
    for coluna in refletida.columns:
        coluna.foreign_keys = {
            fk for fk in coluna.foreign_keys
            if fk.column.table.name != 'user_profiles'
        }
    return refletida


def _apagar_orfaos(bind, tabela, coluna):
    """Apaga as linhas que apontam para um perfil que já não existe.

    Devolve quantas apagou. O aviso vai para o log do Alembic com a contagem:
    apagar linhas em silêncio, numa migração, é como elas apareceram.
    """
    if not _existe(bind, tabela):
        return 0

    contagem = bind.execute(sa.text(
        f'SELECT COUNT(*) FROM "{tabela}" WHERE "{coluna}" IS NOT NULL '
        f'AND "{coluna}" NOT IN (SELECT media_user_id FROM user_profiles)'
    )).scalar() or 0

    if contagem:
        bind.execute(sa.text(
            f'DELETE FROM "{tabela}" WHERE "{coluna}" IS NOT NULL '
            f'AND "{coluna}" NOT IN (SELECT media_user_id FROM user_profiles)'
        ))
        print(
            f"[e1c7a4f92db6] {contagem} linha(s) órfã(s) removida(s) de "
            f"'{tabela}': apontavam para um perfil que já não existe."
        )
    return contagem


def _limpar_coupon_usages(bind):
    """Tira de `coupon_usages` o que impede a chave e o par único de entrarem.

    ⚠️ **Os duplicados têm de sair primeiro.** Criar o UNIQUE por cima de linhas
    que já o violam faz a migração rebentar a meio — e o painel fica sem
    arrancar. Mantém-se o registo MAIS ANTIGO de cada par: é o que conta como "o
    cupão já foi usado por esta pessoa", que é a pergunta que a restrição existe
    para responder.
    """
    if not _existe(bind, 'coupon_usages'):
        return

    duplicados = bind.execute(sa.text(
        'DELETE FROM coupon_usages WHERE id NOT IN ('
        '  SELECT MIN(id) FROM coupon_usages GROUP BY media_user_id, coupon_id'
        ')'
    )).rowcount
    if duplicados:
        print(
            f"[e1c7a4f92db6] {duplicados} utilização(ões) duplicada(s) de cupão "
            "removida(s): o mesmo cupão estava registado mais do que uma vez "
            "para a mesma pessoa."
        )

    # Um uso que aponte para um cupão já apagado não passa na chave nova.
    orfaos = bind.execute(sa.text(
        'DELETE FROM coupon_usages WHERE coupon_id NOT IN (SELECT id FROM coupons)'
    )).rowcount
    if orfaos:
        print(
            f"[e1c7a4f92db6] {orfaos} utilização(ões) de cupão removida(s): "
            "citavam um cupão que já não existe."
        )


def upgrade():
    bind = op.get_bind()

    # 0. `coupon_usages` chegou aqui a menos do que o modelo diz: a
    #    reconstrução histórica da `b7d4e82a16c9` deixou-a sem a chave para
    #    `coupons.id` e sem o UNIQUE(media_user_id, coupon_id) — o que impede o
    #    mesmo cupão de ser usado duas vezes pela mesma pessoa. Nos testes
    #    existiam (vêm do `create_all`), em produção não: a garantia era uma
    #    ilusão exatamente onde é precisa.
    #
    #    ⚠️ **Corre ANTES do ciclo abaixo, e a ordem não é arrumação.** É esse
    #    ciclo que cria o UNIQUE, e o SQLite constrói a tabela nova copiando a
    #    antiga para dentro dela: com duplicados por limpar, o INSERT rebentava
    #    a meio com `UNIQUE constraint failed: _alembic_tmp_coupon_usages...` e
    #    o `flask db upgrade` parava — que, em Docker, é um painel que não
    #    arranca.
    _limpar_coupon_usages(bind)

    # 1. As tabelas de ESTADO: limpar primeiro, impor a seguir.
    #    A ordem importa — criar a chave estrangeira sobre linhas já inválidas
    #    deixaria a base de dados num estado que qualquer verificação recusa.
    for tabela, coluna, anulavel in ESTADO:
        _apagar_orfaos(bind, tabela, coluna)

        if not _existe(bind, tabela):
            continue

        with op.batch_alter_table(
            tabela, copy_from=_sem_a_chave_do_perfil(bind, tabela)
        ) as batch_op:
            batch_op.create_foreign_key(
                f'fk_{tabela}_{coluna}_user_profiles',
                'user_profiles', [coluna], ['media_user_id'],
                ondelete='CASCADE', onupdate='CASCADE',
            )
            if tabela == 'coupon_usages':
                batch_op.create_foreign_key(
                    'fk_coupon_usages_coupon_id_coupons',
                    'coupons', ['coupon_id'], ['id'],
                )
                batch_op.create_unique_constraint(
                    '_user_coupon_uc', ['media_user_id', 'coupon_id']
                )

    # 2. As tabelas de HISTÓRICO: a chave estrangeira sai, o índice fica.
    #    Nada é apagado — é esse o ponto.
    for tabela, coluna, anulavel in HISTORICO:
        if not _existe(bind, tabela):
            continue

        # ⚠️ `recreate='always'` não é decoração: sem ele, um `batch_alter_table`
        # de corpo VAZIO não reconstrói nada — o Alembic só copia a tabela
        # quando há uma operação que o obrigue, e aqui a operação É a cópia
        # (nascer sem as chaves estrangeiras que o `copy_from` já não descreve).
        # A migração corria toda, dizia que sim, e as chaves ficavam lá.
        with op.batch_alter_table(
            tabela,
            copy_from=_sem_a_chave_do_perfil(bind, tabela),
            recreate='always',
        ):
            pass

    # 2b. As colunas legadas perdem a chave estrangeira (ver LEGADAS).
    for tabela, coluna in LEGADAS:
        if not _existe(bind, tabela):
            continue
        colunas = {c['name'] for c in sa.inspect(bind).get_columns(tabela)}
        if coluna not in colunas:
            continue
        with op.batch_alter_table(
            tabela,
            copy_from=_sem_a_chave_do_perfil(bind, tabela),
            recreate='always',
        ):
            pass

    # 3. Uma verificação final: se ficou alguma violação, é melhor saber agora,
    #    com a base de dados ainda intacta, do que no primeiro INSERT em
    #    produção com o PRAGMA já ligado.
    if bind.dialect.name == 'sqlite':
        violacoes = bind.execute(sa.text('PRAGMA foreign_key_check')).fetchall()
        if violacoes:
            raise RuntimeError(
                "Ainda há violações de integridade depois da limpeza: "
                f"{violacoes[:10]}. A migração parou para não deixar o painel "
                "a arrancar com as chaves estrangeiras ligadas sobre dados "
                "inconsistentes."
            )


def downgrade():
    """Repõe as chaves estrangeiras como estavam: sem ON DELETE e em todas.

    Não devolve os órfãos apagados — nenhuma migração consegue. Quem precise
    deles tem o backup anterior ao upgrade.
    """
    bind = op.get_bind()

    for tabela, coluna, anulavel in ESTADO + HISTORICO:
        if not _existe(bind, tabela):
            continue

        with op.batch_alter_table(
            tabela, copy_from=_sem_a_chave_do_perfil(bind, tabela)
        ) as batch_op:
            batch_op.create_foreign_key(
                f'fk_{tabela}_{coluna}_user_profiles',
                'user_profiles', [coluna], ['media_user_id'],
            )
