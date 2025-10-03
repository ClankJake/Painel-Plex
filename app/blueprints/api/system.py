# app/blueprints/api/system.py

import logging
from flask import Blueprint, jsonify, request, current_app, session, url_for
from flask_login import login_user, current_user
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone_name
from datetime import datetime

from ...extensions import plex_manager, tautulli_manager, efi_manager, mercado_pago_manager, bpix_manager, overseerr_manager, scheduler, data_manager
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
        blocked_users_list = data_manager.get_blocked_users_list()
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

@system_api_bp.route('/active-streams')
@login_required
@admin_required
def get_active_streams():
    """Retorna detalhes das sessões ativas."""
    try:
        sessions_data = plex_manager.get_active_sessions()
        return jsonify(sessions_data)
    except Exception as e:
        logger.error(f"Erro ao obter streams ativos: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao obter streams ativos."}), 500

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
        "bpix": bpix_manager.check_status(),
        "scheduler": {
            "status": "RUNNING" if scheduler.running else "STOPPED",
            "message": _("Agendador em execução.") if scheduler.running else _("Agendador parado.")
        }
    }
    return jsonify({"success": True, "health": health_status})

@system_api_bp.route('/termination-logs')
@login_required
@admin_required
def get_termination_logs():
    """NOVO: Endpoint para obter os logs de términos de sessões."""
    try:
        logs = data_manager.get_stream_termination_logs(limit=20)
        # CORREÇÃO: Formata o timestamp para um formato ISO 8601 consistente
        for log in logs:
            if isinstance(log.get('timestamp'), datetime):
                log['timestamp'] = log['timestamp'].strftime('%Y-%m-%dT%H:%M:%S')
        return jsonify({"success": True, "logs": logs})
    except Exception as e:
        logger.error(f"Erro ao obter logs de término: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao obter logs."}), 500

@system_api_bp.route('/termination-logs/<int:log_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_termination_log(log_id):
    """Endpoint para apagar um log de término específico."""
    try:
        if data_manager.delete_stream_termination_log(log_id):
            return jsonify({"success": True, "message": _("Log apagado com sucesso.")})
        else:
            return jsonify({"success": False, "message": _("Log não encontrado.")}), 404
    except Exception as e:
        logger.error(f"Erro ao apagar o log de término {log_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao apagar o log."}), 500

@system_api_bp.route('/termination-logs/clear-all', methods=['POST'])
@login_required
@admin_required
def clear_all_termination_logs():
    """Endpoint para limpar todos os logs de término."""
    try:
        deleted_count = data_manager.clear_all_stream_termination_logs()
        return jsonify({"success": True, "message": _("%(count)d logs foram apagados com sucesso.", count=deleted_count)})
    except Exception as e:
        logger.error(f"Erro ao limpar todos os logs de término: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao limpar os logs."}), 500

