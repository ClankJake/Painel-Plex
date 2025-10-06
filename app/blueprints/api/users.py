# app/blueprints/api/users.py

import logging
import secrets
from datetime import date, datetime, timezone
from flask import Blueprint, jsonify, request, url_for, current_app
from flask_login import current_user
from flask_babel import gettext as _, format_date
from tzlocal import get_localzone
from apscheduler.jobstores.base import JobLookupError
from pydantic import ValidationError

from ...extensions import plex_manager, data_manager, tautulli_manager, overseerr_manager, db
from ...config import load_or_create_config
from ..auth import admin_required, login_required
from .decorators import user_lookup_by_id, validate_json
from .schemas import RenewSubscriptionSchema, UpdateProfileSchema, UpdateAccountProfileSchema
from ...models import UserProfile

logger = logging.getLogger(__name__)
users_api_bp = Blueprint('users_api', __name__)

@users_api_bp.route('/public-profile-by-token/<string:token>')
def get_public_user_profile_by_token(token):
    profile = UserProfile.query.filter_by(payment_token=token).first()
    if not profile:
        return jsonify({"success": False, "message": _("Link de pagamento inválido ou usuário não encontrado.")}), 404

    user = plex_manager.get_user_by_id(profile.plex_user_id)
    if not user:
        return jsonify({"success": False, "message": _("Usuário não encontrado.")}), 404
    
    expiration_date_formatted = None
    if profile.expiration_date:
        try:
            exp_date = datetime.fromisoformat(profile.expiration_date)
            expiration_date_formatted = exp_date.strftime('%d/%m/%Y')
        except (ValueError, TypeError): pass

    public_data = {
        "username": user['username'], "thumb": user['thumb'],
        "expiration_date_formatted": expiration_date_formatted, "expiration_date_iso": profile.expiration_date
    }
    return jsonify({"success": True, "profile": public_data})

@users_api_bp.route('/status')
@login_required
@admin_required
def get_status():
    if not plex_manager.conn.plex: return jsonify({"error": _("Plex não configurado.")}), 500
    
    all_users = plex_manager.get_all_plex_users(force_refresh=request.args.get('force', 'false').lower() == 'true')
    if all_users is None:
        return jsonify({'error': _("Não foi possível obter os utilizadores do Plex. Verifique a ligação e as configurações.")}), 500

    config = load_or_create_config()
    admin_username = config.get('ADMIN_USER')
    users_to_display = [user for user in all_users if user['username'] != admin_username]
    
    plex_user_ids = [u['id'] for u in users_to_display]
    all_user_profiles = data_manager.get_user_profiles_by_id(plex_user_ids)
    blocked_users_data = data_manager.get_blocked_users_dict()
    
    users_with_access = []
    for u in users_to_display:
        profile = all_user_profiles.get(u['id'], {})
        
        is_on_trial = False
        if trial_end_date_str := profile.get('trial_end_date'):
            try:
                if datetime.fromisoformat(trial_end_date_str) > datetime.now(timezone.utc):
                    is_on_trial = True
            except (ValueError, TypeError): pass

        user_data = {
            'id': u['id'], 'username': u['username'], 'email': u['email'], 'thumb': u['thumb'],
            'is_blocked': u['id'] in blocked_users_data,
            'screen_limit': profile.get('screen_limit', 0),
            'expiration_date': profile.get('expiration_date'),
            'trial_end_date': trial_end_date_str, 'is_on_trial': is_on_trial,
            'payment_token': profile.get('payment_token')
        }
        users_with_access.append(user_data)
        
    return jsonify({'users': sorted(users_with_access, key=lambda u: u['username'].lower()), 'libraries': plex_manager.conn.get_libraries()})

