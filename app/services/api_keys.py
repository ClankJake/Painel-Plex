# app/services/api_keys.py

"""Chaves de integração: criar, verificar, revogar.

⚠️ **O painel tinha UMA chave para tudo** — a `INTERNAL_TRIGGER_KEY` do
config.json —, partilhada pelo endpoint de convites para bots e pelo webhook do
Seerr. Duas consequências que só se notam no pior dia:

* regenerá-la porque um bot foi comprometido derrubava também o Seerr, e o
  painel não dava nenhuma forma de saber qual das integrações estava a usá-la;
* a chave dada a um bot de Telegram podia aceitar webhooks em nome do painel, e
  a chave dada ao Seerr podia criar convites com acesso ao servidor.

Ela foi removida — do config.json, das rotas e do cartão que a mostrava. Estas
são as únicas chaves que o painel reconhece.

🛡️ **O que fica gravado é o RESUMO da chave, não a chave.** É a mesma decisão
de `PasswordReset`: quem lesse a base de dados — ou um ZIP de backup, que é só
um ficheiro — ficava com uma porta aberta por cada integração ligada. Por isso
a chave é mostrada UMA vez, no momento em que é criada, e o painel não a sabe
recuperar depois.
"""

import hashlib
import hmac
import json
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

from ..dominios import ESCOPOS_DE_API
from ..extensions import db
from ..models import ApiKey

logger = logging.getLogger(__name__)

# O prefixo identifica a chave na interface e é por ele que a verificação
# encontra a linha — sem isto, validar uma chave era percorrer a tabela a
# comparar resumos, e o tempo dessa procura variava com quantas chaves existem.
PREFIXO = 'pnl'
TAMANHO_DO_PREFIXO = 8

# 🐛 O prefixo NÃO pode sair do `token_urlsafe`. O alfabeto dele inclui o '_',
# que é o separador da chave: um prefixo como `-v_wYCdF` partia a chave em
# quatro pedaços e a leitura ficava com `-v` no lugar do prefixo. A chave era
# gerada com sucesso e depois nunca mais era reconhecida — uma falha que só
# aparece em cerca de um terço das chaves, o que é pior do que aparecer sempre.
ALFABETO_DO_PREFIXO = string.ascii_letters + string.digits

# ⚡ `last_used_at` a cada pedido é uma escrita por cada chamada de API, num
# endpoint que um webhook pode bater dezenas de vezes por minuto. O que
# interessa saber é "esta chave ainda está a ser usada?", e para isso a
# resolução de minutos chega e sobra.
INTERVALO_DE_USO = timedelta(minutes=5)


class DadosInvalidos(Exception):
    """O pedido de criação está incompleto.

    🛡️ Carrega um MOTIVO estável, não uma mensagem. Quem escreve o texto que a
    pessoa lê é a rota, e é essa a diferença que importa: devolver `str(e)` numa
    resposta faz sair dali o que quer que tenha sido levantado dentro do `try`
    — hoje são duas mensagens escritas por nós, amanhã é o texto de um erro do
    SQLAlchemy com o caminho da base de dados lá dentro.

    É a mesma decisão já tomada no resgate de convites, depois de o CodeQL
    marcar o padrão: a mensagem é FIXA e escolhida por quem responde.
    """

    SEM_NOME = 'sem_nome'
    SEM_ESCOPO = 'sem_escopo'

    def __init__(self, motivo):
        super().__init__(motivo)
        self.motivo = motivo


def _pepper():
    """O segredo do painel que entra no resumo. Vazio se ainda não houver chaves."""
    from ..config import load_or_create_config

    return (load_or_create_config().get('API_KEYS_PEPPER') or '').encode('utf-8')


def garantir_pepper():
    """Gera o segredo UMA vez, e nunca substitui um que já exista.

    ⚠️ Trocá-lo invalida TODAS as chaves de uma vez, em silêncio — as
    integrações passam a levar 401 e nada no painel diz porquê. É a mesma
    regra do par VAPID das notificações push.
    """
    from ..config import load_or_create_config, save_app_config

    config = load_or_create_config()
    if config.get('API_KEYS_PEPPER'):
        return

    config['API_KEYS_PEPPER'] = secrets.token_hex(32)
    save_app_config(config)
    logger.info("Segredo das chaves de API gerado.")


