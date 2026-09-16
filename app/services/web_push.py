# app/services/web_push.py

"""Entrega de uma notificação push a um navegador, do zero.

**Porque não há aqui uma biblioteca.** O caminho óbvio seria a `pywebpush`,
mas ela arrasta a `http-ece`, que traz um `setup.py` antigo e deixou de
compilar com as versões atuais do setuptools (`AttributeError: install_layout`).
Uma dependência que não instala é um contentor que não arranca — e o que falta
fazer é pouco e está todo especificado: o `cryptography` já é dependência
fixada do painel e tem tudo o que isto precisa.

O que se implementa aqui são três documentos:

- **RFC 8188** — o formato `aes128gcm`: o corpo do pedido leva o sal, o
  tamanho do registo e a chave pública efémera à frente do texto cifrado, para
  que o navegador consiga decifrar sem mais nada combinado;
- **RFC 8291** — como se chega à chave desse `aes128gcm` a partir das duas
  chaves que o navegador nos deu na subscrição (`p256dh` e `auth`). O serviço
  de push (Google, Mozilla, Apple) transporta o corpo sem nunca o conseguir
  ler;
- **RFC 8292 (VAPID)** — quem está a enviar. Um JWT assinado com a chave
  privada do painel, que o serviço de push verifica contra a chave pública que
  o navegador lhe entregou no momento da subscrição.

⚠️ **A chave VAPID do painel não pode mudar.** Ela é registada no serviço de
push no momento da subscrição: gerar um par novo invalida, de uma vez, TODAS as
subscrições já existentes — toda a gente deixa de receber notificações e
ninguém dá por isso, porque o serviço responde `403` e mais nada. Por isso o
par é gerado UMA vez e guardado no config.json.
"""

import base64
import json
import logging
import os
import struct
import time
from hashlib import sha256
from hmac import HMAC
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils as asym_utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# O tamanho do registo do RFC 8188. Um só registo chega: as notificações do
# painel são curtas e o limite prático dos serviços de push (4096 bytes de
# corpo) é bem mais apertado do que isto.
TAMANHO_DO_REGISTO = 4096

# ⚠️ O limite é do CORPO CIFRADO, não do texto: o cabeçalho do aes128gcm leva
# 86 bytes (sal, tamanho, chave efémera) e o AES-GCM acrescenta 16 de
# autenticação mais 1 do delimitador de preenchimento. Um payload maior do que
# isto é recusado com 413 pelo serviço de push — e a notificação perde-se.
MAXIMO_DO_PAYLOAD = 3993

# (ligação, leitura). Curto de propósito: um serviço de push em baixo não pode
# segurar a confirmação de um pagamento nem o webhook do Seerr.
TEMPO_LIMITE = (5, 10)

# Validade do JWT do VAPID. O máximo que a norma permite é 24 horas; 12 é a
# folga habitual, e não há nada a ganhar em aproximar-se do limite.
VALIDADE_DO_JWT = 12 * 3600


# 🛡️ **O endereço de entrega é escolhido por quem subscreve, e o painel faz-lhe
# POST a partir de DENTRO da rede.** Sem esta lista, qualquer pessoa com sessão
# — não é preciso ser administrador — registava um aparelho a apontar para
# `https://10.0.0.5:8443/` e mandava o painel bater lá de cada vez que houvesse
# uma notificação, com a rota `/push/test` a servir de gatilho à vontade. É
# exatamente a falha que a allowlist do proxy de imagens já existia para fechar
# (`ALLOWED_IMAGE_HOSTS`), e a resposta é a mesma: **o pedido ESCOLHE uma
# entrada desta lista, nunca define um destino novo.**
#
# Aqui a lista é curta por natureza: um endereço de push não é arbitrário, vem
# do serviço do próprio navegador, e são estes.
SERVICOS_DE_PUSH = (
    'fcm.googleapis.com',         # Chrome, Edge, Android
    'android.googleapis.com',     # Chrome antigo
    'push.services.mozilla.com',  # Firefox
    'push.apple.com',             # Safari, iOS e iPadOS
    'notify.windows.com',         # Windows Notification Service
)

