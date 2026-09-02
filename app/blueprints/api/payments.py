import logging
import hmac
import csv
from io import StringIO
import threading
import time
import json
from datetime import datetime, date, timezone
from sqlalchemy.exc import OperationalError

from flask import Blueprint, jsonify, request, url_for, current_app, Response, stream_with_context
from flask_login import current_user, login_required
from flask_babel import gettext as _

from ... import extensions
from ...config import load_or_create_config
from ..auth import admin_required
from ...models import UserProfile, PixPayment
from ...extensions import limiter
from ...services.data_manager import get_app_timezone, normalize_coupon_code
from ...utils.log_sanitizer import mask_token

logger = logging.getLogger(__name__)
payments_api_bp = Blueprint('payments_api', __name__)

# Período máximo de uma exportação. Não é um limite arbitrário: é o que impede
# que um pedido de "desde sempre" leia a tabela inteira num só relatório.
CSV_MAX_INTERVALO_DIAS = 366

# Caracteres que transformam uma célula em fórmula ao abrir o ficheiro no Excel
# ou no Google Sheets.
CSV_PREFIXOS_PERIGOSOS = ('=', '+', '-', '@', '\t', '\r')


def _csv_seguro(valor):
    """
    Neutraliza texto que o Excel interpretaria como fórmula.

    ⚠️ Isto protege contra CSV injection: o nome de utilizador vem do Plex, não
    do administrador. Alguém chamado '=cmd|...' conseguia que a fórmula corresse
    na máquina de quem abrisse o relatório de contabilidade. Prefixar com uma
    plica força a célula a ser tratada como texto.
    """
    if valor is None:
        return ''
    texto = str(valor)
    if texto[:1] in CSV_PREFIXOS_PERIGOSOS:
        return "'" + texto
    return texto


def _dinheiro_csv(valor):
    """Formata um valor monetário no formato português/brasileiro (vírgula decimal)."""
    try:
        return f"{float(valor or 0):.2f}".replace('.', ',')
    except (TypeError, ValueError):
        return "0,00"


