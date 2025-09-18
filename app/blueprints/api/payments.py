# app/blueprints/api/payments.py

import logging
import csv
from io import StringIO
from datetime import datetime, date, timezone
from flask import Blueprint, jsonify, request, url_for, current_app, Response
from flask_login import current_user
from flask_babel import gettext as _
from flask_login import login_required
from functools import wraps
from threading import Lock

from ...extensions import plex_manager, tautulli_manager, data_manager, efi_manager, mercado_pago_manager, bpix_manager
from ...config import load_or_create_config
from ..auth import admin_required
from ...models import UserProfile

logger = logging.getLogger(__name__)
payments_api_bp = Blueprint('payments_api', __name__)

payment_processing_lock = Lock()

def efi_webhook_security(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        EFI_IP = '34.193.116.226'
        remote_ip = request.headers.getlist("X-Forwarded-For")[0].rpartition(' ')[-1] if 'X-Forwarded-For' in request.headers else request.remote_addr or 'UNKNOWN'
        if remote_ip != EFI_IP:
            logger.warning(f"Webhook da Efí bloqueado: IP de origem '{remote_ip}' não corresponde ao IP esperado '{EFI_IP}'.")
            return jsonify(status="error", message="IP not allowed"), 403
        config = load_or_create_config()
        if not config.get("EFI_USE_MTLS", True):
            hmac_secret = config.get("EFI_WEBHOOK_HMAC_SECRET")
            received_hmac = request.args.get('hmac')
            if not hmac_secret or not received_hmac or hmac_secret != received_hmac:
                logger.warning("Webhook da Efí bloqueado: HMAC inválido ou ausente.")
                return jsonify(status="error", message="Invalid HMAC"), 403
        return f(*args, **kwargs)
    return decorated_function

def _process_successful_payment(txid):
    with payment_processing_lock:
        with current_app.app_context():
            try:
                payment = data_manager.get_and_lock_pix_payment(txid)
                if not payment:
                    logger.warning(f"Pagamento com TXID {txid} não encontrado. Ignorando.")
                    data_manager.db.session.rollback()
                    return
                if payment.get('status') == 'CONCLUIDA':
                    logger.warning(f"Pagamento {txid} já está 'CONCLUIDA'. Ignorando processamento duplicado.")
                    data_manager.db.session.commit()
                    return
                data_manager.update_pix_payment_status(txid, 'CONCLUIDA')
                plex_user_id = payment['user_plex_id']
                user = plex_manager.get_user_by_id(plex_user_id)
                if user:
                    config = load_or_create_config()
                    screens_to_set = payment.get('screens')

                    # CORREÇÃO: Verifica se um horário universal de vencimento está ativo
                    # e passa-o para a função de renovação para manter a consistência.
                    expiration_time = None
                    if config.get("UNIVERSAL_EXPIRATION_ENABLED"):
                        expiration_time = config.get("UNIVERSAL_EXPIRATION_TIME", "23:59")
                    
                    new_expiration_date = plex_manager.renew_subscription(
                        plex_user_id, 1, 'expiry_date', expiration_time_str=expiration_time
                    )
                    
                    if screens_to_set is not None and screens_to_set >= 0:
                        profile = data_manager.get_user_profile(plex_user_id)
                        profile['screen_limit'] = screens_to_set
                        data_manager.set_user_profile(plex_user_id, profile)

                    profile = data_manager.get_user_profile(plex_user_id)
                    plex_manager.notifier_manager.send_renewal_notification(user, new_expiration_date, profile)
                    data_manager.create_notification(
                        message=f"Pagamento de {user['username']} (R$ {payment['value']:.2f}) confirmado.", 
                        category='success', link=url_for('main.users_page')
                    )
                    if payment.get('coupon_code'):
                        data_manager.record_coupon_usage(payment['coupon_code'], plex_user_id)
                else:
                    logger.warning(f"Utilizador do pagamento {txid} (ID: {plex_user_id}) não encontrado no Plex. A renovação falhou.")
                data_manager.db.session.commit()
            except Exception as e:
                logger.error(f"Erro crítico ao processar o pagamento para o TXID {txid}: {e}", exc_info=True)
                data_manager.db.session.rollback()

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
    profile = data_manager.get_user_profile_by_username(username)
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
    
    user_profile = data_manager.get_user_profile_by_username(username)
    if not user_profile:
        return jsonify({"success": False, "message": "Utilizador não encontrado."}), 404
        
    plex_user_id = user_profile.get('plex_user_id')

    if not code or screens_str is None:
        return jsonify({"success": False, "message": "Código do cupão e plano são necessários."}), 400

    if data_manager.has_user_used_coupon(plex_user_id, code):
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

        discounted_price = max(0, discounted_price)

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
    token = data.get('token')

    plex_user_id = None
    username = None

    # --- CORREÇÃO INICIA AQUI ---
    # Identifica o utilizador a partir da sessão (se logado) ou do token (página pública)
    if current_user.is_authenticated:
        plex_user_id = int(current_user.id)
        username = current_user.username
    elif token:
        profile = UserProfile.query.filter_by(payment_token=token).first()
        if profile:
            plex_user_id = profile.plex_user_id
            username = profile.username
    
    if not plex_user_id or not username:
        return jsonify({"success": False, "message": _("Usuário não especificado para a cobrança.")}), 400
    # --- CORREÇÃO TERMINA AQUI ---

    config = load_or_create_config()
    profile = data_manager.get_user_profile(plex_user_id)
    
    plex_user = plex_manager.get_user_by_id(plex_user_id)
    if not plex_user:
        return jsonify({"success": False, "message": _("Usuário não encontrado no Plex.")}), 404

    price_str = config.get("SCREEN_PRICES", {}).get(str(screens_str)) or config.get("RENEWAL_PRICE")
    if not price_str or float(price_str) <= 0:
        return jsonify({"success": False, "message": _("Opção de plano inválida ou sem preço definido.")}), 400
        
    final_price = float(price_str)
    if coupon_code:
        if data_manager.has_user_used_coupon(plex_user_id, coupon_code):
             return jsonify({"success": False, "message": "Você já usou esse cupom."}), 403
        coupon = data_manager.get_coupon_by_code(coupon_code)
        if coupon and coupon['is_active'] and coupon['use_count'] < coupon['max_uses'] and (not coupon['expires_at'] or datetime.utcnow() < coupon['expires_at']):
            final_price = final_price * (1 - coupon['value'] / 100) if coupon['discount_type'] == 'percentage' else max(0, final_price - coupon['value'])
        else:
            return jsonify({"success": False, "message": "O cupão fornecido já não é válido."}), 400

    if coupon_code and final_price <= 0:
        try:
            plex_manager.renew_subscription(plex_user_id, 1, 'expiry_date')
            data_manager.record_coupon_usage(coupon_code, plex_user_id)
            data_manager.add_manual_payment(plex_user_id=plex_user_id, username=username, value=0.00, description=f"Renovação via Cupão 100% ({coupon_code})", payment_date_str=datetime.now().isoformat())
            return jsonify({"success": True, "free_renewal": True, "message": _("Assinatura gratuita ativada com sucesso!")})
        except Exception as e:
            return jsonify({"success": False, "message": "Ocorreu um erro ao ativar a sua assinatura gratuita."}), 500

    price, screens = final_price, int(screens_str)
    user_info = {"plex_user_id": plex_user_id, "username": username, "name": profile.get('name', username), "email": plex_user.get('email')}
    
    result = {"success": False, "message": _("O provedor %(provider)s não está habilitado.", provider=provider)}
    if provider == 'EFI' and config.get('EFI_ENABLED'):
        result = efi_manager.create_pix_charge(user_info, price, screens, coupon_code)
    elif provider == 'MERCADOPAGO' and config.get('MERCADOPAGO_ENABLED'):
        result = mercado_pago_manager.create_pix_payment(user_info, price, screens, coupon_code)
    elif provider == 'BPIX' and config.get('BPIX_ENABLED'):
        result = bpix_manager.create_pix_charge(user_info, price, screens, coupon_code)
        
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
    plex_user_id = data.get('plex_user_id')
    value = data.get('value')
    description = data.get('description')
    payment_date = data.get('payment_date')
    if not all([plex_user_id, value, description, payment_date]):
        return jsonify({"success": False, "message": _("Todos os campos são obrigatórios.")}), 400
    try:
        user = plex_manager.get_user_by_id(plex_user_id)
        if not user:
            return jsonify({"success": False, "message": "Utilizador não encontrado."}), 404
        payment_datetime_str = f"{payment_date}T{datetime.now().strftime('%H:%M:%S')}"
        payment = data_manager.add_manual_payment(plex_user_id, user['username'], value, description, payment_datetime_str)
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

@payments_api_bp.route('/financial/export-csv')
@login_required
@admin_required
def export_financial_csv():
    """Endpoint para exportar dados financeiros como um ficheiro CSV."""
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    try:
        if start_date_str:
            datetime.fromisoformat(start_date_str)
        if end_date_str:
            datetime.fromisoformat(end_date_str)
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Formato de data inválido. Use AAAA-MM-DD."}), 400

    if not start_date_str or not end_date_str:
        return jsonify({"success": False, "message": "Datas de início e fim são obrigatórias."}), 400

    try:
        start_date = f"{start_date_str}T00:00:00"
        end_date = f"{end_date_str}T23:59:59"

        payments = data_manager.get_payments_for_export(start_date, end_date)

        total_revenue = sum(payment['value'] for payment in payments)
        total_transactions = len(payments)

        si = StringIO()
        si.write('\ufeff')
        cw = csv.writer(si, delimiter=';')

        headers = ['Data', 'Utilizador', 'Descricao', 'Valor (R$)', 'Provedor', 'Cupao', 'TXID']
        cw.writerow(headers)

        for payment in payments:
            row = [
                datetime.fromisoformat(payment['created_at']).strftime('%Y-%m-%d %H:%M:%S'),
                payment['username'],
                payment['description'] or f"{payment.get('screens', 'N/A')} Telas",
                f"{payment['value']:.2f}".replace('.', ','),
                payment['provider'],
                payment.get('coupon_code', ''),
                payment['txid']
            ]
            cw.writerow(row)

        cw.writerow([])
        cw.writerow([])
        
        cw.writerow(['', '', _('RESUMO DO PERÍODO')])
        cw.writerow([])
        
        cw.writerow(['', '', _('Total Arrecadado'), f"{total_revenue:.2f}".replace('.', ',')])
        cw.writerow(['', '', _('Total de Transações'), total_transactions])

        output = si.getvalue()
        
        filename = f"relatorio_financeiro_{start_date_str}_a_{end_date_str}.csv"
        
        return Response(
            output,
            mimetype="text/csv",
            headers={
                "Content-disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8-sig"
            }
        )

    except Exception as e:
        logger.error(f"Erro ao gerar o relatório CSV: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Ocorreu um erro interno ao gerar o relatório."}), 500
