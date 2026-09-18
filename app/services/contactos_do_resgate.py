# app/services/contactos_do_resgate.py

"""Os contactos que a pessoa escolhe dar logo a seguir a resgatar um convite.

🔔 **Porque é que isto existe.** `resolver_contactos_do_convite` só grava um
contacto quando o convite foi gerado por um BOT para alguém em concreto. Quem
entra por um link público ficava com o perfil sem contacto nenhum — e aí todas
as notificações do painel morrem em silêncio, porque `_prepare_and_send` exige
um `telegram_id`, um `phone_number` ou um `discord_user_id` para sequer tentar.

⚠️ E **não é só o aviso de fim de teste**: `trial_duration_minutes` é 0 por
omissão, por isso num convite normal não há teste nenhum — nem sequer há
`expiration_date` — e o que a pessoa vai perder é o lembrete de vencimento
(diário), a confirmação de renovação, a reativação, a reposição da
palavra-passe, as credenciais de uma conta recriada e o aviso em massa. O
`/pay/<token>` viaja dentro deles.

🛡️ **Duas pessoas não podem ficar no mesmo contacto**, e aqui a regra vale mais
do que na criação do convite: ali quem escreve o ID é o administrador, aqui é
quem acabou de entrar, num formulário público. Apontar o contacto de outra
pessoa redirecionava-lhe as notificações — e entre elas vai o link de
pagamento, que funciona para quem o tiver.
"""

import logging

from flask_babel import gettext as _

from .media_server.invitations import (
    CANAIS_DO_RESGATE,
    canais_ativos_no_resgate,
    normalizar_contacto,
)
from ..utils.identity import same_user
from ..utils.validacao import validar_telefone

logger = logging.getLogger(__name__)

# O nome é o único campo que não é um canal: não serve para entregar nada, é o
# `{name}` que abre as mensagens ("Olá {name}, ..."). Sem ele os modelos caem
# para o `username`, que num servidor de contas locais é o que a pessoa
# escolheu para entrar — não necessariamente como ela quer ser tratada.
COMPRIMENTO_MAXIMO_DO_NOME = 80


class ContactoEmUso(Exception):
    """O ID pertence a outra conta. Quem chama transforma isto num 409."""


def _valor_do_canal(canal, dados):
    """O que veio no pedido para este canal, já normalizado — ou None."""
    bruto = dados.get(canal.canal)
    if bruto is None:
        return None

    if canal.canal == 'whatsapp':
        # ⚠️ Pelo `validar_telefone`, o MESMO que os esquemas usam: só dígitos
        # (o destinatário é `{phone_number}@s.whatsapp.net`) e entre 8 e 15,
        # incluindo o código do país. Uma segunda cópia das regras aqui
        # divergiria da primeira no primeiro ajuste.
        return validar_telefone(bruto)

    return normalizar_contacto(bruto)


def guardar_contactos(data_manager, media_user_id, dados, config):
    """Grava no perfil os contactos escolhidos. Devolve os canais gravados.

    ⚠️ Só se escreve o que veio no pedido: um canal ausente não é um canal a
    APAGAR. A pessoa pode preencher só o WhatsApp e voltar mais tarde à "Minha
    Conta" para o resto, e um `update` que apagasse o resto a cada gravação
    tornava isso impossível.

    ⚠️ E só se aceita um canal que o painel consiga mesmo usar
    (`canais_ativos_no_resgate`): gravar um ID de um canal desligado é guardar
    um dado que ninguém vai ler, e que passa a ocupar a verificação de
    duplicados contra toda a gente.
    """
    perfil = data_manager.get_user_profile(media_user_id)
    if not perfil:
        return []

    mudancas = {}

    nome = (dados.get('name') or '').strip()
    if nome:
        mudancas['name'] = nome[:COMPRIMENTO_MAXIMO_DO_NOME]

    ativos = {c.canal for c in canais_ativos_no_resgate(config)}
    gravados = []

    for canal in CANAIS_DO_RESGATE:
        if canal.canal not in ativos:
            continue

        valor = _valor_do_canal(canal, dados)
        if not valor:
            continue

        dono = data_manager.get_user_profile_by_contacto(canal.canal, valor)
        if dono and not same_user(dono.get('media_user_id'), media_user_id):
            # 🛡️ Não se diz DE QUEM é: quem preenche o formulário não tem de
            # ficar a saber que aquele número já está no painel, nem de quem.
            raise ContactoEmUso(
                _("Este contato de %(canal)s já está vinculado a outra conta.",
                  canal=canal.rotulo)
            )

        mudancas[canal.no_perfil] = valor
        gravados.append(canal.canal)

    if not mudancas:
        return []

    perfil.update(mudancas)
    data_manager.set_user_profile(media_user_id, perfil)
    logger.info(
        f"Contactos escolhidos no resgate por '{perfil.get('username')}': "
        f"{', '.join(gravados) or 'apenas o nome'}."
    )
    return gravados