def _data_local_csv(created_at_iso, local_tz):
    """
    Converte a data guardada (UTC) para o fuso do painel.

    Sem isto, o relatório mostrava horas em UTC enquanto o dashboard mostrava as
    mesmas transações na hora local — três horas de diferença no Brasil.
    """
    try:
        dt = datetime.fromisoformat(created_at_iso)
    except (TypeError, ValueError):
        return _csv_seguro(created_at_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz).strftime('%Y-%m-%d %H:%M:%S')

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
                        message=_("O usuário %(username)s reativou a conta. Pagamento de %(value)s confirmado.", username=profile['username'], value=f"R$ {payment['value']:.2f}"),
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

                # 🔼 UPGRADE PRO-RATA: esta cobrança é apenas a DIFERENÇA de preço
                # pelos dias que faltavam. NÃO é uma renovação — o vencimento tem de
                # ficar exatamente onde estava. Se caísse no fluxo normal abaixo, o
                # utilizador ganharia um mês inteiro por uma fração do preço.
                if payment.get('is_proration'):
                    novo_limite = payment.get('screens')
                    profile_upgrade = extensions.data_manager.get_user_profile(plex_user_id)
                    if profile_upgrade and novo_limite is not None:
                        limite_anterior = profile_upgrade.get('screen_limit')
                        profile_upgrade['screen_limit'] = int(novo_limite)
                        extensions.data_manager.set_user_profile(plex_user_id, profile_upgrade)
                        logger.info(
                            f"Upgrade pro-rata concluído para '{profile_upgrade.get('username')}': "
                            f"{limite_anterior} -> {novo_limite} telas. Vencimento inalterado "
                            f"({profile_upgrade.get('expiration_date')})."
                        )
                        extensions.data_manager.create_notification(
                            message=_("O seu plano foi atualizado para %(screens)d tela(s)!", screens=int(novo_limite)),
                            category='success', link=url_for('main.account_page'), user_plex_id=plex_user_id
                        )
                        extensions.data_manager.create_notification(
                            message=_("%(username)s fez upgrade para %(screens)d tela(s). Pagamento de %(value)s confirmado.",
                                      username=profile_upgrade.get('username'), screens=int(novo_limite),
                                      value=f"R$ {payment['value']:.2f}"),
                            category='success', link=url_for('main.users_page')
                        )
                        if extensions.socketio:
                            extensions.socketio.emit('new_notification', namespace='/')

                    extensions.data_manager.update_pix_payment_status(txid, 'CONCLUIDA')
                    logger.info(f"Processamento do upgrade pro-rata para TXID {mask_token(txid)} concluído.")
                    return

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

                    # 🛡️ O registo do cupão NUNCA pode derrubar a confirmação de um
                    # pagamento. Uma exceção aqui (ex: uso já registado) subia até ao
                    # tratamento de erros abaixo e marcava como 'FALHOU' um pagamento
                    # cuja assinatura já tinha sido renovada — o cliente pagava, era
                    # renovado, e a transação sumia do relatório financeiro.
                    if payment.get('coupon_code'):
                        try:
                            extensions.data_manager.record_coupon_usage(payment['coupon_code'], plex_user_id)
                        except Exception as e:
                            logger.error(
                                f"Erro ao registar o uso do cupão no pagamento {mask_token(txid)}: {e}",
                                exc_info=True
                            )

                    # 💳 CONSUMO DO CRÉDITO DE INDICAÇÕES: só agora, com o pagamento
                    # confirmado, o crédito reservado sai do saldo. Fazê-lo antes
                    # (na criação da cobrança) faria com que um PIX gerado e nunca
                    # pago consumisse o crédito do utilizador sem contrapartida.
                    credit_reserved = float(payment.get('referral_credit_used') or 0)
                    if credit_reserved > 0:
                        try:
                            consumed = extensions.referral_manager.consume_credit(plex_user_id, credit_reserved)
                            logger.info(f"Pagamento {mask_token(txid)}: R$ {consumed:.2f} de crédito de indicações consumido por '{profile.get('username')}'.")
                        except Exception as e:
                            logger.error(f"Erro ao consumir o crédito de indicações no pagamento {mask_token(txid)}: {e}", exc_info=True)

                    # 🎁 INDIQUE E GANHE: se este utilizador foi indicado por alguém e
                    # esta é a sua primeira compra, quem o indicou recebe a recompensa.
                    # Envolvido em try/except por princípio: uma falha no programa de
                    # indicações nunca pode comprometer a confirmação de um pagamento.
                    try:
                        extensions.referral_manager.reward_referrer_on_payment(plex_user_id)
                    except Exception as e:
                        logger.error(f"Erro ao processar a recompensa de indicação para o utilizador {plex_user_id}: {e}", exc_info=True)
                        
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
                logger.info(f"Processamento do pagamento para TXID {mask_token(txid)} concluído com sucesso.")

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
                    logger.error(f"Erro persistente de base de dados bloqueada para o TXID {mask_token(txid)}.", exc_info=True)
            else:
                break
        except Exception as e:
            logger.error(f"Erro no processamento do pagamento {mask_token(txid)}: {e}", exc_info=True)
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
        logger.error(f"Erro ao atualizar estado na base de dados para {mask_token(txid)}: {e}")
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
        return jsonify({"success": False, "message": _("Usuário não especificado ou token inválido.")}), 400

    config = load_or_create_config()
    available_prices = extensions.pricing_manager.get_available_plans(user_profile, is_public_request)
    
    if not available_prices:
        return jsonify({"success": False, "message": _("Nenhum plano de renovação disponível.")}), 404
        
    enabled_providers = {
        "efi": config.get("EFI_ENABLED"), 
        "mercadopago": config.get("MERCADOPAGO_ENABLED"),
        "gates2b": config.get("GATES2B_ENABLED")
    }
    return jsonify({
        "success": True, "prices": available_prices, "providers": enabled_providers,
        "can_downgrade": True,
        # Indica que subir de plano agora exige o pro-rata (renovação a preço
        # cheio com mais telas está bloqueada até perto do vencimento).
        "requires_proration_for_upgrade": extensions.pricing_manager.requires_proration_for_upgrade(user_profile)
    })

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
        return jsonify({"success": False, "message": _("Usuário não autorizado ou token inválido.")}), 404

    if not data.get('code') or data.get('screens') is None:
        return jsonify({"success": False, "message": "Código e plano são obrigatórios."}), 400

    return jsonify(extensions.pricing_manager.calculate_price(
        screens=data.get('screens'), coupon_code=data.get('code'), plex_user_id=plex_user_id
    ))