@users_api_bp.route('/account/details')
@login_required
def get_account_details():
    config = load_or_create_config()
    plex_user_id = int(current_user.id)
    profile = data_manager.get_user_profile(plex_user_id)
    
    is_blocked_info = data_manager.get_blocked_user(plex_user_id)
    is_blocked = is_blocked_info is not None
    block_reason = is_blocked_info.get('block_reason') if is_blocked_info else None
    
    expiration_info = {"date": None, "days_left": None, "status": "active"}
    if exp_str := profile.get('expiration_date'):
        try:
            exp_dt_aware = datetime.fromisoformat(exp_str)
            exp_dt_local = exp_dt_aware.astimezone(get_localzone())
            
            # CORREÇÃO: Usa .date() para formatar apenas a data, ignorando a hora e o fuso horário,
            # o que previne o problema da data ser empurrada para o dia seguinte.
            expiration_info["date"] = format_date(exp_dt_local.date(), 'd \'de\' MMMM \'de\' yyyy')
            
            now_local = datetime.now(get_localzone())

            if exp_dt_local < now_local:
                expiration_info["status"] = "expired"
            else:
                days_left = (exp_dt_local.date() - now_local.date()).days
                expiration_info["days_left"] = days_left
                if days_left < int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7)):
                    expiration_info["status"] = "expiring"
        except (ValueError, TypeError):
            pass

    join_date = _("Não disponível")
    if join_date_str := data_manager.get_user_claim_date(plex_user_id):
        try: join_date = format_date(datetime.fromisoformat(join_date_str), 'd \'de\' MMMM \'de\' yyyy')
        except (ValueError, TypeError): pass
    
    libraries_data = plex_manager.get_user_libraries(plex_user_id)
    watch_data = tautulli_manager.get_user_watch_details(plex_user_id=plex_user_id, current_user=current_user)
    
    notification_settings = {
        "telegram_enabled": config.get("TELEGRAM_ENABLED", False),
        "discord_enabled": config.get("DISCORD_ENABLED", False),
        "webhook_enabled": config.get("WEBHOOK_ENABLED", False)
    }

    details = {
        "success": True, "username": current_user.username, "email": current_user.email, "thumb": current_user.thumb,
        "join_date": join_date, "screen_limit": _("%(num)d Tela(s)", num=profile.get('screen_limit', 0)) if profile.get('screen_limit', 0) > 0 else _("Ilimitado"),
        "libraries": libraries_data.get('libraries', []), "watch_stats": watch_data.get('details', {}),
        "expiration_info": expiration_info, "is_blocked": is_blocked, "block_reason": block_reason,
        "trial_end_date": profile.get('trial_end_date'), "hide_from_leaderboard": profile.get('hide_from_leaderboard', False),
        "notification_settings": notification_settings,
        "profile_details": { "name": profile.get("name"), "telegram_user": profile.get("telegram_user"), "discord_user_id": profile.get("discord_user_id"), "phone_number": profile.get("phone_number"), "overseerr_access": profile.get("overseerr_access", False) }
    }
    return jsonify(details)

@users_api_bp.route('/account/profile', methods=['POST'])
@login_required
@validate_json(UpdateAccountProfileSchema)
def update_account_profile(validated_data):
    data = validated_data.dict(exclude_unset=True)
    plex_user_id = int(current_user.id)
    profile = data_manager.get_user_profile(plex_user_id)
    profile.update(data)
    data_manager.set_user_profile(plex_user_id, profile)
    logger.info(f"Utilizador '{current_user.username}' atualizou o seu próprio perfil.")
    return jsonify({"success": True, "message": _("Perfil atualizado com sucesso.")})

@users_api_bp.route('/account/privacy', methods=['POST'])
@login_required
def update_privacy_settings():
    hide_setting = request.json.get('hide')
    if not isinstance(hide_setting, bool): return jsonify({"success": False, "message": _("Valor inválido.")}), 400
    plex_user_id = int(current_user.id)
    profile = data_manager.get_user_profile(plex_user_id)
    profile['hide_from_leaderboard'] = hide_setting
    data_manager.set_user_profile(plex_user_id, profile)
    logger.info(f"Utilizador '{current_user.username}' atualizou as suas configurações de privacidade para {'oculto' if hide_setting else 'visível'}.")
    return jsonify({"success": True, "message": _("Configuração de privacidade atualizada com sucesso.")})

@users_api_bp.route('/account/requests')
@login_required
def get_account_requests():
    filter_status = request.args.get('filter', 'all', type=str)
    if filter_status not in ['all', 'approved', 'available', 'pending', 'processing', 'declined']: filter_status = 'all'
    if not overseerr_manager.enabled: return jsonify({"success": True, "requests": [], "overseerr_disabled": True})
    return jsonify(overseerr_manager.get_user_requests(current_user.email, limit=20, filter=filter_status))

