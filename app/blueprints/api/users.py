# app/blueprints/api/users.py

import logging
import secrets
from datetime import datetime
from flask import Blueprint, jsonify, request, url_for
from flask_login import current_user
from flask_babel import gettext as _, format_date
from tzlocal import get_localzone
from apscheduler.jobstores.base import JobLookupError

from ...extensions import plex_manager, tautulli_manager, data_manager
from ...config import load_or_create_config
from ..auth import admin_required, login_required
from .decorators import user_lookup

logger = logging.getLogger(__name__)
users_api_bp = Blueprint('users_api', __name__)

@users_api_bp.route('/status')
@login_required
@admin_required
def get_status():
    if not plex_manager.plex: return jsonify({"error": _("Plex não configurado.")}), 500
    all_users = plex_manager.get_all_plex_users(force_refresh=request.args.get('force', 'false').lower() == 'true')
    blocked_users_data = data_manager.get_blocked_users()
    all_user_profiles = {profile['username']: profile for profile in data_manager.get_all_user_profiles()}
    blocked_users = [user['username'] for user in blocked_users_data]
    users_with_access = []
    for u in all_users:
        if u.get('servers'):
            profile = all_user_profiles.get(u['username'], {})
            user_data = {
                'username': u['username'], 'email': u['email'], 'thumb': u['thumb'],
                'is_blocked': u['username'] in blocked_users,
                'screen_limit': profile.get('screen_limit', 0),
                'expiration_date': profile.get('expiration_date'),
                'trial_end_date': profile.get('trial_end_date')
            }
            users_with_access.append(user_data)
    return jsonify({'users': sorted(users_with_access, key=lambda u: u['username'].lower()), 'libraries': plex_manager.get_libraries()})

@users_api_bp.route('/account/details')
@login_required
def get_account_details():
    config = load_or_create_config()
    username = current_user.username
    email = current_user.email
    profile = data_manager.get_user_profile(username)
    expiration_date_str = profile.get('expiration_date')
    blocked_users_data = data_manager.get_blocked_users()
    blocked_user_info = next((u for u in blocked_users_data if u['username'] == username), None)
    is_blocked = blocked_user_info is not None
    block_reason = blocked_user_info.get('block_reason') if is_blocked else None
    expiration_info = { "date": None, "days_left": None, "status": "active" }
    if expiration_date_str:
        try:
            expiration_datetime = datetime.fromisoformat(expiration_date_str)
            local_tz = get_localzone()
            if expiration_datetime.tzinfo is None: expiration_datetime = expiration_datetime.replace(tzinfo=local_tz)
            expiration_info["date"] = format_date(expiration_datetime, 'd \'de\' MMMM \'de\' yyyy')
            now_local = datetime.now(local_tz)
            if expiration_datetime < now_local: expiration_info["status"] = "expired"
            else:
                days_left = (expiration_datetime.date() - now_local.date()).days
                expiration_info["days_left"] = days_left
                days_to_notify = int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7))
                if days_left < days_to_notify: expiration_info["status"] = "expiring"
        except (ValueError, TypeError) as e: logger.error(f"Erro ao processar data de vencimento para {username}: {e}")
    join_date_str = data_manager.get_user_claim_date(username)
    join_date = ""
    if join_date_str:
        try:
            join_date_obj = datetime.fromisoformat(join_date_str)
            join_date = format_date(join_date_obj, 'd \'de\' MMMM \'de\' yyyy')
        except (ValueError, TypeError):
            try:
                join_date_obj = datetime.strptime(join_date_str, '%Y-%m-%dT%H:%M:%S')
                join_date = format_date(join_date_obj, 'd \'de\' MMMM \'de\' yyyy')
            except ValueError: pass
    libraries_data = plex_manager.get_user_libraries(email)
    watch_data = tautulli_manager.get_user_watch_details(username)
    screen_limit = profile.get('screen_limit', 0)
    details = {
        "success": True, "username": username, "email": email, "thumb": current_user.thumb,
        "join_date": join_date or _("Não disponível"),
        "screen_limit": _("%(num)s Tela(s)", num=screen_limit) if screen_limit > 0 else _("Ilimitado"),
        "libraries": libraries_data.get('libraries', []),
        "watch_stats": watch_data.get('details', {}),
        "expiration_info": expiration_info, "is_blocked": is_blocked, "block_reason": block_reason,
        "trial_end_date": profile.get('trial_end_date'),
        "hide_from_leaderboard": profile.get('hide_from_leaderboard', False)
    }
    return jsonify(details)

@users_api_bp.route('/account/privacy', methods=['POST'])
@login_required
def update_privacy_settings():
    data = request.get_json()
    hide_setting = data.get('hide')
    if not isinstance(hide_setting, bool):
        return jsonify({"success": False, "message": _("Valor inválido para a configuração de privacidade.")}), 400
    username = current_user.username
    profile = data_manager.get_user_profile(username)
    profile['hide_from_leaderboard'] = hide_setting
    data_manager.set_user_profile(username, profile)
    logger.info(f"Utilizador '{username}' atualizou a sua preferência de privacidade para: {'Oculto' if hide_setting else 'Visível'}")
    return jsonify({"success": True, "message": _("Configuração de privacidade atualizada com sucesso.")})

