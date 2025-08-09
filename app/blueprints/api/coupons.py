# app/blueprints/api/coupons.py

import logging
from datetime import datetime
from flask import Blueprint, jsonify, request
from flask_login import login_required
from flask_babel import gettext as _

from ...extensions import data_manager
from ..auth import admin_required

logger = logging.getLogger(__name__)
coupons_api_bp = Blueprint('coupons_api', __name__)

@coupons_api_bp.route('/list')
@login_required
@admin_required
def list_coupons():
    try:
        coupons = data_manager.get_all_coupons()
        return jsonify({"success": True, "coupons": coupons})
    except Exception as e:
        logger.error(f"Erro ao listar cupões: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao obter lista de cupões."}), 500

@coupons_api_bp.route('/create', methods=['POST'])
@login_required
@admin_required
def create_coupon():
    data = request.json
    required_fields = ['code', 'discount_type', 'value', 'max_uses']
    if not all(field in data for field in required_fields):
        return jsonify({"success": False, "message": "Campos em falta."}), 400
    
    if data_manager.get_coupon_by_code(data['code']):
        return jsonify({"success": False, "message": "Este código de cupão já existe."}), 409

    try:
        coupon_details = {
            "code": data['code'],
            "discount_type": data['discount_type'],
            "value": float(data['value']),
            "max_uses": int(data['max_uses']),
            "is_active": data.get('is_active', True)
        }
        if data.get('expires_at'):
            coupon_details['expires_at'] = datetime.fromisoformat(data['expires_at'])
        
        new_coupon = data_manager.create_coupon(coupon_details)
        return jsonify({"success": True, "coupon": new_coupon, "message": "Cupão criado com sucesso."})
    except (ValueError, TypeError) as e:
        return jsonify({"success": False, "message": f"Dados inválidos: {e}"}), 400
    except Exception as e:
        logger.error(f"Erro ao criar cupão: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao criar cupão."}), 500

@coupons_api_bp.route('/delete/<int:coupon_id>', methods=['POST'])
@login_required
@admin_required
def delete_coupon(coupon_id):
    try:
        if data_manager.delete_coupon(coupon_id):
            return jsonify({"success": True, "message": "Cupão apagado com sucesso."})
        else:
            return jsonify({"success": False, "message": "Cupão não encontrado."}), 404
    except Exception as e:
        logger.error(f"Erro ao apagar cupão {coupon_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao apagar cupão."}), 500

@coupons_api_bp.route('/toggle/<int:coupon_id>', methods=['POST'])
@login_required
@admin_required
def toggle_coupon(coupon_id):
    try:
        updated_coupon = data_manager.toggle_coupon_active(coupon_id)
        if updated_coupon:
            status = "ativado" if updated_coupon['is_active'] else "desativado"
            return jsonify({"success": True, "coupon": updated_coupon, "message": f"Cupão {status} com sucesso."})
        else:
            return jsonify({"success": False, "message": "Cupão não encontrado."}), 404
    except Exception as e:
        logger.error(f"Erro ao alternar o estado do cupão {coupon_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao alterar o estado do cupão."}), 500

