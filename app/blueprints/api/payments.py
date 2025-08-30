# app/blueprints/api/payments.py

import logging
from datetime import datetime, date, timezone
from flask import Blueprint, jsonify, request, url_for, current_app
from flask_login import current_user
from flask_babel import gettext as _
from flask_login import login_required
from functools import wraps

from ...extensions import plex_manager, tautulli_manager, data_manager, efi_manager, mercado_pago_manager, bpix_manager
from ...config import load_or_create_config
from ..auth import admin_required
from ...models import UserProfile

logger = logging.getLogger(__name__)
payments_api_bp = Blueprint('payments_api', __name__)

def efi_webhook_security(f):
    """Decorator para proteger o endpoint do webhook da Efí."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Verificar o Endereço IP
        EFI_IP = '34.193.116.226'
        
        # Obtém o IP real, considerando proxies como Nginx ou Cloudflare.
        if 'X-Forwarded-For' in request.headers:
            # 'X-Forwarded-For' pode ser uma lista de IPs. O primeiro é o do cliente original.
            remote_ip = request.headers.getlist("X-Forwarded-For")[0].rpartition(' ')[-1]
        else:
            remote_ip = request.remote_addr or 'UNKNOWN'

        if remote_ip != EFI_IP:
            logger.warning(f"Webhook da Efí bloqueado: IP de origem '{remote_ip}' não corresponde ao IP esperado '{EFI_IP}'.")
            return jsonify(status="error", message="IP not allowed"), 403

        # 2. Verificar o HMAC se o mTLS estiver desativado
        config = load_or_create_config()
        use_mtls = config.get("EFI_USE_MTLS", True)

        if not use_mtls:
            hmac_secret = config.get("EFI_WEBHOOK_HMAC_SECRET")
            received_hmac = request.args.get('hmac')

            if not hmac_secret or not received_hmac or hmac_secret != received_hmac:
                logger.warning("Webhook da Efí bloqueado: HMAC inválido ou ausente.")
                return jsonify(status="error", message="Invalid HMAC"), 403
        
        return f(*args, **kwargs)
    return decorated_function

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
            
            # CORREÇÃO: A lógica de limite de telas foi movida do Tautulli para o DataManager.
            # Apenas guardamos o limite no perfil do utilizador. O StreamManager irá tratar da aplicação.
            if screens_to_set is not None and screens_to_set >= 0:
                logger.info(f"Atualizando limite de telas para '{username}' para {screens_to_set}.")
                profile = data_manager.get_user_profile(username)
                profile['screen_limit'] = screens_to_set
                data_manager.set_user_profile(username, profile)
            
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
            
            # Após confirmar o pagamento, registar o uso do cupão pelo utilizador
            if payment and payment.get('coupon_code'):
                data_manager.record_coupon_usage(payment['coupon_code'], payment['username'])

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
    
    enabled_providers = {
        "efi": config.get("EFI_ENABLED"), 
        "mercadopago": config.get("MERCADOPAGO_ENABLED"),
        "bpix": config.get("BPIX_ENABLED")
    }
    return jsonify({"success": True, "prices": available_prices, "providers": enabled_providers, "can_downgrade": can_downgrade})

@payments_api_bp.route('/validate-coupon', methods=['POST'])
def validate_coupon_route():
    data = request.json
    code = data.get('code')
    screens_str = data.get('screens')
    username = data.get('username')

    if not code or screens_str is None:
        return jsonify({"success": False, "message": "Código do cupão e plano são necessários."}), 400

    if not username:
        logger.warning("A validação do cupão foi chamada sem um nome de utilizador. A verificação de uso único por utilizador será ignorada.")
    elif data_manager.has_user_used_coupon(username, code):
        return jsonify({"success": False, "message": "Você já usou esse cupom."}), 403

    coupon = data_manager.get_coupon_by_code(code)

    if not coupon:
        return jsonify({"success": False, "message": "Cupão inválido ou não encontrado."}), 404
    if not coupon['is_active']:
        return jsonify({"success": False, "message": "Este cupão não está mais ativo."}), 403
    if coupon['expires_at'] and datetime.utcnow() > coupon['expires_at']:
        return jsonify({"success": False, "message": "Este cupão expirou."}), 403
    if coupon['use_count'] >= coupon['max_uses']:
        return jsonify({"success": False, "message": "Este cupão já atingiu o limite de utilizações."}), 403

    config = load_or_create_config()
    price_str = config.get("SCREEN_PRICES", {}).get(str(screens_str)) or config.get("RENEWAL_PRICE")
    
    try:
        original_price = float(price_str)
        discounted_price = original_price

        if coupon['discount_type'] == 'percentage':
            discounted_price = original_price * (1 - coupon['value'] / 100)
        elif coupon['discount_type'] == 'fixed':
            discounted_price = original_price - coupon['value']

        discounted_price = max(0, discounted_price) # O preço não pode ser negativo

        return jsonify({
            "success": True,
            "original_price": original_price,
            "discounted_price": discounted_price,
            "message": "Cupão aplicado com sucesso!"
        })

    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Preço do plano inválido."}), 400


@payments_api_bp.route('/create-charge', methods=['POST'])
def create_charge_route():
    data = request.json
    provider = data.get('provider')
    screens_str = data.get('screens')
    coupon_code = data.get('coupon_code')
    
    username = data.get('username') or (current_user.username if current_user.is_authenticated else None)
    if not username:
        return jsonify({"success": False, "message": _("Usuário não especificado para a cobrança.")}), 400

    if not provider and not coupon_code:
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
        
    final_price = float(price_str)

    if coupon_code:
        if data_manager.has_user_used_coupon(username, coupon_code):
             return jsonify({"success": False, "message": "Você já usou esse cupom."}), 403
        
        coupon = data_manager.get_coupon_by_code(coupon_code)
        if coupon and coupon['is_active'] and coupon['use_count'] < coupon['max_uses'] and (not coupon['expires_at'] or datetime.utcnow() < coupon['expires_at']):
            if coupon['discount_type'] == 'percentage':
                final_price *= (1 - coupon['value'] / 100)
            elif coupon['discount_type'] == 'fixed':
                final_price -= coupon['value']
            final_price = max(0, final_price)
        else:
            return jsonify({"success": False, "message": "O cupão fornecido já não é válido."}), 400

    if coupon_code and final_price <= 0:
        try:
            logger.info(f"A processar renovação gratuita para '{username}' com o cupão '{coupon_code}'.")
            plex_manager.renew_subscription(username, 1, 'expiry_date')
            data_manager.record_coupon_usage(coupon_code, username)
            data_manager.add_manual_payment(
                username=username,
                value=0.00,
                description=f"Renovação via Cupão 100% ({coupon_code})",
                payment_date_str=datetime.now().isoformat()
            )
            return jsonify({
                "success": True,
                "free_renewal": True,
                "message": _("Assinatura gratuita ativada com sucesso!")
            })
        except Exception as e:
            logger.error(f"Erro ao processar renovação gratuita para '{username}': {e}", exc_info=True)
            return jsonify({"success": False, "message": "Ocorreu um erro ao ativar a sua assinatura gratuita."}), 500

    price = final_price
    screens = int(screens_str)
    user_info = {"username": username, "name": profile.get('name', username), "email": plex_user.get('email')}
    
    result = {"success": False, "message": _("O provedor %(provider)s não está habilitado.", provider=provider)}
    if provider == 'EFI' and config.get('EFI_ENABLED'):
        result = efi_manager.create_pix_charge(user_info, price, screens)
        if result.get('success'):
            data_manager.create_pix_payment(result['txid'], username, price, 'EFI', screens, None, coupon_code)
    elif provider == 'MERCADOPAGO' and config.get('MERCADOPAGO_ENABLED'):
        result = mercado_pago_manager.create_pix_payment(user_info, price, screens)
        if result.get('success'):
            data_manager.create_pix_payment(result['payment_id'], username, price, 'MERCADOPAGO', screens, result.get('external_reference'), coupon_code)
    elif provider == 'BPIX' and config.get('BPIX_ENABLED'):
        result = bpix_manager.create_pix_charge(user_info, price, screens)
        
    return jsonify(result)

@payments_api_bp.route('/status/<string:txid>')
def get_payment_status(txid):
    payment = data_manager.get_pix_payment(txid)
    if not payment: return jsonify({"success": False, "status": "NOT_FOUND"}), 404
    if payment.get('status') == 'CONCLUIDA': return jsonify({"success": True, "status": "CONCLUIDA"})
    
    provider = payment.get('provider', 'EFI') 
    is_confirmed = False
    
    if provider == 'EFI':
        status_result = efi_manager.detail_pix_charge(txid)
        if status_result.get("success") and status_result.get("data", {}).get("status") == 'CONCLUIDA':
            is_confirmed = True
    elif provider == 'MERCADOPAGO':
        status_result = mercado_pago_manager.get_payment_details(txid)
        if status_result.get("success") and status_result.get("data", {}).get("status") == 'approved':
            is_confirmed = True
    elif provider == 'BPIX':
        status_result = bpix_manager.detail_pix_charge(txid)
        status_data = status_result.get("data", {})
        if status_result.get("success") and (status_data.get("status") == 'Pagamento realizado' or status_data.get("international_status") == "PAYMENT_RECEIVED"):
            is_confirmed = True

    if is_confirmed:
        _process_successful_payment(txid)
        return jsonify({"success": True, "status": "CONCLUIDA"})
        
    return jsonify({"success": True, "status": payment.get('status')})

@payments_api_bp.route('/webhook/efi', methods=['POST'])
@efi_webhook_security
def efi_webhook():
    notification_data = request.get_json(silent=True)
    
    if notification_data is None:
        logger.info("Webhook da Efí recebido, possivelmente uma chamada de validação (corpo vazio). A responder com 200 OK.")
        return jsonify(status="validation_received"), 200

    logger.info(f"Webhook da Efí recebido. Corpo da requisição: {notification_data}")
    try:
        if notification_data.get('evento') == 'teste_webhook':
            logger.info("Webhook de teste da Efí recebido e validado com sucesso.")
            return jsonify(status="received"), 200

        if 'pix' in notification_data and isinstance(notification_data['pix'], list):
            for pix_notification in notification_data['pix']:
                txid = pix_notification.get('txid')
                if not txid:
                    logger.warning("Webhook da Efí recebido sem TXID na notificação pix.")
                    continue
                
                logger.info(f"Processando notificação de webhook para o TXID: {txid}")
                
                efi_status_result = efi_manager.detail_pix_charge(txid)
                
                if efi_status_result.get("success") and efi_status_result.get("data", {}).get("status") == 'CONCLUIDA':
                    logger.info(f"Verificação de status para TXID {txid} confirmada como 'CONCLUIDA'.")
                    _process_successful_payment(txid)
                else:
                    status = efi_status_result.get('data', {}).get('status', 'desconhecido')
                    logger.warning(f"Webhook da Efí para TXID {txid} recebido, mas o estado não é 'CONCLUIDA' ou a verificação falhou. Estado atual: '{status}'")
        else:
            logger.warning(f"Formato de webhook da Efí inesperado. Chave 'pix' não encontrada ou não é uma lista. Payload: {notification_data}")

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

@payments_api_bp.route('/webhook/bpix', methods=['POST'])
def bpix_webhook():
    data = request.json
    logger.info(f"Webhook da BPIX recebido: {data}")
    try:
        txid = data.get("transaction_pix_id")
        status = data.get("status")
        international_status = data.get("international_status")

        is_paid = (status == "Pagamento realizado") or (international_status == "PAYMENT_RECEIVED")

        if txid and is_paid:
            _process_successful_payment(txid)
        else:
            logger.warning(f"Webhook da BPIX para TXID {txid} recebido, mas o estado não indica pagamento confirmado. Status: '{status}', International Status: '{international_status}'")
    except Exception as e:
        logger.error(f"Erro ao processar o webhook da BPIX: {e}", exc_info=True)
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

@payments_api_bp.route('/financial/delete/<string:txid>', methods=['POST'])
@login_required
@admin_required
def delete_payment_route(txid):
    """Endpoint para apagar um registo de pagamento."""
    try:
        if data_manager.delete_pix_payment(txid):
            return jsonify({"success": True, "message": _("Transação apagada com sucesso.")})
        else:
            return jsonify({"success": False, "message": _("Transação não encontrada.")}), 404
    except Exception as e:
        logger.error(f"Erro ao apagar a transação {txid}: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Falha ao apagar a transação.")}), 500