@payments_api_bp.route('/upgrade-quote', methods=['POST'])
@login_required
def get_upgrade_quote():
    """
    Devolve quanto custaria fazer upgrade de plano agora (pro-rata), sem criar
    qualquer cobrança. Serve para a interface mostrar o valor antes de o
    utilizador confirmar.
    """
    data = request.json or {}
    try:
        new_screens = int(data.get('screens'))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": _("Número de telas inválido.")}), 400

    quote = extensions.pricing_manager.calculate_upgrade_proration(int(current_user.id), new_screens)
    return jsonify({"success": True, **quote})


@payments_api_bp.route('/create-charge', methods=['POST'])
@limiter.limit("3 per minute")
def create_charge_route():
    data = request.json or {}
    provider = data.get('provider')
    screens_str = data.get('screens')
    # Forma canónica (maiúsculas, sem espaços) para que o código guardado na
    # cobrança seja o mesmo que a validação e o relatório vêem.
    coupon_code = normalize_coupon_code(data.get('coupon_code')) or None
    
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
        return jsonify({"success": False, "message": _("Usuário não especificado ou token inválido.")}), 400

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

    # 🔼 UPGRADE PRO-RATA: se o pedido vier marcado como upgrade e o utilizador for
    # elegível, cobramos apenas a DIFERENÇA proporcional aos dias restantes, em vez
    # do preço cheio de um novo ciclo.
    is_proration_request = bool(data.get('proration'))
    proration_quote = None
    if is_proration_request:
        proration_quote = extensions.pricing_manager.calculate_upgrade_proration(plex_user_id, screens)
        if not proration_quote.get('eligible'):
            return jsonify({"success": False, "message": proration_quote.get('reason') or _("Upgrade não disponível.")}), 400

        # Valor abaixo do mínimo cobrável: aplica-se de imediato, sem gerar cobrança
        # (cobrar cêntimos custaria mais em taxas do que o próprio valor).
        if proration_quote.get('is_free'):
            profile_free = extensions.data_manager.get_user_profile(plex_user_id)
            anterior = profile_free.get('screen_limit')
            profile_free['screen_limit'] = screens
            extensions.data_manager.set_user_profile(plex_user_id, profile_free)
            logger.info(f"Upgrade gratuito (abaixo do mínimo) para '{username}': {anterior} -> {screens} telas.")
            return jsonify({
                "success": True, "free_upgrade": True,
                "message": _("Plano atualizado para %(screens)d tela(s)!", screens=screens)
            })

    # 🔒 Bloqueio do upgrade a preço cheio a meio do ciclo: sem isto, renovar
    # normalmente com mais telas dava ao utilizador os dias restantes já com o plano
    # superior sem pagar a diferença — tornando o pro-rata sempre desvantajoso e,
    # na prática, inútil.
    if not is_proration_request and screens > int(profile.get('screen_limit') or 0):
        if extensions.pricing_manager.requires_proration_for_upgrade(profile):
            # 🐛 Só bloqueamos se o pro-rata for MESMO uma alternativa viável para
            # este plano. Sem esta verificação, um utilizador cujo pro-rata não
            # fosse elegível (valor abaixo do mínimo sem oferta gratuita, dias
            # insuficientes, preço em falta...) ficava sem qualquer forma de subir
            # de plano: a renovação normal bloqueada e o pro-rata indisponível.
            alternativa = extensions.pricing_manager.calculate_upgrade_proration(plex_user_id, screens)
            if alternativa.get('eligible'):
                return jsonify({
                    "success": False,
                    "requires_proration": True,
                    "message": _("Para aumentar o número de telas agora, use a opção de pagar apenas a diferença. A troca de plano numa renovação completa fica disponível perto do vencimento.")
                }), 400
            logger.info(
                f"Upgrade a preço cheio permitido para '{username}': o pro-rata não está "
                f"disponível ({alternativa.get('reason')})."
            )

    price_calculation = extensions.pricing_manager.calculate_price(
        screens_str, coupon_code, plex_user_id, apply_referral_credit=True
    )
    if not price_calculation.get("success"):
        return jsonify(price_calculation), 400

    final_price = price_calculation.get("discounted_price")
    # Crédito que ESTA cobrança pretende usar. Ainda não foi debitado: fica apenas
    # registado no pagamento e só sai do saldo quando o pagamento for confirmado.
    referral_credit_to_use = float(price_calculation.get("referral_credit_applied") or 0)

    # No upgrade pro-rata o preço é o valor proporcional, não o do plano. Cupões e
    # crédito de indicações NÃO se acumulam aqui: o valor já é reduzido, e somar
    # descontos permitiria fazer upgrade por quase nada.
    if is_proration_request and proration_quote:
        # O preço base passa a ser o valor PROPORCIONAL. Se houver cupão válido,
        # o desconto é aplicado sobre essa diferença (e não sobre o preço cheio do
        # plano) — assim um cupão de 100% torna o upgrade gratuito, sem transformar
        # o pedido numa renovação de mês inteiro.
        final_price = proration_quote['amount']
        if price_calculation.get('coupon_applied'):
            desconto_ratio = 0.0
            original = price_calculation.get('original_price') or 0
            if original > 0:
                desconto_ratio = 1 - (price_calculation.get('discounted_price', 0) / original)
            final_price = round(max(0.0, final_price * (1 - desconto_ratio)), 2)
            logger.info(
                f"Cupão '{coupon_code}' aplicado ao upgrade pro-rata de '{username}': "
                f"R$ {proration_quote['amount']:.2f} -> R$ {final_price:.2f}"
            )
        # Crédito de indicações não se acumula aqui (o valor já é reduzido).
        referral_credit_to_use = 0.0

    # 🔼 UPGRADE PRO-RATA COM VALOR ZERO (ex: cupão de 100%, ou diferença abaixo do
    # mínimo). Este ramo TEM de vir antes do bloco de renovação gratuita: sem ele, um
    # pedido de upgrade acabava tratado como renovação normal e o utilizador ganhava
    # um mês inteiro em vez de apenas trocar de plano — que foi exatamente o que
    # acontecia ao usar um cupão de 100% para subir de telas.
    if is_proration_request and final_price <= 0:
        try:
            profile_up = extensions.data_manager.get_user_profile(plex_user_id)
            anterior = profile_up.get('screen_limit')
            profile_up['screen_limit'] = screens
            extensions.data_manager.set_user_profile(plex_user_id, profile_up)

            # O plano JÁ foi alterado acima: uma falha a registar o cupão não pode
            # transformar isto num erro para o utilizador.
            if coupon_code:
                try:
                    extensions.data_manager.record_coupon_usage(coupon_code, plex_user_id)
                except Exception as e:
                    logger.error(f"Erro ao registar o uso do cupão '{coupon_code}' no upgrade sem custo: {e}", exc_info=True)

            logger.info(
                f"Upgrade pro-rata sem custo para '{username}': {anterior} -> {screens} telas. "
                f"Vencimento mantido em {profile_up.get('expiration_date')}."
            )
            extensions.data_manager.create_notification(
                message=_("O seu plano foi atualizado para %(screens)d tela(s)!", screens=screens),
                category='success', link=url_for('main.account_page'), user_plex_id=plex_user_id
            )
            if extensions.socketio:
                extensions.socketio.emit('new_notification', namespace='/')

            return jsonify({
                "success": True, "free_upgrade": True,
                "message": _("Plano atualizado para %(screens)d tela(s)! O seu vencimento não foi alterado.", screens=screens)
            })
        except Exception as e:
            logger.error(f"Erro ao aplicar upgrade pro-rata sem custo: {e}", exc_info=True)
            return jsonify({"success": False, "message": str(e)}), 500

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
                    message=_("O usuário %(username)s reativou a conta com um cupão de 100%%.", username=username),
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

            # Idem: a assinatura já foi renovada acima.
            if coupon_code:
                try:
                    extensions.data_manager.record_coupon_usage(coupon_code, plex_user_id)
                except Exception as e:
                    logger.error(f"Erro ao registar o uso do cupão '{coupon_code}' na renovação gratuita: {e}", exc_info=True)

            # 💳 Se o crédito de indicações foi (parte do) responsável por zerar o
            # valor, é AQUI que ele sai do saldo — este fluxo conclui na hora, sem
            # webhook, por isso o débito tem de acontecer neste ponto.
            if referral_credit_to_use > 0:
                consumed = extensions.referral_manager.consume_credit(plex_user_id, referral_credit_to_use)
                logger.info(f"Crédito de indicações de R$ {consumed:.2f} usado por '{username}' numa renovação sem custo.")

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
        'GATES2B': ('GATES2B_ENABLED', extensions.gates2b_manager.create_pix_charge if hasattr(extensions, 'gates2b_manager') else None)
    }

    config_key, charge_func = provider_map.get(provider, (None, None))

    if charge_func and config.get(config_key):
        result = charge_func(user_info, final_price, screens, coupon_code)
    else:
        result = {"success": False, "message": _("O provedor %(provider)s não está habilitado ou é inválido.", provider=provider)}

    # 💳 Regista no pagamento quanto crédito de indicações esta cobrança pretende
    # consumir. Fica apenas RESERVADO: o débito real acontece no webhook, quando o
    # pagamento é confirmado. Se o utilizador nunca pagar, o crédito continua
    # intacto no saldo dele.
    # Marca a cobrança como pro-rata para que o webhook aplique só a troca de
    # plano, sem estender o vencimento.
    if is_proration_request and result.get("success") and result.get("txid"):
        try:
            extensions.data_manager.mark_payment_as_proration(result["txid"])
        except Exception as e:
            logger.error(f"Falha ao marcar a cobrança {result.get('txid')} como pro-rata: {e}", exc_info=True)

    if referral_credit_to_use > 0 and result.get("success") and result.get("txid"):
        try:
            extensions.data_manager.set_payment_referral_credit(result["txid"], referral_credit_to_use)
            logger.info(f"Cobrança {result['txid']}: R$ {referral_credit_to_use:.2f} de crédito de indicações reservado para '{username}'.")
        except Exception as e:
            logger.error(f"Não foi possível registar o crédito de indicações na cobrança {result.get('txid')}: {e}", exc_info=True)

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
        elif provider in ('GATES2B', 'BPIX'):  # 'BPIX' aceite por retrocompatibilidade
            status_result = extensions.gates2b_manager.detail_pix_charge(txid)
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
        
        # 🔒 A comparação TEM de ser feita com 'compare_digest'. Um simples '!='
        # compara caractere a caractere e para no primeiro que difere — a
        # diferença no tempo de resposta é mensurável e permite descobrir o
        # segredo byte a byte (timing attack). O 'compare_digest' demora sempre
        # o mesmo tempo, independentemente de onde está a diferença.
        if not hmac_secret or not received_hmac or not hmac.compare_digest(str(hmac_secret), str(received_hmac)):
            logger.warning(f"Webhook Efí bloqueado: HMAC inválido ou ausente. IP: {request.remote_addr}")
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
                    logger.info(f"Pagamento {mask_token(txid)} confirmado via Webhook Efí. A iniciar renovação.")
                    _process_successful_payment(txid)
                else:
                    logger.warning(f"Webhook recebido para {mask_token(txid)}, mas o estado na Efí não indica conclusão.")
                    
    except Exception as e:
        logger.error(f"Erro no processamento do Webhook Efí: {e}", exc_info=True)
        return jsonify(status="error", message="Server Error"), 500
        
    return jsonify(status="received"), 200