def _resumo(chave):
    """O que fica gravado no lugar da chave.

    🛡️ **HMAC com um segredo do painel, e não um resumo simples.** Quem leia só
    a base de dados — um `.db` copiado, uma injeção de SQL — fica com resumos
    que não consegue verificar: para testar um palpite é preciso também o
    `API_KEYS_PEPPER`, que vive no config.json (a 0600, como tudo o que lá
    está).

    ⚠️ **E NÃO é um KDF lento (bcrypt, scrypt, argon2), de propósito.** O
    CodeQL marca isto como "hash fraco sobre dados sensíveis" e, para uma
    palavra-passe escolhida por uma pessoa, teria razão. Aqui não é: o que se
    resume é `pnl_<prefixo>_<token_urlsafe(32)>` — 256 bits vindos do
    `secrets`. Contra isso não há força bruta que um hash lento trave, porque
    não há espaço de palpites para encarecer.

    E um KDF lento sairia caro onde não compra nada. Este resumo é calculado a
    cada pedido cujo PREFIXO encontre uma chave viva — `verificar` sai antes
    disso quando o prefixo não existe, por isso não é qualquer pessoa que o
    consegue disparar. Mas o prefixo não é segredo (está à vista no painel e
    dentro da própria chave), e um deles chega para forçar o trabalho em
    `/api/system/webhook/overseerr`, que é `@limiter.exempt`. O painel corre com
    UM worker gevent de propósito, onde trabalho de CPU não cede a vez a
    ninguém: cada hash lento ali é o painel inteiro parado, e a conta é a mesma
    para os pedidos legítimos.
    """
    return hmac.new(_pepper(), chave.encode('utf-8'), hashlib.sha256).hexdigest()


def _agora():
    # Sem fuso, como o resto das colunas `DateTime` deste esquema (`utcnow`).
    return datetime.now(timezone.utc).replace(tzinfo=None)


def escopos_validos(escopos):
    """Só os escopos que existem, sem repetidos e por ordem estável."""
    pedidos = {str(e).strip().lower() for e in (escopos or [])}
    return [e for e in ESCOPOS_DE_API if e in pedidos]


def criar(nome, escopos):
    """Cria a chave e devolve `(linha, chave_em_claro)`.

    A chave em claro é a ÚNICA vez que ela existe fora da cabeça de quem a
    copiou — não voltar a aparecer é o ponto, não um incómodo.
    """
    nome = (nome or '').strip()
    if not nome:
        raise DadosInvalidos(DadosInvalidos.SEM_NOME)

    escolhidos = escopos_validos(escopos)
    if not escolhidos:
        raise DadosInvalidos(DadosInvalidos.SEM_ESCOPO)

    # Antes de calcular o primeiro resumo: sem segredo, ele não valeria nada.
    garantir_pepper()

    # Um prefixo repetido é improvável (64 bits) mas não impossível, e a coluna
    # é única: falhar aqui seria um 500 numa ação que o administrador pediu.
    for _ in range(5):
        prefixo = ''.join(secrets.choice(ALFABETO_DO_PREFIXO)
                          for _ in range(TAMANHO_DO_PREFIXO))
        if not ApiKey.query.filter_by(prefixo=prefixo).first():
            break
    else:
        raise RuntimeError("Não foi possível gerar um prefixo livre para a chave.")

    chave = f"{PREFIXO}_{prefixo}_{secrets.token_urlsafe(32)}"

    linha = ApiKey(
        nome=nome[:80],
        prefixo=prefixo,
        resumo=_resumo(chave),
        escopos=json.dumps(escolhidos),
        created_at=_agora(),
    )
    db.session.add(linha)
    db.session.commit()

    logger.info(f"Chave de API '{nome}' criada com as permissões {escolhidos}.")
    return linha, chave


def _prefixo_de(chave):
    """O prefixo dentro de `pnl_<prefixo>_<segredo>`, ou None.

    ⚠️ `split('_', 2)`, com o limite. O SEGREDO vem do `token_urlsafe` e traz
    '_' lá dentro à vontade; sem o limite, uma chave partia-se em tantos
    pedaços quantos os underscores do segredo e a leitura continuava a
    funcionar por acaso.
    """
    partes = chave.split('_', 2)
    if len(partes) != 3 or partes[0] != PREFIXO:
        return None
    return partes[1] or None


