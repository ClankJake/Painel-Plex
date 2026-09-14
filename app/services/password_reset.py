# app/services/password_reset.py

"""Repor a palavra-passe de quem se esqueceu dela.

⚠️ **Só faz sentido onde as contas são LOCAIS** (`capabilities.cria_contas`):
aí foi o painel que as criou e é ele o responsável pelas credenciais. Num painel
Plex a palavra-passe vive no plex.tv — o painel não a tem, não a pode mudar, e
oferecer o botão seria prometer o que não pode cumprir.

O caminho é o mesmo de sempre neste painel: **o contacto que a pessoa já
registou**. Não há email a sair daqui — o painel nunca enviou emails — por isso
o link vai por Telegram, Discord, WhatsApp ou webhook, exatamente como os avisos
de vencimento. Quem não registou nenhum contacto não tem por onde receber o
link, e isso é dito ao administrador no log em vez de ficar em silêncio.

Três decisões de segurança que este módulo existe para guardar:

🛡️ **A resposta é sempre a mesma.** Exista a conta ou não, tenha contacto ou
não, quem pede recebe "se existir uma conta, enviámos o link". Responder
"utilizador não encontrado" transformava esta rota num oráculo que diz quais as
contas que existem neste servidor — o oposto do que a mensagem única do login
faz.

🛡️ **O link é a credencial enquanto vive.** Quem o tiver muda a palavra-passe.
Por isso vale minutos, serve uma só vez, e o que fica na base de dados é o
RESUMO dele (ver `PasswordReset`).

🛡️ **Pedir tem um intervalo mínimo.** Sem ele, esta rota era um botão para
encher o Telegram de outra pessoa com mensagens que o painel assina.
"""

import logging

from flask import url_for
from flask_babel import gettext as _

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

# Quanto tempo até se poder pedir outro link para a MESMA conta. Curto o
# suficiente para quem não recebeu o primeiro tentar de novo, longo o suficiente
# para não servir de megafone contra quem quer que seja.
INTERVALO_ENTRE_PEDIDOS_SEGUNDOS = 120


def servidor_repoe_palavras_passe(media_server):
    """Este painel tem sequer palavras-passe para repor?"""
    capacidades = getattr(media_server, 'capabilities', None)
    return bool(getattr(capacidades, 'cria_contas', False))


def _contactos_do_perfil(profile):
    """Os canais por onde o painel sabe falar com esta pessoa."""
    perfil = profile or {}
    return [campo for campo in ('telegram_id', 'telegram_user', 'discord_user_id', 'phone_number')
            if perfil.get(campo)]


def _em_intervalo(media_user_id):
    """Já se pediu um link para esta conta há pouco tempo?"""
    from ..extensions import cache

    chave = f"reposicao_pedida_{media_user_id}"
    try:
        if cache.get(chave):
            return True
        cache.set(chave, True, timeout=INTERVALO_ENTRE_PEDIDOS_SEGUNDOS)
    except Exception:
        # Uma cache indisponível não pode impedir alguém de recuperar a conta.
        return False
    return False


def _link_do_pedido(config, token):
    """O endereço que vai na notificação.

    Como o link de pagamento: a `APP_BASE_URL` manda, porque o `url_for` externo
    falha fora de um pedido HTTP (uma tarefa de fundo não tem `SERVER_NAME`).
    """
    base = (config.get("APP_BASE_URL") or '').strip().rstrip('/')
    caminho = url_for('main.password_reset_page', token=token, _external=False)
    return f"{base}{caminho}" if base else url_for('main.password_reset_page', token=token, _external=True)


def _encontrar_perfil(data_manager, identificador):
    """Pelo nome de utilizador ou pelo email — é o que a pessoa se lembra."""
    identificador = (identificador or '').strip()
    if not identificador:
        return None

    return (data_manager.get_user_profile_by_username(identificador)
            or data_manager.get_user_profile_by_email(identificador))


def pedir_reposicao(identificador, data_manager, media_server, notifier_manager):
    """Cria o pedido e entrega o link, se houver a quem e por onde.

    Devolve sempre `True`: quem chama responde o mesmo em todos os casos. O que
    aconteceu de facto fica no log, que é de quem administra — e não de quem
    está a tentar descobrir se uma conta existe.
    """
    config = load_or_create_config()
    perfil = _encontrar_perfil(data_manager, identificador)

    if not perfil:
        logger.info("Pedido de reposição de palavra-passe para um identificador que não existe.")
        return True

    media_user_id = perfil.get('media_user_id')

    if not _contactos_do_perfil(perfil):
        logger.warning(
            f"'{perfil.get('username')}' pediu para repor a palavra-passe, mas o perfil não tem "
            "nenhum contacto (Telegram, Discord, WhatsApp) por onde enviar o link. "
            "O acesso tem de ser reposto à mão."
        )
        return True

    if _em_intervalo(media_user_id):
        logger.info(f"Pedido de reposição de '{perfil.get('username')}' ignorado: já foi enviado um há pouco.")
        return True

    try:
        token = data_manager.criar_pedido_de_reposicao(media_user_id)
        entrega = notifier_manager.send_password_reset_notification(
            {'id': media_user_id, 'username': perfil.get('username'), 'email': perfil.get('email')},
            perfil,
            _link_do_pedido(config, token),
            data_manager.VALIDADE_DA_REPOSICAO_MINUTOS,
        )
    except Exception as e:
        logger.error(f"Falha ao preparar a reposição de palavra-passe: {e}", exc_info=True)
        return True

    if entrega.get('sent'):
        logger.info(f"Link de reposição enviado a '{perfil.get('username')}' por {', '.join(entrega['sent'])}.")
    else:
        # ⚠️ O pedido existe e o link não chegou a ninguém. Deixá-lo de pé seria
        # um token válido à solta sem dono; apaga-se, e o administrador fica a
        # saber pelo log.
        logger.error(
            f"Nenhum canal aceitou o link de reposição de '{perfil.get('username')}' "
            f"({entrega.get('failed')}). O pedido foi descartado."
        )
        try:
            data_manager.consumir_pedido_de_reposicao(token)
        except Exception:
            pass

    return True


def aplicar_reposicao(token, nova_palavra_passe, data_manager, media_server):
    """Consome o pedido e grava a palavra-passe nova no servidor.

    Devolve `(sucesso, mensagem)`. Aqui, ao contrário do pedido, os erros são
    ditos: quem chegou com um token válido já provou que recebeu a notificação,
    e um "link expirado" é a informação de que precisa.
    """
    media_user_id, motivo = data_manager.consumir_pedido_de_reposicao(token)

    if not media_user_id:
        return False, {
            'expirado': _("Este link já expirou. Peça um novo."),
            'usado': _("Este link já foi usado. Peça um novo se precisar."),
        }.get(motivo, _("Este link não é válido."))

    resultado = media_server.definir_palavra_passe(media_user_id, nova_palavra_passe)
    if not resultado.get('success'):
        # O pedido já foi consumido: é o lado seguro do erro (um token que
        # sobrevivesse a uma falha ficava a valer mais tempo do que devia).
        return False, resultado.get('message') or _("Não foi possível alterar a palavra-passe.")

    perfil = data_manager.get_user_profile(media_user_id) or {}
    logger.info(f"'{perfil.get('username')}' repôs a palavra-passe através de um link.")
    return True, _("Palavra-passe alterada. Já pode entrar com a nova.")
