# app/services/telegram_vinculo.py

"""Vincular o Telegram de quem acabou de resgatar um convite.

⚠️ **O Telegram é o único canal que um formulário digitado não resolve**, e por
duas razões independentes:

1. o painel manda `chat_id = telegram_id or telegram_user` direto para a API
   (`_prepare_and_send`), e um `@username` **não endereça uma conversa
   privada** — só um canal. O que serve ali é o id NUMÉRICO do chat, que a
   pessoa não conhece e não tem como descobrir;
2. mesmo com o id certo, **um bot não pode iniciar uma conversa**. Enquanto a
   pessoa não abrir o bot e tocar em "Começar", qualquer envio devolve 403.

Por isso o `telegram_id` dos convites vem sempre de um bot que JÁ falou com ela.
O WhatsApp e o Discord não têm nada disto — um número vira
`{phone}@s.whatsapp.net`, e o Discord publica no canal do webhook mencionando o
id —, e é por isso que só este canal tem um módulo.

**O que se faz aqui**: a página mostra um link `t.me/<bot>?start=<codigo>`. A
pessoa toca, o Telegram abre o bot, ela toca em "Começar" — e isso resolve o
ponto 2 — e o Telegram entrega ao bot uma mensagem `/start <codigo>`. O painel
vai buscá-la e fica com o `chat.id`, que resolve o ponto 1.

🛡️ **E vai buscá-la SEM OFFSET, de propósito.** `getUpdates` só descarta o que
já foi lido quando é chamado com um offset maior; sem ele, devolve o que está
pendente e **não confirma nada**. Isso é o que permite o painel ler sem roubar
as atualizações de um bot que o administrador já tenha a correr no mesmo token
— e este painel até tem uma API de convites para bots, por isso esse caso não é
hipotético. Se houver um webhook registado, o Telegram recusa com 409 e o
painel DIZ isso, em vez de tentar tomar o lugar dele.
"""

import logging
import time
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Quanto tempo o nome do bot fica em cache. Ele não muda sozinho — só se o
# token mudar, e aí a chave da cache muda com ele.
VALIDADE_DO_NOME = 10 * 60

# O que se procura nas atualizações. O Telegram entrega o payload do deep link
# como argumento do /start.
PREFIXO = '/start'

_nome_em_cache = {}


# Os motivos por que não dá. 🛡️ São CÓDIGOS e não frases: a frase é da rota,
# que é quem fala com a pessoa. Isto era uma exceção cuja `str()` a rota
# devolvia no corpo da resposta, e o CodeQL marcou-o (alerta 89 do PR #44).
# Hoje não vazava nada, mas a rota é PÚBLICA e o padrão estava a uma edição
# natural de vazar: bastava alguém escrever `raise VinculoIndisponivel(f"... {e}")`
# para o erro da API do Telegram ir para o corpo da resposta.
SEM_TELEGRAM = 'sem_telegram'
OCUPADO = 'ocupado'
FALHOU = 'falhou'


class Resultado(NamedTuple):
    """O chat encontrado, ou o motivo por que não dá.

    ⚠️ `chat_id=None` e `motivo=None` juntos querem dizer "ainda não chegou",
    que NÃO é a mesma coisa que "não dá" — e a página precisa da distinção para
    dizer a coisa certa a quem está à espera de ter tocado em Começar.
    """

    chat_id: str = None
    motivo: str = None


def _bot(config):
    from ..extensions import notifier_manager

    if not config.get('TELEGRAM_ENABLED'):
        return None
    if not notifier_manager:
        return None
    return notifier_manager._get_bot(config)


def nome_do_bot(config):
    """O @nome do bot, para montar o deep link. None quando não dá para saber.

    ⚠️ Uma falha de rede NÃO fica em cache: guardar o "não sei" deixava a
    página dez minutos sem o botão por causa de um segundo mau. É a mesma regra
    da deteção dos plugins do Jellyfin.
    """
    bot = _bot(config)
    if not bot:
        return None

    chave = bot.token
    guardado = _nome_em_cache.get(chave)
    if guardado and guardado[0] > time.monotonic():
        return guardado[1]

    try:
        nome = (bot.get_me().username or '').strip() or None
    except Exception as e:
        logger.warning(f"Não foi possível obter o nome do bot do Telegram: {e}")
        return None

    _nome_em_cache[chave] = (time.monotonic() + VALIDADE_DO_NOME, nome)
    return nome


def link_de_vinculo(config, codigo):
    """`https://t.me/<bot>?start=<codigo>`, ou None se não houver bot."""
    nome = nome_do_bot(config)
    if not nome or not codigo:
        return None
    return f"https://t.me/{nome}?start={codigo}"


def procurar_chat(config, codigo):
    """O `chat.id` de quem mandou `/start <codigo>`, ou o motivo de não dar."""
    from telebot.apihelper import ApiTelegramException

    bot = _bot(config)
    if not bot:
        return Resultado(motivo=SEM_TELEGRAM)
    if not codigo:
        return Resultado()

    try:
        # ⚠️ Sem `offset` (não confirma nada, ver o cabeçalho) e sem espera
        # longa: isto corre DENTRO de um pedido HTTP, num painel com um worker
        # gevent. Um `long_polling_timeout` grande seria o painel inteiro
        # parado à espera do Telegram.
        atualizacoes = bot.get_updates(timeout=5, long_polling_timeout=0)
    except ApiTelegramException as e:
        if getattr(e, 'error_code', None) == 409:
            # 🛡️ Há um webhook registado, ou outro processo a fazer polling com
            # o mesmo token. O painel NÃO toma o lugar dele: um `deleteWebhook`
            # aqui partia, em silêncio, o bot que o administrador tem a correr.
            logger.warning(
                "O Telegram recusou o getUpdates com 409: há um webhook registado "
                "ou outro bot a usar este token. O vínculo pelo painel não pode "
                "funcionar enquanto isso durar."
            )
            return Resultado(motivo=OCUPADO)
        # ⚠️ O erro de baixo vai para o LOG e não para a resposta: ele pode
        # trazer o endereço da API e o que mais o cliente lá puser.
        logger.warning(f"Falha a ler as atualizações do Telegram: {e}")
        return Resultado(motivo=FALHOU)
    except Exception as e:
        logger.warning(f"Falha a ler as atualizações do Telegram: {e}")
        return Resultado(motivo=FALHOU)

    procurado = f"{PREFIXO} {codigo}"
    for atualizacao in reversed(atualizacoes or []):
        mensagem = getattr(atualizacao, 'message', None)
        texto = (getattr(mensagem, 'text', '') or '').strip()
        if texto != procurado:
            continue
        chat = getattr(mensagem, 'chat', None)
        chat_id = getattr(chat, 'id', None)
        if chat_id is not None:
            return Resultado(chat_id=str(chat_id))

    # Nem chat nem motivo: "ainda não chegou".
    return Resultado()
