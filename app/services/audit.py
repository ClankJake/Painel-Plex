"""A trilha de auditoria: quem mudou o quê, quando e de onde.

🛡️ Antes disto, o único rasto de uma ação administrativa era uma linha de texto
no `app.log`. Esse ficheiro roda ao fim de alguns megabytes e tem, nas
Configurações, um botão que o trunca — quem quisesse apagar o próprio rasto
tinha-o à distância de um clique. E as mudanças que mais interessam nem
chegavam lá: gravar as Configurações reescreve preços, URLs e credenciais dos
gateways sem escrever uma única linha sobre o que mudou.

Três regras que este módulo existe para guardar:

- 🛡️ **nenhum segredo entra na auditoria.** De uma credencial regista-se que
  mudou, nunca o valor antigo nem o novo. Uma auditoria que guarda segredos é
  mais uma cópia dos segredos — e ela é lida por uma rota, exportada num
  backup e copiada para onde quer que a base de dados vá;

- ⚠️ **falhar a registar nunca pode derrubar a ação.** Um erro aqui vira um
  aviso no log e mais nada: perder a linha de auditoria é mau, perder o
  pagamento que ela descreve é muito pior;

- ⚠️ **a escrita é INDEPENDENTE da transação de quem chama.** Vai por uma
  ligação própria, e não pela `db.session`. Se fosse pela sessão partilhada, o
  `commit` da auditoria levaria consigo tudo o que o chamador tivesse pendente
  — incluindo alterações a meio, que ele ainda podia querer desfazer. Por isso
  também se chama SEMPRE depois de a ação estar feita: o que fica registado
  aconteceu mesmo.
"""

import json
import logging
from datetime import datetime, timezone

from flask import has_request_context, request
from flask_login import current_user
from sqlalchemy import text

from ..extensions import db

logger = logging.getLogger(__name__)

# Uma chave cujo NOME contenha um destes pedaços nunca tem o valor registado.
# É uma regra sobre o nome, e não uma lista de chaves conhecidas, de propósito:
# a credencial que um gateway novo traga amanhã fica coberta sem ninguém se
# lembrar de a acrescentar a lado nenhum.
PEDACOS_SENSIVEIS = ('TOKEN', 'KEY', 'SECRET', 'PASSWORD', 'SENHA', 'PASS')

OCULTADO = '(oculto)'
MUDOU = '(alterado)'


def _e_sensivel(chave):
    return any(pedaco in str(chave).upper() for pedaco in PEDACOS_SENSIVEIS)


def _quem():
    """O nome e o id de quem está a agir, ou (None, None) fora de um pedido.

    As tarefas de fundo e os webhooks dos gateways não têm utilizador — e
    inventar um seria pior do que a coluna ficar vazia, porque a coluna vazia
    diz a verdade: isto não partiu de ninguém a clicar.
    """
    try:
        if has_request_context() and current_user.is_authenticated:
            return getattr(current_user, 'username', None), getattr(current_user, 'id', None)
    except Exception:
        pass
    return None, None


def _de_onde():
    """O endereço de quem fez o pedido, já a contar com o proxy à frente.

    A aplicação tem `ProxyFix` instalado, por isso o `remote_addr` já é o do
    cliente e não o do proxy — não se lê o cabeçalho à mão.
    """
    if not has_request_context():
        return None
    return request.remote_addr