# ⚠️ **Não vai para o config.json nem para a página de Configurações**, ao
# contrário de tudo o resto neste painel — e é de propósito. Isto não é uma
# preferência, é a fronteira que impede um SSRF: pô-la na interface faria de
# qualquer sessão de administrador tomada uma forma de a alargar. Quem corre um
# serviço de push próprio (um autopush da Mozilla na sua rede) acrescenta-o
# aqui, ao lançar o contentor, como já se faz com o `IMAGE_PROXY_ALLOWED_HOSTS`.
EXTRA_HOSTS_ENV_VAR = 'PUSH_ALLOWED_HOSTS'


def hosts_de_push():
    """Os domínios de onde o painel aceita um endereço de entrega."""
    extra = os.environ.get(EXTRA_HOSTS_ENV_VAR, '')
    return SERVICOS_DE_PUSH + tuple(
        parte.strip() for parte in extra.split(',') if parte.strip()
    )


def endpoint_permitido(endpoint) -> bool:
    """Diz se este endereço de entrega é de um serviço de push conhecido.

    A comparação é pela FRONTEIRA do rótulo DNS (`match_domain`), nunca por
    substring: `fcm.googleapis.com.atacante.net` e `naoefcm.googleapis.com`
    contêm ambos o texto do domínio e não são ele.
    """
    from ..utils.url_safety import match_domain

    try:
        partes = urlsplit((endpoint or '').strip())
    except ValueError:
        return False
    if partes.scheme != 'https':
        return False
    return match_domain(partes.hostname, hosts_de_push()) is not None


class PushExpirado(Exception):
    """A subscrição já não existe do lado do serviço de push (404 ou 410).

    É a resposta normal a um navegador desinstalado, a um utilizador que revogou
    a permissão ou a um perfil apagado — e quem chama deve APAGAR a linha, não
    voltar a tentar. Ficar com ela é um erro por notificação, para sempre.
    """


class PushRecusado(Exception):
    """O serviço de push recusou a entrega (403, 429, 5xx, rede em baixo)."""


def b64url(dados: bytes) -> str:
    """Base64 de URL sem o preenchimento — o formato de tudo o que é web push."""
    return base64.urlsafe_b64encode(dados).rstrip(b'=').decode('ascii')


def de_b64url(texto) -> bytes:
    """Lê o base64 de URL aceitando (ou não) o preenchimento e o alfabeto normal.

    🐛 O `p256dh` e o `auth` chegam do navegador, e nem todos os navegadores os
    escrevem da mesma maneira: alguns mandam-nos com `=` no fim, outros com `+`
    e `/` em vez de `-` e `_`. Recusar uma variante dava uma subscrição que era
    gravada e nunca entregava nada.
    """
    if isinstance(texto, bytes):
        bruto = texto
    else:
        bruto = str(texto or '').strip().encode('ascii', 'ignore')
    bruto = bruto.replace(b'+', b'-').replace(b'/', b'_').rstrip(b'=')
    return base64.urlsafe_b64decode(bruto + b'=' * (-len(bruto) % 4))


def _hkdf(sal: bytes, ikm: bytes, info: bytes, tamanho: int) -> bytes:
    """HKDF-SHA256 com uma só iteração de expansão (chega até 32 bytes)."""
    prk = HMAC(sal, ikm, sha256).digest()
    return HMAC(prk, info + b'\x01', sha256).digest()[:tamanho]


# --- VAPID: quem está a enviar --------------------------------------------

def gerar_par_de_chaves() -> dict:
    """Um par VAPID novo, no formato que o navegador e o painel usam.

    A pública é o ponto não comprimido (65 bytes) que vai para o
    `applicationServerKey` do navegador; a privada são os 32 bytes do escalar.
    Ambas em base64 de URL, para caberem no config.json.
    """
    chave = ec.generate_private_key(ec.SECP256R1())
    privada = chave.private_numbers().private_value.to_bytes(32, 'big')
    publica = chave.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return {'publica': b64url(publica), 'privada': b64url(privada)}


