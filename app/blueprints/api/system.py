# app/blueprints/api/system.py

import logging
import os
import pytz
from collections import deque
from datetime import datetime

from flask import Blueprint, jsonify, request, current_app, session, url_for
from flask_login import login_user, current_user
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone_name

from ...extensions import plex_manager, tautulli_manager, efi_manager, mercado_pago_manager, bpix_manager, overseerr_manager, scheduler, data_manager, limiter, stream_manager , notifier_manager
# 🐛 CORREÇÃO: 'backup_manager' NÃO pode ser importado por valor aqui. Ao contrário
# dos outros gestores, ele é instanciado mais tarde no create_app() (depois deste
# módulo já ter sido importado), por isso um "from ...extensions import backup_manager"
# congelava o valor None para sempre e todas as rotas de backup rebentavam com
# "'NoneType' object has no attribute ...". Importamos o MÓDULO e acedemos ao
# atributo em tempo de execução, quando já está preenchido.
from ... import extensions as _ext
from ...config import load_or_create_config, save_app_config, is_configured
from ...models import User
from ..auth import admin_required, login_required

logger = logging.getLogger(__name__)
system_api_bp = Blueprint('system_api', __name__)

# --- HELPERS ---
def _get_safe_timezone():
    """Tenta obter o timezone de forma segura, respeitando variáveis do Docker."""
    tz_env = os.environ.get('TZ')
    if tz_env:
        try: 
            return pytz.timezone(tz_env).zone
        except pytz.UnknownTimeZoneError: 
            pass
    try: 
        return get_localzone_name()
    except Exception: 
        return 'UTC'

# ==========================================
# ROTAS DE MONITORIZAÇÃO E LOGS
# ==========================================

@system_api_bp.route('/logs')
@login_required
@admin_required
@limiter.exempt # Isenta a rota de logs do rate limit para permitir atualizações em tempo real
def get_logs():
    try:
        log_file = current_app.config.get('LOG_FILE', 'app.log')
        # 🚀 OTIMIZAÇÃO: Usa deque(maxlen=500) para ler apenas o fim do ficheiro.
        # Evita picos gigantescos de RAM caso o ficheiro de log cresça muito!
        # O errors='replace' impede crashs na leitura se houver caracteres estranhos no log.
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            lines = deque(f, maxlen=500)
            return jsonify({"success": True, "logs": "".join(lines)})
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
            pass # Trunca o ficheiro para 0 bytes
        logger.info(f"O ficheiro de log '{log_file}' foi limpo pelo utilizador '{current_user.username}'.")
        return jsonify({"success": True, "message": _("Ficheiro de log limpo com sucesso.")})
    except Exception as e:
        logger.error(f"Erro ao limpar o ficheiro de log: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@system_api_bp.route('/dashboard-summary')
@login_required
@admin_required
@limiter.exempt 
def get_dashboard_summary():
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
@limiter.exempt
def get_active_streams():
    """Retorna detalhes das sessões ativas."""
    try:
        sessions_data = stream_manager.get_now_playing()
        return jsonify(sessions_data)
    except Exception as e:
        logger.error(f"Erro ao obter streams ativos: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao obter streams ativos."}), 500

@system_api_bp.route('/system-health')
@login_required
@admin_required
@limiter.exempt 
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
@limiter.exempt
def get_termination_logs():
    """Endpoint para obter os logs de términos de sessões."""
    try:
        logs = data_manager.get_stream_termination_logs(limit=20)
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

