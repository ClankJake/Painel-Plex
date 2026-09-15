"""Os valores que cada coluna de domínio fechado aceita, num sítio só.

⚠️ **Estavam escritos à mão em ambos os lados e em lado nenhum.** Uma coluna
declarada `db.String(20)` não impede nada: o SQLite **não impõe o comprimento de
um VARCHAR** (um `String(20)` aceita 200 caracteres sem se queixar) e nunca
houve `CHECK` nenhum. Gravar `status = 'qualquer-coisa'` num perfil passava —
e a partir daí a pessoa não era nem ativa nem inativa: não aparecia nas
listagens, não era bloqueada, não era removida. Um estado inventado não dá erro,
dá um utilizador invisível.

Este módulo é a fonte única: os modelos constroem os `CHECK` a partir daqui, e
quem quiser validar antes de gravar pergunta à mesma lista. Uma migração NÃO
importa daqui — uma migração é uma fotografia do esquema no momento em que foi
escrita, e tem de continuar a correr igual quando esta lista mudar.
"""

# O perfil está ativo ou inativo. Não há terceiro estado: o "expirado" e o "a
# expirar" que a interface mostra são CALCULADOS a partir da data de vencimento
# (ver `_get_expiration_details`), não guardados.
ESTADOS_DO_PERFIL = ('active', 'inactive')

# O ciclo de vida de uma cobrança PIX.
ESTADOS_DE_PAGAMENTO = ('ATIVA', 'PROCESSANDO', 'CONCLUIDA', 'FALHOU', 'REVERTIDO')

# O tipo de desconto de um cupão. Já era validado no schema Pydantic da rota de
# criação; faltava na coluna, que é por onde passam as migrações, os restauros
# de backup e qualquer código que não venha por essa rota.
TIPOS_DE_DESCONTO = ('percentage', 'fixed')

# A cor/ícone de uma notificação na interface.
CATEGORIAS_DE_NOTIFICACAO = ('info', 'success', 'warning', 'error')

# O estado de uma tarefa da fila de envios em massa.
ESTADOS_DE_TAREFA = ('pending', 'running', 'completed', 'failed')


def clausula_in(coluna, valores, anulavel=False):
    """O texto SQL de um `CHECK` que limita `coluna` a `valores`.

    Devolve texto e não um objeto do SQLAlchemy para poder ser usado tal e qual
    tanto num modelo como numa consulta de diagnóstico.
    """
    lista = ', '.join(f"'{v}'" for v in valores)
    condicao = f'"{coluna}" IN ({lista})'
    return f'"{coluna}" IS NULL OR {condicao}' if anulavel else condicao
