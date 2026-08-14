import logging
import csv
from io import StringIO
import threading
import time
import json
from datetime import datetime, date, timezone
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
# PROCESSAMENTO DE PAGAMENTOS EM BACKGROUND
# ==========================================

def _run_payment_processing_in_thread(app, txid):
    """Executado numa thread separada para validar pagamentos atómicos e renovar contas."""
    MAX_RETRIES = 5
    RETRY_DELAY = 2 
    
    for attempt in range(MAX_RETRIES):
        try:
            with app.test_request_context('/'):
                payment = extensions.data_manager.get_and_lock_pix_payment(txid)
                if not payment:
                    return
                
                current_status = payment.get('status')
                if current_status == 'CONCLUIDA':
                    extensions.db.session.commit()
                    return
                elif current_status == 'ATIVA':
                    extensions.data_manager.update_pix_payment_status(txid, 'PROCESSANDO')
                elif current_status != 'PROCESSANDO':
                    extensions.db.session.commit()
                    return
                
                extensions.db.session.commit()
                
                plex_user_id = payment['user_plex_id']
                profile = extensions.data_manager.get_user_profile(plex_user_id)
                is_reactivation = profile.get('status') == 'inactive'

                if is_reactivation:
                    # Garante que existe um email ou username preenchido para o envio do convite no SubscriptionManager
                    if not profile.get('email'):
                        plex_user = extensions.plex_manager.get_user_by_id(plex_user_id)
                        profile['email'] = plex_user.get('email') if (plex_user and plex_user.get('email')) else profile.get('username')
                        extensions.data_manager.set_user_profile(plex_user_id, profile)
                        
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

                user_info_for_renewal = extensions.plex_manager.get_user_by_id(plex_user_id)
                if not user_info_for_renewal and is_reactivation:
                    user_info_for_renewal = { 'id': plex_user_id, 'username': profile.get('username'), 'email': profile.get('email') }

                if user_info_for_renewal:
                    config = load_or_create_config()
                    expiration_time = config.get("UNIVERSAL_EXPIRATION_TIME", "23:59") if config.get("UNIVERSAL_EXPIRATION_ENABLED") else None
                    renewal_base_mode = 'today' if is_reactivation else 'expiry_date'
                    
                    # O SubscriptionManager agora trata do envio do convite e notificação com link de token
                    new_expiration_date = extensions.plex_manager.renew_subscription(
                        plex_user_id, 1, screens=payment.get('screens'), base_mode=renewal_base_mode,
                        expiration_time_str=expiration_time, is_reactivation=is_reactivation
                    )
                    
                    # Trata o Status Inativo caso o utilizador ainda não tenha aceite o convite no e-mail/notificação
                    if is_reactivation:
                        user_found_in_plex = extensions.plex_manager.get_user_by_id(plex_user_id) is not None
                        if not user_found_in_plex:
                            post_renewal_profile = extensions.data_manager.get_user_profile(plex_user_id)
                            if post_renewal_profile.get('status') == 'active':
                                post_renewal_profile['status'] = 'inactive'
                                extensions.data_manager.set_user_profile(plex_user_id, post_renewal_profile)
                                logger.info(f"Utilizador '{profile.get('username')}' ainda não está na lista de amigos (convite pendente). Status local mantido como 'inactive'.")

                    try:
                        refreshed_profile = extensions.data_manager.get_user_profile(plex_user_id)
                        extensions.plex_manager.notifier_manager.send_renewal_notification(user_info_for_renewal, new_expiration_date, refreshed_profile)
                    except Exception as e:
                        logger.error(f"Erro ao enviar notificação final para '{profile['username']}': {e}")

                    if payment.get('coupon_code'):
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

                extensions.data_manager.update_pix_payment_status(txid, 'CONCLUIDA')
                extensions.db.session.commit()
                logger.info(f"Processamento do pagamento para TXID {txid} concluído com sucesso.")

                if extensions.socketio:
                    toast_msg = _("Reativação concluída.") if is_reactivation else _("Pagamento confirmado.")
                    extensions.socketio.emit('user_list_updated', { 'message': toast_msg }, namespace='/dashboard')
                return

        except OperationalError as e:
            if "database is locked" in str(e):
                with app.app_context(): extensions.db.session.rollback()
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                else:
                    logger.error(f"Erro persistente de base de dados bloqueada para o TXID {txid}.", exc_info=True)
            else:
                break
        except Exception as e:
            logger.error(f"Erro no processamento do pagamento {txid}: {e}", exc_info=True)
            with app.app_context(): 
                extensions.data_manager.update_pix_payment_status(txid, 'FALHOU')
                extensions.db.session.commit()
            break
        finally:
            # seguranca em Threads
            with app.app_context():
                extensions.db.session.remove()