@payments_api_bp.route('/webhook/mercadopago', methods=['POST'])
@limiter.exempt
def mercadopago_webhook():
    data = request.json or {}
    try:
        if data.get("type") != "payment":
            return jsonify(status="ignored"), 200

        payment_id = str(data.get("data", {}).get("id"))

        # 🔒 Valida a assinatura HMAC antes de qualquer trabalho. Sem isto, um
        # atacante podia disparar pedidos em massa nesta rota (que é pública e
        # isenta de rate limit) e cada um provocaria uma chamada à API do Mercado
        # Pago, esgotando o rate limit da conta. Se não houver segredo configurado,
        # a validação é ignorada para não quebrar instalações existentes.
        valido, motivo = extensions.mercado_pago_manager.validate_webhook_signature(
            request.headers.get('x-signature'),
            request.headers.get('x-request-id'),
            payment_id
        )
        if not valido:
            logger.warning(f"Webhook do Mercado Pago rejeitado ({motivo}). IP: {request.remote_addr}")
            return jsonify(status="error", message="Invalid signature"), 401

        # A confirmação NUNCA se baseia no corpo recebido: consultamos sempre a API
        # para saber o estado real do pagamento.
        mp_status_result = extensions.mercado_pago_manager.get_payment_details(payment_id)
        if not mp_status_result.get("success"):
            return jsonify(status="received"), 200

        status = mp_status_result.get("data", {}).get("status")

        if status == "approved":
            logger.info(f"Pagamento {mask_token(payment_id)} confirmado via Webhook Mercado Pago. A iniciar renovação.")
            _process_successful_payment(payment_id)

        # 💸 REEMBOLSOS E ESTORNOS: antes só se tratava 'approved'. Um pagamento
        # devolvido ou contestado passava despercebido e o utilizador ficava com
        # acesso já pago de volta. Não revogamos o acesso automaticamente (pode ser
        # um reembolso parcial ou acordado), mas registamos e avisamos o
        # administrador para que decida.
        elif status in ("refunded", "charged_back", "cancelled"):
            _handle_mercadopago_reversal(payment_id, status)

    except Exception as e:
        logger.error(f"Erro no Webhook Mercado Pago: {e}", exc_info=True)
        return jsonify(status="error", message="Server Error"), 500
    return jsonify(status="received"), 200


