# app/blueprints/api/users.py

import logging
import secrets
import json
from datetime import date, datetime, timezone, timedelta 

from flask import Blueprint, jsonify, request, url_for, current_app
from flask_login import current_user
from flask_babel import gettext as _, format_date
from tzlocal import get_localzone
from pydantic import ValidationError, BaseModel, Field
from sqlalchemy.exc import IntegrityError
from apscheduler.jobstores.base import JobLookupError

from ... import extensions
from ...config import load_or_create_config
from ..auth import admin_required, login_required
from .decorators import user_lookup_by_id, validate_json
from .schemas import RenewSubscriptionSchema, UpdateProfileSchema, UpdateAccountProfileSchema
from ...models import UserProfile

logger = logging.getLogger(__name__)
users_api_bp = Blueprint('users_api', __name__)

class ExtendTrialSchema(BaseModel):
    extend_minutes: int = Field(..., gt=0, description="Duração da extensão em minutos (deve ser maior que zero).")

# ==========================================
# ROTAS PÚBLICAS
# ==========================================

@users_api_bp.route('/public-profile-by-token/<string:token>')
def get_public_user_profile_by_token(token):
    profile = UserProfile.query.filter_by(payment_token=token).first()
    if not profile:
        return jsonify({"success": False, "message": _("Link de pagamento inválido ou utilizador não encontrado.")}), 404

    user_thumb = None
    username = profile.username

    if profile.status == 'active':
        user = extensions.plex_manager.get_user_by_id(profile.plex_user_id)
        if not user:
            logger.warning(f"Utilizador ativo '{username}' (ID: {profile.plex_user_id}) não encontrado no Plex. A tratar como inativo para a página pública.")
        else:
            user_thumb = user.get('thumb')

    expiration_date_formatted = None
    if profile.expiration_date:
        try:
            local_tz = get_localzone()
            exp_date = datetime.fromisoformat(profile.expiration_date).astimezone(local_tz)
            expiration_date_formatted = exp_date.strftime('%d/%m/%Y')
        except (ValueError, TypeError): 
            pass

    public_data = {
        "username": username,
        "thumb": user_thumb,
        "expiration_date_formatted": expiration_date_formatted,
        "expiration_date_iso": profile.expiration_date
    }
    return jsonify({"success": True, "profile": public_data})

@users_api_bp.route('/public/finalize-reactivation', methods=['POST'])
def finalize_reactivation_route():
    """Rota pública chamada pela página de pagamento para aceitar convite via Token."""
    data = request.json
    plex_token = data.get('plex_token')
    payment_token = data.get('payment_token')

    if not plex_token or not payment_token:
        return jsonify({"success": False, "message": _("Dados incompletos.")}), 400

    result = extensions.plex_manager.invites.accept_invite_via_token(plex_token)
    if not result.get('success'):
        return jsonify(result), 400

    plex_user_obj = result.get('user')
    
    try:
        profile = UserProfile.query.filter_by(payment_token=payment_token).first()
        if not profile:
             return jsonify({"success": False, "message": _("Perfil local não encontrado.")}), 404

        # Segurança: Verifica se o ID do Plex que aceitou o convite é o mesmo do perfil local
        if int(profile.plex_user_id) != int(plex_user_obj.id):
             logger.warning(f"Tentativa de reativação com conta incorreta. Token: {profile.plex_user_id}, Login: {plex_user_obj.id}")
             return jsonify({
                "success": False, 
                "message": _("A conta Plex utilizada ('%(plex_user)s') não corresponde à conta original deste perfil. Saia do Plex e entre com a conta original (%(local_user)s).", plex_user=plex_user_obj.username, local_user=profile.username)
             }), 409

        # Verifica mudança de username/email (Evita colisões na DB)
        if profile.username != plex_user_obj.username or profile.email != plex_user_obj.email:
            existing_collision = UserProfile.query.filter_by(username=plex_user_obj.username).first()
            if existing_collision and existing_collision.plex_user_id != profile.plex_user_id:
                 return jsonify({
                    "success": False, 
                    "message": _("Alterou o seu nome no Plex para '%(new_name)s', mas este já existe no sistema. Contacte o suporte.", new_name=plex_user_obj.username)
                 }), 409
            
            profile.username = plex_user_obj.username
            profile.email = plex_user_obj.email

        profile.status = 'active'
        profile.pending_invite_link = None
        extensions.db.session.commit()
        extensions.plex_manager.users.invalidate_user_cache()
        
        logger.info(f"Reativação finalizada com sucesso para o utilizador público: {profile.username}")
        return jsonify({"success": True, "message": _("Conta reativada com sucesso!"), "redirect_url": url_for('main.account_page')})

    except IntegrityError:
        extensions.db.session.rollback()
        return jsonify({"success": False, "message": _("Erro de integridade ao sincronizar nome de utilizador.")}), 409
    except Exception as e:
        extensions.db.session.rollback()
        logger.error(f"Erro ao finalizar reativação local: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Erro ao atualizar sistema local.")}), 500

