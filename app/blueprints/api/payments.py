# app/blueprints/api/payments.py

import logging
from datetime import datetime, date
from flask import Blueprint, jsonify, request, url_for
from flask_login import current_user
from flask_babel import gettext as _
from flask_login import login_required

from ...extensions import plex_manager, tautulli_manager, data_manager, efi_manager, mercado_pago_manager
from ...config import load_or_create_config
from ..auth import admin_required

logger = logging.getLogger(__name__)
payments_api_bp = Blueprint('payments_api', __name__)

def _process_successful_payment(txid):
    logger.info(f"Processando pagamento confirmado para o TXID: {txid}")
    payment = data_manager.get_pix_payment(txid)
    if not payment or payment.get('status') == 'CONCLUIDA':
        logger.warning(f"Pagamento {txid} já processado ou não encontrado. Ignorando.")
        return
    data_manager.update_pix_payment_status(txid, 'CONCLUIDA')
    username = payment['username']
    user = next((u for u in plex_manager.get_all_plex_users() if u['username'] == username), None)
    if user:
        screens_to_set = payment.get('screens')
        new_expiration_date = plex_manager.renew_subscription(username, 1, 'expiry_date')
        if screens_to_set and screens_to_set > 0:
            tautulli_manager.update_screen_limit(user['email'], username, screens_to_set)
        profile = data_manager.get_user_profile(username)
        plex_manager.notifier_manager.send_renewal_notification(user, new_expiration_date, profile)
        data_manager.create_notification(message=f"Pagamento de {username} (R$ {payment['value']:.2f}) confirmado.", category='success', link=url_for('main.users_page'))
        logger.info(f"Subscrição para '{username}' renovada com sucesso.")
    else:
        logger.warning(f"Utilizador '{username}' do pagamento {txid} não encontrado no Plex para renovação.")

@payments_api_bp.route('/options')
@login_required
def get_payment_options():
    config = load_or_create_config()
    screen_prices = config.get("SCREEN_PRICES", {})
    renewal_price = config.get("RENEWAL_PRICE")
    available_prices = {}
    valid_screen_prices = {k: v for k, v in screen_prices.items() if v and float(v) > 0}

    if valid_screen_prices:
        profile = data_manager.get_user_profile(current_user.username)
        current_screens = profile.get('screen_limit', 0)
        expiration_date_str = profile.get('expiration_date')
        days_left = 999 
        if expiration_date_str:
            try:
                expiration_date = datetime.fromisoformat(expiration_date_str).date()
                days_left = (expiration_date - date.today()).days
            except ValueError: pass
        
        renewal_window_days = int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7))
        can_downgrade = days_left <= renewal_window_days
        
        for screens, price in valid_screen_prices.items():
            if can_downgrade or int(screens) >= current_screens:
                available_prices[screens] = price
    
    # Adiciona o preço padrão se nenhum preço por tela estiver disponível
    if not available_prices and renewal_price and float(renewal_price) > 0:
        available_prices["0"] = renewal_price

    enabled_providers = {"efi": config.get("EFI_ENABLED"), "mercadopago": config.get("MERCADOPAGO_ENABLED")}
    return jsonify({"success": True, "prices": available_prices, "providers": enabled_providers})

@payments_api_bp.route('/create-charge', methods=['POST'])
@login_required
def create_charge_route():
    data = request.json
    provider = data.get('provider')
    screens_str = data.get('screens')

    if not provider or screens_str is None:
        return jsonify({"success": False, "message": _("Dados insuficientes para gerar cobrança.")}), 400

    config = load_or_create_config()
    profile = data_manager.get_user_profile(current_user.username)
    
    # --- INÍCIO DA CORREÇÃO DE VALIDAÇÃO ---
    # Revalida no backend se o plano solicitado é permitido para o utilizador.
    
    # 1. Obter as opções de pagamento válidas para este utilizador
    valid_options_response = get_payment_options()
    valid_options_data = valid_options_response.get_json()
    
    if not valid_options_data.get("success"):
        return jsonify({"success": False, "message": _("Não foi possível determinar os planos de pagamento válidos.")}), 500

    available_prices = valid_options_data.get("prices", {})

    # 2. Verificar se o plano solicitado ('screens_str') está na lista de opções válidas
    if screens_str not in available_prices:
        logger.warning(f"Utilizador '{current_user.username}' tentou gerar cobrança para um plano inválido/não permitido: {screens_str} telas. Opções permitidas: {list(available_prices.keys())}")
        return jsonify({"success": False, "message": _("O plano de pagamento solicitado não é válido ou não está disponível para si neste momento.")}), 400
        
    # 3. Obter o preço a partir da lista validada para garantir consistência
    price_str = available_prices.get(screens_str)
    
    # --- FIM DA CORREÇÃO DE VALIDAÇÃO ---

    if not price_str or float(price_str) <= 0:
        return jsonify({"success": False, "message": _("Opção de plano inválida ou sem preço definido.")}), 400
        
    price = float(price_str)
    screens = int(screens_str)
    user_info = {"username": current_user.username, "name": profile.get('name', current_user.username), "email": current_user.email}
    
    result = {"success": False, "message": _("O provedor %(provider)s não está habilitado.", provider=provider)}
    if provider == 'EFI' and config.get('EFI_ENABLED'):
        result = efi_manager.create_pix_charge(user_info, price, screens)
    elif provider == 'MERCADOPAGO' and config.get('MERCADOPAGO_ENABLED'):
        result = mercado_pago_manager.create_pix_payment(user_info, price, screens)
        
    return jsonify(result)

