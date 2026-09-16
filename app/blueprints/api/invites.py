# app/blueprints/api/invites.py

import logging
from types import SimpleNamespace

from flask import Blueprint, jsonify, request
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _
from flask_login import login_required

from ...extensions import media_server, limiter, data_manager
from ..auth import admin_required
from .decorators import validate_json, chave_de_api_necessaria
from .schemas import CreateInviteSchema, CreateInviteBotSchema, validar_email
from ...utils.log_sanitizer import mask_code
from ...utils.enderecos import endereco_publico
from ...services import audit
from ...services.media_server.invitations import (
    CONFLITO, convite_esgotado, convite_expirado,
)

logger = logging.getLogger(__name__)
invites_api_bp = Blueprint('invites_api', __name__)

def _estado_http(resultado):
    """O código HTTP de uma criação recusada.

    ⚠️ O endpoint dos bots respondia 409 a TUDO o que falhasse — inclusive a
    "informe pelo menos uma biblioteca", que é um erro do PEDIDO. Do outro lado
    não havia como saber se valia a pena tentar outra vez com outro código
    (409: o estado é que não deixa) ou se o pedido estava simplesmente errado
    (400: tentar de novo dá o mesmo).
    """
    return 409 if resultado.get('erro') == CONFLITO else 400


def _convite_para_a_api(convite):
    """O convite como uma integração o vê.

    ⚠️ Os nomes são os da API pública e não os das colunas: `active` e
    `uses_left` são o que um bot quer perguntar, e derivá-los no cliente
    obrigava cada integração a repetir as duas regras (esgotado, expirado) e a
    conhecer o formato da data. É o mesmo que o painel faz no cartão da lista.

    🔒 Não leva `libraries`: os nomes das bibliotecas são infraestrutura do
    servidor, e quem tem a chave de um bot não precisa deles para mandar um
    link a alguém.
    """
    expirado = convite_expirado(convite.get('expires_at'), convite.get('code', ''))
    esgotado = convite_esgotado(convite)
    usados, maximo = convite.get('use_count', 0), convite.get('max_uses', 1)

    return {
        'code': convite.get('code'),
        'invite_url': endereco_publico('main.claim_invite_page', code=convite.get('code')),
        'active': not (expirado or esgotado),
        'expired': expirado,
        'exhausted': esgotado,
        'created_at': convite.get('created_at'),
        'expires_at': convite.get('expires_at'),
        'claimed_at': convite.get('claimed_at'),
        'use_count': usados,
        'max_uses': maximo,
        'uses_left': max(maximo - usados, 0),
        'claimed_by': convite.get('claimed_by_users') or [],
        'trial_duration_minutes': convite.get('trial_duration_minutes', 0),
        'screens': convite.get('screen_limit', 0),
        'allow_downloads': bool(convite.get('allow_downloads', False)),
        'overseerr_access': bool(convite.get('overseerr_access', False)),
        'telegram_id': convite.get('telegram_id'),
        'note': convite.get('note'),
    }


def _para_a_auditoria(pedido, code):
    """O que fica registado sobre um convite criado.

    ⚠️ O código vai INTEIRO, ao contrário do que se faz no `app.log`. Ele é uma
    credencial portadora, mas o `mask_code` do log existe porque o log é um
    ficheiro à parte, que se copia e se cola num chat de suporte; a auditoria
    vive na MESMA base de dados que a tabela `invitations`, onde os códigos já
    estão em claro. Mascará-lo aqui não esconderia nada de ninguém e tornava a
    linha inútil — "alguém apagou um convite" sem dizer qual.
    """
    return {
        'codigo': code,
        'bibliotecas': pedido.get('libraries') or [],
        'telas': pedido.get('screens', 0),
        'downloads': bool(pedido.get('allow_downloads', False)),
        'expira_em_minutos': pedido.get('expires_in_minutes'),
        'teste_em_minutos': pedido.get('trial_duration_minutes', 0),
        'acesso_aos_pedidos': bool(pedido.get('overseerr_access', False)),
        'usos': pedido.get('max_uses', 1),
        'telegram_id': pedido.get('telegram_id'),
        'nota': pedido.get('note'),
    }