def chave_privada_valida(privada) -> bool:
    """Diz se o texto guardado no config dá mesmo uma chave utilizável."""
    try:
        _carregar_privada(privada)
        return True
    except Exception:
        return False


def _carregar_privada(privada) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(
        int.from_bytes(de_b64url(privada), 'big'), ec.SECP256R1()
    )


def publica_de(privada) -> str:
    """A chave pública que corresponde a esta privada.

    ⚠️ Serve para apanhar um par DESEMPARELHADO no config.json — uma edição
    manual, um restauro a meio. As duas chaves têm de vir do mesmo par: com a
    pública de um e a privada de outro, o serviço de push responde 403 e a
    notificação desaparece sem deixar rasto.
    """
    publica = _carregar_privada(privada).public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return b64url(publica)


def _origem_de(endpoint: str) -> str:
    partes = urlsplit(endpoint)
    if not partes.scheme or not partes.netloc:
        raise PushRecusado(f"Endereço de push inválido: {endpoint[:40]}")
    return f"{partes.scheme}://{partes.netloc}"


def cabecalho_vapid(endpoint: str, privada, assunto: str, agora=None) -> str:
    """O `Authorization: vapid t=<jwt>,k=<chave>` de um pedido de push.

    O `sub` é um `mailto:` ou um endereço do painel: é por onde o serviço de
    push contacta quem envia, quando alguma coisa corre mal do lado dele.
    """
    agora = int(agora if agora is not None else time.time())
    cabecalho = {'typ': 'JWT', 'alg': 'ES256'}
    reivindicacoes = {
        'aud': _origem_de(endpoint),
        'exp': agora + VALIDADE_DO_JWT,
        'sub': assunto,
    }
    # `separators` sem espaços: o JWT é comparado byte a byte pela assinatura.
    corpo = b'.'.join(
        b64url(json.dumps(parte, separators=(',', ':'), sort_keys=True).encode()).encode()
        for parte in (cabecalho, reivindicacoes)
    )

    chave = _carregar_privada(privada)
    # ⚠️ O `cryptography` assina em DER; o JWS quer os dois inteiros CRUS,
    # 32 bytes cada. Mandar o DER dá um 401 do serviço de push com uma
    # mensagem que não diz nada sobre o formato.
    der = chave.sign(corpo, ec.ECDSA(SHA256()))
    r, s = asym_utils.decode_dss_signature(der)
    assinatura = r.to_bytes(32, 'big') + s.to_bytes(32, 'big')

    jwt = corpo.decode('ascii') + '.' + b64url(assinatura)
    return f"vapid t={jwt},k={publica_de(privada)}"


# --- RFC 8291: o corpo que só o navegador consegue ler ---------------------

def cifrar(payload: bytes, p256dh, auth, sal=None, efemera=None) -> bytes:
    """Cifra o payload para uma subscrição, no formato `aes128gcm`.

    `sal` e `efemera` existem para o teste poder reproduzir o exemplo da
    RFC 8291 — em produção são sempre aleatórios, e TÊM de o ser: repetir o
    par (chave, nonce) do AES-GCM quebra a cifra.
    """
    ua_publica = de_b64url(p256dh)
    segredo = de_b64url(auth)
    if len(ua_publica) != 65 or ua_publica[0] != 0x04:
        raise PushRecusado("Chave pública do navegador em formato inesperado.")
    if len(segredo) != 16:
        raise PushRecusado("Segredo de autenticação do navegador inválido.")
    if len(payload) > MAXIMO_DO_PAYLOAD:
        raise PushRecusado(
            f"Payload de {len(payload)} bytes acima do limite de {MAXIMO_DO_PAYLOAD}."
        )

    sal = sal if sal is not None else os.urandom(16)
    efemera = efemera if efemera is not None else ec.generate_private_key(ec.SECP256R1())
    as_publica = efemera.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )

    partilhado = efemera.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_publica)
    )
    # O segredo `auth` entra aqui: sem ele, quem intercetasse a troca de chaves
    # decifrava o conteúdo. É o que liga esta cifra àquela subscrição.
    ikm = _hkdf(segredo, partilhado, b'WebPush: info\x00' + ua_publica + as_publica, 32)

    cek = _hkdf(sal, ikm, b'Content-Encoding: aes128gcm\x00', 16)
    nonce = _hkdf(sal, ikm, b'Content-Encoding: nonce\x00', 12)

    # 0x02 é o delimitador de ÚLTIMO registo do RFC 8188 (0x01 seria "vem mais").
    cifrado = AESGCM(cek).encrypt(nonce, payload + b'\x02', None)

    return sal + struct.pack('!L', TAMANHO_DO_REGISTO) + bytes([len(as_publica)]) + as_publica + cifrado