def diferenca(antes, depois, apenas=None):
    """O que mudou entre dois dicionários, pronto para ir em `detalhes`.

    Devolve `{chave: {'antes': ..., 'depois': ...}}` só com as chaves que
    MUDARAM — uma auditoria que repete o estado inteiro a cada gravação é
    ilegível, e é o mesmo que não ter nenhuma.

    🛡️ Onde a chave é uma credencial, os dois valores são substituídos por uma
    marca: fica registado que mudou (que é a informação de segurança que
    interessa) sem ficar registado o quê.

    ⚠️ `registar` volta a passar tudo por `_serializavel`, que é mais grosseiro
    e substitui o par inteiro por `(oculto)` quando a chave é sensível. As duas
    passagens são de propósito: esta dá a mensagem legível, a outra é a rede
    por baixo — se um dia alguém chamar `registar` com um dicionário que não
    passou por aqui, o segredo continua a não entrar.

    `apenas` limita a comparação a um conjunto de chaves, para quem só quer
    auditar parte de um dicionário grande.
    """
    antes = antes or {}
    depois = depois or {}
    chaves = set(antes) | set(depois)
    if apenas is not None:
        chaves &= set(apenas)

    mudancas = {}
    for chave in sorted(chaves):
        anterior = antes.get(chave)
        novo = depois.get(chave)
        if anterior == novo:
            continue
        if _e_sensivel(chave):
            mudancas[chave] = {'antes': MUDOU, 'depois': MUDOU}
        else:
            mudancas[chave] = {'antes': anterior, 'depois': novo}
    return mudancas


def _serializavel(valor, _profundidade=0):
    """Reduz `valor` a algo que o `json.dumps` aceite, sem rebentar.

    Um `detalhes` que não serializa perderia a linha inteira — e o que lá chega
    vem de dicionários de perfil com datas, `Decimal` e o que mais houver.
    """
    if _profundidade > 4:
        return str(valor)
    if valor is None or isinstance(valor, (bool, int, float, str)):
        return valor
    if isinstance(valor, dict):
        return {
            str(k): (OCULTADO if _e_sensivel(k) else _serializavel(v, _profundidade + 1))
            for k, v in valor.items()
        }
    if isinstance(valor, (list, tuple, set)):
        return [_serializavel(v, _profundidade + 1) for v in valor]
    return str(valor)


def registar(acao, alvo_tipo=None, alvo_id=None, detalhes=None):
    """Grava uma entrada na auditoria. Nunca levanta.

    `acao` é um verbo curto e estável (`definicoes.gravar`, `pagamento.apagar`):
    é por ele que se filtra meses depois, por isso não deve mudar de nome com a
    interface.
    """
    try:
        ator, ator_id = _quem()
        corpo = None
        if detalhes:
            corpo = json.dumps(_serializavel(detalhes), ensure_ascii=False)[:8000]

        # ⚠️ Ligação PRÓPRIA, com commit próprio: ver o cabeçalho do módulo.
        with db.engine.begin() as ligacao:
            ligacao.execute(
                text(
                    'INSERT INTO audit_logs '
                    '(timestamp, ator, ator_id, acao, alvo_tipo, alvo_id, detalhes, endereco_ip) '
                    'VALUES (:t, :ator, :ator_id, :acao, :alvo_tipo, :alvo_id, :detalhes, :ip)'
                ),
                {
                    't': datetime.now(timezone.utc).replace(tzinfo=None),
                    'ator': ator,
                    'ator_id': str(ator_id) if ator_id is not None else None,
                    'acao': acao,
                    'alvo_tipo': alvo_tipo,
                    'alvo_id': str(alvo_id) if alvo_id is not None else None,
                    'detalhes': corpo,
                    'ip': _de_onde(),
                },
            )
    except Exception as e:
        # Ver a segunda regra do cabeçalho: um erro aqui não pode derrubar nada.
        logger.warning(f"Não foi possível registar '{acao}' na auditoria: {e}")


def listar(limite=100, acao=None, desvio=0):
    """As entradas mais recentes, da mais nova para a mais antiga."""
    from ..models import AuditLog

    consulta = AuditLog.query
    if acao:
        consulta = consulta.filter(AuditLog.acao == acao)

    linhas = (consulta.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
              .offset(max(0, int(desvio)))
              .limit(max(1, min(int(limite), 500)))
              .all())

    return [{
        'id': l.id,
        'timestamp': l.timestamp.isoformat() if l.timestamp else None,
        'ator': l.ator,
        'ator_id': l.ator_id,
        'acao': l.acao,
        'alvo_tipo': l.alvo_tipo,
        'alvo_id': l.alvo_id,
        'detalhes': json.loads(l.detalhes) if l.detalhes else None,
        'endereco_ip': l.endereco_ip,
    } for l in linhas]