def _titulos_do_servidor():
    """Os nomes das bibliotecas, ou None quando não foi possível perguntar.

    ⚠️ A diferença entre `[]` e `None` é a que interessa: as duas conexões
    devolvem uma lista vazia tanto quando o servidor está em baixo como quando
    ele não tem bibliotecas nenhumas, e não há como as distinguir daqui. Tratar
    o vazio como "não sei" é o lado seguro — não saber que bibliotecas existem
    nunca pode ser motivo para impedir o administrador de criar um convite.
    """
    try:
        bibliotecas = media_server.get_libraries() or []
    except Exception as e:
        logger.warning(f"Não foi possível obter as bibliotecas do servidor: {e}")
        return None

    titulos = [b.get('title') for b in bibliotecas if isinstance(b, dict) and b.get('title')]
    return titulos or None


def _resolver_bibliotecas(pedidas):
    """Traduz o que foi pedido para a grafia do SERVIDOR.

    🐛 Um convite era aceite com QUALQUER nome de biblioteca. A falha só
    aparecia no resgate, lá dentro do `send_invite` — "Nenhuma biblioteca
    válida foi encontrada para compartilhar" — ou seja, quem pagava o engano do
    administrador era quem tinha acabado de clicar no link. Um bot, que manda
    os nomes escritos à mão, acertava ainda menos.

    E a grafia importa: o backend do Plex compara `s.title in library_titles`
    exatamente, por isso um convite criado com "filmes" num servidor que tem
    "Filmes" nascia com uma biblioteca que nunca ia ser encontrada. Devolvemos
    sempre o nome como o servidor o escreve.

    Devolve `(titulos, mensagem_de_erro)`.
    """
    do_servidor = _titulos_do_servidor()
    if do_servidor is None:
        return list(pedidas), None

    por_minusculas = {titulo.casefold(): titulo for titulo in do_servidor}

    canonicos, desconhecidas = [], []
    for pedida in pedidas:
        encontrada = por_minusculas.get(str(pedida).strip().casefold())
        if encontrada is None:
            desconhecidas.append(str(pedida))
        else:
            canonicos.append(encontrada)

    if desconhecidas:
        return None, _(
            "Estas bibliotecas não existem no servidor: %(nomes)s.",
            nomes=', '.join(desconhecidas),
        )
    return canonicos, None


@invites_api_bp.route('/create', methods=['POST'])
@login_required
@admin_required
@validate_json(CreateInviteSchema)
def create_invite_route(validated_data):
    data = validated_data.dict()

    libraries, erro = _resolver_bibliotecas(data.get('libraries', []))
    if erro:
        return jsonify({"success": False, "message": erro}), 400

    result = media_server.create_invitation(
        library_titles=libraries, 
        screens=data.get('screens', 0), 
        allow_downloads=data.get('allow_downloads', False), 
        expires_in_minutes=data.get('expires_in_minutes'),
        trial_duration_minutes=data.get('trial_duration_minutes', 0),
        overseerr_access=data.get('overseerr_access', False),
        custom_code=data.get('custom_code'),
        max_uses=data.get('max_uses', 1),
        telegram_id=data.get('telegram_id'),
        note=data.get('note'),
    )
    if result.get('success'):
        result['invite_url'] = endereco_publico('main.claim_invite_page', code=result['code'])
        audit.registar('convite.criar', alvo_tipo='convite', alvo_id=result['code'],
                       detalhes=_para_a_auditoria({**data, 'libraries': libraries}, result['code']))
    return jsonify(result)