# ==========================================
# ROTAS DO UTILIZADOR (MINHA CONTA)
# ==========================================

@users_api_bp.route('/account/details')
@login_required
def get_account_details():
    config = load_or_create_config()
    plex_user_id = int(current_user.id)
    profile = extensions.data_manager.get_user_profile(plex_user_id)
    
    is_blocked_info = extensions.data_manager.get_blocked_user(plex_user_id)

    expiration_info = _get_expiration_details(profile, config)
    
    join_date = _("Não disponível")
    if join_date_str := extensions.data_manager.get_user_claim_date(plex_user_id):
        try: 
            local_tz = get_localzone()
            join_date = format_date(datetime.fromisoformat(join_date_str).astimezone(local_tz), 'd \'de\' MMMM \'de\' yyyy')
        except (ValueError, TypeError): 
            pass

    libraries_data = extensions.plex_manager.get_user_libraries(plex_user_id)
    watch_data = extensions.tautulli_manager.get_user_watch_details(plex_user_id=plex_user_id)

    is_on_trial = False
    if trial_end_date_iso := profile.get('trial_end_date'):
        try:
            if datetime.fromisoformat(trial_end_date_iso) > datetime.now(timezone.utc):
                is_on_trial = True
        except (ValueError, TypeError): 
            pass

    return jsonify({
        "success": True, 
        "username": current_user.username, 
        "email": current_user.email, 
        "thumb": current_user.thumb,
        "join_date": join_date, 
        "screen_limit": _("%(num)d Tela(s)", num=profile.get('screen_limit', 0)) if profile.get('screen_limit', 0) > 0 else _("Ilimitado"),
        "libraries": libraries_data.get('libraries', []), 
        "watch_stats": watch_data.get('details', {}),
        "expiration_info": expiration_info, 
        "is_blocked": is_blocked_info is not None, 
        "block_reason": is_blocked_info.get('block_reason') if is_blocked_info else None,
        "trial_end_date": trial_end_date_iso,
        "is_on_trial": is_on_trial,
        "hide_from_leaderboard": profile.get('hide_from_leaderboard', False),
        "notification_settings": {
            "telegram_enabled": config.get("TELEGRAM_ENABLED", False),
            "discord_enabled": config.get("DISCORD_ENABLED", False),
            "webhook_enabled": config.get("WEBHOOK_ENABLED", False),
            "whatsapp_enabled": config.get("WHATSAPP_ENABLED", False)
        },
        "profile_details": { 
            "name": profile.get("name"), 
            "telegram_user": profile.get("telegram_user"), 
            "discord_user_id": profile.get("discord_user_id"), 
            "phone_number": profile.get("phone_number"), 
            "overseerr_access": profile.get("overseerr_access", False) 
        }
    })

@users_api_bp.route('/account/profile', methods=['POST'])
@login_required
@validate_json(UpdateAccountProfileSchema)
def update_account_profile(validated_data):
    # Compatibilidade com Pydantic v1 e v2
    data = validated_data.dict(exclude_unset=True) if hasattr(validated_data, 'dict') else validated_data.model_dump(exclude_unset=True)
    plex_user_id = int(current_user.id)
    profile = extensions.data_manager.get_user_profile(plex_user_id)
    profile.update(data)
    extensions.data_manager.set_user_profile(plex_user_id, profile)
    return jsonify({"success": True, "message": _("Perfil atualizado com sucesso.")})

@users_api_bp.route('/account/privacy', methods=['POST'])
@login_required
def update_privacy_settings():
    hide_setting = request.json.get('hide')
    if not isinstance(hide_setting, bool): 
        return jsonify({"success": False, "message": _("Valor inválido.")}), 400
    
    plex_user_id = int(current_user.id)
    profile = extensions.data_manager.get_user_profile(plex_user_id)
    profile['hide_from_leaderboard'] = hide_setting
    extensions.data_manager.set_user_profile(plex_user_id, profile)
    return jsonify({"success": True, "message": _("Configuração de privacidade atualizada com sucesso.")})

@users_api_bp.route('/account/requests')
@login_required
def get_account_requests():
    filter_status = request.args.get('filter', 'all', type=str)
    if filter_status not in ['all', 'approved', 'available', 'pending', 'processing', 'declined']: 
        filter_status = 'all'
    if not extensions.overseerr_manager.enabled: 
        return jsonify({"success": True, "requests": [], "overseerr_disabled": True})
    # Paginação: permite ao frontend oferecer "ver mais" em vez de ficar preso aos
    # primeiros 20 pedidos.
    limit = min(request.args.get('limit', 20, type=int), 50)
    skip = max(0, request.args.get('skip', 0, type=int))
    return jsonify(extensions.overseerr_manager.get_user_requests(
        current_user.email, limit=limit, filter=filter_status, skip=skip
    ))