def _handle_mercadopago_reversal(payment_id, status):
    """Regista um reembolso/estorno e notifica o administrador."""
    try:
        payment = extensions.data_manager.get_pix_payment(payment_id)
        if not payment:
            logger.warning(f"Reversão recebida para o pagamento {mask_token(payment_id)}, que não existe localmente.")
            return

        rotulos = {
            "refunded": _("reembolsado"),
            "charged_back": _("contestado (chargeback)"),
            "cancelled": _("cancelado"),
        }
        rotulo = rotulos.get(status, status)

        extensions.data_manager.update_pix_payment_status(payment_id, 'REVERTIDO')
        logger.warning(
            f"⚠️ Pagamento {mask_token(payment_id)} de '{payment.get('username')}' foi {rotulo} "
            f"(valor: R$ {payment.get('value', 0):.2f}). O acesso NÃO foi revogado automaticamente."
        )

        extensions.data_manager.create_notification(
            message=_("Pagamento de %(username)s foi %(estado)s (R$ %(valor).2f). Verifique se o acesso deve ser revogado.",
                      username=payment.get('username'), estado=rotulo, valor=payment.get('value', 0)),
            category='error', link=url_for('main.users_page')
        )
        if extensions.socketio:
            extensions.socketio.emit('new_notification', namespace='/')
    except Exception as e:
        logger.error(f"Erro ao processar a reversão do pagamento {mask_token(payment_id)}: {e}", exc_info=True)

