# app/blueprints/api/invites.py

import logging
from types import SimpleNamespace

from flask import Blueprint, jsonify, request
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _
from flask_login import login_required

from ...extensions import media_server, limiter
from ..auth import admin_required
from .decorators import validate_json, chave_de_api_necessaria
from .schemas import CreateInviteSchema, CreateInviteBotSchema, validar_email
from ...utils.log_sanitizer import mask_code
from ...utils.enderecos import endereco_publico

logger = logging.getLogger(__name__)
invites_api_bp = Blueprint('invites_api', __name__)

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
        telegram_id=data.get('telegram_id') 
    )
    if result.get('success'):
        result['invite_url'] = endereco_publico('main.claim_invite_page', code=result['code'])
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
        telegram_id=data.get('telegram_id')
    )

    if result.get('success'):
        result['invite_url'] = endereco_publico('main.claim_invite_page', code=result['code'])
        result['telegram_id'] = data.get('telegram_id')
        logger.info(f"Convite '{mask_code(result['code'])}' criado via API para o Telegram ID {data.get('telegram_id')}.")
        return jsonify(result), 201

    # Conflitos de unicidade (ID já vinculado, ou já com convite ativo) devolvem 409.
    return jsonify(result), 409


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
    return jsonify(media_server.delete_invitation(code))

# **NOVA ROTA**: Reativar convite (resetar uso)
@invites_api_bp.route('/reactivate', methods=['POST'])
@login_required
@admin_required
def reactivate_invite_route():
    code = (request.get_json(silent=True) or {}).get('code')
    if not code:
        return jsonify({"success": False, "message": "Código do convite não fornecido."}), 400
    return jsonify(media_server.reactivate_invitation(code))

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
        return jsonify(media_server.claim_invitation(data.get('code'), registo))

    try:
        plex_token = data.get('plex_token')
        if not plex_token:
            return jsonify({"success": False, "message": _("Token do Plex não fornecido.")}), 400
        new_user_account = MyPlexAccount(token=plex_token)
        logger.info(f"Token do novo utilizador '{new_user_account.username}' validado com sucesso.")
        return jsonify(media_server.claim_invitation(data.get('code'), new_user_account))
    except Exception as e:
        logger.error(f"Falha ao validar o token do Plex do novo utilizador: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Token do Plex inválido.")}), 401