# ==========================================
# CONFIGURAÇÕES DA APLICAÇÃO (SETTINGS)
# ==========================================

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
            'WEBHOOK_AUTHORIZATION_HEADER', 'WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE', 'WEBHOOK_RENEWAL_MESSAGE_TEMPLATE', 'WEBHOOK_REACTIVATION_MESSAGE_TEMPLATE',
            'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_ENABLED', 'TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE',
            'TELEGRAM_RENEWAL_MESSAGE_TEMPLATE', 'TELEGRAM_REACTIVATION_MESSAGE_TEMPLATE', 'DAYS_TO_NOTIFY_EXPIRATION', 'APP_BASE_URL',
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
            'SHORT_LINK_CLEANUP_ENABLED', 'SHORT_LINK_MAX_AGE_DAYS',
            'ACHIEVEMENT_MOVIE_MARATHON_BRONZE', 'ACHIEVEMENT_MOVIE_MARATHON_SILVER', 'ACHIEVEMENT_MOVIE_MARATHON_GOLD',
            'ACHIEVEMENT_SERIES_BINGER_BRONZE', 'ACHIEVEMENT_SERIES_BINGER_SILVER', 'ACHIEVEMENT_SERIES_BINGER_GOLD',
            'ACHIEVEMENT_TIME_TRAVELER_BRONZE', 'ACHIEVEMENT_TIME_TRAVELER_SILVER', 'ACHIEVEMENT_TIME_TRAVELER_GOLD',
            'ACHIEVEMENT_DIRECTOR_FAN_BRONZE', 'ACHIEVEMENT_DIRECTOR_FAN_SILVER', 'ACHIEVEMENT_DIRECTOR_FAN_GOLD',
            'ACHIEVEMENT_NIGHT_OWL_BRONZE', 'ACHIEVEMENT_NIGHT_OWL_SILVER', 'ACHIEVEMENT_NIGHT_OWL_GOLD',
            'ACHIEVEMENT_PIONEER_BRONZE', 'ACHIEVEMENT_PIONEER_SILVER', 'ACHIEVEMENT_PIONEER_GOLD', 'ACHIEVEMENT_PIONEER_WINDOW_HOURS',
            'XP_PER_MINUTE_WATCHED', 'XP_BONUS_PER_COMPLETED_ITEM', 'XP_COMPLETION_THRESHOLD_PERCENT',
            'XP_LEVEL_TABLE', 'XP_RESET_ENABLED', 'XP_RESET_MONTHS',
            'REFERRAL_ENABLED', 'REFERRAL_REWARD_TYPE', 'REFERRAL_REWARD_DAYS', 'REFERRAL_REWARD_CREDIT',
            'REFERRAL_DEFAULT_INVITE_CODE',
            'TELEGRAM_BULK_MESSAGE_TEMPLATE', 'DISCORD_BULK_MESSAGE_TEMPLATE', 'WEBHOOK_BULK_MESSAGE_TEMPLATE',
            'UNIVERSAL_EXPIRATION_ENABLED', 'UNIVERSAL_EXPIRATION_TIME',
            'DISCORD_ENABLED', 'DISCORD_WEBHOOK_URL', 'DISCORD_EXPIRATION_MESSAGE_TEMPLATE',
            'DISCORD_RENEWAL_MESSAGE_TEMPLATE', 'DISCORD_REACTIVATION_MESSAGE_TEMPLATE', 'DISCORD_TRIAL_END_MESSAGE_TEMPLATE',
            'STREAM_CHECK_INTERVAL_SECONDS', 'TERMINATION_MSG_BLOCKED_MANUAL', 'TERMINATION_MSG_BLOCKED_EXPIRED',
            'TERMINATION_MSG_BLOCKED_TRIAL_EXPIRED', 'TERMINATION_MSG_SCREEN_LIMIT', 'SCREEN_LIMIT_TERMINATION_STRATEGY',
            'BACKUP_ENABLED', 'BACKUP_TIME', 'BACKUP_MAX_COUNT'
        ]
        
        numeric_fields = [
            'DAYS_TO_REMOVE_BLOCKED_USER', 'DAYS_TO_NOTIFY_EXPIRATION',
            'CLEANUP_PENDING_PAYMENTS_DAYS', 'IMAGE_CACHE_MAX_AGE_DAYS',
            'PAYMENT_LINK_GRACE_PERIOD_DAYS',
            'ACHIEVEMENT_MOVIE_MARATHON_BRONZE', 'ACHIEVEMENT_MOVIE_MARATHON_SILVER', 'ACHIEVEMENT_MOVIE_MARATHON_GOLD',
            'ACHIEVEMENT_SERIES_BINGER_BRONZE', 'ACHIEVEMENT_SERIES_BINGER_SILVER', 'ACHIEVEMENT_SERIES_BINGER_GOLD',
            'ACHIEVEMENT_TIME_TRAVELER_BRONZE', 'ACHIEVEMENT_TIME_TRAVELER_SILVER', 'ACHIEVEMENT_TIME_TRAVELER_GOLD',
            'ACHIEVEMENT_DIRECTOR_FAN_BRONZE', 'ACHIEVEMENT_DIRECTOR_FAN_SILVER', 'ACHIEVEMENT_DIRECTOR_FAN_GOLD',
            'ACHIEVEMENT_NIGHT_OWL_BRONZE', 'ACHIEVEMENT_NIGHT_OWL_SILVER', 'ACHIEVEMENT_NIGHT_OWL_GOLD',
            'ACHIEVEMENT_PIONEER_BRONZE', 'ACHIEVEMENT_PIONEER_SILVER', 'ACHIEVEMENT_PIONEER_GOLD', 'ACHIEVEMENT_PIONEER_WINDOW_HOURS',
            'STREAM_CHECK_INTERVAL_SECONDS', 'BACKUP_MAX_COUNT', 'XP_BONUS_PER_COMPLETED_ITEM', 'XP_COMPLETION_THRESHOLD_PERCENT', 'REFERRAL_REWARD_DAYS'
        ]

        # 🐛 CORREÇÃO: XP_PER_MINUTE_WATCHED precisa de aceitar valores decimais
        # (ex: 0.1), mas estava dentro de 'numeric_fields', que força int(value)
        # e truncava qualquer casa decimal para 0. Tratado à parte, com float().
        float_fields = ['XP_PER_MINUTE_WATCHED', 'REFERRAL_REWARD_CREDIT']

        if 'SCREEN_PRICES' in new_data:
            config_to_update['SCREEN_PRICES'] = new_data['SCREEN_PRICES']
            
        for field in fields_to_update:
            if field in new_data:
                value = new_data[field]
                
                # Ignora as chaves escondidas pela interface
                if isinstance(value, dict) and "is_set" in value:
                    continue

                if field in numeric_fields: 
                    # 🚀 OTIMIZAÇÃO: Conversão segura (Evita ValueError e corrupção da DB)
                    try: 
                        config_to_update[field] = int(value) if value else 0
                    except (ValueError, TypeError): 
                        config_to_update[field] = old_config.get(field, 0)
                elif field in float_fields:
                    try:
                        config_to_update[field] = float(value) if value not in (None, '') else 0.0
                    except (ValueError, TypeError):
                        config_to_update[field] = old_config.get(field, 0.0)
                elif field == 'XP_LEVEL_TABLE':
                    # Tabela de níveis personalizada: sanitiza no servidor (ordena por XP,
                    # remove entradas inválidas/duplicadas, garante que começa em 0 XP)
                    # para que uma edição malformada na interface nunca corrompa o cálculo
                    # de níveis de todos os utilizadores.
                    from ...services.tautulli.stats_handler import normalize_level_table, get_default_level_table
                    if not value:
                        config_to_update[field] = []  # lista vazia = volta a usar a tabela padrão
                    else:
                        normalized = normalize_level_table(value)
                        config_to_update[field] = [] if normalized == get_default_level_table() else normalized
                elif field == 'SCREEN_LIMIT_TERMINATION_STRATEGY':
                    # Defesa extra: só aceita os dois valores válidos, mesmo que a UI já restrinja isso.
                    config_to_update[field] = value if value in ('oldest', 'newest') else old_config.get(field, 'oldest')
                elif isinstance(value, bool): 
                    config_to_update[field] = value
                else: 
                    config_to_update[field] = value
                    
        if new_data.get('plex_token') and new_data.get('plex_url'):
            config_to_update['PLEX_TOKEN'] = new_data['plex_token']
            config_to_update['PLEX_URL'] = new_data['plex_url']
            
        save_app_config(config_to_update)
        
        # Atualiza a configuração global da App
        app = current_app._get_current_object()
        app.config.update(config_to_update)

        # ⚡ RECARGA SELETIVA: antes, QUALQUER gravação de configurações (mesmo
        # alterar apenas o texto de uma mensagem do Telegram) reconectava ao Plex,
        # reinicializava todos os clientes de pagamento, chamava a API da Efí para
        # reconfigurar o webhook e deitava fora todas as caches — até 3 chamadas de
        # rede externas e a perda de todo o trabalho de cache já feito.
        # Agora só recarregamos os serviços cujas credenciais realmente mudaram.
        def _changed(*keys):
            return any(old_config.get(k) != config_to_update.get(k) for k in keys)

        plex_changed = _changed('PLEX_URL', 'PLEX_TOKEN')
        efi_changed = _changed(
            'EFI_CLIENT_ID', 'EFI_CLIENT_SECRET', 'EFI_CERTIFICATE', 'EFI_SANDBOX',
            'EFI_PIX_KEY', 'EFI_ENABLED', 'EFI_USE_MTLS', 'EFI_WEBHOOK_HMAC_SECRET',
            'APP_BASE_URL'
        )
        tautulli_changed = _changed('TAUTULLI_URL', 'TAUTULLI_API_KEY')
        mp_changed = _changed('MERCADOPAGO_ACCESS_TOKEN', 'MERCADOPAGO_ENABLED')
        overseerr_changed = _changed('OVERSEERR_URL', 'OVERSEERR_API_KEY', 'OVERSEERR_ENABLED')

        if efi_changed:
            efi_manager.reload_credentials()
        if mp_changed:
            mercado_pago_manager.reload_credentials()
        if tautulli_changed:
            tautulli_manager.reload_credentials()

        if overseerr_changed:
            if hasattr(overseerr_manager, 'reload_credentials'):
                overseerr_manager.reload_credentials()
            elif hasattr(overseerr_manager, 'reload_config'):
                overseerr_manager.reload_config()

        # A configuração do webhook na Efí é uma chamada de rede à API deles:
        # só faz sentido quando algo relevante para o webhook mudou.
        if efi_changed and config_to_update.get("EFI_ENABLED"):
            efi_manager.configure_webhook()

        # Atualização dinâmica do Nível de Logs
        log_level_map = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING, 'ERROR': logging.ERROR}
        new_log_level = config_to_update.get('LOG_LEVEL', 'INFO')
        if new_log_level != old_config.get('LOG_LEVEL'):
            logging.getLogger().setLevel(log_level_map.get(new_log_level, logging.INFO))
            app.logger.setLevel(log_level_map.get(new_log_level, logging.INFO))
            logger.info(f"Nível de log atualizado para {new_log_level}")

        # Re-agendamento de Tarefas Inteligente
        def reschedule_job(job_id, config_key, old_config, new_config, trigger_type='cron'):
            new_value = new_config.get(config_key)
            if new_value and new_value != old_config.get(config_key):
                try:
                    if trigger_type == 'cron':
                        hour, minute = map(int, new_value.split(':')[:2])
                        tz_str = _get_safe_timezone()
                        
                        scheduler.reschedule_job(job_id, trigger=CronTrigger(hour=hour, minute=minute, timezone=tz_str))
                        logger.info(f"Tarefa '{job_id}' reagendada para as {hour:02d}:{minute:02d} ({tz_str}).")
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

        # Backup automático: além de reagendar quando o horário muda, também trata
        # a ativação/desativação em tempo real (adiciona ou remove o job na hora,
        # sem precisar reiniciar o serviço).
        if config_to_update.get('BACKUP_ENABLED'):
            backup_time = config_to_update.get('BACKUP_TIME', '05:00')
            try:
                hour, minute = map(int, backup_time.split(':')[:2])
                tz_str = _get_safe_timezone()
                from apscheduler.triggers.cron import CronTrigger as _CronTrigger
                from ...scheduler import backup_job as _backup_job_func
                scheduler.add_job(
                    id='backup_job', func=_backup_job_func,
                    trigger=_CronTrigger(hour=hour, minute=minute, timezone=tz_str),
                    replace_existing=True, misfire_grace_time=3600
                )
                logger.info(f"Tarefa 'backup_job' agendada/atualizada para as {hour:02d}:{minute:02d} ({tz_str}).")
            except Exception as e:
                logger.error(f"Falha ao agendar a tarefa 'backup_job': {e}", exc_info=True)
        elif old_config.get('BACKUP_ENABLED'):
            try:
                scheduler.remove_job('backup_job')
                logger.info("Tarefa 'backup_job' removida (backup automático desativado).")
            except Exception:
                pass

        # Só reconecta ao Plex (2 chamadas de rede + perda das caches de utilizadores
        # e bibliotecas) quando o URL ou o Token mudaram de facto.
        if plex_changed:
            success, message = plex_manager.reload_connections()
        else:
            success, message = True, _("Configurações guardadas com sucesso.")

        return jsonify({"success": success, "message": message})

    # --- GET SETTINGS (Com Ocultação de Chaves Sensíveis) ---
    config_to_send = load_or_create_config()

    sensitive_keys = [
        'SECRET_KEY', 'PLEX_TOKEN', 'INTERNAL_TRIGGER_KEY',
        'TELEGRAM_BOT_TOKEN', 'TAUTULLI_API_KEY', 'EFI_CLIENT_SECRET',
        'MERCADOPAGO_ACCESS_TOKEN', 'BPIX_AUTH_TOKEN', 'OVERSEERR_API_KEY'
    ]

    for key in sensitive_keys:
        if key in config_to_send and config_to_send[key]:
            config_to_send[key] = {
                "is_set": True,
                "length": len(config_to_send[key])
            }
        else:
            if key in config_to_send:
                config_to_send[key] = { "is_set": False, "length": 0 }

    config_to_send.pop('SECRET_KEY', None)
    config_to_send.pop('INTERNAL_TRIGGER_KEY', None)

    # 🪜 Editor de níveis: se o administrador ainda não personalizou nada, a chave
    # 'XP_LEVEL_TABLE' está vazia (o que significa "usar a tabela padrão do código").
    # Enviamos aqui a tabela EFETIVA para que o editor na interface já apareça
    # preenchido com os níveis padrão, prontos a editar, em vez de vazio.
    from ...services.tautulli.stats_handler import normalize_level_table
    config_to_send['XP_LEVEL_TABLE'] = normalize_level_table(config_to_send.get('XP_LEVEL_TABLE'))

    return jsonify(config_to_send)