@system_api_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def api_settings():
    if request.method == 'POST':
        old_config = load_or_create_config()
        config_to_update = load_or_create_config()
        new_data = request.json
        fields_to_update = [
            'APP_TITLE', 'LOG_LEVEL', 'DAYS_TO_REMOVE_BLOCKED_USER',
            'EXPIRATION_NOTIFICATION_TIME', 'BLOCK_REMOVAL_TIME', 'WEBHOOK_URL', 'WEBHOOK_ENABLED', 
            'WEBHOOK_AUTHORIZATION_HEADER', 'WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE', 'WEBHOOK_RENEWAL_MESSAGE_TEMPLATE',
            'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_ENABLED', 'TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE',
            'TELEGRAM_RENEWAL_MESSAGE_TEMPLATE', 'DAYS_TO_NOTIFY_EXPIRATION', 'APP_BASE_URL', 
            'TAUTULLI_URL', 'TAUTULLI_API_KEY',
            'EFI_CLIENT_ID', 'EFI_CLIENT_SECRET', 'EFI_CERTIFICATE', 'EFI_SANDBOX', 'EFI_PIX_KEY',
            'EFI_USE_MTLS', 'EFI_WEBHOOK_HMAC_SECRET',
            'MERCADOPAGO_ACCESS_TOKEN', 'RENEWAL_PRICE', 'EFI_ENABLED', 'MERCADOPAGO_ENABLED',
            'BPIX_ENABLED', 'BPIX_AUTH_TOKEN',
            'TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE', 'WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE',
            'OVERSEERR_ENABLED', 'OVERSEERR_URL', 'OVERSEERR_API_KEY',
            'CLEANUP_PENDING_PAYMENTS_ENABLED', 'CLEANUP_PENDING_PAYMENTS_DAYS', 'CLEANUP_TIME',
            'IMAGE_CACHE_CLEANUP_ENABLED', 'IMAGE_CACHE_MAX_AGE_DAYS', 'IMAGE_CACHE_CLEANUP_TIME',
            'ENABLE_LINK_SHORTENER', 'PAYMENT_LINK_GRACE_PERIOD_DAYS',
            'ACHIEVEMENT_MOVIE_MARATHON_BRONZE', 'ACHIEVEMENT_MOVIE_MARATHON_SILVER', 'ACHIEVEMENT_MOVIE_MARATHON_GOLD',
            'ACHIEVEMENT_SERIES_BINGER_BRONZE', 'ACHIEVEMENT_SERIES_BINGER_SILVER', 'ACHIEVEMENT_SERIES_BINGER_GOLD',
            'ACHIEVEMENT_TIME_TRAVELER_BRONZE', 'ACHIEVEMENT_TIME_TRAVELER_SILVER', 'ACHIEVEMENT_TIME_TRAVELER_GOLD',
            'ACHIEVEMENT_DIRECTOR_FAN_BRONZE', 'ACHIEVEMENT_DIRECTOR_FAN_SILVER', 'ACHIEVEMENT_DIRECTOR_FAN_GOLD',
            'TELEGRAM_BULK_MESSAGE_TEMPLATE', 'DISCORD_BULK_MESSAGE_TEMPLATE', 'WEBHOOK_BULK_MESSAGE_TEMPLATE',
            'UNIVERSAL_EXPIRATION_ENABLED', 'UNIVERSAL_EXPIRATION_TIME',
            # Novas chaves de configuração
            'STREAM_CHECK_INTERVAL_SECONDS', 'TERMINATION_MSG_BLOCKED_MANUAL', 'TERMINATION_MSG_BLOCKED_EXPIRED',
            'TERMINATION_MSG_BLOCKED_TRIAL_EXPIRED', 'TERMINATION_MSG_SCREEN_LIMIT'
        ]
        numeric_fields = [
            'DAYS_TO_REMOVE_BLOCKED_USER', 'DAYS_TO_NOTIFY_EXPIRATION', 
            'CLEANUP_PENDING_PAYMENTS_DAYS', 'IMAGE_CACHE_MAX_AGE_DAYS',
            'PAYMENT_LINK_GRACE_PERIOD_DAYS',
            'ACHIEVEMENT_MOVIE_MARATHON_BRONZE', 'ACHIEVEMENT_MOVIE_MARATHON_SILVER', 'ACHIEVEMENT_MOVIE_MARATHON_GOLD',
            'ACHIEVEMENT_SERIES_BINGER_BRONZE', 'ACHIEVEMENT_SERIES_BINGER_SILVER', 'ACHIEVEMENT_SERIES_BINGER_GOLD',
            'ACHIEVEMENT_TIME_TRAVELER_BRONZE', 'ACHIEVEMENT_TIME_TRAVELER_SILVER', 'ACHIEVEMENT_TIME_TRAVELER_GOLD',
            'ACHIEVEMENT_DIRECTOR_FAN_BRONZE', 'ACHIEVEMENT_DIRECTOR_FAN_SILVER', 'ACHIEVEMENT_DIRECTOR_FAN_GOLD',
            'STREAM_CHECK_INTERVAL_SECONDS'
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

        if config_to_update.get("EFI_ENABLED"):
            efi_manager.configure_webhook()

        log_level_map = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING, 'ERROR': logging.ERROR}
        new_log_level = config_to_update.get('LOG_LEVEL', 'INFO')
        if new_log_level != old_config.get('LOG_LEVEL'):
            logging.getLogger().setLevel(log_level_map.get(new_log_level, logging.INFO))
            app.logger.setLevel(log_level_map.get(new_log_level, logging.INFO))
            logger.info(f"Nível de log atualizado para {new_log_level}")
        
        def reschedule_job(job_id, config_key, old_config, new_config, trigger_type='cron'):
            new_value = new_config.get(config_key)
            if new_value and new_value != old_config.get(config_key):
                try:
                    if trigger_type == 'cron':
                        hour, minute = map(int, new_value.split(':')[:2])
                        try:
                            tz_str = get_localzone_name()
                        except Exception:
                            tz_str = 'UTC'
                        scheduler.reschedule_job(job_id, trigger=CronTrigger(hour=hour, minute=minute, timezone=tz_str))
                        logger.info(f"Tarefa '{job_id}' reagendada para as {hour:02d}:{minute:02d}.")
                    elif trigger_type == 'interval':
                        seconds = int(new_value)
                        scheduler.reschedule_job(job_id, trigger='interval', seconds=seconds)
                        logger.info(f"Tarefa '{job_id}' reagendada para um intervalo de {seconds} segundos.")
                except Exception as e:
                    logger.error(f"Falha ao reagendar a tarefa '{job_id}': {e}", exc_info=True)

        reschedule_job('expiration_notification_job', 'EXPIRATION_NOTIFICATION_TIME', old_config, config_to_update)
        reschedule_job('removal_job', 'BLOCK_REMOVAL_TIME', old_config, config_to_update)
        reschedule_job('cleanup_job', 'CLEANUP_TIME', old_config, config_to_update)
        reschedule_job('cleanup_image_cache_job', 'IMAGE_CACHE_CLEANUP_TIME', old_config, config_to_update)
        reschedule_job('stream_check_job', 'STREAM_CHECK_INTERVAL_SECONDS', old_config, config_to_update, trigger_type='interval')


        success, message = plex_manager.reload_connections()
        return jsonify({"success": success, "message": message})
    
    # GET Request Logic
    config_to_send = load_or_create_config()

    # Define secret keys that should not be sent to the client
    sensitive_keys = [
        'SECRET_KEY', 'PLEX_TOKEN', 'INTERNAL_TRIGGER_KEY',
        'TELEGRAM_BOT_TOKEN', 'TAUTULLI_API_KEY', 'EFI_CLIENT_SECRET',
        'MERCADOPAGO_ACCESS_TOKEN', 'BPIX_AUTH_TOKEN', 'OVERSEERR_API_KEY'
    ]

    # Process sensitive keys to return only their length if they are set
    for key in sensitive_keys:
        if key in config_to_send and config_to_send[key]:
            # Replace the secret with an object containing its length
            config_to_send[key] = {
                "is_set": True,
                "length": len(config_to_send[key])
            }
        else:
            # Ensure the key exists but is marked as not set
            if key in config_to_send:
                config_to_send[key] = { "is_set": False, "length": 0 }

    # Pop keys that should absolutely never be sent, even as length
    config_to_send.pop('SECRET_KEY', None)
    config_to_send.pop('INTERNAL_TRIGGER_KEY', None)
    
    return jsonify(config_to_send)

