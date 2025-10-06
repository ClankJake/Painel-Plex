# app/scheduler.py

import logging
import time
import os
import json
from datetime import datetime, timezone
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone_name

from .config import load_or_create_config
from .locks import single_instance_job

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 10 

_app = None

def set_app_for_jobs(app):
    global _app
    _app = app

def _execute_with_retry(action, description):
    for attempt in range(MAX_RETRIES):
        try:
            action()
            return True
        except Exception as e:
            logger.warning(
                f"Tentativa {attempt + 1}/{MAX_RETRIES} falhou para '{description}'. Erro: {e}. "
                f"A tentar novamente em {RETRY_DELAY}s..."
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    logger.error(f"Todas as {MAX_RETRIES} tentativas falharam para '{description}'. A tarefa irá desistir.")
    return False

@single_instance_job('task_processor_job')
def task_processor_job():
    """
    Processador de tarefas em segundo plano.
    CORREÇÃO: Esta tarefa agora lida apenas com notificações em massa.
    O processamento de pagamentos foi movido para uma thread dedicada.
    """
    if not _app: return
    with _app.test_request_context():
        from . import extensions
        
        task_obj = extensions.data_manager.get_next_pending_task('bulk_notification')
        if task_obj:
            extensions.data_manager.update_task(task_obj.id, {'status': 'running', 'started_at': datetime.now(timezone.utc)})
            extensions.notifier_manager.process_bulk_notification_task(task_obj)

@single_instance_job('stream_check_job')
def stream_check_job():
    if not _app: return
    with _app.test_request_context():
        from . import extensions
        extensions.stream_manager.check_and_enforce_streams()

@single_instance_job('expiration_notification_job')
def expiration_notification_job():
    if not _app: return
    with _app.test_request_context():
        from . import extensions
        users_to_check = extensions.plex_manager.get_users_within_notification_window()
        for plex_user_id in users_to_check:
            user_info = extensions.plex_manager.get_user_by_id(plex_user_id)
            if user_info:
                extensions.plex_manager.send_expiration_notification_if_needed(user_info)

def end_trial_job(plex_user_id):
    if not _app: return
    with _app.test_request_context():
        from . import extensions
        
        user_info = extensions.plex_manager.get_user_by_id(plex_user_id)
        user_identifier = user_info['username'] if user_info else f"ID '{plex_user_id}'"
        
        logger.info(f"Fim do período de teste para '{user_identifier}'. A acionar o bloqueio.")
        
        if user_info:
            success = _execute_with_retry(
                action=lambda: extensions.plex_manager.block_user(plex_user_id, reason='trial_expired'),
                description=f"bloquear utilizador por fim de teste '{user_identifier}'"
            )
            
            if success:
                profile = extensions.data_manager.get_user_profile(plex_user_id)
                if profile:
                    extensions.notifier_manager.send_trial_end_notification(user_info, profile)
                    profile['trial_job_id'] = None
                    extensions.data_manager.set_user_profile(plex_user_id, profile)
        else:
            logger.warning(f"Utilizador com ID '{plex_user_id}' não encontrado durante a tarefa de fim de teste.")


def end_subscription_job(plex_user_id):
    """Tarefa individual acionada no fim exato da subscrição de um utilizador."""
    if not _app:
        logger.error(f"A instância da app não foi definida para a tarefa 'end_subscription_job' (ID: {plex_user_id}). A tarefa foi ignorada.")
        return

    with _app.test_request_context():
        from . import extensions
        
        user_info = extensions.plex_manager.get_user_by_id(plex_user_id)
        user_identifier = user_info['username'] if user_info else f"ID '{plex_user_id}'"

        logger.info(f"Fim da subscrição para '{user_identifier}'. A acionar o bloqueio.")
        
        if user_info:
            _execute_with_retry(
                action=lambda: extensions.plex_manager.block_user(plex_user_id, reason='expired'),
                description=f"bloquear utilizador por subscrição expirada '{user_identifier}'"
            )
            # CORREÇÃO: Limpa o ID da tarefa do perfil do utilizador após a sua execução bem-sucedida.
            # Isto impede que a aplicação tente remover uma tarefa que já não existe em futuras renovações.
            try:
                profile = extensions.data_manager.get_user_profile(plex_user_id)
                if profile and profile.get('expiration_job_id'):
                    logger.info(f"A limpar o ID da tarefa de expiração '{profile['expiration_job_id']}' do perfil do utilizador '{user_identifier}'.")
                    profile['expiration_job_id'] = None
                    extensions.data_manager.set_user_profile(plex_user_id, profile)
            except Exception as e:
                logger.error(f"Erro ao limpar o ID da tarefa de expiração do perfil do utilizador {plex_user_id}: {e}", exc_info=True)
        else:
            logger.warning(f"Utilizador com ID '{plex_user_id}' não encontrado durante a tarefa de fim de subscrição.")

@single_instance_job('removal_job')
def removal_job():
    if not _app: return
    with _app.test_request_context():
        from . import extensions
        users_to_remove = extensions.plex_manager.get_users_to_remove()
        for plex_user_id in users_to_remove:
            user_info = extensions.plex_manager.get_user_by_id(plex_user_id)
            user_identifier = user_info['username'] if user_info else f"ID '{plex_user_id}'"
            _execute_with_retry(
                action=lambda: extensions.plex_manager.remove_user(plex_user_id),
                description=f"remover utilizador bloqueado '{user_identifier}'"
            )

@single_instance_job('cleanup_job')
def cleanup_job():
    if not _app: return
    with _app.test_request_context():
        from . import extensions
        config = load_or_create_config()
        if config.get("CLEANUP_PENDING_PAYMENTS_ENABLED", False):
            days = config.get("CLEANUP_PENDING_PAYMENTS_DAYS", 3)
            extensions.data_manager.delete_old_pending_payments(days)

@single_instance_job('cleanup_image_cache_job')
def cleanup_image_cache_job():
    if not _app: return
    with _app.app_context():
        from .config import load_or_create_config
        from .blueprints.image import IMAGE_CACHE_DIR

        config = load_or_create_config()
        if not config.get("IMAGE_CACHE_CLEANUP_ENABLED", False): return
        
        max_age_days = config.get("IMAGE_CACHE_MAX_AGE_DAYS", 30)
        cutoff_time = time.time() - (max_age_days * 86400)
        
        try:
            if not os.path.exists(IMAGE_CACHE_DIR): return
            for filename in os.listdir(IMAGE_CACHE_DIR):
                filepath = os.path.join(IMAGE_CACHE_DIR, filename)
                if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff_time:
                    os.remove(filepath)
        except Exception as e:
            logger.error(f"Erro durante a limpeza do cache de imagens: {e}", exc_info=True)


def setup_scheduler(app):
    """Configura e inicia o agendador com as tarefas recorrentes da aplicação."""
    from . import extensions
    config = load_or_create_config()
    
    try: tz_str = get_localzone_name()
    except Exception: tz_str = 'UTC'
    
    tz = extensions.scheduler.timezone

    extensions.scheduler.add_job(
        id='stream_check_job', func=stream_check_job,
        trigger='interval', seconds=config.get("STREAM_CHECK_INTERVAL_SECONDS", 15),
        replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=90
    )
    
    exp_time_parts = config.get("EXPIRATION_NOTIFICATION_TIME", "09:00").split(':')
    extensions.scheduler.add_job(
        id='expiration_notification_job', func=expiration_notification_job,
        trigger=CronTrigger(hour=int(exp_time_parts[0]), minute=int(exp_time_parts[1]), timezone=tz),
        replace_existing=True,
        # CORREÇÃO: Aumenta a tolerância a atrasos para 300 segundos (5 minutos).
        # Isto irá suprimir os avisos "was missed by" para esta tarefa.
        misfire_grace_time=300
    )

    block_time_parts = config.get("BLOCK_REMOVAL_TIME", "02:00").split(':')
    extensions.scheduler.add_job(
        id='removal_job', func=removal_job,
        trigger=CronTrigger(hour=int(block_time_parts[0]), minute=int(block_time_parts[1]), timezone=tz),
        replace_existing=True,
        misfire_grace_time=300
    )

    cleanup_time_parts = config.get("CLEANUP_TIME", "03:00").split(':')
    extensions.scheduler.add_job(
        id='cleanup_job', func=cleanup_job,
        trigger=CronTrigger(hour=int(cleanup_time_parts[0]), minute=int(cleanup_time_parts[1]), timezone=tz),
        replace_existing=True,
        misfire_grace_time=300
    )

    if config.get("IMAGE_CACHE_CLEANUP_ENABLED", False):
        cache_cleanup_time_parts = config.get("IMAGE_CACHE_CLEANUP_TIME", "04:00").split(':')
        extensions.scheduler.add_job(
            id='cleanup_image_cache_job', func=cleanup_image_cache_job,
            trigger=CronTrigger(hour=int(cache_cleanup_time_parts[0]), minute=int(cache_cleanup_time_parts[1]), timezone=tz),
            replace_existing=True,
            misfire_grace_time=300
        )

    extensions.scheduler.add_job(
        id='task_processor_job', func=task_processor_job,
        trigger='interval', seconds=20, replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=120
    )

    if not extensions.scheduler.running:
        extensions.scheduler.start()
        logger.info(f"Agendador de tarefas iniciado com PID: {os.getpid()}.")

