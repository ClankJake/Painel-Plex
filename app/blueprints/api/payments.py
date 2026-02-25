# app/blueprints/api/payments.py

import logging
import csv
from io import StringIO
import threading
import time
import json
from datetime import datetime, date, timezone
from functools import wraps
from sqlalchemy.exc import OperationalError

from flask import Blueprint, jsonify, request, url_for, current_app, Response
from flask_login import current_user, login_required
from flask_babel import gettext as _

from ... import extensions
from ...config import load_or_create_config
from ..auth import admin_required
from ...models import UserProfile, PixPayment
from ...extensions import limiter

logger = logging.getLogger(__name__)
payments_api_bp = Blueprint('payments_api', __name__)

# ==========================================
# DECORADORES E SEGURANÇA
# ==========================================

def efi_webhook_security(f):
    """
    Decorador de segurança para o webhook da Efí.
    Valida IP de origem e HMAC caso o mTLS não esteja em uso.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        config = load_or_create_config()
        if not config.get("EFI_USE_MTLS", True):
            EFI_IP = '34.193.116.226'
            remote_ip = request.headers.getlist("X-Forwarded-For")[0].rpartition(' ')[-1] if 'X-Forwarded-For' in request.headers else request.remote_addr or 'UNKNOWN'
            
            if remote_ip != EFI_IP:
                logger.warning(f"Webhook da Efí bloqueado: IP '{remote_ip}' não corresponde a '{EFI_IP}'.")
                return jsonify(status="error", message="IP not allowed"), 403
            
            hmac_secret = config.get("EFI_WEBHOOK_HMAC_SECRET")
            received_hmac = request.args.get('hmac')
            if not hmac_secret or not received_hmac or hmac_secret != received_hmac:
                logger.warning("Webhook da Efí bloqueado: HMAC inválido.")
                return jsonify(status="error", message="Invalid HMAC"), 403

        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# PROCESSAMENTO DE PAGAMENTOS EM BACKGROUND
# ==========================================

def _handle_reactivation_invite(profile, plex_user_id):
    """
    Tenta enviar um convite do Plex para um utilizador que está a ser reativado.
    Retorna True se o utilizador já constar na lista de amigos do Plex após o convite.
    """
    target_identifier = profile.get('email')
    
    if not target_identifier:
        logger.info(f"E-mail não encontrado no perfil local para a reativação de '{profile.get('username')}'. A procurar no Plex...")
        try:
            plex_user = extensions.plex_manager.get_user_by_id(plex_user_id)
            if plex_user and plex_user.get('email'):
                target_identifier = plex_user.get('email')
                profile['email'] = target_identifier
                extensions.data_manager.set_user_profile(plex_user_id, profile)
                logger.info(f"E-mail recuperado do Plex e salvo com sucesso: {target_identifier}")
        except Exception as e:
            logger.warning(f"Erro ao tentar recuperar e-mail do Plex: {e}")

    if not target_identifier:
        logger.warning(f"E-mail não encontrado para '{profile.get('username')}'. A usar o Nome de Utilizador como fallback.")
        target_identifier = profile.get('username')

    user_found_in_plex = False
    if target_identifier:
        try:
            libraries = json.loads(profile.get('libraries', '[]'))
            invite_result = extensions.plex_manager.invites.send_plex_invite(target_identifier, libraries, plex_user_id=plex_user_id)
            
            if not invite_result.get('success'):
                logger.error(f"Falha ao reconvidar '{target_identifier}': {invite_result.get('message')}")
            else:
                logger.info(f"Convite de reativação enviado com sucesso para: {target_identifier}")
        except Exception as invite_error:
            logger.error(f"Erro crítico ao convidar '{target_identifier}': {invite_error}")

        logger.info(f"A aguardar que a API do Plex processe o convite de '{profile.get('username')}'...")
        time.sleep(2)
        extensions.plex_manager.users.invalidate_user_cache()
        
        updated_plex_user = extensions.plex_manager.get_user_by_id(plex_user_id)
        if updated_plex_user:
            profile['status'] = 'active'
            extensions.data_manager.set_user_profile(plex_user_id, profile)
            user_found_in_plex = True
            logger.info(f"Utilizador '{profile.get('username')}' confirmado como ativo no Plex. Status local atualizado para 'active'.")
        else:
            logger.info(f"Utilizador '{profile.get('username')}' ainda não está na lista de amigos (convite pendente). Status local mantido como 'inactive'.")
    else:
        logger.error(f"FALHA CRÍTICA: Nenhum identificador válido (email/username) para reativar o utilizador {plex_user_id}.")

    return user_found_in_plex

def _run_payment_processing_in_thread(app, txid):
    """Executado numa thread separada para validar pagamentos atómicos e renovar contas."""
    MAX_RETRIES = 5
    RETRY_DELAY = 2 
    
    logger.info(f"A iniciar a lógica de processamento em background para o pagamento TXID: {txid}")

    for attempt in range(MAX_RETRIES):
        try:
            with app.test_request_context():
                # 1. Obter e Trancar o Pagamento
                with extensions.db.session.begin_nested():
                    payment = extensions.data_manager.get_and_lock_pix_payment(txid)
                    if not payment:
                        logger.warning(f"Pagamento {txid} não encontrado na tentativa {attempt + 1}. A ignorar.")
                        return
                    
                    current_status = payment.get('status')
                    if current_status == 'CONCLUIDA':
                        logger.info(f"Pagamento {txid} já se encontra concluído. A evitar processamento duplicado.")
                        return
                    elif current_status == 'ATIVA':
                        extensions.data_manager.update_pix_payment_status(txid, 'PROCESSANDO')
                    elif current_status != 'PROCESSANDO':
                        return
                extensions.db.session.commit()
                
                # 2. Processar Lógica de Utilizador
                plex_user_id = payment['user_plex_id']
                profile = extensions.data_manager.get_user_profile(plex_user_id)
                is_reactivation = profile.get('status') == 'inactive'
                user_found_in_plex = False

                if is_reactivation:
                    logger.info(f"A processar a reativação paga para o utilizador '{profile['username']}' (ID: {plex_user_id}).")
                    extensions.data_manager.create_notification(
                        message=_("O utilizador %(username)s reativou a conta. Pagamento de %(value)s confirmado.", username=profile['username'], value=f"R$ {payment['value']:.2f}"),
                        category='success', link=url_for('main.users_page')
                    )
                    extensions.data_manager.create_notification(
                        message=_("A sua conta foi reativada com sucesso! Pagamento de %(value)s confirmado.", value=f"R$ {payment['value']:.2f}"),
                        category='success', link=url_for('main.account_page'), user_plex_id=plex_user_id
                    )
                    if extensions.socketio:
                        extensions.socketio.emit('new_notification', namespace='/')
                        
                    user_found_in_plex = _handle_reactivation_invite(profile, plex_user_id)

                # Obter dados para renovação
                user_info_for_renewal = extensions.plex_manager.get_user_by_id(plex_user_id)
                if not user_info_for_renewal and is_reactivation:
                    logger.info(f"A usar dados do perfil local de '{profile['username']}' para a renovação (não encontrado no Plex).")
                    user_info_for_renewal = { 'id': plex_user_id, 'username': profile.get('username'), 'email': profile.get('email') }

                if user_info_for_renewal:
                    config = load_or_create_config()
                    expiration_time = config.get("UNIVERSAL_EXPIRATION_TIME", "23:59") if config.get("UNIVERSAL_EXPIRATION_ENABLED") else None
                    renewal_base_mode = 'today' if is_reactivation else 'expiry_date'
                    
                    new_expiration_date = extensions.plex_manager.renew_subscription(
                        plex_user_id, 1, screens=payment.get('screens'), base_mode=renewal_base_mode,
                        expiration_time_str=expiration_time, is_reactivation=is_reactivation
                    )
                    
                    if is_reactivation and not user_found_in_plex:
                        post_renewal_profile = extensions.data_manager.get_user_profile(plex_user_id)
                        if post_renewal_profile.get('status') == 'active':
                            logger.info(f"A reverter o status de '{profile['username']}' para 'inactive' localmente (aguarda aceite do convite).")
                            post_renewal_profile['status'] = 'inactive'
                            extensions.data_manager.set_user_profile(plex_user_id, post_renewal_profile)

                    try:
                        refreshed_profile = extensions.data_manager.get_user_profile(plex_user_id)
                        extensions.plex_manager.notifier_manager.send_renewal_notification(user_info_for_renewal, new_expiration_date, refreshed_profile)
                        logger.info(f"Notificação de renovação enviada para o utilizador '{profile['username']}'.")
                    except Exception as e:
                        logger.error(f"Erro ao enviar notificação final para '{profile['username']}': {e}")

                    # Aplicar Cupões e Log local
                    if payment.get('coupon_code'):
                        logger.info(f"A registar a utilização do cupão '{payment['coupon_code']}' para '{profile['username']}'.")
                        extensions.data_manager.record_coupon_usage(payment['coupon_code'], plex_user_id)
                        
                    if not is_reactivation:
                        extensions.data_manager.create_notification(
                            message=_("Pagamento de %(username)s (%(value)s) confirmado.", username=profile['username'], value=f"R$ {payment['value']:.2f}"), 
                            category='success', link=url_for('main.users_page')
                        )
                        extensions.data_manager.create_notification(
                            message=_("A sua renovação de %(value)s foi confirmada.", value=f"R$ {payment['value']:.2f}"), 
                            category='success', link=url_for('main.account_page'), user_plex_id=plex_user_id
                        )
                        if extensions.socketio:
                            extensions.socketio.emit('new_notification', namespace='/')

                # 3. Finalizar
                extensions.data_manager.update_pix_payment_status(txid, 'CONCLUIDA')
                extensions.db.session.commit()
                logger.info(f"Processamento do pagamento para TXID {txid} concluído com sucesso!")

                if extensions.socketio:
                    toast_msg = _("Reativação concluída.") if is_reactivation else _("Pagamento confirmado.")
                    extensions.socketio.emit('user_list_updated', { 'message': toast_msg }, namespace='/dashboard')
                return

        except OperationalError as e:
            if "database is locked" in str(e):
                with app.app_context(): extensions.db.session.rollback()
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Base de dados bloqueada ao processar {txid} (Tentativa {attempt + 1}/{MAX_RETRIES}). A tentar novamente...")
                    time.sleep(RETRY_DELAY)
                else:
                    logger.error(f"Erro BD Bloqueada persistente para {txid}.", exc_info=True)
            else:
                break
        except Exception as e:
            logger.error(f"Erro crítico no processamento do pagamento {txid}: {e}", exc_info=True)
            with app.app_context(): 
                extensions.data_manager.update_pix_payment_status(txid, 'FALHOU')
                extensions.db.session.commit()
            break

def _process_successful_payment(txid):
    """Inicia o processamento verificando atomicamente o estado."""
    app = current_app._get_current_object()
    try:
        affected_rows = extensions.db.session.query(PixPayment).filter(
            PixPayment.txid == txid, PixPayment.status == 'ATIVA'
        ).update({"status": "PROCESSANDO"}, synchronize_session=False)
        extensions.db.session.commit()
        
        if affected_rows == 0:
            logger.info(f"Pagamento {txid} ignorado (Já está a ser processado noutra thread ou já foi concluído).")
            return 
            
    except Exception as e:
        logger.error(f"Erro de lock atómico na base de dados para {txid}: {e}")
        extensions.db.session.rollback()
        return

    logger.info(f"Estado atómico adquirido para o pagamento {txid}. A iniciar a thread secundária de processamento.")
    thread = threading.Thread(target=_run_payment_processing_in_thread, args=(app, txid))
    thread.daemon = True
    thread.start()

# ==========================================
# ROTAS DE GERAÇÃO E ESTADO DE COBRANÇAS
# ==========================================

@payments_api_bp.route('/options')
@limiter.limit("20 per minute")
def get_payment_options():
    token = request.args.get('token')
    is_public_request = bool(token)
    user_profile = None

    if token:
        profile_from_token = UserProfile.query.filter_by(payment_token=token).first()
        if profile_from_token:
            user_profile = extensions.data_manager._row_to_dict(profile_from_token)
    elif current_user.is_authenticated:
        user_profile = extensions.data_manager.get_user_profile(int(current_user.id))
    
    if not user_profile:
        return jsonify({"success": False, "message": _("Utilizador não especificado ou token inválido.")}), 400

    config = load_or_create_config()
    available_prices = extensions.pricing_manager.get_available_plans(user_profile, is_public_request)
    
    if not available_prices:
        logger.warning(f"Nenhum plano de preços encontrado para o utilizador '{user_profile.get('username')}'.")
        return jsonify({"success": False, "message": _("Nenhum plano de renovação disponível.")}), 404
        
    enabled_providers = {
        "efi": config.get("EFI_ENABLED"), 
        "mercadopago": config.get("MERCADOPAGO_ENABLED"),
        "bpix": config.get("BPIX_ENABLED")
    }
    return jsonify({"success": True, "prices": available_prices, "providers": enabled_providers, "can_downgrade": True})

@payments_api_bp.route('/validate-coupon', methods=['POST'])
@limiter.limit("5 per minute")
def validate_coupon_route():
    data = request.json
    user_profile = extensions.data_manager.get_user_profile_by_username(data.get('username'))
    if not user_profile:
        return jsonify({"success": False, "message": "Utilizador não encontrado."}), 404

    if not data.get('code') or data.get('screens') is None:
        return jsonify({"success": False, "message": "Código e plano são obrigatórios."}), 400

    return jsonify(extensions.pricing_manager.calculate_price(
        screens=data.get('screens'), coupon_code=data.get('code'), plex_user_id=user_profile.get('plex_user_id')
    ))

@payments_api_bp.route('/create-charge', methods=['POST'])
@limiter.limit("3 per minute")
def create_charge_route():
    data = request.json
    provider = data.get('provider')
    screens_str = data.get('screens')
    coupon_code = data.get('coupon_code')
    token = data.get('token')

    plex_user_id, username = None, None

    if current_user.is_authenticated:
        plex_user_id, username = int(current_user.id), current_user.username
    elif token:
        if profile := UserProfile.query.filter_by(payment_token=token).first():
            plex_user_id, username = profile.plex_user_id, profile.username
    
    if not plex_user_id:
        return jsonify({"success": False, "message": _("Utilizador não especificado.")}), 400

    profile = extensions.data_manager.get_user_profile(plex_user_id)
    if not profile:
        return jsonify({"success": False, "message": _("Perfil não encontrado.")}), 404

    logger.info(f"A preparar a geração de cobrança PIX via '{provider}' para '{username}' (ID: {plex_user_id}). Plano: {screens_str} telas.")

    # Tratamento de Info Base
    user_email = profile.get('email')
    if not user_email:
        logger.info(f"E-mail não encontrado localmente para '{username}'. A tentar obter do Plex...")
        try:
            if plex_user := extensions.plex_manager.get_user_by_id(plex_user_id):
                user_email = plex_user.get('email')
                logger.info(f"E-mail recuperado do Plex para '{username}': {user_email}")
        except Exception as e:
            logger.warning(f"Aviso: Não foi possível aceder ao e-mail no Plex para '{username}': {e}")
            
    if not user_email:
        logger.warning(f"Aviso: A prosseguir com a geração da cobrança para '{username}' sem e-mail associado.")

    user_info = {
        "plex_user_id": plex_user_id, "username": username,
        "name": profile.get('name', username), "email": user_email
    }

    price_calculation = extensions.pricing_manager.calculate_price(screens_str, coupon_code, plex_user_id)
    if not price_calculation.get("success"):
        logger.warning(f"Falha ao calcular o preço para '{username}': {price_calculation.get('message')}")
        return jsonify(price_calculation), 400

    final_price = price_calculation.get("discounted_price")
    logger.info(f"Preço final calculado para '{username}': R$ {final_price:.2f} (Cupão aplicado: {price_calculation.get('coupon_applied')})")

    # Lógica de Cobrança Gratuita (100% Desconto)
    if final_price <= 0:
        logger.info(f"A processar renovação/reativação 100% gratuita para '{username}'.")
        try:
            if profile.get('status') == 'inactive':
                _handle_reactivation_invite(profile, plex_user_id)
                # Garante atualização manual local imediata em casos de cupons 100%
                profile = extensions.data_manager.get_user_profile(plex_user_id)
                profile['status'] = 'active'
                extensions.data_manager.set_user_profile(plex_user_id, profile)

            new_expiration_date = extensions.plex_manager.renew_subscription(plex_user_id, 1, base_mode='expiry_date')
            
            if coupon_code: extensions.data_manager.record_coupon_usage(coupon_code, plex_user_id)
            
            extensions.data_manager.add_manual_payment(
                plex_user_id=plex_user_id, username=username, value=0.00,
                description=f"Reativação/Renovação Cupão 100% ({coupon_code})",
                payment_date_str=datetime.now(timezone.utc).isoformat()
            )
            extensions.db.session.commit()
            
            extensions.plex_manager.notifier_manager.send_renewal_notification(user_info, new_expiration_date, profile)
            
            extensions.data_manager.create_notification(message=_("Renovação gratuita via cupão registada para %(username)s.", username=username), category='success', link=url_for('main.users_page'))
            if extensions.socketio:
                extensions.socketio.emit('new_notification', namespace='/')
            
            logger.info(f"Renovação gratuita para '{username}' concluída com sucesso.")
            return jsonify({"success": True, "free_renewal": True, "message": _("Assinatura ativada gratuitamente!")})
            
        except Exception as e:
            extensions.db.session.rollback()
            logger.error(f"Erro crítico durante a assinatura gratuita para '{username}': {e}", exc_info=True)
            return jsonify({"success": False, "message": _("Ocorreu um erro interno ao ativar.")}), 500

    # Lógica Regular
    config = load_or_create_config()
    result = {"success": False, "message": _("O provedor %(provider)s não está habilitado.", provider=provider)}
    
    if provider == 'EFI' and config.get('EFI_ENABLED'):
        result = extensions.efi_manager.create_pix_charge(user_info, final_price, int(screens_str), coupon_code)
    elif provider == 'MERCADOPAGO' and config.get('MERCADOPAGO_ENABLED'):
        result = extensions.mercado_pago_manager.create_pix_payment(user_info, final_price, int(screens_str), coupon_code)
    elif provider == 'BPIX' and config.get('BPIX_ENABLED'):
        result = extensions.bpix_manager.create_pix_charge(user_info, final_price, int(screens_str), coupon_code)
        
    if result.get("success"):
        logger.info(f"Cobrança gerada com sucesso via '{provider}' para '{username}'. TXID associado.")
    else:
        logger.error(f"Falha na API do provedor '{provider}' ao gerar cobrança para '{username}': {result.get('message')}")

    return jsonify(result)

@payments_api_bp.route('/status/<string:txid>')
@limiter.limit("60 per minute")
def get_payment_status_route(txid):
    extensions.db.session.expire_all()
    payment = extensions.data_manager.get_pix_payment(txid)
    
    if not payment: return jsonify({"success": False, "status": "NOT_FOUND"}), 404
    if payment.get('status') == 'CONCLUIDA':
        profile = extensions.data_manager.get_user_profile(payment['user_plex_id'])
        return jsonify({"success": True, "status": "CONCLUIDA", "user_status": profile.get('status', 'unknown') if profile else 'unknown'})
    if payment.get('status') == 'PROCESSANDO':
        return jsonify({"success": True, "status": "PROCESSANDO"})

    provider = payment.get('provider', 'EFI') 
    is_confirmed = False
    
    try:
        if provider == 'EFI':
            status_result = extensions.efi_manager.detail_pix_charge(txid)
            if status_result.get("success") and status_result.get("data", {}).get("status") == 'CONCLUIDA':
                is_confirmed = True
        elif provider == 'MERCADOPAGO':
            status_result = extensions.mercado_pago_manager.get_payment_details(txid)
            if status_result.get("success") and status_result.get("data", {}).get("status") == 'approved':
                is_confirmed = True
        elif provider == 'BPIX':
            status_result = extensions.bpix_manager.detail_pix_charge(txid)
            st = status_result.get("data", {})
            if status_result.get("success") and (st.get("status") == 'Pagamento realizado' or st.get("international_status") == "PAYMENT_RECEIVED"):
                is_confirmed = True
    except Exception as e:
        logger.warning(f"Erro ao consultar status em provider {provider} para TXID {txid}: {e}")

    if is_confirmed:
        logger.info(f"O polling detetou que o pagamento {txid} foi aprovado na API do provedor. A agendar processamento.")
        _process_successful_payment(txid)
        return jsonify({"success": True, "status": "PROCESSANDO"})
        
    return jsonify({"success": True, "status": payment.get('status')})

# ==========================================
# WEBHOOKS (ISENTOS DE LIMITES DE REDE)
# ==========================================

@payments_api_bp.route('/webhook/efi', methods=['POST'])
@efi_webhook_security
@limiter.exempt
def efi_webhook():
    notification_data = request.get_json(silent=True)
    if not notification_data: 
        logger.info("Webhook da Efí recebido (Corpo vazio / Validação).")
        return jsonify(status="validation_received"), 200

    logger.info(f"Notificação de Webhook Efí recebida: {notification_data}")
    try:
        if notification_data.get('evento') == 'teste_webhook': return jsonify(status="received"), 200

        for pix_notification in notification_data.get('pix', []):
            if txid := pix_notification.get('txid'):
                logger.info(f"A verificar o TXID {txid} recebido via Webhook Efí.")
                efi_status_result = extensions.efi_manager.detail_pix_charge(txid)
                if efi_status_result.get("success") and efi_status_result.get("data", {}).get("status") == 'CONCLUIDA':
                    _process_successful_payment(txid)
                else:
                    logger.warning(f"Webhook Efí para {txid} ignorado, o estado real na API não é 'CONCLUIDA'.")
    except Exception as e:
        logger.error(f"Erro webhook Efí: {e}", exc_info=True)
        return jsonify(status="error", message="Server Error"), 500
    return jsonify(status="received"), 200

@payments_api_bp.route('/webhook/mercadopago', methods=['POST'])
@limiter.exempt
def mercadopago_webhook():
    data = request.json
    logger.info(f"Notificação de Webhook Mercado Pago recebida. Payload: {data}")
    try:
        if data.get("type") == "payment":
            payment_id = str(data.get("data", {}).get("id"))
            mp_status_result = extensions.mercado_pago_manager.get_payment_details(payment_id)
            if mp_status_result.get("success") and mp_status_result.get("data", {}).get("status") == "approved":
                _process_successful_payment(payment_id)
            else:
                logger.warning(f"Webhook Mercado Pago para {payment_id} ignorado. Estado na API não é 'approved'.")
    except Exception as e:
        logger.error(f"Erro webhook Mercado Pago: {e}", exc_info=True)
        return jsonify(status="error", message="Server Error"), 500
    return jsonify(status="received"), 200

@payments_api_bp.route('/webhook/bpix', methods=['POST'])
@limiter.exempt
def bpix_webhook():
    data = request.json
    logger.info(f"Notificação de Webhook BPIX recebida. Payload: {data}")
    try:
        txid = data.get("transaction_pix_id")
        if txid and ((data.get("status") == "Pagamento realizado") or (data.get("international_status") == "PAYMENT_RECEIVED")):
            _process_successful_payment(txid)
        else:
            logger.warning(f"Webhook BPIX para {txid} ignorado. Estado não indica pagamento confirmado.")
    except Exception as e:
        logger.error(f"Erro webhook BPIX: {e}", exc_info=True)
        return jsonify(status="error", message="Server Error"), 500
    return jsonify(status="received"), 200

# ==========================================
# ROTAS FINANCEIRAS E RELATÓRIOS
# ==========================================

@payments_api_bp.route('/financial/summary')
@login_required
@admin_required
def get_financial_summary_route():
    try:
        year = request.args.get('year', datetime.now().year, type=int)
        month = request.args.get('month', datetime.now().month, type=int)
        renewal_days = request.args.get('renewal_days', 7, type=int)
    except:
        year, month, renewal_days = datetime.now().year, datetime.now().month, 7
    return jsonify({
        "success": True, 
        "summary": extensions.data_manager.get_financial_summary(year, month, renewal_days=renewal_days), 
        "query_date": {"year": year, "month": month}
    })

@payments_api_bp.route('/financial/add-manual', methods=['POST'])
@login_required
@admin_required
def add_manual_payment_route():
    data = request.json
    plex_user_id, value, desc, payment_date = data.get('plex_user_id'), data.get('value'), data.get('description'), data.get('payment_date')
    
    if not all([plex_user_id, value, desc, payment_date]):
        return jsonify({"success": False, "message": _("Todos os campos são obrigatórios.")}), 400
        
    try:
        user = extensions.plex_manager.get_user_by_id(plex_user_id)
        if not user: return jsonify({"success": False, "message": "Utilizador não encontrado."}), 404
        
        current_time_str = datetime.now(timezone.utc).strftime('%H:%M:%S')
        try:
            payment_datetime_str = datetime.fromisoformat(f"{payment_date}T{current_time_str}+00:00").isoformat()
        except:
            payment_datetime_str = datetime.now(timezone.utc).isoformat()

        payment = extensions.data_manager.add_manual_payment(plex_user_id, user['username'], value, desc, payment_datetime_str)
        extensions.db.session.commit()
        logger.info(f"Pagamento manual de R$ {value} adicionado com sucesso para '{user['username']}' pelo Admin.")
        return jsonify({"success": True, "message": _("Pagamento registado."), "payment": payment})
    except Exception as e:
        extensions.db.session.rollback()
        logger.error(f"Erro ao adicionar pagamento manual: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@payments_api_bp.route('/financial/delete/<string:txid>', methods=['POST'])
@login_required
@admin_required
def delete_payment_route(txid):
    try:
        if extensions.data_manager.delete_pix_payment(txid):
            logger.info(f"Transação {txid} apagada manualmente pelo Admin.")
            return jsonify({"success": True, "message": _("Transação apagada.")})
        return jsonify({"success": False, "message": _("Transação não encontrada.")}), 404
    except Exception as e:
        logger.error(f"Erro ao apagar a transação {txid}: {e}")
        return jsonify({"success": False, "message": _("Falha ao apagar.")}), 500

@payments_api_bp.route('/financial/export-csv')
@login_required
@admin_required
def export_financial_csv():
    start_str, end_str = request.args.get('start_date'), request.args.get('end_date')
    if not start_str or not end_str:
        return jsonify({"success": False, "message": "Datas são obrigatórias."}), 400

    try:
        start_date = f"{start_str}T00:00:00+00:00"
        end_date = f"{end_str}T23:59:59+00:00"
        payments = extensions.data_manager.get_payments_for_export(start_date, end_date)

        logger.info(f"Exportação CSV solicitada pelo Admin (Período: {start_str} até {end_str}). Total de registos: {len(payments)}")

        si = StringIO()
        si.write('\ufeff') 
        cw = csv.writer(si, delimiter=';')

        cw.writerow(['Data', 'Utilizador', 'Descricao', 'Valor (R$)', 'Provedor', 'Cupao', 'TXID'])

        for p in payments:
            cw.writerow([
                datetime.fromisoformat(p['created_at']).strftime('%Y-%m-%d %H:%M:%S'),
                p['username'], p['description'] or f"{p.get('screens', 'N/A')} Telas",
                f"{p['value']:.2f}".replace('.', ','), p['provider'], p.get('coupon_code', ''), p['txid']
            ])

        cw.writerow([])
        cw.writerow(['', '', _('RESUMO DO PERÍODO')])
        cw.writerow(['', '', _('Total Arrecadado'), f"{sum(p['value'] for p in payments):.2f}".replace('.', ',')])
        cw.writerow(['', '', _('Total de Transações'), len(payments)])

        return Response(
            si.getvalue(), mimetype="text/csv",
            headers={ "Content-disposition": f"attachment; filename=relatorio_{start_str}_a_{end_str}.csv", "Content-Type": "text/csv; charset=utf-8-sig" }
        )
    except Exception as e:
        logger.error(f"Erro ao gerar CSV: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Erro ao gerar."}), 500
