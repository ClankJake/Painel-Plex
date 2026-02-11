# app/blueprints/api/payments.py

import logging
import csv
from io import StringIO
import threading
import time
import json
from datetime import datetime, date, timezone
from flask import Blueprint, jsonify, request, url_for, current_app, Response
from flask_login import current_user
from flask_babel import gettext as _
from flask_login import login_required
from functools import wraps
from sqlalchemy.exc import OperationalError

from ... import extensions
from ...config import load_or_create_config
from ..auth import admin_required
from ...models import UserProfile, PixPayment

# Importa o limiter
from ...extensions import limiter

logger = logging.getLogger(__name__)
payments_api_bp = Blueprint('payments_api', __name__)

def efi_webhook_security(f):
    """
    Decorador de segurança para o webhook da Efí.
    - Se mTLS estiver desativado, valida o IP de origem e o HMAC.
    - Se mTLS estiver ativo, a segurança é garantida na camada de transporte.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        config = load_or_create_config()
        
        # Se mTLS estiver desativado (skip-mtls), aplicamos a verificação de IP e HMAC.
        if not config.get("EFI_USE_MTLS", True):
            # 1. Verificação do IP de Origem
            EFI_IP = '34.193.116.226'
            # Obtém o IP real do cliente, mesmo atrás de um proxy reverso
            remote_ip = request.headers.getlist("X-Forwarded-For")[0].rpartition(' ')[-1] if 'X-Forwarded-For' in request.headers else request.remote_addr or 'UNKNOWN'
            
            if remote_ip != EFI_IP:
                logger.warning(f"Webhook da Efí bloqueado: IP de origem '{remote_ip}' não corresponde ao IP esperado '{EFI_IP}'.")
                return jsonify(status="error", message="IP not allowed"), 403
            
            # 2. Verificação do HMAC
            hmac_secret = config.get("EFI_WEBHOOK_HMAC_SECRET")
            received_hmac = request.args.get('hmac')
            if not hmac_secret or not received_hmac or hmac_secret != received_hmac:
                logger.warning("Webhook da Efí bloqueado: HMAC inválido ou ausente.")
                return jsonify(status="error", message="Invalid HMAC"), 403

        return f(*args, **kwargs)
    return decorated_function

def _run_payment_processing_in_thread(app, txid):
    """
    Função executada numa thread separada para processar o pagamento confirmado.
    Usa o seu próprio contexto de aplicação e uma lógica de retentativas para
    lidar com bloqueios da base de dados.
    """
    MAX_RETRIES = 5
    RETRY_DELAY = 2  # segundos
    
    for attempt in range(MAX_RETRIES):
        try:
            with app.test_request_context():
                with extensions.db.session.begin_nested():
                    payment = extensions.data_manager.get_and_lock_pix_payment(txid)
                    if not payment:
                        logger.warning(f"Pagamento com TXID {txid} não encontrado na tentativa {attempt + 1}. A ignorar.")
                        return
                    
                    # MELHORIA: Aceita 'PROCESSANDO' também, pois o main thread agora faz a atualização atômica.
                    # Se for 'ATIVA', atualizamos aqui por segurança (caso venha de outro fluxo).
                    # Se for 'CONCLUIDA', ignoramos.
                    current_status = payment.get('status')

                    if current_status == 'CONCLUIDA':
                        logger.warning(f"Pagamento {txid} já foi concluído. A ignorar processamento duplicado.")
                        return
                    
                    if current_status == 'ATIVA':
                        extensions.data_manager.update_pix_payment_status(txid, 'PROCESSANDO')
                        logger.info(f"Pagamento {txid} marcado como 'PROCESSANDO' na thread.")
                    elif current_status == 'PROCESSANDO':
                        # Se já está processando, assumimos que esta thread ganhou o direito de processar
                        # através do check atômico no _process_successful_payment.
                        # Continuamos normalmente.
                        pass
                    else:
                        logger.warning(f"Pagamento {txid} com estado inesperado '{current_status}'. A ignorar.")
                        return

                extensions.db.session.commit()
                
                # Início da Lógica de Renovação
                logger.info(f"A iniciar a lógica de renovação para o pagamento {txid}.")

                plex_user_id = payment['user_plex_id']
                profile = extensions.data_manager.get_user_profile(plex_user_id)
                
                is_reactivation = profile.get('status') == 'inactive'
                user_found_in_plex = False

                if is_reactivation:
                    logger.info(f"Processando reativação para o utilizador '{profile['username']}' (ID: {plex_user_id}).")
                    
                    extensions.data_manager.create_notification(
                        message=_("O utilizador %(username)s reativou a conta. Pagamento de %(value)s confirmado.", 
                                  username=profile['username'], 
                                  value=f"R$ {payment['value']:.2f}"),
                        category='success', link=url_for('main.users_page')
                    )
                    extensions.data_manager.create_notification(
                        message=_("A sua conta foi reativada com sucesso! Pagamento de %(value)s confirmado.", 
                                  value=f"R$ {payment['value']:.2f}"),
                        category='success', link=url_for('main.account_page'), user_plex_id=plex_user_id
                    )

                    # --- LÓGICA DE ENVIO DE CONVITE (CORRIGIDA) ---
                    target_identifier = profile.get('email')
                    
                    if not target_identifier:
                        # Tenta recuperar o email do Plex novamente
                        logger.info(f"Email não encontrado no perfil local para reativação de '{profile['username']}'. Tentando buscar no Plex...")
                        try:
                            plex_user = extensions.plex_manager.get_user_by_id(plex_user_id)
                            if plex_user and plex_user.get('email'):
                                target_identifier = plex_user.get('email')
                                profile['email'] = target_identifier
                                extensions.data_manager.set_user_profile(plex_user_id, profile)
                                logger.info(f"Email recuperado do Plex e salvo: {target_identifier}")
                        except Exception as e:
                            logger.warning(f"Erro ao tentar recuperar email do Plex: {e}")

                    # Se ainda não temos email, usamos o USERNAME (ID numérico causa erro 500 no Plex)
                    if not target_identifier:
                        logger.warning(f"Email não encontrado para '{profile['username']}'. Usando NOME DE UTILIZADOR como fallback.")
                        target_identifier = profile.get('username')

                    if target_identifier:
                        try:
                            invite_result = extensions.plex_manager.invites.send_plex_invite(target_identifier, json.loads(profile.get('libraries', '[]')))
                            if not invite_result.get('success'):
                                logger.error(f"Falha ao reconvidar '{profile['username']}' ({target_identifier}): {invite_result.get('message')}")
                            else:
                                logger.info(f"Convite de reativação enviado com sucesso para: {target_identifier}")
                        except Exception as invite_error:
                            # Captura erro crítico no envio do convite para NÃO falhar o pagamento
                            logger.error(f"EXCEÇÃO ao enviar convite para '{target_identifier}': {invite_error}")
                    else:
                        logger.error(f"FALHA CRÍTICA: Não foi possível encontrar identificador válido (email/username) para reativar (ID: {plex_user_id}).")
                    # -----------------------------------------------

                    logger.info(f"Aguardando 8 segundos para a API do Plex processar a reativação de '{profile['username']}'...")
                    time.sleep(8)
                    extensions.plex_manager.users.invalidate_user_cache()
                    
                    # Verifica se o utilizador já aparece no Plex
                    try:
                        updated_plex_user = extensions.plex_manager.get_user_by_id(plex_user_id)
                        if updated_plex_user:
                             profile['status'] = 'active'
                             extensions.data_manager.set_user_profile(plex_user_id, profile)
                             user_found_in_plex = True
                             logger.info(f"Utilizador '{profile['username']}' confirmado como ativo no Plex. Status local atualizado para 'active'.")
                        else:
                             user_found_in_plex = False
                             logger.info(f"Utilizador '{profile['username']}' ainda não encontrado na lista de amigos (convite pendente). Mantendo status local como 'inactive'.")
                    except Exception as check_err:
                        logger.warning(f"Erro ao verificar status do utilizador no Plex após reativação: {check_err}")


                user_info_for_renewal = extensions.plex_manager.get_user_by_id(plex_user_id)
                if not user_info_for_renewal and is_reactivation:
                    logger.info(f"Utilizador '{profile['username']}' está a ser reativado. A usar dados do perfil local para a renovação.")
                    user_info_for_renewal = { 'id': plex_user_id, 'username': profile.get('username'), 'email': profile.get('email') }

                if user_info_for_renewal:
                    config = load_or_create_config()
                    screens_to_set = payment.get('screens')
                    expiration_time = config.get("UNIVERSAL_EXPIRATION_TIME", "23:59") if config.get("UNIVERSAL_EXPIRATION_ENABLED") else None
                    renewal_base_mode = 'today' if is_reactivation else 'expiry_date'
                    
                    new_expiration_date = extensions.plex_manager.renew_subscription(
                        plex_user_id, 1, screens=screens_to_set, base_mode=renewal_base_mode,
                        expiration_time_str=expiration_time, is_reactivation=is_reactivation
                    )
                    
                    # CORREÇÃO CRÍTICA: Mantém status inactive se convite estiver pendente
                    if is_reactivation and not user_found_in_plex:
                        post_renewal_profile = extensions.data_manager.get_user_profile(plex_user_id)
                        if post_renewal_profile.get('status') == 'active':
                            logger.info(f"CORREÇÃO: Revertendo status de '{profile['username']}' para 'inactive' (convite pendente).")
                            post_renewal_profile['status'] = 'inactive'
                            extensions.data_manager.set_user_profile(plex_user_id, post_renewal_profile)

                    refreshed_profile = extensions.data_manager.get_user_profile(plex_user_id)
                    extensions.plex_manager.notifier_manager.send_renewal_notification(user_info_for_renewal, new_expiration_date, refreshed_profile)
                    
                    if not is_reactivation:
                        extensions.data_manager.create_notification(
                            message=_("Pagamento de %(username)s (%(value)s) confirmado.", username=profile['username'], value=f"R$ {payment['value']:.2f}"), 
                            category='success', link=url_for('main.users_page')
                        )
                        extensions.data_manager.create_notification(
                            message=_("A sua renovação de %(value)s foi confirmada.", value=f"R$ {payment['value']:.2f}"),
                            category='success', link=url_for('main.account_page'), user_plex_id=plex_user_id
                        )

                    if payment.get('coupon_code'):
                        extensions.data_manager.record_coupon_usage(payment['coupon_code'], plex_user_id)
                else:
                    logger.warning(f"Utilizador do pagamento {txid} (ID: {plex_user_id}) não encontrado. A renovação falhou.")

                extensions.data_manager.update_pix_payment_status(txid, 'CONCLUIDA')
                extensions.db.session.commit()
                logger.info(f"Processamento do pagamento para TXID {txid} concluído com sucesso.")

                if extensions.socketio:
                    toast_message = _("O utilizador %(username)s foi reativado após pagamento.", username=profile['username']) if is_reactivation else _("Pagamento de %(username)s confirmado. A lista será atualizada.", username=profile['username'])
                    extensions.socketio.emit('user_list_updated', { 'message': toast_message }, namespace='/dashboard')
                return

        except OperationalError as e:
            if "database is locked" in str(e):
                with app.app_context(): extensions.db.session.rollback()
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"Base de dados bloqueada na tentativa {attempt + 1}/{MAX_RETRIES} para o TXID {txid}. A tentar novamente em {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                else:
                    logger.error(f"Erro crítico: A base de dados permaneceu bloqueada após {MAX_RETRIES} tentativas para o TXID {txid}. A tarefa falhou.", exc_info=True)
            else:
                logger.error(f"Erro operacional inesperado ao processar o pagamento para o TXID {txid}: {e}", exc_info=True)
                break
        except Exception as e:
            logger.error(f"Erro crítico ao processar o pagamento para o TXID {txid} na thread: {e}", exc_info=True)
            with app.app_context(): 
                extensions.data_manager.update_pix_payment_status(txid, 'FALHOU')
                extensions.db.session.commit()
            break

def _process_successful_payment(txid):
    """
    Inicia o processamento do pagamento em background, com verificação atômica
    para evitar múltiplas threads.
    """
    app = current_app._get_current_object()
    
    # --- MELHORIA: ATUALIZAÇÃO ATÔMICA ---
    try:
        # Tenta mudar de 'ATIVA' para 'PROCESSANDO' atomicamente.
        # Se retornar > 0, esta thread ganhou a corrida.
        affected_rows = extensions.db.session.query(PixPayment).filter(
            PixPayment.txid == txid,
            PixPayment.status == 'ATIVA'
        ).update({"status": "PROCESSANDO"}, synchronize_session=False)
        
        extensions.db.session.commit()
        
        if affected_rows == 0:
            # Verifica o status atual para logar corretamente
            current = extensions.db.session.query(PixPayment.status).filter_by(txid=txid).first()
            current_status = current.status if current else 'UNKNOWN'
            
            # Se não afetou linhas, ou não existe ou já não está ATIVA (já processando/concluído)
            if current_status in ['PROCESSANDO', 'CONCLUIDA']:
                logger.info(f"Pagamento {txid} ignorado (Status atual: {current_status}). Já está a ser processado ou foi concluído.")
            else:
                logger.warning(f"Tentativa de processar pagamento {txid} falhou na verificação atômica. Status atual: {current_status}")
            return

    except Exception as e:
        logger.error(f"Erro ao tentar realizar lock atômico no pagamento {txid}: {e}")
        extensions.db.session.rollback()
        # Em caso de erro no banco, abortamos para não causar inconsistência
        return

    # Se chegamos aqui, o status agora é 'PROCESSANDO' e podemos iniciar a thread
    thread = threading.Thread(target=_run_payment_processing_in_thread, args=(app, txid))
    thread.daemon = True
    thread.start()
    logger.info(f"Thread iniciada para processar o pagamento do TXID {txid} (Lock adquirido).")


@payments_api_bp.route('/options')
@limiter.limit("20 per minute") # Limita consultas de opções de pagamento
def get_payment_options():
    token = request.args.get('token')
    username = None
    is_public_request = bool(token)
    user_profile = None

    if token:
        profile_from_token = UserProfile.query.filter_by(payment_token=token).first()
        if profile_from_token:
            username = profile_from_token.username
            user_profile = extensions.data_manager._row_to_dict(profile_from_token)
    elif current_user.is_authenticated:
        username = current_user.username
        user_profile = extensions.data_manager.get_user_profile(int(current_user.id))
    
    if not user_profile:
        return jsonify({"success": False, "message": _("Usuário não especificado ou token inválido.")}), 400

    config = load_or_create_config()
    
    available_prices = extensions.pricing_manager.get_available_plans(user_profile, is_public_request)
    
    if not available_prices:
        return jsonify({"success": False, "message": _("Nenhum plano de renovação disponível ou definido pelo administrador.")}), 404
        
    enabled_providers = {
        "efi": config.get("EFI_ENABLED"), 
        "mercadopago": config.get("MERCADOPAGO_ENABLED"),
        "bpix": config.get("BPIX_ENABLED")
    }
    return jsonify({"success": True, "prices": available_prices, "providers": enabled_providers, "can_downgrade": True})

@payments_api_bp.route('/validate-coupon', methods=['POST'])
@limiter.limit("5 per minute") # Protege contra força bruta em códigos de cupom
def validate_coupon_route():
    data = request.json
    code = data.get('code')
    screens_str = data.get('screens')
    username = data.get('username')
    
    user_profile = extensions.data_manager.get_user_profile_by_username(username)
    if not user_profile:
        return jsonify({"success": False, "message": "Utilizador não encontrado."}), 404
        
    plex_user_id = user_profile.get('plex_user_id')

    if not code or screens_str is None:
        return jsonify({"success": False, "message": "Código do cupão e plano são necessários."}), 400

    result = extensions.pricing_manager.calculate_price(
        screens=screens_str,
        coupon_code=code,
        plex_user_id=plex_user_id
    )
    
    return jsonify(result)


@payments_api_bp.route('/create-charge', methods=['POST'])
@limiter.limit("3 per minute") # Limita a criação de cobranças PIX para evitar spam/custos
def create_charge_route():
    data = request.json
    provider = data.get('provider')
    screens_str = data.get('screens')
    coupon_code = data.get('coupon_code')
    token = data.get('token')

    plex_user_id = None
    username = None

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

    profile = extensions.data_manager.get_user_profile(plex_user_id)
    if not profile:
        return jsonify({"success": False, "message": _("Perfil do usuário não encontrado.")}), 404

    user_info = {}

    if profile.get('status') == 'inactive':
        logger.info(f"Gerando cobrança para o utilizador inativo '{username}' a partir dos dados locais.")
        
        user_email = profile.get('email')
        
        # Lógica de Fallback: Tenta recuperar o email do Plex se não existir localmente
        if not user_email:
            logger.info(f"E-mail não encontrado localmente para '{username}'. Tentando buscar no Plex pelo ID: {plex_user_id}")
            try:
                plex_user = extensions.plex_manager.get_user_by_id(plex_user_id)
                if plex_user and plex_user.get('email'):
                    user_email = plex_user.get('email')
                    logger.info(f"E-mail recuperado do Plex para '{username}': {user_email}")
                    # Salva no perfil local para o futuro
                    profile['email'] = user_email
                    extensions.data_manager.set_user_profile(plex_user_id, profile)
            except Exception as e:
                logger.warning(f"Falha ao tentar recuperar e-mail do Plex para '{username}': {e}")
        
        # Se ainda não tiver e-mail, logamos o aviso mas permitimos prosseguir
        if not user_email:
            logger.warning(f"Atenção: A gerar cobrança para '{username}' sem e-mail definido.")

        user_info = {
            "plex_user_id": plex_user_id, "username": username,
            "name": profile.get('name', username), "email": user_email
        }
    else:
        plex_user = extensions.plex_manager.get_user_by_id(plex_user_id)
        if not plex_user:
            return jsonify({"success": False, "message": _("Usuário não encontrado no Plex.")}), 404
        user_info = {
            "plex_user_id": plex_user_id, "username": username,
            "name": profile.get('name', username), "email": plex_user.get('email')
        }

    price_calculation = extensions.pricing_manager.calculate_price(
        screens=screens_str, coupon_code=coupon_code, plex_user_id=plex_user_id
    )

    if not price_calculation.get("success"):
        return jsonify(price_calculation), 400

    final_price = price_calculation.get("discounted_price")

    if final_price <= 0:
        try:
            is_reactivation = profile.get('status') == 'inactive'
            
            if is_reactivation:
                logger.info(f"Processando reativação gratuita para o utilizador '{username}' (ID: {plex_user_id}).")
                
                # --- LÓGICA DE ENVIO DE CONVITE (CORRIGIDA) PARA GRATUITO ---
                target_identifier = user_info.get('email')
                if not target_identifier:
                    # Fallback para Username
                    logger.info(f"Email não encontrado para '{username}'. Usando Username para reativação gratuita.")
                    target_identifier = username

                if not target_identifier:
                     raise Exception(f"Não foi possível encontrar identificador (email/username) para reativar o utilizador '{username}'.")

                try:
                    invite_result = extensions.plex_manager.invites.send_plex_invite(target_identifier, json.loads(profile.get('libraries', '[]')))
                    if not invite_result.get('success'):
                        logger.error(f"Falha ao reconvidar o utilizador inativo '{username}' via '{target_identifier}': {invite_result.get('message')}")
                    else:
                        logger.info(f"Convite de reativação (gratuita) enviado para {target_identifier}.")
                except Exception as invite_error:
                    logger.error(f"Erro ao enviar convite gratuito: {invite_error}")
                # -----------------------------------------------------

                time.sleep(3)
                extensions.plex_manager.users.invalidate_user_cache()
                
                # Atualiza explicitamente para ativo no caso gratuito
                user_profile_obj = UserProfile.query.get(plex_user_id)
                if user_profile_obj:
                    user_profile_obj.status = 'active'
                logger.info(f"Status do utilizador '{username}' definido como 'active' na sessão.")

            new_expiration_date = extensions.plex_manager.renew_subscription(
                plex_user_id,
                months_to_add=1,
                base_mode='expiry_date'
            )
            
            if coupon_code:
                if not extensions.data_manager.record_coupon_usage(coupon_code, plex_user_id):
                     raise Exception("Falha ao registrar o uso do cupão.")
            
            extensions.data_manager.add_manual_payment(
                plex_user_id=plex_user_id, username=username, value=0.00,
                description=f"Renovação/Reativação via Cupão 100% ({coupon_code})",
                payment_date_str=datetime.now().isoformat()
            )

            extensions.db.session.commit()

            user_info_for_notification = {'id': plex_user_id, 'username': username}
            updated_profile = extensions.data_manager.get_user_profile(plex_user_id)
            extensions.plex_manager.notifier_manager.send_renewal_notification(user_info_for_notification, new_expiration_date, updated_profile)

            if extensions.socketio:
                extensions.socketio.emit('user_list_updated', {'message': f'User {plex_user_id} status updated after free renewal.'}, namespace='/dashboard')
                logger.info(f"Socket.IO event 'user_list_updated' emitted for user {plex_user_id} (free renewal).")

            return jsonify({"success": True, "free_renewal": True, "message": _("Assinatura gratuita ativada com sucesso!")})
        
        except Exception as e:
            extensions.db.session.rollback()
            logger.error(f"Erro ao ativar assinatura gratuita com cupão: {e}", exc_info=True)
            return jsonify({"success": False, "message": "Ocorreu um erro ao ativar a sua assinatura gratuita."}), 500

    price, screens = final_price, int(screens_str)
    
    config = load_or_create_config()
    result = {"success": False, "message": _("O provedor %(provider)s não está habilitado.", provider=provider)}
    if provider == 'EFI' and config.get('EFI_ENABLED'):
        result = extensions.efi_manager.create_pix_charge(user_info, price, screens, coupon_code)
    elif provider == 'MERCADOPAGO' and config.get('MERCADOPAGO_ENABLED'):
        result = extensions.mercado_pago_manager.create_pix_payment(user_info, price, screens, coupon_code)
    elif provider == 'BPIX' and config.get('BPIX_ENABLED'):
        result = extensions.bpix_manager.create_pix_charge(user_info, price, screens, coupon_code)
        
    return jsonify(result)

@payments_api_bp.route('/status/<string:txid>')
@limiter.limit("60 per minute") # Polling de status a cada 1s (frontend)
def get_payment_status_route(txid):
    extensions.db.session.expire_all()

    payment = extensions.data_manager.get_pix_payment(txid)
    if not payment:
        return jsonify({"success": False, "status": "NOT_FOUND"}), 404
    
    if payment.get('status') == 'CONCLUIDA':
        profile = extensions.data_manager.get_user_profile(payment['user_plex_id'])
        user_status = profile.get('status', 'inactive') if profile else 'unknown'
        logger.info(f"Status check para {txid}: Pagamento CONCLUIDA. Status do utilizador: {user_status}")
        return jsonify({"success": True, "status": "CONCLUIDA", "user_status": user_status})
    
    if payment.get('status') == 'PROCESSANDO':
        return jsonify({"success": True, "status": "PROCESSANDO"})

    provider = payment.get('provider', 'EFI') 
    is_confirmed = False
    
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
        status_data = status_result.get("data", {})
        if status_result.get("success") and (status_data.get("status") == 'Pagamento realizado' or status_data.get("international_status") == "PAYMENT_RECEIVED"):
            is_confirmed = True

    if is_confirmed:
        _process_successful_payment(txid)
        return jsonify({"success": True, "status": "PROCESSANDO"})
        
    return jsonify({"success": True, "status": payment.get('status')})

@payments_api_bp.route('/webhook/efi', methods=['POST'])
@efi_webhook_security
@limiter.exempt # Webhooks não devem ser limitados, pois vêm dos provedores
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
                
                efi_status_result = extensions.efi_manager.detail_pix_charge(txid)
                
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
@limiter.exempt
def mercadopago_webhook():
    data = request.json
    logger.info(f"Webhook do Mercado Pago recebido: {data}")
    try:
        if data.get("type") == "payment":
            payment_id = str(data.get("data", {}).get("id"))
            if not payment_id:
                logger.warning("Webhook do Mercado Pago recebido sem ID de pagamento.")
                return jsonify(status="received"), 200

            mp_status_result = extensions.mercado_pago_manager.get_payment_details(payment_id)
            if mp_status_result.get("success") and mp_status_result.get("data", {}).get("status") == "approved":
                _process_successful_payment(payment_id)
            else:
                logger.warning(f"Webhook do Mercado Pago para ID {payment_id} recebido, mas o estado não é 'approved' ou a verificação falhou. Estado: {mp_status_result.get('data', {}).get('status')}")
    except Exception as e:
        logger.error(f"Erro ao processar o webhook do Mercado Pago: {e}", exc_info=True)
        return jsonify(status="error", message="Internal Server Error"), 500

    return jsonify(status="received"), 200

@payments_api_bp.route('/webhook/bpix', methods=['POST'])
@limiter.exempt
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
    summary = extensions.data_manager.get_financial_summary(year, month, renewal_days=renewal_days)
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
        user = extensions.plex_manager.get_user_by_id(plex_user_id)
        if not user:
            return jsonify({"success": False, "message": "Utilizador não encontrado."}), 404
        payment_datetime_str = f"{payment_date}T{datetime.now().strftime('%H:%M:%S')}"
        payment = extensions.data_manager.add_manual_payment(plex_user_id, user['username'], value, description, payment_datetime_str)
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
        if extensions.data_manager.delete_pix_payment(txid):
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

        payments = extensions.data_manager.get_payments_for_export(start_date, end_date)

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