@payments_api_bp.route('/status/<string:txid>')
@login_required
def get_payment_status(txid):
    payment = data_manager.get_pix_payment(txid)
    if not payment: return jsonify({"success": False, "status": "NOT_FOUND"}), 404
    if payment.get('status') == 'CONCLUIDA': return jsonify({"success": True, "status": "CONCLUIDA"})
    provider = payment.get('provider', 'EFI') 
    is_confirmed = False
    if provider == 'EFI':
        efi_status_result = efi_manager.detail_pix_charge(txid)
        if efi_status_result.get("success") and efi_status_result.get("data", {}).get("status") == 'CONCLUIDA':
            is_confirmed = True
    elif provider == 'MERCADOPAGO':
        mp_status_result = mercado_pago_manager.get_payment_details(txid)
        if mp_status_result.get("success") and mp_status_result.get("data", {}).get("status") == 'approved':
            is_confirmed = True
    if is_confirmed:
        _process_successful_payment(txid)
        return jsonify({"success": True, "status": "CONCLUIDA"})
    return jsonify({"success": True, "status": payment.get('status')})

@payments_api_bp.route('/webhook/efi', methods=['POST'])
def efi_webhook():
    notification_data = request.json
    logger.info(f"Webhook da Efí recebido: {notification_data}")
    if 'pix' in notification_data:
        for pix_notification in notification_data['pix']:
            txid = pix_notification.get('txid')
            if not txid: continue
            efi_status_result = efi_manager.detail_pix_charge(txid)
            if efi_status_result.get("success") and efi_status_result.get("data", {}).get("status") == 'CONCLUIDA':
                 _process_successful_payment(txid)
    return jsonify(status="received"), 200

@payments_api_bp.route('/webhook/mercadopago', methods=['POST'])
def mercadopago_webhook():
    data = request.json
    logger.info(f"Webhook do Mercado Pago recebido: {data}")
    if data.get("type") == "payment":
        payment_id = str(data["data"]["id"])
        mp_status_result = mercado_pago_manager.get_payment_details(payment_id)
        if mp_status_result.get("success") and mp_status_result.get("data", {}).get("status") == "approved":
            _process_successful_payment(payment_id)
    return jsonify(status="received"), 200

@payments_api_bp.route('/financial/summary')
@login_required
@admin_required
def get_financial_summary_route():
    try:
        year = request.args.get('year', datetime.now().year, type=int)
        month = request.args.get('month', datetime.now().month, type=int)
        renewal_days = request.args.get('renewal_days', 7, type=int)
    except (ValueError, TypeError):
        now = datetime.now()
        year = now.year
        month = now.month
        renewal_days = 7
    summary = data_manager.get_financial_summary(year, month, renewal_days=renewal_days)
    return jsonify({"success": True, "summary": summary, "query_date": {"year": year, "month": month}})

@payments_api_bp.route('/financial/add-manual', methods=['POST'])
@login_required
@admin_required
def add_manual_payment_route():
    data = request.json
    username = data.get('username')
    value = data.get('value')
    description = data.get('description')
    payment_date = data.get('payment_date')
    if not all([username, value, description, payment_date]):
        return jsonify({"success": False, "message": _("Todos os campos são obrigatórios.")}), 400
    try:
        payment_datetime_str = f"{payment_date}T{datetime.now().strftime('%H:%M:%S')}"
        payment = data_manager.add_manual_payment(username, value, description, payment_datetime_str)
        return jsonify({"success": True, "message": _("Pagamento manual registado com sucesso."), "payment": payment})
    except Exception as e:
        logger.error(f"Erro ao adicionar pagamento manual: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