@users_api_bp.route('/account/devices')
@login_required
def get_account_devices():
    return jsonify(extensions.tautulli_manager.get_user_devices(int(current_user.id)))

# ==========================================
# ROTAS ADMIN (GERENCIAMENTO)
# ==========================================

@users_api_bp.route('/status')
@login_required
@admin_required
def get_status():
    """Rota principal do Dashboard Administrativo. Sincroniza dados e retorna a lista de utilizadores."""
    if not extensions.plex_manager.conn.plex:
        return jsonify({"error": _("Plex não configurado.")}), 500

    force_refresh = request.args.get('force', 'false').lower() == 'true'
    all_plex_users_list = extensions.plex_manager.get_all_plex_users(force_refresh=force_refresh) or []
    
    config = load_or_create_config()
    all_users_to_return = _sync_plex_and_local_profiles(all_plex_users_list, config.get('ADMIN_USER'))

    return jsonify({
        'users': sorted(all_users_to_return, key=lambda u: u['username'].lower()),
        'libraries': extensions.plex_manager.conn.get_libraries(),
        'telegram_enabled': config.get("TELEGRAM_ENABLED", False)
    })

@users_api_bp.route('/list')
@login_required
@admin_required
def get_user_list():
    """Retorna lista simplificada para Dropdowns (apenas utilizadores com contactos)."""
    try:
        plex_users = extensions.plex_manager.get_all_plex_users() or []
        user_profiles = extensions.data_manager.get_all_user_profiles()
        profiles_map = {p['plex_user_id']: p for p in user_profiles}

        filtered_users = []
        for user in plex_users:
            profile = profiles_map.get(user['id'], {})
            if profile.get('telegram_user') or profile.get('discord_user_id') or profile.get('phone_number'):
                filtered_users.append({
                    'id': user['id'],
                    'username': user.get('username', user.get('title', 'N/A')),
                    'email': user.get('email', '')
                })

        return jsonify({"success": True, "users": sorted(filtered_users, key=lambda x: str(x['username']).lower())})
    except Exception as e:
        logger.error(f"Erro ao listar utilizadores para seleção: {e}")
        return jsonify({"success": False, "message": _("Erro interno ao obter lista.")}), 500