def _process_successful_payment(txid):
    """Inicia o processamento verificando atomicamente o estado."""
    app = current_app._get_current_object()
    try:
        affected_rows = extensions.db.session.query(PixPayment).filter(
            PixPayment.txid == txid, PixPayment.status == 'ATIVA'
        ).update({"status": "PROCESSANDO"}, synchronize_session=False)
        extensions.db.session.commit()
        
        if affected_rows == 0:
            return 
            
    except Exception as e:
        logger.error(f"Erro ao atualizar estado na base de dados para {txid}: {e}")
        extensions.db.session.rollback()
        return

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
    data = request.json or {}
    
    token = data.get('token') or request.args.get('token')
    if not token and request.referrer and '/pay/' in request.referrer:
        token = request.referrer.split('/pay/')[-1].split('?')[0].split('/')[0]
        
    plex_user_id = None
    
    if token:
        profile = UserProfile.query.filter_by(payment_token=token).first()
        if profile:
            plex_user_id = profile.plex_user_id
            
    if not plex_user_id and current_user.is_authenticated:
        plex_user_id = int(current_user.id)
        
    if not plex_user_id:
        return jsonify({"success": False, "message": _("Utilizador não autorizado ou token inválido.")}), 404

    if not data.get('code') or data.get('screens') is None:
        return jsonify({"success": False, "message": "Código e plano são obrigatórios."}), 400

    return jsonify(extensions.pricing_manager.calculate_price(
        screens=data.get('screens'), coupon_code=data.get('code'), plex_user_id=plex_user_id
    ))

