# app/services/plex/subscription_manager.py
import logging
import calendar
import secrets
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from tzlocal import get_localzone
from apscheduler.jobstores.base import JobLookupError
from flask_babel import gettext as _

logger = logging.getLogger(__name__)

class PlexSubscriptionManager:
    """
    Gere a lógica de subscrição dos utilizadores, como renovações.
    """
    def __init__(self, data_manager, user_manager):
        self.data_manager = data_manager
        self.user_manager = user_manager
        self.plex_manager = None

    def renew_subscription(self, plex_user_id, months_to_add, base_mode='today', base_date_str=None, expiration_time_str=None):
        from app.extensions import scheduler
        from app.scheduler import end_subscription_job
        from app.config import load_or_create_config

        config = load_or_create_config()
        # CORREÇÃO: O nome da função correta é 'get_user_profile' (singular).
        profile = self.data_manager.get_user_profile(plex_user_id)
        local_tz = get_localzone()

        if old_job_id := profile.get('expiration_job_id'):
            try:
                scheduler.remove_job(old_job_id)
            except JobLookupError:
                pass

        if profile.get('trial_end_date'):
            if trial_job_id := profile.get('trial_job_id'):
                try:
                    scheduler.remove_job(trial_job_id)
                except JobLookupError:
                    pass
            profile['trial_end_date'] = None
            profile['trial_job_id'] = None

        now_aware = datetime.now(local_tz)
        start_date = now_aware

        if base_date_str:
            try:
                start_date = datetime.strptime(base_date_str, '%Y-%m-%d').replace(tzinfo=local_tz)
            except (ValueError, TypeError):
                pass
        
        # Usa a data de expiração atual como base se for no futuro e o modo for 'expiry_date'
        elif profile.get('expiration_date') and base_mode == 'expiry_date':
            try:
                current_expiration = datetime.fromisoformat(profile['expiration_date'])
                if current_expiration.tzinfo is None:
                    current_expiration = current_expiration.replace(tzinfo=local_tz)
                if current_expiration > now_aware:
                    start_date = current_expiration
            except (ValueError, TypeError):
                pass

        # MELHORIA: Usa relativedelta para adicionar meses de forma mais precisa.
        new_expiration_date = start_date + relativedelta(months=months_to_add)

        final_expiration_time_str = expiration_time_str
        if config.get("UNIVERSAL_EXPIRATION_ENABLED"):
            final_expiration_time_str = config.get("UNIVERSAL_EXPIRATION_TIME", "23:59")
        
        if final_expiration_time_str:
            try:
                time_parts = list(map(int, final_expiration_time_str.split(':')))
                new_expiration_date = new_expiration_date.replace(hour=time_parts[0], minute=time_parts[1], second=0, microsecond=0)
            except (ValueError, IndexError):
                pass
        
        # Garante que a data da tarefa agendada não tenha fuso horário (necessário pelo APScheduler)
        naive_run_date = new_expiration_date.astimezone(local_tz).replace(tzinfo=None)
        job_id = f"sub_end_{plex_user_id}_{secrets.token_hex(4)}"
        scheduler.add_job(id=job_id, func=end_subscription_job, args=[plex_user_id], trigger='date', run_date=naive_run_date, replace_existing=True)
        
        profile['expiration_date'] = new_expiration_date.isoformat()
        profile['expiration_job_id'] = job_id
        self.data_manager.set_user_profile(plex_user_id, profile)

        # CORREÇÃO: Simplifica a lógica de desbloqueio
        if self.data_manager.get_blocked_user(plex_user_id):
            if self.plex_manager:
                self.plex_manager.unblock_user(plex_user_id)
            else:
                logger.error("PlexManager não injetado. Não é possível desbloquear.")

        return new_expiration_date