@users_api_bp.route('/profile/<int:plex_user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def user_profile_route(plex_user_id):
    """Consulta ou edita diretamente as informações de um utilizador específico (Admin)."""
    user_info = extensions.plex_manager.get_user_by_id(plex_user_id)
    if not user_info:
        return jsonify({"success": False, "message": _("Utilizador não encontrado no Plex.")}), 404

    username = user_info['username']

    if request.method == 'GET':
        profile = extensions.data_manager.get_user_profile(plex_user_id)
        config = load_or_create_config()
        
        is_on_trial = False
        if trial_end_date_iso := profile.get('trial_end_date'):
            try:
                if datetime.fromisoformat(trial_end_date_iso) > datetime.now(timezone.utc):
                    is_on_trial = True
            except (ValueError, TypeError): pass

        return jsonify({
            "success": True, "profile": profile, "is_on_trial": is_on_trial,
            "notification_settings": {
                "telegram_enabled": config.get("TELEGRAM_ENABLED", False), 
                "discord_enabled": config.get("DISCORD_ENABLED", False), 
                "webhook_enabled": config.get("WEBHOOK_ENABLED", False),
                "whatsapp_enabled": config.get("WHATSAPP_ENABLED", False)
            },
            "universal_expiration_settings": {
                "enabled": config.get("UNIVERSAL_EXPIRATION_ENABLED", False), 
                "time": config.get("UNIVERSAL_EXPIRATION_TIME", "23:59")
            }
        })

    # Tratamento de POST (Atualização de Perfil)
    try: 
        validated_data = UpdateProfileSchema(**request.json)
    except ValidationError as e: 
        return jsonify({"success": False, "message": _("Dados inválidos."), "errors": {err['loc'][0]: err['msg'] for err in e.errors()}}), 400

    # Compatibilidade Pydantic v1 / v2
    data = validated_data.dict(exclude_unset=True) if hasattr(validated_data, 'dict') else validated_data.model_dump(exclude_unset=True)
    local_datetime_str = data.pop('expiration_datetime_local', None)
    
    profile_to_update = extensions.data_manager.get_user_profile(plex_user_id)
    profile_to_update.update(data)

    _update_manual_expiration_job(plex_user_id, username, profile_to_update, local_datetime_str)
    extensions.data_manager.set_user_profile(plex_user_id, profile_to_update)
    _enforce_user_status_by_date(plex_user_id, username, profile_to_update)

    logger.info(f"Admin '{current_user.username}' atualizou o perfil de '{username}'.")
    return jsonify({"success": True, "message": _("Perfil do utilizador atualizado com sucesso.")})

@users_api_bp.route('/extend-trial/<int:plex_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
@validate_json(ExtendTrialSchema)
def extend_trial_route(user, validated_data):
    from ...extensions import scheduler
    from ...scheduler import end_trial_job

    plex_user_id = user['id']
    username = user['username']
    extend_minutes = validated_data.extend_minutes
    profile = extensions.data_manager.get_user_profile(plex_user_id)
    
    try:
        now_utc = datetime.now(timezone.utc)
        current_trial_end_utc = now_utc

        if trial_end_date_str := profile.get('trial_end_date'):
            try:
                current_trial_end_utc = datetime.fromisoformat(trial_end_date_str)
            except (ValueError, TypeError):
                current_trial_end_utc = now_utc

        base_time = max(current_trial_end_utc, now_utc)
        new_trial_end_utc = base_time + timedelta(minutes=extend_minutes)

        if old_job_id := profile.get('trial_job_id'):
            try: scheduler.remove_job(old_job_id)
            except JobLookupError: pass

        new_job_id = f"trial_end_{plex_user_id}_{secrets.token_hex(4)}"
        naive_run_date = new_trial_end_utc.astimezone(scheduler.timezone).replace(tzinfo=None)
        scheduler.add_job(id=new_job_id, func=end_trial_job, args=[plex_user_id], trigger='date', run_date=naive_run_date, replace_existing=True, misfire_grace_time=3600)

        profile['trial_end_date'] = new_trial_end_utc.isoformat()
        profile['trial_job_id'] = new_job_id
        
        # Limpa expiração regular
        if profile.get('expiration_date'):
             profile['expiration_date'] = None
        if old_exp_job := profile.get('expiration_job_id'):
            try: scheduler.remove_job(old_exp_job)
            except JobLookupError: pass
            profile['expiration_job_id'] = None

        extensions.data_manager.set_user_profile(plex_user_id, profile)

        blocked_info = extensions.data_manager.get_blocked_user(plex_user_id)
        if blocked_info and blocked_info.get('block_reason') in ['trial_expired', 'expired']:
            extensions.plex_manager.unblock_user(plex_user_id)

        logger.info(f"Admin '{current_user.username}' estendeu/iniciou o período de teste de '{username}' por {extend_minutes} minutos.")
        return jsonify({"success": True, "message": _("Período de teste estendido/definido. Fim a %(date)s.", date=naive_run_date.strftime('%d/%m/%Y %H:%M'))})
    except Exception as e:
        logger.error(f"Erro ao estender teste para '{username}': {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Ocorreu um erro interno ao estender o teste.")}), 500

@users_api_bp.route('/reactivate', methods=['POST'])
@login_required
@admin_required
def reactivate_user_route():
    plex_user_id = request.json.get('plex_user_id')
    libraries = request.json.get('libraries')

    if not plex_user_id or not libraries:
        return jsonify({"success": False, "message": _("Dados incompletos fornecidos.")}), 400

    profile = extensions.data_manager.get_user_profile(plex_user_id)
    if not profile or profile.get('status') != 'inactive':
        return jsonify({"success": False, "message": _("Apenas utilizadores inativos podem ser reativados.")}), 404

    username = profile.get('username')
    identifier = profile.get('email') or username
    
    if not identifier:
        return jsonify({"success": True, "message": _("Reativado localmente, mas sem email para enviar convite.")})

    try:
        logger.info(f"Admin '{current_user.username}' a iniciar reativação manual de '{username}'.")
        invite_result = extensions.plex_manager.invites.send_plex_invite(identifier, libraries, plex_user_id=plex_user_id)
        
        if not invite_result.get('success'):
            logger.error(f"Falha ao enviar convite de reativação para '{username}': {invite_result.get('message')}")
            return jsonify({"success": False, "message": invite_result.get('message', 'Erro ao convidar.')})

        extensions.data_manager.set_user_profile(plex_user_id, {'status': 'active', 'libraries': json.dumps(libraries)})
        extensions.data_manager.remove_blocked_user(plex_user_id)

        if extensions.socketio:
            extensions.socketio.emit('user_list_updated', {'message': _("O utilizador %(username)s foi reativado.", username=username)}, namespace='/dashboard')

        return jsonify({"success": True, "message": _("Utilizador reativado. Convite enviado com sucesso!")})

    except Exception as e:
        logger.error(f"Erro interno ao reativar {plex_user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Erro interno ao processar reativação.")}), 500