def verificar(chave, escopo):
    """A chave é válida PARA ESTE escopo? Devolve a linha, ou None.

    ⚠️ Uma chave sem o escopo é recusada como se não existisse. Dizer "esta
    chave existe mas não pode fazer isto" seria confirmar a quem tenta que
    acertou na chave — e a diferença não ajuda quem está a configurar uma
    integração, porque o painel mostra as permissões de cada chave.
    """
    chave = (chave or '').strip()
    if not chave:
        return None

    prefixo = _prefixo_de(chave)
    if not prefixo:
        return None

    linha = ApiKey.query.filter_by(prefixo=prefixo).first()
    if linha is None or linha.revoked_at is not None:
        return None

    # 🛡️ `compare_digest` para o tempo de resposta não dizer quantos caracteres
    # do resumo estavam certos.
    if not secrets.compare_digest(linha.resumo, _resumo(chave)):
        if not _pepper():
            # Há chaves na tabela e o segredo desapareceu do config.json — um
            # config restaurado à mão, provavelmente. NENHUMA chave vai voltar
            # a valer, e sem esta linha o log só mostrava 401 sem explicação.
            logger.error(
                "A tabela tem chaves de API mas o API_KEYS_PEPPER está vazio: "
                "nenhuma vai ser aceite. Crie chaves novas."
            )
        return None

    if escopo not in json.loads(linha.escopos or '[]'):
        return None

    _marcar_uso(linha)
    return linha


def _marcar_uso(linha):
    """Regista que a chave foi usada, sem escrever a cada pedido.

    ⚠️ **Escreve pela `db.session`, e isso é seguro por causa de QUANDO corre.**
    Um `commit()` na sessão partilhada leva consigo o que quer que esteja
    pendente nela — a armadilha que fez o `audit.registar` escrever por uma
    ligação própria. Aqui não há nada pendente: isto corre dentro de um
    decorador, ANTES do corpo da rota, e os dois `before_request` do painel
    (o do assistente de instalação e o que revalida a sessão) só leem.

    🐛 A ligação própria, que parece a correção óbvia, é PIOR neste sítio, e a
    diferença é o momento: a auditoria corre DEPOIS de o chamador ter feito
    commit, isto corre a meio. Com uma escrita pendente na sessão, o SQLite tem
    o ficheiro trancado e a segunda ligação fica à espera do `busy_timeout`
    inteiro — trinta segundos de pedido pendurado para gravar uma data. Foi
    medido, num teste que demorava isso.

    ⚠️ E nunca derruba o pedido: saber quando a chave foi usada é útil, perder
    o pedido que ela autorizou é pior.
    """
    agora = _agora()
    if linha.last_used_at and (agora - linha.last_used_at) < INTERVALO_DE_USO:
        return
    try:
        linha.last_used_at = agora
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Não foi possível registar o uso da chave de API: {e}")


def listar():
    """As chaves, para a interface. Nunca a chave em si — ela não existe aqui."""
    linhas = ApiKey.query.order_by(ApiKey.created_at.desc()).all()
    return [{
        'id': l.id,
        'nome': l.nome,
        'prefixo': l.prefixo,
        'escopos': json.loads(l.escopos or '[]'),
        'created_at': l.created_at.isoformat() if l.created_at else None,
        'last_used_at': l.last_used_at.isoformat() if l.last_used_at else None,
        'revoked_at': l.revoked_at.isoformat() if l.revoked_at else None,
    } for l in linhas]


def revogar(id_da_chave):
    """Desliga a chave. A linha FICA.

    "Esta chave foi revogada em março" é diferente de "esta chave nunca
    existiu", e é a primeira que responde a quem vai perceber, meses depois, o
    que é que deixou de funcionar.
    """
    linha = ApiKey.query.get(id_da_chave)
    if linha is None or linha.revoked_at is not None:
        return None

    linha.revoked_at = _agora()
    db.session.commit()
    logger.info(f"Chave de API '{linha.nome}' revogada.")
    return linha