@system_api_bp.route('/setup/servers')
def get_plex_servers():
    token = session.get('plex_token')
    if not token:
        return jsonify({"success": False, "message": _("Token do Plex não encontrado. Autentique-se novamente.")}), 401
    try:
        account = MyPlexAccount(token=token)
        resources = account.resources()
        servers = []
        for r in resources:
            if r.product == 'Plex Media Server' and r.owned:
                server_connections = []
                processed_uris = set()

                for c in r.connections:
                    if c.uri not in processed_uris:
                        server_connections.append({"uri": c.uri, "local": c.local})
                        processed_uris.add(c.uri)

                    http_uri = f"http://{c.address}:{c.port}"
                    if http_uri not in processed_uris:
                        server_connections.append({"uri": http_uri, "local": c.local})
                        processed_uris.add(http_uri)
                
                if server_connections:
                    servers.append({
                        "name": r.name,
                        "connections": server_connections
                    })
        
        if not servers:
            return jsonify({"success": True, "servers": [], "message": _("Nenhum servidor encontrado na sua conta Plex.")})
            
        return jsonify({"success": True, "servers": servers, "token": token, "username": account.username})
    except Exception as e:
        logger.error(f"Erro ao buscar servidores Plex: {e}", exc_info=True)
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
    efi_manager.reload_credentials() # Recarrega as credenciais da Efí
    
    # **MELHORIA: Tenta configurar o webhook da Efí após o setup inicial**
    if config.get("EFI_ENABLED"):
        efi_manager.configure_webhook()

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
    
    config = load_or_create_config()
    data = request.get_json()
    url = data.get('url')
    api_key = data.get('api_key')

    if not url:
        return jsonify({'success': False, 'message': _('URL é obrigatória.')}), 400

    # Lógica para usar a chave guardada se a chave recebida for um placeholder
    is_placeholder = all(char == '*' for char in api_key) if api_key else False
    if is_placeholder:
        # Tenta usar a chave da configuração
        api_key = config.get('TAUTULLI_API_KEY')
        logger.info("Chave de API do Tautulli recebida como placeholder. A usar a chave guardada na configuração para o teste.")

    if not api_key: 
        return jsonify({'success': False, 'message': _('Chave da API é obrigatória.')}), 400
    
    return jsonify(tautulli_manager.test_connection(url, api_key))