@users_api_bp.route('/renew/<int:plex_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
@validate_json(RenewSubscriptionSchema)
def renew_user_subscription_route(user, validated_data):
    try:
        data = validated_data
        
        logger.info(f"Admin '{current_user.username}' solicitou a renovação manual de '{user['username']}' (ID: {user['id']}) por {data.months} mês/meses.")

        new_expiration_date = extensions.plex_manager.renew_subscription(
            user['id'], data.months, base_mode=data.base,
            base_date_str=data.base_date, expiration_time_str=data.expiration_time
        )

        config = load_or_create_config()
        profile = extensions.data_manager.get_user_profile(user['id'])
        
        # --- LÓGICA DE FALLBACK INTELIGENTE DE PREÇO ---
        current_screens = profile.get('screen_limit', 0)
        screen_prices = config.get("SCREEN_PRICES", {})
        renewal_price = config.get("RENEWAL_PRICE")
        
        # 1. Tenta o preço exato do limite atual
        monthly_price_str = screen_prices.get(str(current_screens))
        
        # 2. Tenta o fallback geral
        if not monthly_price_str or not str(monthly_price_str).strip():
            monthly_price_str = renewal_price
            
        # 3. Fallback inteligente (procura o menor valor ativo)
        if not monthly_price_str or not str(monthly_price_str).strip():
            valid_prices = []
            for v in screen_prices.values():
                try:
                    if v and str(v).strip():
                        valid_prices.append(float(str(v).replace(',', '.')))
                except ValueError:
                    continue
            monthly_price_str = str(min(valid_prices)) if valid_prices else "0.00"

        # 4. Conversão à prova de falhas
        try:
            clean_price = str(monthly_price_str).replace(',', '.').strip()
            base_price = float(clean_price) if clean_price else 0.0
        except (ValueError, TypeError):
            base_price = 0.0
            
        total_value = base_price * data.months
        # --- FIM DA LÓGICA DE PREÇO ---

        with extensions.db.session.begin_nested():
            # Cria a entrada no histórico de pagamentos (financeiro)
            extensions.data_manager.add_manual_payment(
                user['id'], user['username'], total_value,
                f"Renovação Admin (+{data.months} mês/meses)", datetime.now(timezone.utc).isoformat()
            )
            # Cria a notificação para o Admin (sino vermelho)
            extensions.data_manager.create_notification(
                message=_("Renovação manual de %(username)s (%(value)s) registada.", username=user['username'], value=f"R$ {total_value:.2f}"),
                category='success', link=url_for('main.users_page')
            )
        extensions.db.session.commit()
        
        # Acende a luz do sino em tempo real
        if extensions.socketio:
            extensions.socketio.emit('new_notification', namespace='/')
            
        logger.info(f"Renovação manual processada. Nova data de expiração para '{user['username']}': {new_expiration_date.strftime('%Y-%m-%d')}. Valor registado: R$ {total_value:.2f}")
        
        try:
            extensions.plex_manager.notifier_manager.send_renewal_notification(user, new_expiration_date, profile)
            logger.info(f"Notificação de renovação enviada para '{user['username']}'.")
        except Exception as notify_error:
            logger.error(f"Falha ao enviar notificação de renovação para '{user['username']}': {notify_error}")

        return jsonify({"success": True, "message": _("Subscrição renovada. Novo vencimento em %(date)s.", date=new_expiration_date.strftime('%d/%m/%Y'))})
    except Exception as e:
        extensions.db.session.rollback()
        logger.error(f"Erro crítico durante a renovação manual de '{user['username']}': {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Ocorreu um erro interno ao processar a renovação.")}), 500

@users_api_bp.route('/delete-permanently', methods=['POST'])
@login_required
@admin_required
def delete_permanently_route():
    plex_user_id = request.json.get('plex_user_id')
    profile = extensions.data_manager.get_user_profile(plex_user_id)
    if not profile or profile.get('status') != 'inactive':
        return jsonify({"success": False, "message": _("Apenas utilizadores inativos podem ser apagados permanentemente.")}), 400

    try:
        username = profile.get('username', 'Desconhecido')
        extensions.data_manager.delete_user_profile(plex_user_id)
        logger.info(f"Admin '{current_user.username}' apagou permanentemente o utilizador '{username}' (ID: {plex_user_id}).")
        return jsonify({"success": True, "message": _("Utilizador apagado permanentemente.")})
    except Exception as e:
        logger.error(f"Erro ao apagar utilizador {plex_user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Erro interno ao apagar o utilizador.")}), 500

