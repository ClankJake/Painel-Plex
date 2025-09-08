# app/scheduler.py

import logging
import time
import os
from datetime import datetime, timedelta
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone, get_localzone_name

from .config import load_or_create_config

logger = logging.getLogger(__name__)

# --- OTIMIZAÇÃO: Constantes para o mecanismo de retry ---
MAX_RETRIES = 3  # Número máximo de tentativas para uma operação crítica
RETRY_DELAY = 10 # Segundos de espera entre as tentativas

# Variavel global para guardar a instância da app, que será definida em __init__.py
_app = None

def set_app_for_jobs(app):
    """
    Guarda a instância da app para ser usada pelas tarefas agendadas.
    Isto evita a necessidade de recriar a aplicação a cada execução de tarefa.
    """
    global _app
    _app = app

# --- OTIMIZAÇÃO: Função auxiliar para executar ações com tentativas ---
def _execute_with_retry(action, description):
    """
    Tenta executar uma ação várias vezes em caso de falha.

    :param action: A função (lambda ou nome de função) a ser executada.
    :param description: Uma descrição da ação para fins de log.
    :return: True se a ação for bem-sucedida, False caso contrário.
    """
    for attempt in range(MAX_RETRIES):
        try:
            action()
            return True  # Sucesso
        except Exception as e:
            logger.warning(
                f"Tentativa {attempt + 1}/{MAX_RETRIES} falhou para '{description}'. Erro: {e}. "
                f"A tentar novamente em {RETRY_DELAY}s..."
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
    logger.error(f"Todas as {MAX_RETRIES} tentativas falharam para '{description}'. A tarefa irá desistir.")
    return False # Falha

def stream_check_job():
    """Tarefa agendada para verificar e impor os limites de stream."""
    if not _app:
        logger.error("A instância da app não foi definida para a tarefa 'stream_check_job'. A tarefa foi ignorada.")
        return
    
    # --- CORREÇÃO: Usa test_request_context para permitir a geração de URLs ---
    with _app.test_request_context():
        from . import extensions
        extensions.stream_manager.check_and_enforce_streams()

def expiration_notification_job():
    """Tarefa agendada para enviar notificações de vencimento."""
    if not _app:
        logger.error("A instância da app não foi definida para a tarefa 'expiration_notification_job'. A tarefa foi ignorada.")
        return

    # --- CORREÇÃO: Usa test_request_context para permitir a geração de URLs ---
    with _app.test_request_context():
        from . import extensions
        logger.info("A executar a tarefa de notificação de vencimentos...")
        try:
            all_users = extensions.plex_manager.get_all_plex_users()
            if all_users is None:
                logger.error("Não foi possível obter a lista de utilizadores do Plex para a tarefa de notificação.")
                return

            users_to_notify = extensions.plex_manager.get_users_to_notify()
            local_tz = get_localzone()

            for username in users_to_notify:
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
    if not _app:
        logger.error("A instância da app não foi definida para a tarefa 'end_trial_job'. A tarefa foi ignorada.")
        return

    # --- CORREÇÃO: Usa test_request_context para permitir a geração de URLs ---
    with _app.test_request_context():
        from . import extensions
        logger.info(f"Fim do período de teste para '{username}'. A acionar o bloqueio.")
        
        all_users = extensions.plex_manager.get_all_plex_users()
        if all_users is None:
            logger.error(f"Não foi possível obter a lista de utilizadores do Plex para a tarefa de fim de teste de '{username}'.")
            return

        user_info = next((u for u in all_users if u['username'] == username), None)
        if user_info:
            success = _execute_with_retry(
                action=lambda: extensions.plex_manager.block_user(user_info['email'], reason='trial_expired'),
                description=f"bloquear utilizador por fim de teste '{username}'"
            )
            
            if success:
                profile = extensions.data_manager.get_user_profile(username)
                if profile:
                    extensions.plex_manager.notifier_manager.send_trial_end_notification(user_info, profile)
                    profile['trial_job_id'] = None
                    extensions.data_manager.set_user_profile(username, profile)
        else:
            logger.warning(f"Utilizador '{username}' não encontrado na lista do Plex durante a tarefa de fim de teste.")

def end_subscription_job(username):
    """Tarefa individual acionada no fim exato da subscrição de um utilizador."""
    if not _app:
        logger.error("A instância da app não foi definida para a tarefa 'end_subscription_job'. A tarefa foi ignorada.")
        return

    # --- CORREÇÃO: Usa test_request_context para permitir a geração de URLs ---
    with _app.test_request_context():
        from . import extensions
        logger.info(f"Fim da subscrição para '{username}'. A acionar o bloqueio.")
        
        all_users = extensions.plex_manager.get_all_plex_users()
        if all_users is None:
            logger.error(f"Não foi possível obter a lista de utilizadores do Plex para a tarefa de bloqueio de '{username}'.")
            return

        user_info = next((u for u in all_users if u['username'] == username), None)
        if user_info:
            _execute_with_retry(
                action=lambda: extensions.plex_manager.block_user(user_info['email'], reason='expired'),
                description=f"bloquear utilizador por subscrição expirada '{username}'"
            )
        else:
            logger.warning(f"Utilizador '{username}' não encontrado na lista do Plex durante a tarefa de fim de subscrição.")


def removal_job():
    """Tarefa agendada para remover os que estão bloqueados há muito tempo."""
    if not _app:
        logger.error("A instância da app não foi definida para a tarefa 'removal_job'. A tarefa foi ignorada.")
        return

    # --- CORREÇÃO: Usa test_request_context para permitir a geração de URLs ---
    with _app.test_request_context():
        from . import extensions
        logger.info("A executar a tarefa de remoção de utilizadores bloqueados...")
        
        all_users = extensions.plex_manager.get_all_plex_users(force_refresh=True)
        if all_users is None:
            logger.error("Não foi possível obter a lista de utilizadores do Plex para a tarefa de remoção.")
            return
        
        users_to_remove = extensions.plex_manager.get_users_to_remove()
        for username in users_to_remove:
            user_info = next((u for u in all_users if u['username'] == username), None)
            if user_info:
                _execute_with_retry(
                    action=lambda: extensions.plex_manager.remove_user(user_info['email']),
                    description=f"remover utilizador bloqueado '{username}'"
                )
            else:
                extensions.data_manager.remove_blocked_user(username)
        
        logger.info("Tarefa de remoção concluída.")

def cleanup_job():
    """Tarefa agendada para limpar dados antigos da aplicação."""
    if not _app:
        logger.error("A instância da app não foi definida para a tarefa 'cleanup_job'. A tarefa foi ignorada.")
        return

    # --- CORREÇÃO: Usa test_request_context para permitir a geração de URLs ---
    with _app.test_request_context():
        from . import extensions
        logger.info("A executar a tarefa de limpeza de dados antigos...")
        try:
            config = load_or_create_config()
            if config.get("CLEANUP_PENDING_PAYMENTS_ENABLED", False):
                days = config.get("CLEANUP_PENDING_PAYMENTS_DAYS", 3)
                extensions.data_manager.delete_old_pending_payments(days)
        except Exception as e:
            logger.error(f"Erro durante a execução da tarefa de limpeza: {e}", exc_info=True)
        logger.info("Tarefa de limpeza concluída.")

# NOVO: Tarefa para limpar o cache de imagens em disco
def cleanup_image_cache_job():
    """Tarefa agendada para apagar imagens antigas do cache em disco."""
    if not _app:
        logger.error("A instância da app não foi definida para a tarefa 'cleanup_image_cache_job'. A tarefa foi ignorada.")
        return

    with _app.app_context():
        from .config import load_or_create_config
        from .blueprints.image_proxy import IMAGE_CACHE_DIR

        config = load_or_create_config()
        if not config.get("IMAGE_CACHE_CLEANUP_ENABLED", False):
            logger.info("Limpeza do cache de imagens está desativada. A ignorar a tarefa.")
            return
        
        max_age_days = config.get("IMAGE_CACHE_MAX_AGE_DAYS", 30)
        cutoff_time = time.time() - (max_age_days * 86400)
        deleted_count = 0

        logger.info(f"A executar a limpeza do cache de imagens. A apagar ficheiros mais antigos que {max_age_days} dias...")
        try:
            for filename in os.listdir(IMAGE_CACHE_DIR):
                filepath = os.path.join(IMAGE_CACHE_DIR, filename)
                if os.path.isfile(filepath):
                    if os.path.getmtime(filepath) < cutoff_time:
                        os.remove(filepath)
                        deleted_count += 1
            if deleted_count > 0:
                logger.info(f"Limpeza do cache de imagens concluída. {deleted_count} ficheiros apagados.")
        except Exception as e:
            logger.error(f"Erro durante a limpeza do cache de imagens: {e}", exc_info=True)


def setup_scheduler(app):
    """Configura e inicia o agendador com as tarefas recorrentes da aplicação."""
    from . import extensions
    config = load_or_create_config()
    
    tz_str = 'UTC'
    try:
        tz_str = get_localzone_name()
    except Exception:
        logger.warning("Não foi possível detetar o fuso horário local. A usar UTC como padrão para o agendador.")
    
    tz = extensions.scheduler.timezone

    # Adiciona a tarefa de verificação de streams do StreamManager
    extensions.scheduler.add_job(
        id='stream_check_job', 
        func=stream_check_job,
        trigger='interval', 
        seconds=config.get("STREAM_CHECK_INTERVAL_SECONDS", 15),
        replace_existing=True
    )
    
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

    # NOVO: Adiciona a tarefa de limpeza do cache de imagens
    if config.get("IMAGE_CACHE_CLEANUP_ENABLED", False):
        cache_cleanup_time_parts = config.get("IMAGE_CACHE_CLEANUP_TIME", "04:00").split(':')
        extensions.scheduler.add_job(
            id='cleanup_image_cache_job', 
            func=cleanup_image_cache_job,
            trigger=CronTrigger(hour=int(cache_cleanup_time_parts[0]), minute=int(cache_cleanup_time_parts[1]), timezone=tz),
            replace_existing=True
        )

    if not extensions.scheduler.running:
        extensions.scheduler.start()
        logger.info("Agendador de tarefas iniciado.")