@payments_api_bp.route('/create-charge', methods=['POST'])
@limiter.limit("3 per minute")
def create_charge_route():
    data = request.json or {}
    provider = data.get('provider')
    screens_str = data.get('screens')
    coupon_code = data.get('coupon_code')
    
    token = data.get('token') or request.args.get('token')
    if not token and request.referrer and '/pay/' in request.referrer:
        token = request.referrer.split('/pay/')[-1].split('?')[0].split('/')[0]

    try:
        screens = int(screens_str) if screens_str is not None else 0
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": _("Número de telas inválido.")}), 400

    plex_user_id, username = None, None

    if token:
        profile_model = UserProfile.query.filter_by(payment_token=token).first()
        if profile_model:
            plex_user_id, username = profile_model.plex_user_id, profile_model.username
            
    if not plex_user_id and current_user.is_authenticated:
        plex_user_id, username = int(current_user.id), current_user.username
    
    if not plex_user_id:
        return jsonify({"success": False, "message": _("Utilizador não especificado ou token inválido.")}), 400

    profile = extensions.data_manager.get_user_profile(plex_user_id)
    if not profile:
        return jsonify({"success": False, "message": _("Perfil não encontrado.")}), 404

    user_email = profile.get('email')
    if not user_email:
        try:
            if plex_user := extensions.plex_manager.get_user_by_id(plex_user_id):
                user_email = plex_user.get('email')
        except:
            pass

    user_info = {
        "plex_user_id": plex_user_id, "username": username,
        "name": profile.get('name', username), "email": user_email
    }

    price_calculation = extensions.pricing_manager.calculate_price(screens_str, coupon_code, plex_user_id)
    if not price_calculation.get("success"):
        return jsonify(price_calculation), 400

    final_price = price_calculation.get("discounted_price")

    if final_price <= 0:
        try:
            is_reactivation = (profile.get('status') == 'inactive')

            # 1. Criação das notificações para o Sino e Histórico
            if is_reactivation:
                # Previne o envio sem e-mail ou identificador no SubscriptionManager
                if not profile.get('email'):
                    plex_user = extensions.plex_manager.get_user_by_id(plex_user_id)
                    profile['email'] = plex_user.get('email') if (plex_user and plex_user.get('email')) else profile.get('username')
                    extensions.data_manager.set_user_profile(plex_user_id, profile)
                    
                extensions.data_manager.create_notification(
                    message=_("O utilizador %(username)s reativou a conta com um cupão de 100%%.", username=username),
                    category='success', link=url_for('main.users_page')
                )
                extensions.data_manager.create_notification(
                    message=_("A sua conta foi reativada gratuitamente com sucesso!"),
                    category='success', link=url_for('main.account_page'), user_plex_id=plex_user_id
                )
            else:
                extensions.data_manager.create_notification(
                    message=_("Renovação de %(username)s (Cupão 100%%) confirmada.", username=username),
                    category='success', link=url_for('main.users_page')
                )
                extensions.data_manager.create_notification(
                    message=_("A sua renovação com cupão foi confirmada com sucesso."),
                    category='success', link=url_for('main.account_page'), user_plex_id=plex_user_id
                )

            # 2. Renovação exata baseada nas telas escolhidas (Trata também o Convite)
            renewal_base_mode = 'today' if is_reactivation else 'expiry_date'
            config = load_or_create_config()
            expiration_time = config.get("UNIVERSAL_EXPIRATION_TIME", "23:59") if config.get("UNIVERSAL_EXPIRATION_ENABLED") else None
            
            new_expiration_date = extensions.plex_manager.renew_subscription(
                plex_user_id, 1, screens=screens, base_mode=renewal_base_mode,
                expiration_time_str=expiration_time, is_reactivation=is_reactivation
            )
            
            # 3. Tratamento de Reativação Pendente (Retorna o status a 'inactive' se ainda não aceitou)
            if is_reactivation:
                user_found_in_plex = extensions.plex_manager.get_user_by_id(plex_user_id) is not None
                if not user_found_in_plex:
                    post_renewal_profile = extensions.data_manager.get_user_profile(plex_user_id)
                    if post_renewal_profile.get('status') == 'active':
                        post_renewal_profile['status'] = 'inactive'
                        extensions.data_manager.set_user_profile(plex_user_id, post_renewal_profile)
                        logger.info(f"Utilizador '{username}' ainda não está na lista de amigos (convite pendente). Status local mantido como 'inactive'.")

            if coupon_code: extensions.data_manager.record_coupon_usage(coupon_code, plex_user_id)
            
            extensions.data_manager.add_manual_payment(
                plex_user_id=plex_user_id, username=username, value=0.00,
                description=f"Renovação Cupão 100% ({coupon_code})",
                payment_date_str=datetime.now(timezone.utc).isoformat()
            )
            extensions.db.session.commit()
            
            # 4. Tocar o Sino em Tempo Real (WebSockets)
            if extensions.socketio:
                extensions.socketio.emit('new_notification', namespace='/')
                extensions.socketio.emit('user_list_updated', {'message': _("Renovação gratuita concluída.")}, namespace='/dashboard')

            refreshed_profile = extensions.data_manager.get_user_profile(plex_user_id)
            extensions.plex_manager.notifier_manager.send_renewal_notification(user_info, new_expiration_date, refreshed_profile)
            
            # 5. Retornar o estado do utilizador para o JS saber se mostra o botão do Plex
            return jsonify({
                "success": True, 
                "free_renewal": True, 
                "user_status": refreshed_profile.get('status', 'active'),
                "invite_link": refreshed_profile.get('pending_invite_link'),
                "message": _("Assinatura ativada gratuitamente!")
            })
            
        except Exception as e:
            logger.error(f"Erro na renovação gratuita: {e}", exc_info=True)
            extensions.db.session.rollback()
            return jsonify({"success": False, "message": _("Ocorreu um erro interno ao ativar.")}), 500

    config = load_or_create_config()
    
    provider_map = {
        'EFI': ('EFI_ENABLED', extensions.efi_manager.create_pix_charge if hasattr(extensions, 'efi_manager') else None),
        'MERCADOPAGO': ('MERCADOPAGO_ENABLED', extensions.mercado_pago_manager.create_pix_payment if hasattr(extensions, 'mercado_pago_manager') else None),
        'BPIX': ('BPIX_ENABLED', extensions.bpix_manager.create_pix_charge if hasattr(extensions, 'bpix_manager') else None)
    }

    config_key, charge_func = provider_map.get(provider, (None, None))

    if charge_func and config.get(config_key):
        result = charge_func(user_info, final_price, screens, coupon_code)
    else:
        result = {"success": False, "message": _("O provedor %(provider)s não está habilitado ou é inválido.", provider=provider)}

    return jsonify(result)