@users_api_bp.route('/notify/<int:plex_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def notify_user_route(user):
    profile = extensions.data_manager.get_user_profile(user['id'])
    if not profile.get('expiration_date'):
        return jsonify({"success": False, "message": _("Utilizador sem data de vencimento.")})
    
    local_tz = get_localzone()
    exp_date = datetime.fromisoformat(profile['expiration_date']).astimezone(local_tz).date()
    days_left = (exp_date - datetime.now(local_tz).date()).days
    extensions.plex_manager.notifier_manager.send_expiration_notification(user, days_left, profile)
    
    logger.info(f"Admin '{current_user.username}' disparou uma notificação manual de vencimento para '{user['username']}'.")
    return jsonify({"success": True, "message": _("Notificação de vencimento enviada.")})

@users_api_bp.route('/libraries/<int:plex_user_id>')
@login_required
@admin_required
@user_lookup_by_id
def get_user_libraries_route(user): return jsonify(extensions.plex_manager.get_user_libraries(user['id']))

@users_api_bp.route('/update-libraries', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def update_libraries_route(user): 
    libs = request.json.get('libraries', [])
    allow_sync = request.json.get('allow_sync')
    
    res = extensions.plex_manager.update_user_libraries(user['id'], libs, allow_sync=allow_sync)
    if res.get('success'): 
        logger.info(f"Admin '{current_user.username}' atualizou as bibliotecas e permissões de '{user['username']}'.")
    return jsonify(res)

@users_api_bp.route('/update-all-libraries', methods=['POST'])
@login_required
@admin_required
def update_all_libraries_route(): 
    logger.info(f"Admin '{current_user.username}' iniciou a atualização em massa de bibliotecas.")
    return jsonify(extensions.plex_manager.update_all_users_libraries(request.json.get('libraries')))

@users_api_bp.route('/remove', methods=['POST'])
@login_required
@admin_required
def remove_user_route(): 
    res = extensions.plex_manager.remove_user(request.json.get('plex_user_id'))
    if res.get('success'): logger.info(f"Admin '{current_user.username}' removeu/inativou um utilizador com sucesso.")
    return jsonify(res)

@users_api_bp.route('/block', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def block_user_route(user): 
    res = extensions.plex_manager.block_user(user['id'], reason='manual')
    if res.get('success'): logger.info(f"Admin '{current_user.username}' bloqueou manualmente '{user['username']}'.")
    return jsonify(res)

@users_api_bp.route('/unblock', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def unblock_user_route(user): 
    res = extensions.plex_manager.unblock_user(user['id'])
    if res.get('success'): logger.info(f"Admin '{current_user.username}' desbloqueou manualmente '{user['username']}'.")
    return jsonify(res)

@users_api_bp.route('/update-limit', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def update_limit_route(user):
    screens = request.json.get('screens', 0)
    profile = extensions.data_manager.get_user_profile(user['id'])
    profile['screen_limit'] = screens
    extensions.data_manager.set_user_profile(user['id'], profile)
    logger.info(f"Admin '{current_user.username}' alterou limite de telas de '{user['username']}' para {screens}.")
    return jsonify({"success": True, "message": _("Limite aplicado.")})

@users_api_bp.route('/update-all-limits', methods=['POST'])
@login_required
@admin_required
def update_all_limits_route():
    screens = max(0, request.json.get('screens', -1))
    all_users = extensions.plex_manager.get_all_plex_users() or []
    for user in all_users:
        if user['id'] != int(current_user.id):
            if profile := extensions.data_manager.get_user_profile(user['id']):
                profile['screen_limit'] = screens
                extensions.data_manager.set_user_profile(user['id'], profile)
    logger.info(f"Admin '{current_user.username}' aplicou limite global de {screens} telas para todos.")
    return jsonify({"success": True, "message": _("Limites atualizados para todos.")})

@users_api_bp.route('/toggle-overseerr', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def toggle_overseerr_access_route(user): 
    access = request.json.get('access', False)
    res = extensions.plex_manager.toggle_overseerr_access(user['id'], access)
    if res.get('success'): logger.info(f"Admin '{current_user.username}' alterou acesso Overseerr de '{user['username']}' para {access}.")
    return jsonify(res)

@users_api_bp.route('/payments/<int:plex_user_id>')
@login_required
def get_user_payments_history(plex_user_id):
    if not current_user.is_admin and int(current_user.id) != plex_user_id:
        return jsonify({"success": False, "message": _("Acesso não autorizado.")}), 403
    return jsonify({"success": True, "payments": extensions.data_manager.get_payments_by_user(plex_user_id)})

# ==========================================
# FUNÇÕES AUXILIARES (HELPERS PRIVADOS)
# ==========================================

def _get_expiration_details(profile, config):
    expiration_info = {"date": None, "days_left": None, "status": "active"}
    if exp_str := profile.get('expiration_date'):
        try:
            local_tz = get_localzone()
            exp_dt_aware = datetime.fromisoformat(exp_str)
            exp_dt_local = exp_dt_aware.astimezone(local_tz)
            expiration_info["date"] = format_date(exp_dt_local.date(), 'd \'de\' MMMM \'de\' yyyy')
            
            now_local = datetime.now(local_tz)
            if exp_dt_local < now_local:
                expiration_info["status"] = "expired"
            else:
                days_left = (exp_dt_local.date() - now_local.date()).days
                expiration_info["days_left"] = days_left
                if days_left < int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7)):
                    expiration_info["status"] = "expiring"
        except (ValueError, TypeError): 
            pass
    return expiration_info

def _sync_plex_and_local_profiles(all_plex_users_list, admin_username):
    plex_user_details = {u['id']: u for u in all_plex_users_list}
    plex_user_ids = set(plex_user_details.keys())
    all_profiles_from_db = extensions.data_manager.get_all_user_profiles()
    blocked_users_data = extensions.data_manager.get_blocked_users_dict()
    local_profile_ids = {p.get('plex_user_id') for p in all_profiles_from_db}

    all_users_to_return = []
    profiles_to_create = []

    for plex_id, plex_data in plex_user_details.items():
        if plex_id not in local_profile_ids and plex_data['username'] != admin_username:
            new_profile_data = {
                'plex_user_id': plex_id, 'username': plex_data['username'], 'email': plex_data.get('email'),
                'screen_limit': 0, 'status': 'active', 'hide_from_leaderboard': False, 'overseerr_access': False
            }
            profiles_to_create.append(new_profile_data)
            all_users_to_return.append({
                'id': plex_id, 'username': plex_data['username'], 'name': None, 'email': plex_data.get('email'),
                'thumb': plex_data.get('thumb'), 'is_blocked': False, 'status': 'active', 'screen_limit': 0,
                'expiration_date': None, 'trial_end_date': None, 'is_on_trial': False, 'payment_token': None
            })

    if profiles_to_create:
        for new_profile in profiles_to_create:
            extensions.data_manager.set_user_profile(new_profile['plex_user_id'], new_profile)

    for profile in all_profiles_from_db:
        plex_user_id = profile.get('plex_user_id')
        username = profile.get('username')

        if not plex_user_id or username == admin_username or plex_user_id not in plex_user_ids:
            if profile.get('status') == 'active':
                 profile['status'] = 'inactive'
                 extensions.data_manager.set_user_profile(plex_user_id, {'status': 'inactive'})
            if username != admin_username:
                 all_users_to_return.append({
                     'id': plex_user_id, 'username': username, 'name': profile.get('name'), 
                     'email': profile.get('email'), 'thumb': None, 'is_blocked': plex_user_id in blocked_users_data,
                     'status': 'inactive', 'screen_limit': profile.get('screen_limit', 0),
                     'expiration_date': profile.get('expiration_date'), 'trial_end_date': profile.get('trial_end_date'),
                     'is_on_trial': False, 'payment_token': profile.get('payment_token')
                 })
            continue

        plex_data = plex_user_details.get(plex_user_id, {})
        if profile.get('username') != plex_data.get('username'):
              profile['username'] = plex_data.get('username')
              extensions.data_manager.set_user_profile(plex_user_id, {'username': plex_data.get('username')})
              username = plex_data.get('username')

        is_blocked = plex_user_id in blocked_users_data
        final_status = profile.get('status', 'inactive')
        
        if final_status == 'inactive' and not is_blocked:
              final_status = 'active'
              extensions.data_manager.set_user_profile(plex_user_id, {'status': 'active'})

        is_on_trial = False
        if trial_end_date_str := profile.get('trial_end_date'):
            try:
                if datetime.fromisoformat(trial_end_date_str) > datetime.now(timezone.utc):
                    is_on_trial = True
            except (ValueError, TypeError): pass

        user_data = {
            'id': plex_user_id, 'username': username, 'name': profile.get('name'), 
            'email': plex_data.get('email', profile.get('email')), 'thumb': plex_data.get('thumb'),
            'is_blocked': is_blocked, 'status': final_status, 'screen_limit': profile.get('screen_limit', 0),
            'expiration_date': profile.get('expiration_date'), 'trial_end_date': profile.get('trial_end_date'),
            'is_on_trial': is_on_trial, 'payment_token': profile.get('payment_token')
        }
        
        existing_index = next((i for i, u in enumerate(all_users_to_return) if u['id'] == plex_user_id), -1)
        if existing_index != -1:
            all_users_to_return[existing_index] = user_data
        else:
            all_users_to_return.append(user_data)

    return all_users_to_return

def _update_manual_expiration_job(plex_user_id, username, profile_to_update, local_datetime_str):
    from ...extensions import scheduler
    from ...scheduler import end_subscription_job

    if local_datetime_str:
        if profile_to_update.get('trial_end_date'):
            profile_to_update['trial_end_date'] = None
        if profile_to_update.get('trial_job_id'):
            try: scheduler.remove_job(profile_to_update['trial_job_id'])
            except JobLookupError: pass
            profile_to_update['trial_job_id'] = None

    if not local_datetime_str:
        profile_to_update['expiration_date'] = None
        if old_job_id := profile_to_update.pop('expiration_job_id', None):
            try: scheduler.remove_job(old_job_id)
            except JobLookupError: pass
    else:
        naive_dt = datetime.fromisoformat(local_datetime_str)
        config = load_or_create_config()
        if config.get("UNIVERSAL_EXPIRATION_ENABLED"):
            try:
                time_parts = list(map(int, config.get("UNIVERSAL_EXPIRATION_TIME", "23:59").split(':')))
                naive_dt = naive_dt.replace(hour=time_parts[0], minute=time_parts[1], second=0, microsecond=0)
            except (ValueError, IndexError): pass

        if old_job_id := profile_to_update.pop('expiration_job_id', None):
            try: scheduler.remove_job(old_job_id)
            except JobLookupError: pass

        new_job_id = f"sub_end_{plex_user_id}_{secrets.token_hex(4)}"
        scheduler.add_job(id=new_job_id, func=end_subscription_job, args=[plex_user_id], trigger='date', run_date=naive_dt, misfire_grace_time=3600)

        profile_to_update['expiration_date'] = naive_dt.astimezone(timezone.utc).isoformat()
        profile_to_update['expiration_job_id'] = new_job_id

        # 🐛 CORREÇÃO: ao definir a data de vencimento MANUALMENTE, é preciso mover
        # também a âncora do dia de faturação. Caso contrário, a âncora antiga
        # continuava a mandar nas renovações seguintes e "puxava" o vencimento de
        # volta para o dia antigo — por exemplo, definir 05/09 e, ao renovar, obter
        # 26/10 em vez de 05/10, porque o billing_day ainda era 26.
        # Uma data definida à mão pelo administrador é uma decisão explícita e deve
        # passar a ser a nova referência.
        profile_to_update['billing_day'] = naive_dt.day

def _enforce_user_status_by_date(plex_user_id, username, profile_to_update):
    is_blocked = extensions.data_manager.get_blocked_user(plex_user_id) is not None
    now_utc = datetime.now(timezone.utc)
    new_status = 'active'

    if exp_date_str := profile_to_update.get('expiration_date'):
        exp_date_utc = datetime.fromisoformat(exp_date_str).astimezone(timezone.utc)
        if exp_date_utc <= now_utc: new_status = 'expired'
    elif trial_end_str := profile_to_update.get('trial_end_date'):
        trial_end_utc = datetime.fromisoformat(trial_end_str).astimezone(timezone.utc)
        if trial_end_utc <= now_utc: new_status = 'trial_expired'

    if new_status != 'active':
        current_block_info = extensions.data_manager.get_blocked_user(plex_user_id)
        if not current_block_info or current_block_info.get('block_reason') != new_status:
            extensions.plex_manager.block_user(plex_user_id, reason=new_status)
    elif is_blocked:
        block_reason = extensions.data_manager.get_blocked_user(plex_user_id).get('block_reason')
        if block_reason in ['expired', 'trial_expired']:
            extensions.plex_manager.unblock_user(plex_user_id)


# ==========================================
# SISTEMA DE REFERÊNCIA ("INDIQUE E GANHE")
# ==========================================

@users_api_bp.route('/referral/me', methods=['GET'])
@login_required
def get_my_referral_info():
    """
    Devolve o código, o link e as estatísticas de indicação do utilizador
    autenticado. O código é gerado na primeira vez que esta rota é chamada.
    """
    config = load_or_create_config()
    if not config.get("REFERRAL_ENABLED", False):
        return jsonify({"success": True, "enabled": False})

    try:
        plex_user_id = int(current_user.id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": _("Utilizador inválido.")}), 400

    code = extensions.referral_manager.get_or_create_code(plex_user_id)
    stats = extensions.referral_manager.get_referral_stats(plex_user_id)

    # O link aponta para a página pública de convite, com o código anexado.
    base_url = (config.get("APP_BASE_URL") or request.host_url).rstrip('/')
    stats['link'] = f"{base_url}/r/{code}" if code else None

    return jsonify({"success": True, **stats})


@users_api_bp.route('/referral/claim', methods=['POST'])
@login_required
def claim_referral_code():
    """
    Regista que o utilizador autenticado foi indicado por alguém. Só tem efeito
    uma vez por utilizador; a recompensa só é paga quando ele efetuar a primeira
    compra (ver ReferralManager.reward_referrer_on_payment).
    """
    data = request.json or {}
    code = str(data.get('code', '')).strip()

    try:
        plex_user_id = int(current_user.id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": _("Utilizador inválido.")}), 400

    result = extensions.referral_manager.register_referral(plex_user_id, code)
    return jsonify(result), (200 if result.get('success') else 400)
