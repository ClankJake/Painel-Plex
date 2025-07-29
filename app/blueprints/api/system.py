# app/blueprints/api/system.py

import logging
from flask import Blueprint, jsonify, request, current_app, session, url_for
from flask_login import login_user, current_user
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone_name

from ...extensions import plex_manager, tautulli_manager, efi_manager, mercado_pago_manager, overseerr_manager, scheduler
from ...config import load_or_create_config, save_app_config, is_configured
from ...models import User
from ..auth import admin_required, login_required

logger = logging.getLogger(__name__)
system_api_bp = Blueprint('system_api', __name__)

@system_api_bp.route('/logs')
@login_required
@admin_required
def get_logs():
    try:
        log_file = current_app.config.get('LOG_FILE', 'app.log')
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            return jsonify({"success": True, "logs": "".join(lines[-500:])})
    except FileNotFoundError:
        return jsonify({"success": True, "logs": _("O ficheiro de log ainda não foi criado.")})
    except Exception as e:
        logger.error(f"Erro ao ler o ficheiro de log: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@system_api_bp.route('/logs/clear', methods=['POST'])
@login_required
@admin_required
def clear_logs():
    try:
        log_file = current_app.config.get('LOG_FILE', 'app.log')
        with open(log_file, 'w') as f:
            pass
        logger.info(f"O ficheiro de log '{log_file}' foi limpo pelo utilizador '{current_user.username}'.")
        return jsonify({"success": True, "message": _("Ficheiro de log limpo com sucesso.")})
    except Exception as e:
        logger.error(f"Erro ao limpar o ficheiro de log: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@system_api_bp.route('/dashboard-summary')
@login_required
@admin_required
def get_dashboard_summary():
    from ...extensions import data_manager
    from datetime import datetime
    try:
        active_streams_data = plex_manager.get_active_sessions()
        active_streams = active_streams_data.get('stream_count', 0)
            
        all_users = plex_manager.get_all_plex_users()
        total_users = len(all_users) if all_users else 0
        blocked_users_list = data_manager.get_blocked_users()
        blocked_users = len(blocked_users_list)
        active_users = total_users - blocked_users

        now = datetime.now()
        financial_summary = data_manager.get_financial_summary(now.year, now.month, renewal_days=7)
        
        summary_data = {
            "active_streams": active_streams,
            "total_users": total_users,
            "active_users": active_users,
            "blocked_users": blocked_users,
            "monthly_revenue": financial_summary.get('total_revenue', 0),
            "upcoming_renewals": len(financial_summary.get('upcoming_expirations', [])),
            "daily_revenue": financial_summary.get('daily_revenue', {})
        }
        
        return jsonify({"success": True, "summary": summary_data})
    except Exception as e:
        logger.error(f"Erro ao obter o resumo do dashboard: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao obter dados do dashboard."}), 500

@system_api_bp.route('/system-health')
@login_required
@admin_required
def get_system_health():
    """Verifica e retorna o estado de todos os serviços integrados."""
    health_status = {
        "plex": plex_manager.check_status(),
        "tautulli": tautulli_manager.check_status(),
        "efi": efi_manager.check_status(),
        "mercado_pago": mercado_pago_manager.check_status(),
        "scheduler": {
            "status": "RUNNING" if scheduler.running else "STOPPED",
            "message": _("Agendador em execução.") if scheduler.running else _("Agendador parado.")
        }
    }
    return jsonify({"success": True, "health": health_status})

@system_api_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def api_settings():
    if request.method == 'POST':
        old_config = load_or_create_config()
        config_to_update = load_or_create_config()
        new_data = request.json
        fields_to_update = [
            'APP_TITLE', 'APP_HOST', 'APP_PORT', 'LOG_LEVEL', 'DAYS_TO_REMOVE_BLOCKED_USER',
            'EXPIRATION_NOTIFICATION_TIME', 'BLOCK_REMOVAL_TIME', 'WEBHOOK_URL', 'WEBHOOK_ENABLED', 
            'WEBHOOK_AUTHORIZATION_HEADER', 'WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE', 'WEBHOOK_RENEWAL_MESSAGE_TEMPLATE',
            'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_ENABLED', 'TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE',
            'TELEGRAM_RENEWAL_MESSAGE_TEMPLATE', 'DAYS_TO_NOTIFY_EXPIRATION', 'APP_BASE_URL', 
            'TAUTULLI_URL', 'TAUTULLI_API_KEY', 'BLOCKING_NOTIFIER_ID', 'SCREEN_LIMIT_NOTIFIER_ID',
            'EFI_CLIENT_ID', 'EFI_CLIENT_SECRET', 'EFI_CERTIFICATE', 'EFI_SANDBOX', 'EFI_PIX_KEY', 
            'MERCADOPAGO_ACCESS_TOKEN', 'RENEWAL_PRICE', 'EFI_ENABLED', 'MERCADOPAGO_ENABLED',
            'TRIAL_BLOCK_NOTIFIER_ID', 'TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE', 'WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE',
            'OVERSEERR_ENABLED', 'OVERSEERR_URL', 'OVERSEERR_API_KEY',
            'CLEANUP_PENDING_PAYMENTS_ENABLED', 'CLEANUP_PENDING_PAYMENTS_DAYS', 'CLEANUP_TIME',
            'ENABLE_LINK_SHORTENER', 'PAYMENT_LINK_GRACE_PERIOD_DAYS',
            # Novas chaves de gamificação
            'ACHIEVEMENT_MOVIE_MARATHON_BRONZE', 'ACHIEVEMENT_MOVIE_MARATHON_SILVER', 'ACHIEVEMENT_MOVIE_MARATHON_GOLD',
            'ACHIEVEMENT_SERIES_BINGER_BRONZE', 'ACHIEVEMENT_SERIES_BINGER_SILVER', 'ACHIEVEMENT_SERIES_BINGER_GOLD',
            'ACHIEVEMENT_TIME_TRAVELER_BRONZE', 'ACHIEVEMENT_TIME_TRAVELER_SILVER', 'ACHIEVEMENT_TIME_TRAVELER_GOLD',
            'ACHIEVEMENT_DIRECTOR_FAN_BRONZE', 'ACHIEVEMENT_DIRECTOR_FAN_SILVER', 'ACHIEVEMENT_DIRECTOR_FAN_GOLD'
        ]
        numeric_fields = [
            'DAYS_TO_REMOVE_BLOCKED_USER', 'DAYS_TO_NOTIFY_EXPIRATION', 'APP_PORT', 
            'BLOCKING_NOTIFIER_ID', 'SCREEN_LIMIT_NOTIFIER_ID', 'CLEANUP_PENDING_PAYMENTS_DAYS',
            'PAYMENT_LINK_GRACE_PERIOD_DAYS',
            # Novas chaves numéricas de gamificação
            'ACHIEVEMENT_MOVIE_MARATHON_BRONZE', 'ACHIEVEMENT_MOVIE_MARATHON_SILVER', 'ACHIEVEMENT_MOVIE_MARATHON_GOLD',
            'ACHIEVEMENT_SERIES_BINGER_BRONZE', 'ACHIEVEMENT_SERIES_BINGER_SILVER', 'ACHIEVEMENT_SERIES_BINGER_GOLD',
            'ACHIEVEMENT_TIME_TRAVELER_BRONZE', 'ACHIEVEMENT_TIME_TRAVELER_SILVER', 'ACHIEVEMENT_TIME_TRAVELER_GOLD',
            'ACHIEVEMENT_DIRECTOR_FAN_BRONZE', 'ACHIEVEMENT_DIRECTOR_FAN_SILVER', 'ACHIEVEMENT_DIRECTOR_FAN_GOLD'
        ]
        
        if 'SCREEN_PRICES' in new_data:
            config_to_update['SCREEN_PRICES'] = new_data['SCREEN_PRICES']
        for field in fields_to_update:
            if field in new_data:
                value = new_data[field]
                if field in numeric_fields: config_to_update[field] = int(value) if value else 0
                elif isinstance(value, bool): config_to_update[field] = value
                else: config_to_update[field] = value
        if new_data.get('plex_token') and new_data.get('plex_url'):
            config_to_update['PLEX_TOKEN'] = new_data['plex_token']
            config_to_update['PLEX_URL'] = new_data['plex_url']
        save_app_config(config_to_update)
        app = current_app._get_current_object()
        app.config.update(config_to_update)

        efi_manager.reload_credentials()
        mercado_pago_manager.reload_credentials()
        tautulli_manager.reload_credentials()
        overseerr_manager.reload_config()

        log_level_map = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING, 'ERROR': logging.ERROR}
        new_log_level = config_to_update.get('LOG_LEVEL', 'INFO')
        if new_log_level != old_config.get('LOG_LEVEL'):
            logging.getLogger().setLevel(log_level_map.get(new_log_level, logging.INFO))
            app.logger.setLevel(log_level_map.get(new_log_level, logging.INFO))
            logger.info(f"Nível de log atualizado para {new_log_level}")
        
        def reschedule_job(job_id, time_key, old_config, new_config):
            new_time = new_config.get(time_key)
            if new_time and new_time != old_config.get(time_key):
                try:
                    hour, minute = map(int, new_time.split(':')[:2])
                    try:
                        tz_str = get_localzone_name()
                    except Exception:
                        tz_str = 'UTC'
                    scheduler.reschedule_job(job_id, trigger=CronTrigger(hour=hour, minute=minute, timezone=tz_str))
                    logger.info(f"Tarefa '{job_id}' reagendada para as {hour:02d}:{minute:02d}.")
                except Exception as e:
                    logger.error(f"Falha ao reagendar a tarefa '{job_id}': {e}", exc_info=True)

        reschedule_job('expiration_notification_job', 'EXPIRATION_NOTIFICATION_TIME', old_config, config_to_update)
        reschedule_job('removal_job', 'BLOCK_REMOVAL_TIME', old_config, config_to_update)
        reschedule_job('cleanup_job', 'CLEANUP_TIME', old_config, config_to_update)

        success, message = plex_manager.reload_connections()
        if config_to_update.get('APP_PORT') != old_config.get('APP_PORT') or config_to_update.get('APP_HOST') != old_config.get('APP_HOST'):
            message += " " + _("As alterações de Host/Porta requerem um reinício da aplicação para terem efeito.")
        return jsonify({"success": success, "message": message})
    
    config_to_send = load_or_create_config()
    for key in ['SECRET_KEY', 'PLEX_TOKEN', 'INTERNAL_TRIGGER_KEY']: config_to_send.pop(key, None)
    return jsonify(config_to_send)