# --- A entrega -------------------------------------------------------------

def _sessao():
    """Sessão partilhada: sem ela, cada aparelho custava um handshake TLS novo."""
    global _SESSAO
    if _SESSAO is None:
        sessao = requests.Session()
        # Só se repete o que comprovadamente NÃO foi entregue. O 500 fica de
        # fora: pode ter sido processado, e repetir duplicaria a notificação.
        sessao.mount('https://', HTTPAdapter(max_retries=Retry(
            total=2, backoff_factor=0.5, status_forcelist=(429, 502, 503, 504),
            allowed_methods=frozenset(['POST']), respect_retry_after_header=True,
            raise_on_status=False,
        ), pool_connections=10, pool_maxsize=20))
        _SESSAO = sessao
    return _SESSAO


_SESSAO = None


def enviar(subscricao: dict, payload: dict, privada, assunto: str, ttl: int = 86400,
           urgencia: str = 'normal', sessao=None) -> int:
    """Entrega uma notificação a UM aparelho. Devolve o código da resposta.

    Levanta `PushExpirado` quando a subscrição já não existe (para quem chama a
    apagar) e `PushRecusado` em tudo o resto.
    """
    endpoint = (subscricao or {}).get('endpoint') or ''
    if not endpoint:
        raise PushRecusado("Subscrição sem endereço de entrega.")

    # 🛡️ A verificação é feita DUAS vezes — à entrada (no schema) e aqui, à
    # saída. A da entrada dá o erro logo a quem subscreve; esta é a que vale,
    # porque uma linha pode ter entrado na tabela por outro caminho: um backup
    # restaurado de antes desta versão, ou um INSERT à mão.
    if not endpoint_permitido(endpoint):
        raise PushRecusado(
            "O endereço de entrega não é de um serviço de push conhecido "
            f"({urlsplit(endpoint).hostname})."
        )

    corpo = cifrar(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
        subscricao.get('p256dh'), subscricao.get('auth'),
    )

    cabecalhos = {
        'Authorization': cabecalho_vapid(endpoint, privada, assunto),
        'Content-Encoding': 'aes128gcm',
        'Content-Type': 'application/octet-stream',
        'TTL': str(int(ttl)),
        'Urgency': urgencia,
    }

    try:
        resposta = (sessao or _sessao()).post(
            endpoint, data=corpo, headers=cabecalhos, timeout=TEMPO_LIMITE
        )
    except requests.RequestException as e:
        raise PushRecusado(f"Falha de rede ao entregar o push: {e}") from e

    if resposta.status_code in (404, 410):
        raise PushExpirado(f"Subscrição já não existe ({resposta.status_code}).")
    if resposta.status_code >= 300:
        # O corpo da resposta traz a razão (chave VAPID errada, payload grande
        # demais); é curto, e sem ele o log não diz nada acionável.
        raise PushRecusado(
            f"O serviço de push respondeu {resposta.status_code}: "
            f"{(resposta.text or '')[:200]}"
        )
    return resposta.status_code