@payments_api_bp.route('/webhook/gates2b', methods=['POST'])
# Rota antiga mantida para não quebrar integrações já configuradas no painel
# do gateway antes da mudança de marca (BPIX -> Gates2b).
@payments_api_bp.route('/webhook/bpix', methods=['POST'])
@limiter.exempt
def gates2b_webhook():
    data = request.json or {}
    try:
        # 🔀 Aceita o formato NOVO (/charge) e o ANTIGO (/payments), porque durante
        # a transição podem existir cobranças criadas pelos dois endpoints ainda
        # por pagar. Procuramos o identificador em todos os campos conhecidos.
        txid = (
            data.get("id")                      # /charge: 'chg_...'
            or data.get("chargeId")
            or data.get("charge_id")
            or data.get("transaction_pix_id")   # /payments (formato antigo)
        )

        # Alguns webhooks encapsulam a cobrança num objeto 'charge'/'data'.
        if not txid:
            interno = data.get("charge") or data.get("data") or {}
            if isinstance(interno, dict):
                txid = interno.get("id") or interno.get("transaction_pix_id")

        estado = str(
            data.get("status")
            or data.get("international_status")
            or ((data.get("charge") or data.get("data") or {}).get("status") if isinstance(data.get("charge") or data.get("data"), dict) else "")
            or ""
        ).strip().upper()

        # Estados que significam 'pago', nos dois formatos.
        PAGO = {"PAID", "PAYMENT_RECEIVED", "APPROVED", "COMPLETED", "PAGAMENTO REALIZADO"}

        if txid and estado in PAGO:
            logger.info(f"Pagamento {mask_token(txid)} confirmado via Webhook Gates2b (estado: {estado}). A iniciar renovação.")
            _process_successful_payment(txid)
        elif txid:
            logger.debug(f"Webhook Gates2b recebido para {mask_token(txid)} com estado '{estado}'. Nenhuma ação necessária.")
        else:
            # Sem identificador não há nada a fazer — registamos para diagnóstico.
            logger.warning(f"Webhook Gates2b sem identificador reconhecível. Campos recebidos: {list(data.keys())}")
    except Exception as e:
        logger.error(f"Erro no Webhook Gates2b: {e}", exc_info=True)
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
            logger.info(f"Transação {mask_token(txid)} apagada manualmente pelo Admin.")
            return jsonify({"success": True, "message": _("Transação apagada.")})
        return jsonify({"success": False, "message": _("Transação não encontrada.")}), 404
    except Exception as e:
        logger.error(f"Erro ao apagar a transação {mask_token(txid)}: {e}")
        return jsonify({"success": False, "message": _("Falha ao excluir.")}), 500

