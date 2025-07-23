# app/blueprints/api/payments.py

import logging
from datetime import datetime, date
from flask import Blueprint, jsonify, request, url_for, current_app
from flask_login import current_user
from flask_babel import gettext as _
from flask_login import login_required

from ...extensions import plex_manager, tautulli_manager, data_manager, efi_manager, mercado_pago_manager
from ...config import load_or_create_config
from ..auth import admin_required
from ...models import UserProfile

logger = logging.getLogger(__name__)
payments_api_bp = Blueprint('payments_api', __name__)

def _process_successful_payment(txid):
    """
    Processa um pagamento bem-sucedido, renovando a assinatura do usuário e
    realizando todas as ações necessárias. Esta função é à prova de falhas
    e pode ser chamada tanto pelo polling da web quanto por webhooks.
    """
    try:
        logger.info(f"Processando pagamento confirmado para o TXID: {txid}")
        payment = data_manager.get_pix_payment(txid)
        
        if not payment:
            logger.warning(f"Pagamento com TXID {txid} não encontrado na base de dados. Ignorando.")
            return
        
        if payment.get('status') == 'CONCLUIDA':
            logger.warning(f"Pagamento {txid} já está com o estado 'CONCLUIDA'. Ignorando processamento duplicado.")
            return

        data_manager.update_pix_payment_status(txid, 'CONCLUIDA')
        
        username = payment['username']
        # Força a atualização da lista de usuários do Plex para garantir que usuários recém-adicionados sejam encontrados.
        user = next((u for u in plex_manager.get_all_plex_users(force_refresh=True) if u['username'] == username), None)
        
        if user:
            screens_to_set = payment.get('screens')
            new_expiration_date = plex_manager.renew_subscription(username, 1, 'expiry_date')
            
            if screens_to_set is not None and screens_to_set > 0:
                logger.info(f"Atualizando limite de telas para '{username}' para {screens_to_set}.")
                tautulli_manager.update_screen_limit(user['email'], username, screens_to_set)
            
            profile = data_manager.get_user_profile(username)
            plex_manager.notifier_manager.send_renewal_notification(user, new_expiration_date, profile)
            
            # Garante que o contexto da aplicação está disponível para gerar o link da notificação.
            with current_app.app_context():
                user_page_link = url_for('main.users_page', _external=False)
                data_manager.create_notification(
                    message=f"Pagamento de {username} (R$ {payment['value']:.2f}) confirmado.", 
                    category='success', 
                    link=user_page_link
                )
            
            logger.info(f"Subscrição para '{username}' renovada com sucesso. Novo vencimento: {new_expiration_date.strftime('%d/%m/%Y')}")
        else:
            logger.warning(f"Utilizador '{username}' do pagamento {txid} não encontrado no Plex para renovação. O pagamento foi marcado como concluído, mas a renovação falhou.")
    except Exception as e:
        logger.error(f"Ocorreu um erro crítico ao processar o pagamento para o TXID {txid}: {e}", exc_info=True)

@payments_api_bp.route('/options')
def get_payment_options():
    token = request.args.get('token')
    username = None
    is_public_request = bool(token)

    if token:
        profile = UserProfile.query.filter_by(payment_token=token).first()
        if profile:
            username = profile.username
    elif current_user.is_authenticated:
        username = current_user.username
    
    if not username:
        return jsonify({"success": False, "message": _("Usuário não especificado ou token inválido.")}), 400

    config = load_or_create_config()
    profile = data_manager.get_user_profile(username)
    current_screens = profile.get('screen_limit', 0)
    
    screen_prices = config.get("SCREEN_PRICES", {})
    renewal_price = config.get("RENEWAL_PRICE")
    
    available_prices = {}
    can_downgrade = True

    if is_public_request:
        price_for_current_plan = screen_prices.get(str(current_screens), renewal_price)
        if price_for_current_plan and float(price_for_current_plan) > 0:
            available_prices = {str(current_screens): price_for_current_plan}
        else:
            return jsonify({"success": False, "message": _("O seu plano atual não tem um preço de renovação definido.")}), 404
    else:
        valid_screen_prices = {k: v for k, v in screen_prices.items() if v and float(v) > 0}
        if valid_screen_prices:
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
        
        if not available_prices and renewal_price and float(renewal_price) > 0:
            available_prices["0"] = renewal_price
    
    enabled_providers = {"efi": config.get("EFI_ENABLED"), "mercadopago": config.get("MERCADOPAGO_ENABLED")}
    return jsonify({"success": True, "prices": available_prices, "providers": enabled_providers, "can_downgrade": can_downgrade})