@payments_api_bp.route('/status/<string:txid>')
@limiter.limit("60 per minute")
def get_payment_status_route(txid):
    extensions.db.session.expire_all()
    payment = extensions.data_manager.get_pix_payment(txid)
    
    if not payment: return jsonify({"success": False, "status": "NOT_FOUND"}), 404
    if payment.get('status') == 'CONCLUIDA':
        profile = extensions.data_manager.get_user_profile(payment['user_plex_id'])
        return jsonify({
            "success": True,
            "status": "CONCLUIDA",
            "user_status": profile.get('status', 'unknown') if profile else 'unknown',
            "invite_link": profile.get('pending_invite_link') if profile else None
        })
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
            if status_result.get("success") and status_result.get("data", {}).get("status") == "approved":
                is_confirmed = True
        elif provider == 'BPIX':
            status_result = extensions.bpix_manager.detail_pix_charge(txid)
            st = status_result.get("data", {})
            if status_result.get("success") and (st.get("status") == 'Pagamento realizado' or st.get("international_status") == "PAYMENT_RECEIVED"):
                is_confirmed = True
    except Exception as e:
        pass

    if is_confirmed:
        _process_successful_payment(txid)
        return jsonify({"success": True, "status": "PROCESSANDO"})
        
    return jsonify({"success": True, "status": payment.get('status')})

# ==========================================
# WEBHOOKS (ISENTOS DE LIMITES DE REDE)
# ==========================================

@payments_api_bp.route('/webhook/efi', methods=['POST', 'PUT', 'GET', 'OPTIONS'], strict_slashes=False)
@limiter.exempt
def efi_webhook():
    if request.method == 'OPTIONS':
        return '', 200

    config = load_or_create_config()
    
    if not config.get("EFI_USE_MTLS", True):
        hmac_secret = config.get("EFI_WEBHOOK_HMAC_SECRET")
        received_hmac = request.args.get('hmac')
        
        if not hmac_secret or not received_hmac or hmac_secret != received_hmac:
            logger.warning("Webhook Efí bloqueado: HMAC inválido ou ausente.")
            return jsonify(status="error", message="Invalid HMAC"), 403

    try:
        raw_data = request.get_data(as_text=True)
        if not raw_data:
            return jsonify(status="received"), 200
            
        notification_data = json.loads(raw_data)
    except Exception as e:
        logger.error(f"Formato JSON inválido recebido no Webhook Efí: {e}")
        return jsonify(status="error", message="Invalid JSON format"), 400

    try:
        if notification_data.get('evento') == 'teste_webhook': 
            logger.info("Webhook Efí: Evento de validação concluído.")
            return jsonify(status="received"), 200

        pix_array = notification_data.get('pix', [])
        for pix_notification in pix_array:
            txid = pix_notification.get('txid')
            
            if txid:
                payment = extensions.data_manager.get_pix_payment(txid)
                if payment and payment.get('status') == 'CONCLUIDA':
                    continue

                efi_status_result = extensions.efi_manager.detail_pix_charge(txid)
                if efi_status_result.get("success") and efi_status_result.get("data", {}).get("status") == 'CONCLUIDA':
                    logger.info(f"Pagamento {txid} confirmado via Webhook Efí. A iniciar renovação.")
                    _process_successful_payment(txid)
                else:
                    logger.warning(f"Webhook recebido para {txid}, mas o estado na Efí não indica conclusão.")
                    
    except Exception as e:
        logger.error(f"Erro no processamento do Webhook Efí: {e}", exc_info=True)
        return jsonify(status="error", message="Server Error"), 500
        
    return jsonify(status="received"), 200