@invites_api_bp.route('/bot/create', methods=['POST'])
@limiter.limit("30 per minute")
@chave_de_api_necessaria
@validate_json(CreateInviteBotSchema)
def create_invite_for_bot(validated_data):
    """
    Endpoint de integração para bots: cria um convite já vinculado a um Telegram ID.

    🔒 AUTENTICAÇÃO: esta rota não usa sessão de navegador (um bot não tem uma),
    por isso é protegida pela chave de API — ver `chave_de_api_necessaria`, que
    é o único sítio do painel onde essa verificação vive.

    Exemplo:
        curl -X POST https://o-seu-painel/api/invites/bot/create \\
             -H "X-API-Key: SUA_CHAVE" \\
             -H "Content-Type: application/json" \\
             -d '{"telegram_id": "123456789", "screens": 1, "trial_duration_minutes": 60}'
    """
    data = validated_data.dict()

    # Se o bot não indicar bibliotecas, usamos todas as do servidor — é o
    # comportamento esperado numa automação, que normalmente não as conhece.
    libraries = data.get('libraries')
    if not libraries:
        # 🐛 Isto nunca funcionou. `get_libraries()` devolve uma LISTA e o
        # código pedia-lhe `.get('success')` — um `AttributeError` que caía no
        # `except` mesmo com o servidor a responder perfeitamente. Resultado: o
        # campo que a documentação anuncia como opcional dava sempre 400, e
        # nenhum bot podia deixar de conhecer os nomes das bibliotecas.
        libraries = _titulos_do_servidor()

        if not libraries:
            return jsonify({
                "success": False,
                "message": _("Não foi possível determinar as bibliotecas automaticamente. Informe 'libraries' na requisição.")
            }), 400
    else:
        libraries, erro = _resolver_bibliotecas(libraries)
        if erro:
            return jsonify({"success": False, "message": erro}), 400

    result = media_server.create_invitation(
        library_titles=libraries,
        screens=data.get('screens', 0),
        allow_downloads=data.get('allow_downloads', False),
        expires_in_minutes=data.get('expires_in_minutes'),
        trial_duration_minutes=data.get('trial_duration_minutes', 0),
        overseerr_access=data.get('overseerr_access', False),
        custom_code=data.get('custom_code'),
        max_uses=data.get('max_uses', 1),
        telegram_id=data.get('telegram_id'),
        note=data.get('note'),
    )

    if result.get('success'):
        result['invite_url'] = endereco_publico('main.claim_invite_page', code=result['code'])
        result['telegram_id'] = data.get('telegram_id')
        logger.info(f"Convite '{mask_code(result['code'])}' criado via API para o Telegram ID {data.get('telegram_id')}.")
        # Sem sessão, o ator fica vazio e o que identifica quem agiu é o
        # ENDEREÇO — que é a verdade sobre um convite criado por uma máquina.
        audit.registar('convite.criar', alvo_tipo='convite', alvo_id=result['code'],
                       detalhes={**_para_a_auditoria({**data, 'libraries': libraries}, result['code']),
                                 'origem': 'bot'})
        return jsonify(result), 201

    return jsonify(result), _estado_http(result)


@invites_api_bp.route('/bot/invite/<string:code>', methods=['GET'])
@limiter.limit("60 per minute")
@chave_de_api_necessaria
def bot_invite_status(code):
    """O estado de um convite: já foi usado? ainda vale?

    Sem isto, um bot que gera um convite fica sem saber o que lhe aconteceu. A
    única alternativa era perguntar à pessoa — ou esperar que ela diga que já
    entrou.

    ⚠️ A leitura é a CRUA (`data_manager.get_invitation`) e não a
    `get_invitation_by_code`: esta última é a porta do RESGATE e devolve `None`
    para um convite expirado ou esgotado. Aqui, "expirado" é precisamente a
    resposta que se veio buscar — com a outra porta, um convite gasto seria
    indistinguível de um que nunca existiu.

    O caminho é `/bot/invite/<code>` e não `/bot/<code>` porque um código
    personalizado pode ser a palavra `create`.
    """
    convite = data_manager.get_invitation(code)
    if not convite:
        return jsonify({"success": False, "message": _("Convite não encontrado.")}), 404
    return jsonify({"success": True, "invite": _convite_para_a_api(convite)})


@invites_api_bp.route('/bot/invite/<string:code>', methods=['DELETE'])
@limiter.limit("30 per minute")
@chave_de_api_necessaria
def bot_invite_delete(code):
    """Revoga um convite que já foi enviado.

    Um link mandado para o chat errado não se desfaz do lado do Telegram, e até
    aqui a única forma de o travar era entrar no painel — o que uma automação,
    por definição, não faz.

    ⚠️ Revogar NÃO apaga quem já resgatou: quem entrou, entrou, e a conta dessa
    pessoa não é assunto deste pedido. O que deixa de valer é o link.
    """
    convite = data_manager.get_invitation(code) or {}
    if not convite:
        return jsonify({"success": False, "message": _("Convite não encontrado.")}), 404

    resultado = media_server.delete_invitation(code)
    if resultado.get('success'):
        audit.registar('convite.apagar', alvo_tipo='convite', alvo_id=code, detalhes={
            'codigo': code,
            'usos': f"{convite.get('use_count', 0)}/{convite.get('max_uses', 1)}",
            'resgatado_por': convite.get('claimed_by_users') or [],
            'origem': 'bot',
        })
    return jsonify(resultado)