# ... (restante do código do ficheiro sem alterações)
@system_api_bp.route('/setup/servers')
def get_plex_servers():
    token = session.get('plex_token')
    if not token: return jsonify({"success": False, "message": _("Token do Plex não encontrado. Autentique-se novamente.")}), 401
    try:
        account = MyPlexAccount(token=token)
        resources = account.resources()
        servers = []
        for r in resources:
            if r.product == 'Plex Media Server' and r.owned:
                connections = sorted(r.connections, key=lambda c: (c.local, not c.uri.startswith('https')), reverse=False)
                if connections: servers.append({ "name": r.name, "uri": connections[0].uri })
        if not servers: return jsonify({"success": True, "servers": [], "message": _("Nenhum servidor encontrado na sua conta Plex.")})
        return jsonify({"success": True, "servers": servers, "token": token, "username": account.username})
    except Exception as e:
        logger.error(f"Erro ao buscar servidores Plex: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
@system_api_bp.route('/setup/save', methods=['POST'])
def save_setup():
    data = request.json
    config = load_or_create_config()
    normalized_data = {}
    for key, value in data.items():
        upper_key = key.upper()
        if upper_key.endswith('_ID') or upper_key == 'DAYS_TO_REMOVE_BLOCKED_USER' or upper_key == 'DAYS_TO_NOTIFY_EXPIRATION':
            try: value = int(value)
            except (ValueError, TypeError): value = 0
        normalized_data[upper_key] = value
    config.update(normalized_data)
    config['IS_CONFIGURED'] = True
    save_app_config(config)
    tautulli_manager.reload_credentials()
    overseerr_manager.reload_config()
    success, message = plex_manager.reload_connections()
    if success:
        user_details = {'id': config.get('ADMIN_USER'), 'username': config.get('ADMIN_USER'), 'role': 'admin'}
        user = User(**user_details)
        login_user(user)
        session['user_details'] = user_details
        session.pop('plex_token', None)
        session.pop('plex_username', None)
        return jsonify({"success": True, "redirect_url": url_for('main.index')})
    config['IS_CONFIGURED'] = False
    save_app_config(config)
    return jsonify({"success": False, "message": _("Configuração salva, mas falha ao conectar: %(message)s", message=message)})
@system_api_bp.route('/test/tautulli-connection', methods=['POST'])
def test_tautulli_connection():
    if is_configured() and not (current_user.is_authenticated and current_user.is_admin()):
        return jsonify({'success': False, 'message': _('Acesso não autorizado.')}), 403
    data = request.get_json()
    url = data.get('url')
    api_key = data.get('api_key')
    if not url or not api_key: return jsonify({'success': False, 'message': _('URL e Chave da API são obrigatórios.')}), 400
    return jsonify(tautulli_manager.test_connection(url, api_key))
@system_api_bp.route('/test/overseerr-connection', methods=['POST'])
def test_overseerr_connection():
    if is_configured() and not (current_user.is_authenticated and current_user.is_admin()):
        return jsonify({'success': False, 'message': _('Acesso não autorizado.')}), 403
    data = request.get_json()
    url = data.get('url')
    api_key = data.get('api_key')
    return jsonify(overseerr_manager.test_connection(url, api_key))
@system_api_bp.route('/tautulli/auto-configure', methods=['POST'])
def auto_configure_tautulli_notifier():
    if is_configured() and not (current_user.is_authenticated and current_user.is_admin()):
        return jsonify({'success': False, 'message': _('Acesso não autorizado.')}), 403
    data = request.get_json()
    notifier_id = data.get('notifier_id')
    notifier_type = data.get('notifier_type')
    if not notifier_id or not notifier_type:
        return jsonify({"success": False, "message": _("ID e tipo do notificador são obrigatórios.")}), 400
    if not is_configured():
        tautulli_url = data.get('url')
        tautulli_api_key = data.get('api_key')
    else:
        config = load_or_create_config()
        tautulli_url = config.get("TAUTULLI_URL")
        tautulli_api_key = config.get("TAUTULLI_API_KEY")
    if not tautulli_url or not tautulli_api_key:
        return jsonify({"success": False, "message": _("URL e Chave API do Tautulli não estão preenchidos.")}), 400
    result = tautulli_manager.set_notifier_conditions(tautulli_url, tautulli_api_key, notifier_id, notifier_type)
    return jsonify(result)
