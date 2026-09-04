# app/utils/log_sanitizer.py
"""
Utilitários para ocultar dados sensíveis nos registos (logs).

Porque isto importa: os ficheiros de log são frequentemente partilhados em
pedidos de suporte, colados em issues do GitHub, recolhidos por ferramentas de
monitorização ou simplesmente lidos por quem tem acesso ao servidor. Sem
mascaramento, um único log podia expor números de telemóvel completos dos
utilizadores, emails, tokens de pagamento e chaves de API — dados que, no caso
dos contactos pessoais, também são informação protegida pelo RGPD/LGPD.

A regra seguida aqui é: manter o suficiente para DIAGNOSTICAR (perceber de que
registo se trata, comparar entre linhas), sem permitir RECONSTRUIR o valor.
"""

import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit


def mask_phone(phone):
    """
    Mascara um número de telefone preservando o país/DDD e os últimos dígitos.

        5521985852539  ->  5521*****2539

    O prefixo continua visível porque é útil para diagnóstico (confirmar que o
    código do país foi aplicado corretamente, por exemplo), e os últimos dígitos
    permitem ao administrador confirmar com o utilizador qual é o número — sem
    que o log contenha o contacto completo.
    """
    if not phone:
        return "(vazio)"

    digits = re.sub(r'\D', '', str(phone))
    if len(digits) <= 6:
        # Número curto demais para mascarar de forma útil: escondemos quase tudo.
        return f"{digits[:1]}***" if digits else "(inválido)"

    prefix = digits[:4]
    suffix = digits[-4:]
    hidden = '*' * max(3, len(digits) - 8)
    return f"{prefix}{hidden}{suffix}"


def mask_email(email):
    """
    Mascara um email preservando a primeira letra e o domínio.

        joao.silva@gmail.com  ->  j*********@gmail.com

    O domínio fica visível porque raramente é identificador por si só e ajuda a
    diagnosticar problemas de entrega.
    """
    if not email or '@' not in str(email):
        return "(vazio)" if not email else "***"

    local, _, domain = str(email).partition('@')
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 1)}@{domain}"


def mask_token(token, visible=4):
    """
    Mascara um token, chave ou identificador de transação, mantendo apenas o
    início — o suficiente para cruzar linhas do log referentes à mesma operação.

        9b758729835449ec966dde5d6b652987  ->  9b75...(28 chars)
    """
    if not token:
        return "(vazio)"

    token = str(token)
    if len(token) <= visible:
        return "*" * len(token)
    return f"{token[:visible]}...({len(token)} chars)"


def mask_code(code, visible=4):
    """
    Mascara um código de convite ou de indicação.

        9bT4xQ2mL8vZ...  ->  9bT4...(22 chars)
        PROMO            ->  P...(5 chars)

    Um código de convite é um SEGREDO PARTILHÁVEL: quem o lê consegue resgatar
    o convite e entrar no servidor. Como os logs acabam em pedidos de suporte,
    issues do GitHub e ferramentas de monitorização, o código completo não pode
    lá estar. O prefixo é mantido para dar para cruzar linhas do log sobre o
    mesmo convite.

    Ao contrário do `mask_token`, o prefixo visível encolhe com o tamanho do
    código: um código personalizado curto como 'PROMO' ficaria praticamente
    inteiro à vista se revelássemos sempre 4 caracteres.
    """
    if not code:
        return "(vazio)"

    code = str(code)
    visible = min(visible, len(code) // 3)
    if visible <= 0:
        return f"***({len(code)} chars)"
    return f"{code[:visible]}...({len(code)} chars)"


# Nomes de parâmetros cujo VALOR nunca deve aparecer num log.
_CHAVES_SENSIVEIS = ('token', 'code', 'key', 'secret', 'password', 'senha')


def mask_link(url):
    """
    Mascara os segredos de um URL antes de o registar: o último segmento do
    caminho e os valores de parâmetros de consulta sensíveis.

        .../invite/9bT4xQ2mL8vZ  ->  .../invite/9bT4...(12 chars)
        ...accept?invite_token=X ->  ...accept?invite_token=***(1 chars)

    Porque isto é preciso: o encurtador de links embrulha links de convite do
    Plex (que levam um `invite_token` — uma credencial viva que dá acesso ao
    servidor partilhado) e links de pagamento (que levam o `payment_token` do
    utilizador). Ambos eram registados por inteiro, em INFO, na criação do link
    E outra vez a cada clique no redirecionamento.

    O esquema, o domínio e a rota continuam visíveis: é o que serve para
    diagnosticar. O fragmento (#...) é descartado por inteiro, porque também
    transporta segredos e nunca é necessário no log.
    """
    if not url:
        return "(vazio)"

    texto = str(url)
    try:
        partes = urlsplit(texto)
    except ValueError:
        return "***"

    # Não é um URL absoluto: trata-se como um código solto.
    if not partes.scheme and not partes.netloc:
        return mask_code(texto)

    caminho = partes.path
    if caminho and caminho.strip('/'):
        segmentos = caminho.rstrip('/').split('/')
        segmentos[-1] = mask_code(segmentos[-1])
        caminho = '/'.join(segmentos)

    consulta = ''
    if partes.query:
        pares = []
        for chave, valor in parse_qsl(partes.query, keep_blank_values=True):
            if valor and any(sensivel in chave.lower() for sensivel in _CHAVES_SENSIVEIS):
                valor = mask_code(valor)
            pares.append(f"{chave}={valor}")
        consulta = '&'.join(pares)

    return urlunsplit((partes.scheme, partes.netloc, caminho, consulta, ''))


def mask_secret(value):
    """
    Mascara completamente um segredo (palavra-passe, chave de API, credenciais
    de Basic Auth). Aqui NÃO se preserva nenhum caractere: ao contrário de um
    txid, não há qualquer necessidade de diagnóstico que justifique expor parte
    de uma credencial.
    """
    if not value:
        return "(não definido)"
    return f"***({len(str(value))} chars)"


def mask_url_credentials(url):
    """
    Remove credenciais embutidas num URL (http://user:senha@host/...), que de
    outro modo apareceriam em claro em mensagens de erro de rede.
    """
    if not url:
        return url
    return re.sub(r'://[^/@\s]+:[^/@\s]+@', '://***:***@', str(url))