@invites_api_bp.route('/bot/invites', methods=['GET'])
@limiter.limit("60 per minute")
@chave_de_api_necessaria
def bot_invites_por_telegram():
    """Os convites gerados para um Telegram ID, do mais recente para o mais antigo.

    É a pergunta que um bot faz antes de gerar outro: a criação recusa-se
    (409) quando já existe um convite ATIVO para aquele ID, e sem esta rota o
    bot só descobria isso ao levar com o erro — sem saber qual é o link que já
    tinha mandado, nem se a pessoa já o usou.
    """
    telegram_id = (request.args.get('telegram_id') or '').strip()
    if not telegram_id:
        return jsonify({
            "success": False,
            "message": _("Informe o 'telegram_id' na consulta."),
        }), 400

    convites = [
        _convite_para_a_api(convite)
        for convite in media_server.list_invitations()
        # A mesma normalização da criação: um bot manda o ID como número e um
        # formulário como texto, e '123' tem de encontrar ' 123 '.
        if str(convite.get('telegram_id') or '').strip() == telegram_id
    ]
    return jsonify({"success": True, "telegram_id": telegram_id, "invites": convites})


@invites_api_bp.route('/list', methods=['GET'])
@login_required
@admin_required
@limiter.exempt # Adicionado para ignorar o limite de requisições nesta rota (polling do frontend)
def list_invites_route():
    return jsonify(media_server.list_invitations())

@invites_api_bp.route('/delete', methods=['POST'])
@login_required
@admin_required
def delete_invite_route():
    code = (request.get_json(silent=True) or {}).get('code')
    if not code:
        return jsonify({"success": False, "message": "Código do convite não fornecido."}), 400

    # A auditoria fica com o que o convite ERA: depois de apagado não há a quem
    # perguntar, e "apagou um convite" sem dizer qual não responde a nada.
    #
    # ⚠️ A leitura é a CRUA (`data_manager.get_invitation`) e não a
    # `get_invitation_by_code`, que recusa um convite expirado ou esgotado
    # devolvendo `None` — e esses são precisamente os que mais se apagam. Com a
    # outra porta, a linha da auditoria ficava vazia justamente no caso comum.
    convite = data_manager.get_invitation(code) or {}
    resultado = media_server.delete_invitation(code)
    if resultado.get('success'):
        audit.registar('convite.apagar', alvo_tipo='convite', alvo_id=code, detalhes={
            'codigo': code,
            'usos': f"{convite.get('use_count', 0)}/{convite.get('max_uses', 1)}",
            'resgatado_por': convite.get('claimed_by_users') or [],
        })
    return jsonify(resultado)

# **NOVA ROTA**: Reativar convite (resetar uso)
@invites_api_bp.route('/reactivate', methods=['POST'])
@login_required
@admin_required
def reactivate_invite_route():
    code = (request.get_json(silent=True) or {}).get('code')
    if not code:
        return jsonify({"success": False, "message": "Código do convite não fornecido."}), 400

    resultado = media_server.reactivate_invitation(code)
    if resultado.get('success'):
        audit.registar('convite.reativar', alvo_tipo='convite', alvo_id=code,
                       detalhes={'codigo': code})
    return jsonify(resultado)

@invites_api_bp.route('/details/<string:code>', methods=['GET'])
@limiter.limit("30 per minute")
def get_invite_details_route(code):
    """
    🔒 Rota PÚBLICA (sem sessão): responde 200 para um código válido e 404 para
    um inválido, ou seja, é um oráculo que diz se um código existe. O limitador
    global não define `default_limits`, por isso sem este decorador não havia
    limite nenhum e um código personalizado curto podia ser descoberto à força
    bruta. 30/min chega para qualquer utilização legítima (a página valida o
    convite uma vez ao abrir).
    """
    invitation, message = media_server.get_invitation_by_code(code)
    if not invitation: return jsonify({"success": False, "message": message}), 404
    return jsonify({"success": True, "details": {"expires_at": invitation.get("expires_at")}})

# Os mesmos limites da rota de login: é o ponto onde o pedido para, antes de
# haver viagem ao servidor de média.
MAX_UTILIZADOR = 128
MAX_PALAVRA_PASSE = 256
MAX_EMAIL = 254