@payments_api_bp.route('/create-charge', methods=['POST'])
def create_charge_route():
    data = request.json
    provider = data.get('provider')
    screens_str = data.get('screens')
    
    username = data.get('username') or (current_user.username if current_user.is_authenticated else None)
    if not username:
        return jsonify({"success": False, "message": _("Usuário não especificado para a cobrança.")}), 400

    if not provider or screens_str is None:
        return jsonify({"success": False, "message": _("Dados insuficientes para gerar cobrança.")}), 400

    config = load_or_create_config()
    profile = data_manager.get_user_profile(username)
    
    plex_user = next((u for u in plex_manager.get_all_plex_users() if u['username'] == username), None)
    if not plex_user:
        return jsonify({"success": False, "message": _("Usuário não encontrado no Plex.")}), 404
    
    price_str = None
    if str(screens_str) in config.get("SCREEN_PRICES", {}):
        price_str = config.get("SCREEN_PRICES")[str(screens_str)]
    elif str(screens_str) == "0":
        price_str = config.get("RENEWAL_PRICE")

    if not price_str or float(price_str) <= 0:
        return jsonify({"success": False, "message": _("Opção de plano inválida ou sem preço definido.")}), 400
        
    price = float(price_str)
    screens = int(screens_str)
    user_info = {"username": username, "name": profile.get('name', username), "email": plex_user.get('email')}
    
    result = {"success": False, "message": _("O provedor %(provider)s não está habilitado.", provider=provider)}
    if provider == 'EFI' and config.get('EFI_ENABLED'):
        result = efi_manager.create_pix_charge(user_info, price, screens)
    elif provider == 'MERCADOPAGO' and config.get('MERCADOPAGO_ENABLED'):
        result = mercado_pago_manager.create_pix_payment(user_info, price, screens)
        
    return jsonify(result)

@payments_api_bp.route('/status/<string:txid>')
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
    try:
        if 'pix' in notification_data:
            for pix_notification in notification_data['pix']:
                txid = pix_notification.get('txid')
                if not txid:
                    logger.warning("Webhook da Efí recebido sem TXID na notificação pix.")
                    continue
                
                efi_status_result = efi_manager.detail_pix_charge(txid)
                if efi_status_result.get("success") and efi_status_result.get("data", {}).get("status") == 'CONCLUIDA':
                    _process_successful_payment(txid)
                else:
                    logger.warning(f"Webhook da Efí para TXID {txid} recebido, mas o estado não é 'CONCLUIDA' ou a verificação falhou. Estado: {efi_status_result.get('data', {}).get('status')}")
    except Exception as e:
        logger.error(f"Erro ao processar o webhook da Efí: {e}", exc_info=True)
        return jsonify(status="error", message="Internal Server Error"), 500
        
    return jsonify(status="received"), 200

@payments_api_bp.route('/webhook/mercadopago', methods=['POST'])
def mercadopago_webhook():
    data = request.json
    logger.info(f"Webhook do Mercado Pago recebido: {data}")
    try:
        if data.get("type") == "payment":
            payment_id = str(data.get("data", {}).get("id"))
            if not payment_id:
                logger.warning("Webhook do Mercado Pago recebido sem ID de pagamento.")
                return jsonify(status="received"), 200

            mp_status_result = mercado_pago_manager.get_payment_details(payment_id)
            if mp_status_result.get("success") and mp_status_result.get("data", {}).get("status") == "approved":
                _process_successful_payment(payment_id)
            else:
                logger.warning(f"Webhook do Mercado Pago para ID {payment_id} recebido, mas o estado não é 'approved' ou a verificação falhou. Estado: {mp_status_result.get('data', {}).get('status')}")
    except Exception as e:
        logger.error(f"Erro ao processar o webhook do Mercado Pago: {e}", exc_info=True)
        return jsonify(status="error", message="Internal Server Error"), 500

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
