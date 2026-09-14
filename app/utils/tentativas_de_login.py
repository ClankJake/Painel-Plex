# app/utils/tentativas_de_login.py

"""O travão às tentativas de adivinhar palavras-passe.

⚠️ **O limite por IP não chega.** `/auth/login/credentials` é a única rota do
painel onde se podem testar palavras-passe, e o limitador do Flask-Limiter
conta por ENDEREÇO. Dez por minuto por IP são catorze mil por dia a partir de
UM só endereço — e quem ataca tem-nas todas para a mesma conta. O que fecha
essa porta é contar também por CONTA: ao fim de algumas falhas, aquele nome
deixa de aceitar tentativas durante uns minutos, venham de onde vierem.

Só se aplica aos servidores de contas locais (o Jellyfin). Num painel Plex a
palavra-passe nunca passa por aqui: quem a verifica é o plex.tv, pelo fluxo do
PIN.

Três decisões que não são óbvias:

- ⚠️ **conta-se o que foi ESCRITO, não a conta que existe.** Travar apenas
  contas reais diria a quem tenta quais os nomes que existem neste servidor —
  exatamente o que a mensagem de erro única evita. O nome inventado é travado
  como qualquer outro.
- **o IP tem um limite mais largo do que a conta.** Uma casa inteira sai pelo
  mesmo endereço, e trancar a família porque alguém se enganou cinco vezes é
  pior do que o ataque que se quer travar.
- **a chave é o resumo (`sha256`) do identificador**, não o identificador. A
  cache é `FileSystemCache`: sem isto, o nome de quem tentou entrar ficava
  escrito no disco sem necessidade nenhuma. A palavra-passe tentada não é
  guardada em sítio nenhum, nem sequer contada.

O contador vive na cache e perde-se num reinício. É uma trava contra a força
bruta, não um registo de auditoria — o registo é o que fica no log.
"""

import hashlib
import time

# Ao fim de cinco falhas, aquela CONTA descansa quinze minutos.
LIMITE_POR_CONTA = 5
BLOQUEIO_POR_CONTA = 15 * 60

# O mesmo ENDEREÇO tem mais folga: partilham-no casas inteiras.
LIMITE_POR_IP = 20
BLOQUEIO_POR_IP = 15 * 60

# Quanto tempo uma falha isolada conta para o total. Sem isto, duas falhas por
# semana durante um mês acabavam por trancar quem só escreve mal a palavra-passe.
JANELA = 15 * 60

_PREFIXO = 'login_falhas'


def _chave(especie, valor):
    resumo = hashlib.sha256(str(valor or '').strip().lower().encode('utf-8')).hexdigest()
    return f"{_PREFIXO}:{especie}:{resumo}"


def _ler(chave):
    from ..extensions import cache

    try:
        return cache.get(chave) or {}
    except Exception:
        # Uma cache indisponível não pode impedir ninguém de entrar: sem ela
        # fica o limite por IP do Flask-Limiter, que é o que havia antes.
        return {}


def _gravar(chave, registo, ttl):
    from ..extensions import cache

    try:
        cache.set(chave, registo, timeout=ttl)
    except Exception:
        pass


def _apagar(chave):
    from ..extensions import cache

    try:
        cache.delete(chave)
    except Exception:
        pass


def _segundos_em_falta(chave):
    registo = _ler(chave)
    restante = int((registo.get('bloqueado_ate') or 0) - time.time())
    return restante if restante > 0 else 0


def _contar_falha(chave, limite, bloqueio):
    agora = time.time()
    registo = _ler(chave)

    # Falhas antigas não contam: a janela recomeça.
    if agora - (registo.get('ultima') or 0) > JANELA:
        registo = {}

    falhas = int(registo.get('falhas') or 0) + 1
    novo = {'falhas': falhas, 'ultima': agora}

    if falhas >= limite:
        novo['bloqueado_ate'] = agora + bloqueio
        _gravar(chave, novo, bloqueio)
        return bloqueio

    _gravar(chave, novo, JANELA)
    return 0


def segundos_de_espera(username, ip=None):
    """Quantos segundos faltam até esta tentativa poder ser feita (0 = pode já)."""
    espera = _segundos_em_falta(_chave('conta', username))
    if ip:
        espera = max(espera, _segundos_em_falta(_chave('ip', ip)))
    return espera


def registar_falha(username, ip=None):
    """Conta mais uma tentativa falhada. Devolve os segundos de bloqueio, se algum."""
    bloqueio = _contar_falha(_chave('conta', username), LIMITE_POR_CONTA, BLOQUEIO_POR_CONTA)
    if ip:
        bloqueio = max(bloqueio, _contar_falha(_chave('ip', ip), LIMITE_POR_IP, BLOQUEIO_POR_IP))
    return bloqueio


def registar_sucesso(username, ip=None):
    """Uma entrada bem-sucedida limpa o que ficou para trás.

    Quem se enganou três vezes e à quarta acertou não fica com essas três
    penduradas à espera da próxima distração.
    """
    _apagar(_chave('conta', username))
    if ip:
        _apagar(_chave('ip', ip))