def _registar_resgate(code, resultado, username):
    """Deixa na auditoria que um convite foi resgatado — e por quem.

    🛡️ Só os campos escolhidos à mão. O corpo deste pedido traz a PALAVRA-PASSE
    que a pessoa acabou de escolher num servidor de contas locais, e a
    auditoria vai dentro do ZIP de backup: passar o corpo inteiro aqui era a
    forma mais fácil de a fazer viajar para onde nunca devia ir.

    O ator fica vazio de propósito — quem resgata ainda não tem sessão no
    painel — e o que identifica o pedido é o endereço, que o `registar` já
    guarda.
    """
    if not resultado.get('success'):
        return
    audit.registar('convite.resgatar', alvo_tipo='convite', alvo_id=code, detalhes={
        'codigo': code,
        'usuario': username,
    })


@invites_api_bp.route('/claim', methods=['POST'])
@limiter.limit("10 per minute")
def claim_invite_route():
    """
    🔒 Rota PÚBLICA e a mais cara de todas: cada chamada valida um token junto da
    plex.tv e, se o convite for válido, concede acesso ao servidor. Sem limite,
    servia para testar códigos e tokens à vontade.
    """
    data = request.get_json(silent=True) or {}

    # Num servidor de contas locais (Jellyfin), resgatar um convite é CRIAR a
    # conta: em vez de um token de uma conta que já existe, chegam as
    # credenciais que a pessoa acabou de escolher. Ver
    # `JellyfinAccountManager.claim_invitation`.
    if media_server.capabilities.cria_contas:
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''
        email = (data.get('email') or '').strip()

        if not username or not password:
            return jsonify({"success": False, "message": _("Informe um nome de usuário e uma senha.")}), 400

        if len(password) < 6:
            return jsonify({"success": False, "message": _("A senha precisa ter pelo menos 6 caracteres.")}), 400

        # 🛡️ Esta rota é PÚBLICA e o que aqui chega vai direto para o servidor de
        # média (criar a conta) e para a base de dados (o perfil). Sem um limite
        # ao tamanho, um nome ou uma palavra-passe de megabytes era lido para
        # memória, enviado ao servidor e gravado — por quem nem precisa de ter
        # sessão. Os limites acompanham os do login (`auth.py`).
        if len(username) > MAX_UTILIZADOR or len(password) > MAX_PALAVRA_PASSE or len(email) > MAX_EMAIL:
            logger.warning("Resgate de convite recusado: campos acima do tamanho aceite.")
            return jsonify({"success": False, "message": _("Os dados informados são longos demais.")}), 400

        # ⚠️ O email é OPCIONAL aqui (nas contas locais ninguém é obrigado a
        # dar um), mas quando vem tem de ter forma: é por ele que o Seerr
        # encontra a pessoa e que os avisos chegam. Um erro de escrita não dava
        # erro nenhum — dava uma aba "Meus Pedidos" vazia para sempre.
        #
        # 🛡️ A mensagem é FIXA e não o texto da exceção. Esta rota é PÚBLICA, e
        # devolver `str(e)` num caminho público é entregar a quem pede aquilo
        # que o servidor sabe sobre a falha — aqui seria inofensivo (a mensagem
        # é escrita por nós, logo ali em `validar_email`), mas o padrão não é:
        # basta alguém pôr outra coisa a levantar dentro deste `try` para
        # passar a sair daqui o que essa outra coisa quiser dizer. Foi o que o
        # CodeQL marcou, e tem razão sobre a forma.
        try:
            email = validar_email(email) or ''
        except ValueError:
            return jsonify({
                "success": False,
                "message": _("Informe um e-mail válido, como nome@exemplo.com."),
            }), 400

        registo = SimpleNamespace(username=username, password=password, email=email)
        resultado = media_server.claim_invitation(data.get('code'), registo)
        _registar_resgate(data.get('code'), resultado, username)
        return jsonify(resultado)

    try:
        plex_token = data.get('plex_token')
        if not plex_token:
            return jsonify({"success": False, "message": _("Token do Plex não fornecido.")}), 400
        new_user_account = MyPlexAccount(token=plex_token)
        logger.info(f"Token do novo utilizador '{new_user_account.username}' validado com sucesso.")
        resultado = media_server.claim_invitation(data.get('code'), new_user_account)
        _registar_resgate(data.get('code'), resultado, new_user_account.username)
        return jsonify(resultado)
    except Exception as e:
        logger.error(f"Falha ao validar o token do Plex do novo utilizador: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Token do Plex inválido.")}), 401