# ==========================================
# SETUP E DIAGNÓSTICO (TESTES)
# ==========================================

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

@system_api_bp.route('/setup/restore-backup', methods=['POST'])
@limiter.limit("5 per hour")
def setup_restore_backup():
    """
    Restaura um backup durante a configuração inicial (antes de existir qualquer
    administrador autenticado). É o caminho de recuperação para quem reinstalou
    o painel e quer recuperar tudo de uma vez.

    🔒 SEGURANÇA: esta rota não pode exigir login (ainda não há utilizadores), por
    isso está protegida de outra forma: só funciona enquanto o sistema NÃO estiver
    configurado. Depois da configuração inicial concluída, devolve 403 — a partir
    daí o restauro só é possível pela área de administração autenticada
    (/api/system/backup/restore). Sem isto, qualquer pessoa na rede poderia
    sobrescrever uma instalação em produção enviando um ficheiro .zip.
    Há ainda um rate limit para travar tentativas repetidas.
    """
    import threading
    import signal

    if is_configured():
        logger.warning("Tentativa de usar o restauro de backup do setup com o sistema já configurado. Pedido negado.")
        return jsonify({
            "success": False,
            "message": _("O sistema já está configurado. Use a área de administração para restaurar um backup.")
        }), 403

    if 'file' not in request.files:
        return jsonify({"success": False, "message": _("Nenhum ficheiro enviado.")}), 400

    uploaded_file = request.files['file']
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"success": False, "message": _("Nenhum ficheiro selecionado.")}), 400

    try:
        _ext.backup_manager.restore_from_zip(uploaded_file.stream)
    except ValueError as e:
        # Validação falhou (ZIP inválido / config.json corrompido) — nada foi alterado.
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        logger.error(f"Erro crítico ao restaurar backup durante o setup: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Erro inesperado ao restaurar o backup: %(error)s", error=str(e))}), 500

    logger.warning("⚠️ RESTAURO DE BACKUP concluído a partir do assistente de configuração. A aplicação vai reiniciar...")

    def _delayed_restart():
        import time as _time
        _time.sleep(2)  # dá tempo à resposta HTTP de chegar ao navegador
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_delayed_restart, daemon=True).start()

    return jsonify({
        "success": True,
        "message": _("Backup restaurado com sucesso! A aplicação será reiniciada — aguarde e recarregue a página.")
    })


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
    efi_manager.reload_credentials() 
    
    if hasattr(overseerr_manager, 'reload_credentials'):
        overseerr_manager.reload_credentials()
    elif hasattr(overseerr_manager, 'reload_config'):
        overseerr_manager.reload_config()

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

    is_placeholder = all(char == '*' for char in api_key) if api_key else False
    if is_placeholder:
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
    try:
        data = request.get_json()
        message = data.get('message')
        target_audience = data.get('target_audience', 'active')
        target_user_ids = data.get('user_ids')

        if not message:
            return jsonify({"success": False, "message": _("A mensagem não pode estar vazia.")}), 400

        if target_audience == 'specific':
            if not target_user_ids or not isinstance(target_user_ids, list):
                return jsonify({"success": False, "message": _("Lista de IDs de utilizadores inválida ou ausente para o público 'specific'.")}), 400
            try:
                target_user_ids = [int(uid) for uid in target_user_ids]
            except (ValueError, TypeError):
                 return jsonify({"success": False, "message": _("Lista de IDs de utilizadores contém valores inválidos.")}), 400

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
            'target_audience': target_audience,
            'user_ids': target_user_ids if target_audience == 'specific' else None 
        }
        
        # 1. Cria a tarefa na base de dados
        task = data_manager.create_task('bulk_notification', task_payload)
        task_id = task['id'] if isinstance(task, dict) else task.id

        # 2. Marcamos logo como 'running' para o Agendador antigo ignorar
        data_manager.update_task(task_id, {'status': 'running'})
        
        # 3. Disparamos o envio em massa IMEDIATAMENTE e no ambiente correto
        notifier_manager.process_bulk_notification_task(task)

        return jsonify({"success": True, "message": _("A tarefa de envio de notificações em massa foi iniciada e está a correr em tempo real!"), "task_id": task_id})
    except Exception as e:
        logger.error(f"Erro na rota bulk-notify: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


# ==========================================
# BACKUP E RESTAURO
# ==========================================

# ==========================================
# TEMPORADAS DE XP
# ==========================================

@system_api_bp.route('/xp/season', methods=['GET'])
@login_required
@admin_required
def xp_season_info():
    """Devolve o estado da temporada de XP atual (data de fim, dias restantes)."""
    try:
        return jsonify({"success": True, "season": tautulli_manager.get_season_info()})
    except Exception as e:
        logger.error(f"Erro ao obter informação da temporada de XP: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@system_api_bp.route('/xp/season/reset', methods=['POST'])
@login_required
@admin_required
def xp_season_reset():
    """
    Força o reinício imediato da temporada de XP: repõe o XP de todos os
    utilizadores a zero e recomeça a contagem do período. O XP acumulado de
    sempre ('lifetime_xp') é preservado.
    """
    try:
        result = tautulli_manager.reset_season_if_due(force=True)
        status = 200 if result.get("success") else 400
        return jsonify(result), status
    except Exception as e:
        logger.error(f"Erro ao repor a temporada de XP: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


# ==========================================
# BACKUP E RESTAURO
# ==========================================

@system_api_bp.route('/backup/download', methods=['GET'])
@login_required
@admin_required
def backup_download_now():
    """Gera um backup na hora e devolve-o diretamente como download (não fica guardado em disco)."""
    from flask import send_file
    import io
    try:
        backup_bytes = _ext.backup_manager.create_backup_bytes()
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        filename = f"painel-plex-backup-{timestamp}.zip"
        return send_file(
            io.BytesIO(backup_bytes),
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Erro ao gerar backup sob demanda: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Falha ao gerar o backup: %(error)s", error=str(e))}), 500


@system_api_bp.route('/backup/list', methods=['GET'])
@login_required
@admin_required
def backup_list():
    """Lista os backups automáticos guardados em disco."""
    try:
        return jsonify({"success": True, "backups": _ext.backup_manager.list_backups()})
    except Exception as e:
        logger.error(f"Erro ao listar backups: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@system_api_bp.route('/backup/download/<path:filename>', methods=['GET'])
@login_required
@admin_required
def backup_download_stored(filename):
    """Descarrega um backup automático já guardado em disco."""
    from flask import send_file
    filepath = _ext.backup_manager.get_backup_path(filename)
    if not filepath:
        return jsonify({"success": False, "message": _("Backup não encontrado.")}), 404
    return send_file(filepath, mimetype='application/zip', as_attachment=True, download_name=os.path.basename(filepath))


@system_api_bp.route('/backup/<path:filename>', methods=['DELETE'])
@login_required
@admin_required
def backup_delete(filename):
    """Apaga um backup automático guardado em disco."""
    try:
        if _ext.backup_manager.delete_backup(filename):
            return jsonify({"success": True, "message": _("Backup removido com sucesso.")})
        return jsonify({"success": False, "message": _("Backup não encontrado.")}), 404
    except Exception as e:
        logger.error(f"Erro ao apagar backup '{filename}': {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


@system_api_bp.route('/backup/restore', methods=['POST'])
@login_required
@admin_required
def backup_restore():
    """
    Restaura o config.json e as bases de dados a partir de um ZIP de backup
    enviado pelo administrador. ⚠️ Ação destrutiva e irreversível: substitui
    os dados atuais pelos do backup. Após concluir, a aplicação é reiniciada
    automaticamente (é necessário que o contentor/processo tenha uma política
    de reinício automático, ex: 'restart: unless-stopped' no Docker) para que
    as novas ligações à base de dados sejam recriadas de forma limpa.
    """
    import threading
    import signal

    if 'file' not in request.files:
        return jsonify({"success": False, "message": _("Nenhum ficheiro enviado.")}), 400

    uploaded_file = request.files['file']
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"success": False, "message": _("Nenhum ficheiro selecionado.")}), 400

    try:
        _ext.backup_manager.restore_from_zip(uploaded_file.stream)
    except ValueError as e:
        # Erro de validação (ZIP inválido, config.json corrompido, etc.) — seguro, nada foi alterado.
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        logger.error(f"Erro crítico ao restaurar backup: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Erro inesperado ao restaurar o backup: %(error)s", error=str(e))}), 500

    logger.warning(f"⚠️ RESTAURO DE BACKUP CONCLUÍDO por '{current_user.username}'. A aplicação vai reiniciar em instantes...")

    def _delayed_restart():
        import time as _time
        _time.sleep(2)  # dá tempo da resposta HTTP chegar ao navegador antes do processo terminar
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_delayed_restart, daemon=True).start()

    return jsonify({
        "success": True,
        "message": _("Backup restaurado com sucesso! A aplicação será reiniciada automaticamente em alguns segundos — aguarde e recarregue a página.")
    })