@payments_api_bp.route('/webhook/mercadopago', methods=['POST'])
@limiter.exempt
def mercadopago_webhook():
    data = request.json
    try:
        if data.get("type") == "payment":
            payment_id = str(data.get("data", {}).get("id"))
            mp_status_result = extensions.mercado_pago_manager.get_payment_details(payment_id)
            if mp_status_result.get("success") and mp_status_result.get("data", {}).get("status") == "approved":
                logger.info(f"Pagamento {payment_id} confirmado via Webhook Mercado Pago. A iniciar renovação.")
                _process_successful_payment(payment_id)
    except Exception as e:
        logger.error(f"Erro no Webhook Mercado Pago: {e}", exc_info=True)
        return jsonify(status="error", message="Server Error"), 500
    return jsonify(status="received"), 200

@payments_api_bp.route('/webhook/bpix', methods=['POST'])
@limiter.exempt
def bpix_webhook():
    data = request.json
    try:
        txid = data.get("transaction_pix_id")
        if txid and ((data.get("status") == "Pagamento realizado") or (data.get("international_status") == "PAYMENT_RECEIVED")):
            logger.info(f"Pagamento {txid} confirmado via Webhook BPIX. A iniciar renovação.")
            _process_successful_payment(txid)
    except Exception as e:
        logger.error(f"Erro no Webhook BPIX: {e}", exc_info=True)
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

        def generate():
            si = StringIO()
            si.write('\ufeff')
            cw = csv.writer(si, delimiter=';')

            cw.writerow(['Data', 'Utilizador', 'Descricao', 'Valor (R$)', 'Provedor', 'Cupao', 'TXID'])
            yield si.getvalue()
            si.seek(0)
            si.truncate(0)

            total_value = 0
            
            for p in payments:
                total_value += p['value']
                cw.writerow([
                    datetime.fromisoformat(p['created_at']).strftime('%Y-%m-%d %H:%M:%S'),
                    p['username'], p['description'] or f"{p.get('screens', 'N/A')} Telas",
                    f"{p['value']:.2f}".replace('.', ','), p['provider'], p.get('coupon_code', ''), p['txid']
                ])
                yield si.getvalue()
                si.seek(0)
                si.truncate(0)

            cw.writerow([])
            cw.writerow(['', '', _('RESUMO DO PERÍODO')])
            cw.writerow(['', '', _('Total Arrecadado'), f"{total_value:.2f}".replace('.', ',')])
            cw.writerow(['', '', _('Total de Transações'), len(payments)])
            yield si.getvalue()

        return Response(
            generate(), mimetype="text/csv",
            headers={ "Content-disposition": f"attachment; filename=relatorio_{start_str}_a_{end_str}.csv", "Content-Type": "text/csv; charset=utf-8-sig" }
        )
    except Exception as e:
        logger.error(f"Erro ao gerar CSV: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Erro ao gerar."}), 500
