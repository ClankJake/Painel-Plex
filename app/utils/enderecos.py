# app/utils/enderecos.py

"""O endereço público de uma página do painel.

⚠️ **`url_for(..., _external=True)` monta o endereço a partir do PEDIDO** — o
cabeçalho `Host`, ou o `SERVER_NAME` quando não há pedido nenhum. Isso está
certo para um link clicado dentro do navegador de quem já está no painel, e
está errado para tudo o que SAI daqui: um link que vai para o Telegram, para o
Discord ou para a resposta de uma API é aberto noutro sítio, noutro dia.

O caso que isto existe para fechar é o do convite criado por um bot. Um bot que
corre na mesma rede de contentores chama o painel pelo nome interno
(`http://painel:5000/api/invites/bot/create`), e o `invite_url` da resposta
saía com esse nome lá dentro — `http://painel:5000/invite/abc`. O bot mandava-o
a quem ia entrar, e o link nascia quebrado: só funciona de dentro da rede, que
é precisamente onde a pessoa não está.

A `APP_BASE_URL` é a resposta que o painel já dá a esta pergunta noutros
sítios (o link de pagamento, o de reposição de palavra-passe): é o endereço
por onde as pessoas chegam, escrito pelo administrador. Manda ela; o `url_for`
externo fica como recurso para quem ainda não a preencheu.
"""

from flask import url_for

from ..config import load_or_create_config


def endereco_publico(endpoint, **valores):
    """O endereço completo de `endpoint`, para ser enviado para fora do painel."""
    base = (load_or_create_config().get("APP_BASE_URL") or '').strip().rstrip('/')
    if base:
        return f"{base}{url_for(endpoint, _external=False, **valores)}"
    return url_for(endpoint, _external=True, **valores)
