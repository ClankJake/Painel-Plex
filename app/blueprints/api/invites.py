# app/blueprints/api/invites.py

import logging
from flask import Blueprint, jsonify, request, url_for
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _
from flask_login import login_required

from ...extensions import plex_manager
from ..auth import admin_required
from .decorators import validate_json
from .schemas import CreateInviteSchema

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
        max_uses=data.get('max_uses', 1)
    )
    if result.get('success'):
        result['invite_url'] = url_for('main.claim_invite_page', code=result['code'], _external=True)
    return jsonify(result)

@invites_api_bp.route('/list', methods=['GET'])
@login_required
@admin_required
def list_invites_route():
    return jsonify(plex_manager.list_invitations())

@invites_api_bp.route('/delete', methods=['POST'])
@login_required
@admin_required
def delete_invite_route():
    code = request.json.get('code')
    if not code:
        return jsonify({"success": False, "message": "Código do convite não fornecido."}), 400
    return jsonify(plex_manager.delete_invitation(code))

@invites_api_bp.route('/details/<string:code>', methods=['GET'])
def get_invite_details_route(code):
    invitation, message = plex_manager.get_invitation_by_code(code)
    if not invitation: return jsonify({"success": False, "message": message}), 404
    return jsonify({"success": True, "details": {"expires_at": invitation.get("expires_at")}})

@invites_api_bp.route('/claim', methods=['POST'])
def claim_invite_route():
    data = request.json
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