@users_api_bp.route('/renew/<username>', methods=['POST'])
@login_required
@admin_required
@user_lookup
def renew_user_subscription_route(user):
    data = request.json
    months_to_add = data.get('months', 1)
    base_mode = data.get('base', 'today')
    base_date_str = data.get('base_date')
    expiration_time_str = data.get('expiration_time')
    username = user['username']
    if not isinstance(months_to_add, int) or months_to_add <= 0:
        return jsonify({"success": False, "message": _("Número de meses inválido.")}), 400
    new_expiration_date = plex_manager.renew_subscription(username, months_to_add, base_mode, base_date_str=base_date_str, expiration_time_str=expiration_time_str)
    try:
        config = load_or_create_config()
        user_profile = data_manager.get_user_profile(username)
        user_screen_limit = user_profile.get('screen_limit', 0)
        monthly_price_str = config.get("RENEWAL_PRICE", "0.00")
        if user_screen_limit > 0:
            screen_prices = config.get("SCREEN_PRICES", {})
            monthly_price_str = screen_prices.get(str(user_screen_limit), monthly_price_str)
        total_value = float(monthly_price_str) * months_to_add
        data_manager.add_manual_payment(username=username, value=total_value, description=f"Renovação Admin (+{months_to_add} mês/meses)", payment_date_str=datetime.now().isoformat())
        logger.info(f"Registo financeiro automático de R${total_value:.2f} criado para a renovação de '{username}'.")
        data_manager.create_notification(message=f"Renovação manual para {username} por {months_to_add} mês(es) registada.", category='info', link=url_for('main.users_page'))
    except Exception as e:
        logger.error(f"Falha ao criar registo financeiro automático para a renovação de '{username}': {e}")
    profile = data_manager.get_user_profile(username)
    try:
        plex_manager.notifier_manager.send_renewal_notification(user, new_expiration_date, profile)
    except Exception as e:
        logger.error(f"Falha ao enviar notificação de renovação para {username}: {e}")
    return jsonify({"success": True, "message": _("Subscrição renovada com sucesso. Novo vencimento em %(date)s.", date=new_expiration_date.strftime('%d/%m/%Y'))})

@users_api_bp.route('/profile/<username>', methods=['GET', 'POST'])
@login_required
@admin_required
def user_profile_route(username):
    if request.method == 'GET':
        profile = data_manager.get_user_profile(username)
        return jsonify({"success": True, "profile": profile})
    
    # POST
    from ...extensions import scheduler
    from ...scheduler import end_subscription_job
    
    data = request.json
    local_datetime_str = data.pop('expiration_datetime_local', None)
    
    profile_to_update = data_manager.get_user_profile(username)
    profile_to_update.update(data)
    
    if not local_datetime_str:
        logger.info(f"Removendo data de vencimento para o utilizador '{username}'.")
        profile_to_update['expiration_date'] = None
        old_job_id = profile_to_update.pop('expiration_job_id', None)
        if old_job_id:
            try: scheduler.remove_job(old_job_id)
            except JobLookupError: logger.warning(f"Tentativa de remover a tarefa de bloqueio '{old_job_id}', mas ela não foi encontrada no agendador.")
    else:
        try:
            naive_dt = datetime.fromisoformat(local_datetime_str)
            old_job_id = profile_to_update.pop('expiration_job_id', None)
            if old_job_id:
                try: scheduler.remove_job(old_job_id)
                except JobLookupError: pass
            
            new_job_id = f"sub_end_{username}_{secrets.token_hex(4)}"
            scheduler.add_job(id=new_job_id, func=end_subscription_job, args=[username], trigger='date', run_date=naive_dt, misfire_grace_time=3600)
            
            local_tz = get_localzone()
            local_dt = naive_dt.replace(tzinfo=local_tz)
            profile_to_update['expiration_date'] = local_dt.isoformat()
            profile_to_update['expiration_job_id'] = new_job_id
            logger.info(f"Tarefa de bloqueio para '{username}' reagendada para {local_dt} com ID '{new_job_id}'.")
        except (ValueError, TypeError) as e:
            logger.error(f"Erro ao processar a data/hora de vencimento para '{username}': {e}")
            return jsonify({"success": False, "message": _("Formato de data ou hora inválido.")}), 400

    data_manager.set_user_profile(username, profile_to_update)

    final_expiration_date_str = profile_to_update.get('expiration_date')
    if final_expiration_date_str:
        final_expiration_date = datetime.fromisoformat(final_expiration_date_str)
        now_local = datetime.now().astimezone(get_localzone())
        user_info = next((u for u in plex_manager.get_all_plex_users() if u['username'] == username), None)
        if user_info:
            is_currently_blocked = data_manager.get_blocked_users(username=username) is not None
            if now_local > final_expiration_date:
                tautulli_manager.manage_block_unblock(user_info['email'], username, 'add', reason='expired')
            elif is_currently_blocked:
                tautulli_manager.manage_block_unblock(user_info['email'], username, 'remove')
    
    return jsonify({"success": True, "message": _("Perfil do utilizador atualizado com sucesso.")})


