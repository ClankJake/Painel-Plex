# app/scheduler.py

import logging
from datetime import datetime
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone 

from .config import load_or_create_config

logger = logging.getLogger(__name__)

def expiration_notification_job():
    """Tarefa agendada para enviar notificações de vencimento."""
    from . import create_app, extensions
    app = create_app(_from_job=True)
    with app.app_context():
        with app.test_request_context():
            logger.info("A executar a tarefa de notificação de vencimentos...")
            try:
                all_users = extensions.plex_manager.get_all_plex_users()
                if all_users is None:
                    logger.error("Não foi possível obter a lista de utilizadores do Plex para a tarefa de notificação. A tentar novamente mais tarde.")
                    return

                users_to_notify = extensions.plex_manager.get_users_to_notify()
                local_tz = get_localzone()

                for username in users_to_notify:
                    logger.info(f"A processar notificação de vencimento para: {username}")
                    user_info = next((u for u in all_users if u['username'] == username), None)
                    if user_info:
                        profile = extensions.data_manager.get_user_profile(username)
                        expiration_date_str = profile.get('expiration_date')
                        if expiration_date_str:
                            expiration_date = datetime.fromisoformat(expiration_date_str).date()
                            days_left = (expiration_date - datetime.now(local_tz).date()).days
                            extensions.plex_manager.notifier_manager.send_expiration_notification(user_info, days_left, profile)
            except Exception as e:
                logger.error(f"Erro durante a execução da tarefa de notificação de vencimentos: {e}", exc_info=True)
            logger.info("Tarefa de notificação de vencimentos concluída.")

def end_trial_job(username):
    """Tarefa individual acionada no fim exato do período de teste de um utilizador."""
    from . import create_app, extensions
    app = create_app(_from_job=True)
    with app.app_context():
        with app.test_request_context():
            logger.info(f"Fim do período de teste para '{username}'. A acionar o bloqueio.")
            try:
                all_users = extensions.plex_manager.get_all_plex_users()
                if all_users is None:
                    logger.error(f"Não foi possível obter a lista de utilizadores do Plex para a tarefa de fim de teste de '{username}'. A tarefa será ignorada por agora.")
                    return

                user_info = next((u for u in all_users if u['username'] == username), None)
                if user_info:
                    config = load_or_create_config()
                    profile = extensions.data_manager.get_user_profile(username)
                    extensions.plex_manager.notifier_manager.send_trial_end_notification(user_info, profile)
                    notifier_id = config.get('TRIAL_BLOCK_NOTIFIER_ID')
                    extensions.tautulli_manager.manage_block_unblock(user_info['email'], username, 'add', notifier_id=notifier_id, reason='trial_expired')
                    if profile:
                        profile['trial_job_id'] = None
                        extensions.data_manager.set_user_profile(username, profile)
                else:
                    logger.warning(f"Utilizador '{username}' não encontrado na lista do Plex durante a tarefa de fim de teste.")
            except Exception as e:
                logger.error(f"Erro ao executar a tarefa de fim de teste para '{username}': {e}", exc_info=True)

def end_subscription_job(username):
    """Tarefa individual acionada no fim exato da subscrição de um utilizador."""
    from . import create_app, extensions
    app = create_app(_from_job=True)
    with app.app_context():
        with app.test_request_context():
            logger.info(f"Fim da subscrição para '{username}'. A acionar o bloqueio.")
            try:
                all_users = extensions.plex_manager.get_all_plex_users()
                if all_users is None:
                    logger.error(f"Não foi possível obter a lista de utilizadores do Plex para a tarefa de bloqueio de '{username}'. A tarefa será ignorada por agora.")
                    return

                user_info = next((u for u in all_users if u['username'] == username), None)
                if user_info:
                    config = load_or_create_config()
                    notifier_id = config.get('BLOCKING_NOTIFIER_ID')
                    extensions.tautulli_manager.manage_block_unblock(user_info['email'], username, 'add', notifier_id=notifier_id, reason='expired')
                else:
                    logger.warning(f"Utilizador '{username}' não encontrado na lista do Plex durante a tarefa de fim de subscrição. O utilizador pode já ter sido removido.")
            except Exception as e:
                logger.error(f"Erro ao executar a tarefa de fim de subscrição para '{username}': {e}", exc_info=True)

def removal_job():
    """Tarefa agendada para remover os que estão bloqueados há muito tempo."""
    from . import create_app, extensions
    app = create_app(_from_job=True)
    with app.app_context():
        with app.test_request_context():
            logger.info("A executar a tarefa de remoção de utilizadores bloqueados...")
            try:
                all_users = extensions.plex_manager.get_all_plex_users(force_refresh=True)
                if all_users is None:
                    logger.error("Não foi possível obter a lista de utilizadores do Plex para a tarefa de remoção. A tentar novamente mais tarde.")
                    return
                
                users_to_remove = extensions.plex_manager.get_users_to_remove()
                for username in users_to_remove:
                    logger.info(f"A processar remoção de utilizador bloqueado: {username}")
                    user_info = next((u for u in all_users if u['username'] == username), None)
                    if user_info:
                        extensions.plex_manager.remove_user(user_info['email'])
                    else:
                        extensions.data_manager.remove_blocked_user(username)
            except Exception as e:
                logger.error(f"Erro durante a execução da tarefa de remoção: {e}", exc_info=True)
            logger.info("Tarefa de remoção concluída.")

def cleanup_job():
    """Tarefa agendada para limpar dados antigos da aplicação."""
    from . import create_app, extensions
    app = create_app(_from_job=True)
    with app.app_context():
        with app.test_request_context():
            logger.info("A executar a tarefa de limpeza de dados antigos...")
            try:
                config = load_or_create_config()
                if config.get("CLEANUP_PENDING_PAYMENTS_ENABLED", False):
                    days = config.get("CLEANUP_PENDING_PAYMENTS_DAYS", 3)
                    extensions.data_manager.delete_old_pending_payments(days)
            except Exception as e:
                logger.error(f"Erro durante a execução da tarefa de limpeza: {e}", exc_info=True)
            logger.info("Tarefa de limpeza concluída.")

def setup_scheduler(app):
    """Configura e inicia o agendador com as tarefas recorrentes da aplicação."""
    from . import extensions
    with app.app_context():
        config = load_or_create_config()
        
        tz = extensions.scheduler.timezone

        exp_time_parts = config.get("EXPIRATION_NOTIFICATION_TIME", "09:00").split(':')
        extensions.scheduler.add_job(
            id='expiration_notification_job', 
            func=expiration_notification_job,
            trigger=CronTrigger(hour=int(exp_time_parts[0]), minute=int(exp_time_parts[1]), timezone=tz),
            replace_existing=True
        )

        block_time_parts = config.get("BLOCK_REMOVAL_TIME", "02:00").split(':')
        extensions.scheduler.add_job(
            id='removal_job', 
            func=removal_job,
            trigger=CronTrigger(hour=int(block_time_parts[0]), minute=int(block_time_parts[1]), timezone=tz),
            replace_existing=True
        )

        cleanup_time_parts = config.get("CLEANUP_TIME", "03:00").split(':')
        extensions.scheduler.add_job(
            id='cleanup_job', 
            func=cleanup_job,
            trigger=CronTrigger(hour=int(cleanup_time_parts[0]), minute=int(cleanup_time_parts[1]), timezone=tz),
            replace_existing=True
        )

        if not extensions.scheduler.running:
            extensions.scheduler.start()
            logger.info("Agendador de tarefas iniciado.")