@users_api_bp.route('/renew/<int:plex_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
@validate_json(RenewSubscriptionSchema)
def renew_user_subscription_route(user, validated_data):
    try:
        data = validated_data
        new_expiration_date = plex_manager.renew_subscription(user['id'], data.months, data.base, base_date_str=data.base_date, expiration_time_str=data.expiration_time)
        
        config, profile = load_or_create_config(), data_manager.get_user_profile(user['id'])
        monthly_price_str = config.get("SCREEN_PRICES", {}).get(str(profile.get('screen_limit', 0)), config.get("RENEWAL_PRICE", "0.00"))
        total_value = float(monthly_price_str) * data.months
        
        with db.session.begin_nested():
            data_manager.add_manual_payment(user['id'], user['username'], total_value, f"Renovação Admin (+{data.months} mês/meses)", datetime.now().isoformat())
            data_manager.create_notification(
                message=_("Renovação manual de %(username)s (%(value)s) registada.", username=user['username'], value=f"R$ {total_value:.2f}"),
                category='success',
                link=url_for('main.users_page')
            )
        db.session.commit()

        plex_manager.notifier_manager.send_renewal_notification(user, new_expiration_date, profile)
        logger.info(f"Admin '{current_user.username}' renovou a subscrição de '{user['username']}' por {data.months} mes(es).")
        return jsonify({"success": True, "message": _("Subscrição renovada. Novo vencimento em %(date)s.", date=new_expiration_date.strftime('%d/%m/%Y'))})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao processar renovação manual para '{user['username']}': {e}", exc_info=True)
        return jsonify({"success": False, "message": "Ocorreu um erro interno ao processar a renovação."}), 500


@users_api_bp.route('/profile/<int:plex_user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def user_profile_route(plex_user_id):
    user_info = plex_manager.get_user_by_id(plex_user_id)
    if not user_info:
        return jsonify({"success": False, "message": "Utilizador não encontrado."}), 404

    username = user_info['username']

    if request.method == 'GET':
        profile = data_manager.get_user_profile(plex_user_id)
        config = load_or_create_config()
        return jsonify({ "success": True, "profile": profile, 
                         "notification_settings": {"telegram_enabled": config.get("TELEGRAM_ENABLED", False), "discord_enabled": config.get("DISCORD_ENABLED", False), "webhook_enabled": config.get("WEBHOOK_ENABLED", False)},
                         "universal_expiration_settings": {"enabled": config.get("UNIVERSAL_EXPIRATION_ENABLED", False), "time": config.get("UNIVERSAL_EXPIRATION_TIME", "23:59")} })

    if request.method == 'POST':
        from ...extensions import scheduler
        from ...scheduler import end_subscription_job
        
        try: validated_data = UpdateProfileSchema(**request.json)
        except ValidationError as e: return jsonify({"success": False, "message": "Dados inválidos.", "errors": {err['loc'][0]: err['msg'] for err in e.errors()}}), 400

        data = validated_data.dict(exclude_unset=True)
        local_datetime_str = data.pop('expiration_datetime_local', None)
        
        profile_to_update = data_manager.get_user_profile(plex_user_id)
        profile_to_update.update(data)
        
        if not local_datetime_str:
            profile_to_update['expiration_date'] = None
            if old_job_id := profile_to_update.pop('expiration_job_id', None):
                try:
                    scheduler.remove_job(old_job_id)
                    logger.info(f"Tarefa de bloqueio para '{username}' removida (ID: {old_job_id}).")
                except JobLookupError:
                    logger.warning(f"Não foi possível encontrar a tarefa de bloqueio '{old_job_id}' para remover para o utilizador '{username}'.")
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
            
            profile_to_update['expiration_date'] = naive_dt.astimezone(get_localzone()).isoformat()
            profile_to_update['expiration_job_id'] = new_job_id
            logger.info(f"Tarefa de bloqueio para '{username}' reagendada para {naive_dt.strftime('%Y-%m-%d %H:%M:%S')} com ID '{new_job_id}'.")

        data_manager.set_user_profile(plex_user_id, profile_to_update)
        logger.info(f"Admin '{current_user.username}' atualizou o perfil de '{username}'.")
        
        is_blocked = data_manager.get_blocked_user(plex_user_id) is not None
        now_utc = datetime.now(timezone.utc)
        
        if exp_date_str := profile_to_update.get('expiration_date'):
            exp_date_utc = datetime.fromisoformat(exp_date_str).astimezone(timezone.utc)
            if exp_date_utc > now_utc:
                if is_blocked: plex_manager.unblock_user(plex_user_id)
            elif not is_blocked: plex_manager.block_user(plex_user_id, reason='expired')
        elif trial_end_str := profile_to_update.get('trial_end_date'):
            trial_end_utc = datetime.fromisoformat(trial_end_str).astimezone(timezone.utc)
            if trial_end_utc < now_utc:
                if not is_blocked: plex_manager.block_user(plex_user_id, reason='trial_expired')
            elif is_blocked: plex_manager.unblock_user(plex_user_id)
        elif is_blocked: plex_manager.unblock_user(plex_user_id)
        
        return jsonify({"success": True, "message": _("Perfil do utilizador atualizado com sucesso.")})

@users_api_bp.route('/notify/<int:plex_user_id>', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def notify_user_route(user):
    profile = data_manager.get_user_profile(user['id'])
    expiration_date_str = profile.get('expiration_date')

    if not expiration_date_str:
        return jsonify({"success": False, "message": _("Este utilizador não tem uma data de vencimento definida.")})
    
    try:
        # CORREÇÃO: Calcula os dias restantes antes de chamar a função de notificação.
        exp_date = datetime.fromisoformat(expiration_date_str).date()
        days_left = (exp_date - date.today()).days
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": _("Formato de data de expiração inválido no perfil do utilizador.")})

    # CORREÇÃO: Passa o argumento 'days_left' que estava em falta.
    plex_manager.notifier_manager.send_expiration_notification(user, days_left, profile)
    
    logger.info(f"Admin '{current_user.username}' enviou uma notificação manual para '{user['username']}'.")
    return jsonify({"success": True, "message": _("Notificação de vencimento enviada para %(username)s.", username=user['username'])})


@users_api_bp.route('/libraries/<int:plex_user_id>')
@login_required
@admin_required
@user_lookup_by_id
def get_user_libraries_route(user):
    return jsonify(plex_manager.get_user_libraries(user['id']))

@users_api_bp.route('/update-libraries', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def update_libraries_route(user):
    result = plex_manager.update_user_libraries(user['id'], request.json.get('libraries', []))
    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' atualizou as bibliotecas de '{user['username']}'.")
    return jsonify(result)

@users_api_bp.route('/update-all-libraries', methods=['POST'])
@login_required
@admin_required
def update_all_libraries_route():
    result = plex_manager.update_all_users_libraries(request.json.get('libraries'))
    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' iniciou a atualização de bibliotecas para todos os utilizadores.")
    return jsonify(result)

@users_api_bp.route('/remove', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def remove_user_route(user):
    result = plex_manager.remove_user(user['id'])
    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' removeu o utilizador '{user['username']}'.")
    return jsonify(result)

@users_api_bp.route('/block', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def block_user_route(user):
    result = plex_manager.block_user(user['id'], reason='manual')
    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' bloqueou o utilizador '{user['username']}'.")
    return jsonify(result)

@users_api_bp.route('/unblock', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def unblock_user_route(user):
    result = plex_manager.unblock_user(user['id'])
    if result.get('success'):
        logger.info(f"Admin '{current_user.username}' desbloqueou o utilizador '{user['username']}'.")
    return jsonify(result)

@users_api_bp.route('/update-limit', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def update_limit_route(user):
    screens = request.json.get('screens', 0)
    profile = data_manager.get_user_profile(user['id'])
    profile['screen_limit'] = screens
    data_manager.set_user_profile(user['id'], profile)
    logger.info(f"Admin '{current_user.username}' atualizou o limite de telas de '{user['username']}' para {screens}.")
    return jsonify({"success": True, "message": _("Limite de %(screens)d tela(s) aplicado.", screens=screens) if screens > 0 else _("Limite removido.")})

@users_api_bp.route('/update-all-limits', methods=['POST'])
@login_required
@admin_required
def update_all_limits_route():
    screens = request.json.get('screens', -1)
    final_limit = 0 if screens < 0 else screens
    all_users = plex_manager.get_all_plex_users()
    if all_users:
        for user in all_users:
            profile = data_manager.get_user_profile(user['id'])
            profile['screen_limit'] = final_limit
            data_manager.set_user_profile(user['id'], profile)
    logger.info(f"Admin '{current_user.username}' atualizou o limite de telas para todos os utilizadores para {final_limit}.")
    return jsonify({"success": True, "message": _("Limite de %(screens)d tela(s) aplicado para todos.", screens=final_limit) if final_limit > 0 else _("Limites removidos de todos.")})

@users_api_bp.route('/toggle-overseerr', methods=['POST'])
@login_required
@admin_required
@user_lookup_by_id
def toggle_overseerr_access_route(user):
    access = request.json.get('access', False)
    result = plex_manager.toggle_overseerr_access(user['id'], access)
    if result.get('success'):
        action = 'concedeu' if access else 'removeu'
        logger.info(f"Admin '{current_user.username}' {action} o acesso ao Overseerr para '{user['username']}'.")
    return jsonify(result)

@users_api_bp.route('/list')
@login_required
@admin_required
def get_user_list():
    try:
        all_users = plex_manager.get_all_plex_users()
        if all_users is None:
            return jsonify({"success": False, "message": "Falha ao obter lista de utilizadores do Plex."}), 500
        users = [{'id': u['id'], 'username': u['username'], 'email': u['email']} for u in all_users]
        return jsonify({"success": True, "users": sorted(users, key=lambda u: u['username'].lower())})
    except Exception: 
        return jsonify({"success": False, "message": "Falha ao obter lista de utilizadores."}), 500

@users_api_bp.route('/payments/<int:plex_user_id>')
@login_required
def get_user_payments_history(plex_user_id):
    if not current_user.is_admin and int(current_user.id) != plex_user_id:
        return jsonify({"success": False, "message": _("Acesso não autorizado.")}), 403
    return jsonify({"success": True, "payments": data_manager.get_payments_by_user(plex_user_id)})

@users_api_bp.route('/account/devices')
@login_required
def get_account_devices():
    return jsonify(tautulli_manager.get_user_devices(int(current_user.id)))