@payments_api_bp.route('/financial/export-csv')
@login_required
@admin_required
def export_financial_csv():
    start_str, end_str = request.args.get('start_date'), request.args.get('end_date')
    if not start_str or not end_str:
        return jsonify({"success": False, "message": _("Datas são obrigatórias.")}), 400

    # As datas eram coladas diretamente no filtro e no nome do ficheiro. Como o
    # filtro compara TEXTO, uma data malformada não dava erro: devolvia em
    # silêncio um relatório vazio ou errado.
    try:
        dia_inicio = datetime.strptime(start_str, '%Y-%m-%d').date()
        dia_fim = datetime.strptime(end_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": _("Datas inválidas. Use o formato AAAA-MM-DD.")}), 400

    if dia_fim < dia_inicio:
        return jsonify({"success": False, "message": _("A data de fim não pode ser anterior à data de início.")}), 400

    if (dia_fim - dia_inicio).days > CSV_MAX_INTERVALO_DIAS:
        return jsonify({
            "success": False,
            "message": _("O período não pode exceder %(dias)d dias. Exporte por partes.", dias=CSV_MAX_INTERVALO_DIAS)
        }), 400

    try:
        # 🕓 O mesmo fuso horário usado pelo dashboard. Antes, o CSV assumia UTC:
        # num fuso como o do Brasil, o total do relatório de um mês não batia
        # certo com o cartão "Receita do Mês" do próprio painel.
        local_tz = get_app_timezone()
        inicio_utc = local_tz.localize(
            datetime.combine(dia_inicio, datetime.min.time())
        ).astimezone(timezone.utc).isoformat()
        fim_utc = local_tz.localize(
            datetime.combine(dia_fim, datetime.max.time())
        ).astimezone(timezone.utc).isoformat()

        # 'stream_with_context' é essencial: sem ele o gerador só é consumido
        # DEPOIS de o Flask desmontar o contexto do pedido, o que deixava as
        # traduções inertes e tornava impossível ler seja o que for da base de
        # dados enquanto o ficheiro é escrito.
        @stream_with_context
        def generate():
            si = StringIO()
            si.write('\ufeff')
            cw = csv.writer(si, delimiter=';')

            def despejar():
                valor = si.getvalue()
                si.seek(0)
                si.truncate(0)
                return valor

            cw.writerow([
                'Data', 'Utilizador', 'Descricao', 'Valor (R$)',
                'Credito Indicacoes (R$)', 'Tipo', 'Provedor', 'Cupao', 'TXID'
            ])
            yield despejar()

            total_value = 0.0
            total_credito = 0.0
            total_transacoes = 0

            # ⚠️ A resposta já começou a ser enviada, por isso um erro aqui não
            # pode virar um 500: sem esta marca, o ficheiro terminava a meio e
            # parecia completo a quem o abrisse na contabilidade.
            try:
                # Iterador por lotes: o relatório começa a ser enviado sem ter de
                # carregar o período inteiro para memória.
                for p in extensions.data_manager.iter_payments_for_export(inicio_utc, fim_utc):
                    valor = float(p.get('value') or 0)
                    credito = float(p.get('referral_credit_used') or 0)
                    total_value += valor
                    total_credito += credito
                    total_transacoes += 1

                    cw.writerow([
                        _data_local_csv(p.get('created_at'), local_tz),
                        _csv_seguro(p.get('username')),
                        _csv_seguro(p.get('description') or f"{p.get('screens', 'N/A')} Telas"),
                        _dinheiro_csv(valor),
                        _dinheiro_csv(credito),
                        _('Upgrade pro-rata') if p.get('is_proration') else _('Renovação'),
                        _csv_seguro(p.get('provider')),
                        _csv_seguro(p.get('coupon_code') or ''),
                        _csv_seguro(p.get('txid')),
                    ])
                    yield despejar()
            except Exception as e:
                logger.error(f"Erro a meio da geração do CSV: {e}", exc_info=True)
                cw.writerow([])
                cw.writerow(['', '', _('RELATÓRIO INCOMPLETO: ocorreu um erro. Repita a exportação.')])
                yield despejar()
                return

            cw.writerow([])
            cw.writerow(['', '', _('RESUMO DO PERÍODO')])
            cw.writerow(['', '', _('Total Arrecadado'), _dinheiro_csv(total_value)])
            cw.writerow(['', '', _('Crédito de Indicações Usado'), _dinheiro_csv(total_credito)])
            cw.writerow(['', '', _('Total de Transações'), total_transacoes])
            yield despejar()

        # O nome do ficheiro usa as datas já validadas, e nunca o texto cru que
        # veio no pedido.
        nome_ficheiro = f"relatorio_{dia_inicio.isoformat()}_a_{dia_fim.isoformat()}.csv"
        return Response(
            generate(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={nome_ficheiro}",
                "Content-Type": "text/csv; charset=utf-8",
            }
        )
    except Exception as e:
        logger.error(f"Erro ao gerar CSV: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Erro ao gerar."}), 500