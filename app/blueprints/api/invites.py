# app/blueprints/api/invites.py

import logging
import secrets
from flask import Blueprint, jsonify, request, url_for
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _
from flask_login import login_required

from ...extensions import plex_manager, limiter
from ..auth import admin_required
from .decorators import validate_json
from .schemas import CreateInviteSchema, CreateInviteBotSchema
from ...config import load_or_create_config
from ...utils.log_sanitizer import mask_code

logger = logging.getLogger(__name__)
invites_api_bp = Blueprint('invites_api', __name__)

@invites_api_bp.route('/create', methods=['POST'])
@login_required
@admin_required
@validate_json(CreateInviteSchema)
def create_invite_route(validated_data):
    data = validated_data.dict()
    result = plex_manager.create_invitation(
        library_titles=data.get('libraries', []), 
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
        result['invite_url'] = url_for('main.claim_invite_page', code=result['code'], _external=True)
    return jsonify(result)

@invites_api_bp.route('/bot/create', methods=['POST'])
@limiter.limit("30 per minute")
@validate_json(CreateInviteBotSchema)
def create_invite_for_bot(validated_data):
    """
    Endpoint de integração para bots: cria um convite já vinculado a um Telegram ID.

    🔒 AUTENTICAÇÃO: esta rota não usa sessão de navegador (um bot não tem uma), por
    isso é protegida por uma chave de API enviada no cabeçalho 'X-API-Key' (ou
    'Authorization: Bearer <chave>'). A chave é a 'INTERNAL_TRIGGER_KEY', que já é
    gerada automaticamente na configuração e nunca é exposta pela API de definições.

    A comparação é feita com 'secrets.compare_digest' para não vazar informação
    através do tempo de resposta (timing attack).

    Exemplo:
        curl -X POST https://o-seu-painel/api/invites/bot/create \\
             -H "X-API-Key: SUA_CHAVE" \\
             -H "Content-Type: application/json" \\
             -d '{"telegram_id": "123456789", "screens": 1, "trial_duration_minutes": 60}'
    """
    config = load_or_create_config()
    expected_key = str(config.get('INTERNAL_TRIGGER_KEY') or '')

    provided_key = request.headers.get('X-API-Key', '')
    if not provided_key:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.lower().startswith('bearer '):
            provided_key = auth_header[7:].strip()

    if not expected_key or not provided_key or not secrets.compare_digest(provided_key, expected_key):
        logger.warning(f"Tentativa de acesso não autorizado ao endpoint de convites para bots (IP: {request.remote_addr}).")
        return jsonify({"success": False, "message": _("Chave de API inválida ou em falta.")}), 401

    data = validated_data.dict()

    # Se o bot não indicar bibliotecas, usamos todas as do servidor — é o
    # comportamento esperado numa automação, que normalmente não as conhece.
    libraries = data.get('libraries')
    if not libraries:
        try:
            libraries_result = plex_manager.get_libraries()
            libraries = [lib['title'] for lib in libraries_result.get('libraries', [])] if libraries_result.get('success') else []
        except Exception as e:
            logger.error(f"Não foi possível obter a lista de bibliotecas para o convite via bot: {e}")
            libraries = []

        if not libraries:
            return jsonify({
                "success": False,
                "message": _("Não foi possível determinar as bibliotecas automaticamente. Indique 'libraries' no pedido.")
            }), 400

    result = plex_manager.create_invitation(
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
        result['invite_url'] = url_for('main.claim_invite_page', code=result['code'], _external=True)
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
    return jsonify(plex_manager.list_invitations())

@invites_api_bp.route('/delete', methods=['POST'])
@login_required
@admin_required
def delete_invite_route():
    code = (request.get_json(silent=True) or {}).get('code')
    if not code:
        return jsonify({"success": False, "message": "Código do convite não fornecido."}), 400
    return jsonify(plex_manager.delete_invitation(code))

# **NOVA ROTA**: Reativar convite (resetar uso)
@invites_api_bp.route('/reactivate', methods=['POST'])
@login_required
@admin_required
def reactivate_invite_route():
    code = (request.get_json(silent=True) or {}).get('code')
    if not code:
        return jsonify({"success": False, "message": "Código do convite não fornecido."}), 400
    return jsonify(plex_manager.reactivate_invitation(code))

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
    invitation, message = plex_manager.get_invitation_by_code(code)
    if not invitation: return jsonify({"success": False, "message": message}), 404
    return jsonify({"success": True, "details": {"expires_at": invitation.get("expires_at")}})

@invites_api_bp.route('/claim', methods=['POST'])
@limiter.limit("10 per minute")
def claim_invite_route():
    """
    🔒 Rota PÚBLICA e a mais cara de todas: cada chamada valida um token junto da
    plex.tv e, se o convite for válido, concede acesso ao servidor. Sem limite,
    servia para testar códigos e tokens à vontade.
    """
    data = request.get_json(silent=True) or {}
    try:
        plex_token = data.get('plex_token')
        if not plex_token:
            return jsonify({"success": False, "message": _("Token do Plex não fornecido.")}), 400
        new_user_account = MyPlexAccount(token=plex_token)
        logger.info(f"Token do novo utilizador '{new_user_account.username}' validado com sucesso.")
        return jsonify(plex_manager.claim_invitation(data.get('code'), new_user_account))
    except Exception as e:
        logger.error(f"Falha ao validar o token do Plex do novo utilizador: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Token do Plex inválido.")}), 401
