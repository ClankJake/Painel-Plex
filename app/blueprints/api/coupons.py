# app/blueprints/api/coupons.py

import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify
from flask_login import login_required
from flask_babel import gettext as _

from ...extensions import data_manager
from ...services.data_manager import get_app_timezone
from ..auth import admin_required
from .decorators import validate_json
from .schemas import CreateCouponSchema

logger = logging.getLogger(__name__)
coupons_api_bp = Blueprint('coupons_api', __name__)


def _fim_do_dia_em_utc(data_str):
    """
    Converte 'YYYY-MM-DD' no instante em que esse dia ACABA, no fuso do painel,
    devolvido em UTC (sem fuso, que é como a coluna é lida).

    Antes guardava-se a meia-noite "nua" da data escolhida, que a validação
    interpretava como meia-noite UTC: um cupão marcado como válido "até 30/09"
    morria às 21h de 29/09 no horário de Brasília — um dia inteiro a menos do que
    o administrador tinha configurado.
    """
    dia = datetime.strptime(data_str, '%Y-%m-%d')
    local_tz = get_app_timezone()
    fim_do_dia_local = local_tz.localize(dia.replace(hour=23, minute=59, second=59))
    return fim_do_dia_local.astimezone(timezone.utc).replace(tzinfo=None)

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
@validate_json(CreateCouponSchema)
def create_coupon(validated_data):
    # A procura é agora indiferente a maiúsculas/minúsculas, por isso 'promo25'
    # deixa de poder coexistir com 'PROMO25' como se fossem cupões diferentes.
    if data_manager.get_coupon_by_code(validated_data.code):
        return jsonify({"success": False, "message": _("Este código de cupão já existe.")}), 409

    try:
        coupon_details = {
            "code": validated_data.code,
            "discount_type": validated_data.discount_type,
            "value": validated_data.value,
            "max_uses": validated_data.max_uses,
            "is_active": validated_data.is_active
        }
        if validated_data.expires_at:
            coupon_details['expires_at'] = _fim_do_dia_em_utc(validated_data.expires_at)

        new_coupon = data_manager.create_coupon(coupon_details)
        return jsonify({"success": True, "coupon": new_coupon, "message": _("Cupão criado com sucesso.")})
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

