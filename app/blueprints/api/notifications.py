# app/blueprints/api/notifications.py

import logging
from datetime import datetime
from flask import Blueprint, jsonify
from flask_login import login_required, current_user
from flask_babel import gettext as _

from ...extensions import data_manager

logger = logging.getLogger(__name__)
notifications_api_bp = Blueprint('notifications_api', __name__)

@notifications_api_bp.route('/')
@login_required
def get_notifications_route():
    try:
        # Admin vê notificações do sistema (username=None)
        # Utilizadores vêm as suas próprias (username=current_user.username)
        target_username = None if current_user.is_admin() else current_user.username
        
        notifications_data = data_manager.get_notifications(username=target_username, limit=15, include_read=True)
        unread_count = data_manager.get_unread_notification_count(username=target_username)

        for n in notifications_data:
            if isinstance(n.get('timestamp'), datetime):
                n['timestamp'] = n['timestamp'].strftime('%Y-%m-%dT%H:%M:%S')
        return jsonify({"success": True, "notifications": notifications_data, "unread_count": unread_count})
    except Exception as e:
        logger.error(f"Erro ao buscar notificações: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao buscar notificações."}), 500

@notifications_api_bp.route('/read-all', methods=['POST'])
@login_required
def mark_all_notifications_as_read_route():
    try:
        target_username = None if current_user.is_admin() else current_user.username
        updated_count = data_manager.mark_all_as_read(username=target_username)
        return jsonify({"success": True, "message": f"{updated_count} notificações marcadas como lidas."})
    except Exception as e:
        logger.error(f"Erro ao marcar notificações como lidas: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao marcar notificações como lidas."}), 500

@notifications_api_bp.route('/clear-all', methods=['POST'])
@login_required
def clear_all_notifications_route():
    try:
        target_username = None if current_user.is_admin() else current_user.username
        deleted_count = data_manager.delete_all_notifications(username=target_username)
        return jsonify({"success": True, "message": f"{deleted_count} notificações foram limpas com sucesso."})
    except Exception as e:
        logger.error(f"Erro ao limpar todas as notificações: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao limpar as notificações."}), 500