@users_api_bp.route('/notify/<username>', methods=['POST'])
@login_required
@admin_required
@user_lookup
def notify_user_route(user):
    username = user['username']
    profile = data_manager.get_user_profile(username)
    expiration_date_str = profile.get('expiration_date')
    if not expiration_date_str: return jsonify({"success": False, "message": _("Este utilizador não tem uma data de vencimento definida.")})
    try:
        expiration_date = datetime.fromisoformat(expiration_date_str).date()
        days_left = (expiration_date - datetime.now().date()).days
        if days_left < 0: return jsonify({"success": False, "message": _("A data de vencimento deste utilizador já passou.")})
        plex_manager.notifier_manager.send_expiration_notification(user, days_left, profile)
        return jsonify({"success": True, "message": _("Notificação de vencimento enviada para %(username)s.", username=username)})
    except (ValueError, TypeError) as e:
        logger.error(f"Erro ao processar notificação manual para {username}: {e}")
        return jsonify({"success": False, "message": _("Formato de data de vencimento inválido.")})

@users_api_bp.route('/libraries/<string:email>')
@login_required
@admin_required
@user_lookup
def get_user_libraries_route(user): 
    return jsonify(plex_manager.get_user_libraries(user['email']))

@users_api_bp.route('/update-libraries', methods=['POST'])
@login_required
@admin_required
@user_lookup
def update_libraries_route(user):
    data = request.json
    return jsonify(plex_manager.update_user_libraries(user['email'], data.get('libraries', [])))

@users_api_bp.route('/remove', methods=['POST'])
@login_required
@admin_required
@user_lookup
def remove_user_route(user):
    return jsonify(plex_manager.remove_user(user['email']))

@users_api_bp.route('/block', methods=['POST'])
@login_required
@admin_required
@user_lookup
def block_user_route(user):
    return jsonify(tautulli_manager.manage_block_unblock(user['email'], user['username'], 'add', reason='manual'))

@users_api_bp.route('/unblock', methods=['POST'])
@login_required
@admin_required
@user_lookup
def unblock_user_route(user):
    return jsonify(tautulli_manager.manage_block_unblock(user['email'], user['username'], 'remove'))

@users_api_bp.route('/update-limit', methods=['POST'])
@login_required
@admin_required
@user_lookup
def update_limit_route(user):
    data = request.json
    return jsonify(tautulli_manager.update_screen_limit(user['email'], user['username'], data.get('screens', 0)))

@users_api_bp.route('/update-all-limits', methods=['POST'])
@login_required
@admin_required
def update_all_limits_route():
    users = [u for u in plex_manager.get_all_plex_users() if u.get('servers')]
    return jsonify(tautulli_manager.update_all_users_screen_limit(users, request.json.get('screens', -1)))

@users_api_bp.route('/toggle-overseerr', methods=['POST'])
@login_required
@admin_required
@user_lookup
def toggle_overseerr_access_route(user):
    data = request.json
    access = data.get('access', False)
    result = plex_manager.toggle_overseerr_access(user['email'], user['username'], access)
    return jsonify(result)

@users_api_bp.route('/list')
@login_required
@admin_required
def get_user_list():
    try:
        users = plex_manager.get_all_plex_users()
        user_list = [{'username': u['username'], 'email': u['email']} for u in users if u.get('servers')]
        return jsonify({"success": True, "users": sorted(user_list, key=lambda u: u['username'].lower())})
    except Exception as e:
        logger.error(f"Erro ao obter a lista de utilizadores: {e}")
        return jsonify({"success": False, "message": "Falha ao obter lista de utilizadores."}), 500

@users_api_bp.route('/payments/<username>')
@login_required
def get_user_payments_history(username):
    """Endpoint para obter o histórico de pagamentos de um utilizador."""
    # Apenas o admin ou o próprio utilizador podem ver o histórico
    if not current_user.is_admin() and current_user.username != username:
        return jsonify({"success": False, "message": _("Acesso não autorizado.")}), 403
    
    try:
        payments = data_manager.get_payments_by_user(username)
        return jsonify({"success": True, "payments": payments})
    except Exception as e:
        logger.error(f"Erro ao obter o histórico de pagamentos para {username}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao obter histórico de pagamentos."}), 500

@users_api_bp.route('/account/devices')
@login_required
def get_account_devices():
    """Endpoint para obter os dispositivos conectados do utilizador."""
    try:
        devices_data = tautulli_manager.get_user_devices(current_user.username)
        return jsonify(devices_data)
    except Exception as e:
        logger.error(f"Erro ao obter dispositivos para {current_user.username}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao obter lista de dispositivos."}), 500