@system_api_bp.route('/test/overseerr-connection', methods=['POST'])
def test_overseerr_connection():
    if is_configured() and not (current_user.is_authenticated and current_user.is_admin()):
        return jsonify({'success': False, 'message': _('Acesso não autorizado.')}), 403
    
    config = load_or_create_config()
    data = request.get_json()
    url = data.get('url')
    api_key = data.get('api_key')

    if not url:
        return jsonify({'success': False, 'message': _('URL é obrigatória.')}), 400

    is_placeholder = all(char == '*' for char in api_key) if api_key else False
    if is_placeholder:
        api_key = config.get('OVERSEERR_API_KEY')
        logger.info("Chave de API do Overseerr recebida como placeholder. A usar a chave guardada na configuração para o teste.")

    if not api_key:
        return jsonify({'success': False, 'message': _('Chave da API é obrigatória.')}), 400

    return jsonify(overseerr_manager.test_connection(url, api_key))

@system_api_bp.route('/bulk-notify', methods=['POST'])
@login_required
@admin_required
def bulk_notify():
    data = request.get_json()
    message = data.get('message')
    target_audience = data.get('target_audience', 'active')

    if not message:
        return jsonify({"success": False, "message": _("A mensagem não pode estar vazia.")}), 400

    config = load_or_create_config()
    is_any_notifier_enabled = (
        config.get("TELEGRAM_ENABLED", False) or
        config.get("DISCORD_ENABLED", False) or
        config.get("WEBHOOK_ENABLED", False)
    )
    if not is_any_notifier_enabled:
        return jsonify({"success": False, "message": _("Nenhum agente de notificação (Telegram, Discord, etc.) está ativado nas configurações.")}), 400

    task_payload = {
        'message': message,
        'target_audience': target_audience
    }
    task = data_manager.create_task('bulk_notification', task_payload)

    return jsonify({"success": True, "message": _("A tarefa de envio de notificações em massa foi agendada e será iniciada em breve."), "task_id": task['id']})

