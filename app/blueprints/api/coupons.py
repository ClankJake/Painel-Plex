# app/blueprints/api/coupons.py

import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from flask_login import login_required
from flask_babel import gettext as _

from ...extensions import data_manager
from ...services import audit
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
        coupons = data_manager.get_all_coupons(apagados=request.args.get('apagados') == '1')
        zona = get_app_timezone()
        for cupom in coupons:
            fim = cupom['expires_at']
            cupom['data_validade'] = fim.replace(tzinfo=timezone.utc).astimezone(zona).date().isoformat() if fim else None
        return jsonify({"success": True, "coupons": coupons})
    except Exception as e:
        logger.error(f"Erro ao listar cupões: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Falha ao obter a lista de cupons.")}), 500

@coupons_api_bp.route('/create', methods=['POST'])
@login_required
@admin_required
@validate_json(CreateCouponSchema)
def create_coupon(validated_data):
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
        if new_coupon is None:
            return jsonify({"success": False, "message": _("Este código de cupom já existe.")}), 409

        audit.registar('cupao.criar', alvo_tipo='cupao',
                       alvo_id=new_coupon['id'], detalhes=coupon_details)
        return jsonify({"success": True, "coupon": new_coupon, "message": _("Cupom criado com sucesso.")})
    except (ValueError, TypeError) as e:
        return jsonify({"success": False, "message": _("Dados inválidos: %(erro)s", erro=e)}), 400
    except Exception as e:
        logger.error(f"Erro ao criar cupão: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Falha ao criar o cupom.")}), 500

@coupons_api_bp.route('/delete/<int:coupon_id>', methods=['POST'])
@login_required
@admin_required
def delete_coupon(coupon_id):
    try:
        anterior = data_manager.get_coupon_by_id(coupon_id)
        if data_manager.delete_coupon(coupon_id):
            audit.registar('cupao.apagar', alvo_tipo='cupao', alvo_id=coupon_id,
                           detalhes={'cupao': anterior})
            return jsonify({"success": True, "message": _("Cupom apagado com sucesso.")})
        else:
            return jsonify({"success": False, "message": _("Cupom não encontrado.")}), 404
    except Exception as e:
        logger.error(f"Erro ao apagar cupão {coupon_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Falha ao apagar o cupom.")}), 500

@coupons_api_bp.route('/toggle/<int:coupon_id>', methods=['POST'])
@login_required
@admin_required
def toggle_coupon(coupon_id):
    try:
        updated_coupon = data_manager.toggle_coupon_active(coupon_id)
        if updated_coupon:
            status = "ativado" if updated_coupon['is_active'] else "desativado"
            audit.registar('cupao.alternar_estado', alvo_tipo='cupao', alvo_id=coupon_id,
                           detalhes={'depois': updated_coupon['is_active']})
            return jsonify({"success": True, "coupon": updated_coupon, "message": _("Cupom %(estado)s com sucesso.", estado=status)})
        else:
            return jsonify({"success": False, "message": _("Cupom não encontrado.")}), 404
    except Exception as e:
        logger.error(f"Erro ao alternar o estado do cupão {coupon_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Falha ao alterar o estado do cupom.")}), 500


@coupons_api_bp.post('/edit/<int:coupon_id>')
@login_required
@admin_required
@validate_json(CreateCouponSchema)
def edit_coupon(coupon_id, validated_data):
    campos = {
        'discount_type': validated_data.discount_type,
        'value': validated_data.value,
        'max_uses': validated_data.max_uses,
        'expires_at': _fim_do_dia_em_utc(validated_data.expires_at) if validated_data.expires_at else None,
    }
    if not data_manager.edit_coupon(coupon_id, validated_data.code, campos):
        return jsonify(success=False, message=_("Cupom indisponível para edição.")), 409
    audit.registar('cupao.editar', alvo_tipo='cupao', alvo_id=coupon_id, detalhes=campos)
    return jsonify(success=True, message=_("Cupom atualizado."))


@coupons_api_bp.post('/restore/<int:coupon_id>')
@login_required
@admin_required
def restore_coupon(coupon_id):
    if not data_manager.restore_coupon(coupon_id):
        return jsonify(success=False, message=_("Confira se o cupom está na lixeira e se o código está disponível.")), 409
    audit.registar('cupao.restaurar', alvo_tipo='cupao', alvo_id=coupon_id)
    return jsonify(success=True, message=_("Cupom restaurado e ativado."))


@coupons_api_bp.post('/purge/<int:coupon_id>')
@login_required
@admin_required
def purge_coupon(coupon_id):
    if not data_manager.purge_coupon(coupon_id):
        return jsonify(success=False, message=_("Só é possível excluir permanentemente cupons na lixeira sem resgates ou pagamentos vinculados.")), 409
    audit.registar('cupao.excluir', alvo_tipo='cupao', alvo_id=coupon_id)
    return jsonify(success=True, message=_("Cupom excluído permanentemente."))
