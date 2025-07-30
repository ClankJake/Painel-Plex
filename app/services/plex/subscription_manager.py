# app/services/plex/subscription_manager.py
import logging
import calendar
import secrets
from datetime import datetime, timedelta
from tzlocal import get_localzone
from apscheduler.jobstores.base import JobLookupError
from flask_babel import gettext as _

logger = logging.getLogger(__name__)

class PlexSubscriptionManager:
    """
    Gere a lógica de subscrição dos utilizadores, como renovações.
    """
    def __init__(self, data_manager, user_manager, tautulli_manager):
        self.data_manager = data_manager
        self.user_manager = user_manager
        self.tautulli_manager = tautulli_manager

    def renew_subscription(self, username, months_to_add, base_mode='today', base_date_str=None, expiration_time_str=None):
        from app.extensions import scheduler
        from app.scheduler import end_subscription_job, end_trial_job
        from app.config import load_or_create_config

        profile = self.data_manager.get_user_profile(username)
        local_tz = get_localzone()

        # Cancela qualquer tarefa de expiração de subscrição anterior
        if profile.get('expiration_job_id'):
            try: scheduler.remove_job(profile['expiration_job_id'])
            except JobLookupError: logger.info(_("A tarefa de expiração para '%(username)s' não foi encontrada, provavelmente já foi executada.", username=username))

        # --- INÍCIO DA CORREÇÃO ---
        # Verifica se o utilizador estava em teste e limpa os dados relacionados.
        was_on_trial = bool(profile.get('trial_end_date'))
        if was_on_trial:
            logger.info(_("Utilizador '%(username)s' está a renovar após um período de teste. A limpar os dados do teste.", username=username))
            trial_job_id = profile.get('trial_job_id')
            if trial_job_id:
                try:
                    scheduler.remove_job(trial_job_id)
                    logger.info(_("Tarefa de fim de teste pendente '%(job_id)s' foi removida.", job_id=trial_job_id))
                except JobLookupError:
                    logger.info(_("A tarefa de fim de teste '%(job_id)s' já não existia no agendador.", job_id=trial_job_id))
            
            # Limpa os campos de teste do perfil
            profile['trial_end_date'] = None
            profile['trial_job_id'] = None
        # --- FIM DA CORREÇÃO ---

        # Determina a data de início para o cálculo da renovação
        now_aware = datetime.now(local_tz)
        start_date = now_aware

        if base_date_str:
            try: 
                # CORREÇÃO: Usa .replace(tzinfo=...) em vez de .localize()
                start_date = datetime.strptime(base_date_str, '%Y-%m-%d').replace(tzinfo=local_tz)
            except (ValueError, TypeError): 
                base_date_str = None

        if not base_date_str and profile.get('expiration_date'):
            try:
                current_expiration = datetime.fromisoformat(profile['expiration_date'])
                if current_expiration.tzinfo is None:
                    # CORREÇÃO: Usa .replace(tzinfo=...) em vez de .localize()
                    current_expiration = current_expiration.replace(tzinfo=local_tz)
                if current_expiration > now_aware:
                    start_date = current_expiration
            except (ValueError, TypeError):
                logger.error(_("Erro ao analisar a data de expiração para %(username)s. A usar a data de hoje como padrão.", username=username))

        # Calcula a nova data de expiração
        if base_mode == 'expiry_date':
            new_month = start_date.month + months_to_add
            new_year = start_date.year + (new_month - 1) // 12
            new_month = (new_month - 1) % 12 + 1
            day = min(start_date.day, calendar.monthrange(new_year, new_month)[1])
            new_expiration_date = start_date.replace(year=new_year, month=new_month, day=day)
        else:
            days_to_add = 30 * months_to_add
            new_expiration_date = start_date + timedelta(days=days_to_add)

        if expiration_time_str:
            try:
                time_parts = list(map(int, expiration_time_str.split(':')))
                new_expiration_date = new_expiration_date.replace(hour=time_parts[0], minute=time_parts[1], second=0)
            except (ValueError, IndexError):
                logger.warning(_("Formato de hora inválido '%(time)s'. A ignorar.", time=expiration_time_str))

        # Agenda a nova tarefa de expiração
        naive_run_date = new_expiration_date.replace(tzinfo=None)
        job_id = f"sub_end_{username}_{secrets.token_hex(4)}"
        scheduler.add_job(id=job_id, func=end_subscription_job, args=[username], trigger='date', run_date=naive_run_date, replace_existing=True)
        
        # Atualiza o perfil do utilizador
        profile['expiration_date'] = new_expiration_date.isoformat()
        profile['expiration_job_id'] = job_id
        self.data_manager.set_user_profile(username, profile)

        # Lógica de desbloqueio
        blocked_user_info = self.data_manager.get_blocked_users(username=username)
        if blocked_user_info:
            logger.info(_("O utilizador '%(username)s' está bloqueado. A tentar desbloquear após a renovação.", username=username))
            plex_user = next((u for u in self.user_manager.get_all_plex_users() if u['username'] == username), None)
            if plex_user:
                config = load_or_create_config()
                block_reason = blocked_user_info.get('block_reason')
                notifier_id_to_unblock = config.get('TRIAL_BLOCK_NOTIFIER_ID') if block_reason == 'trial_expired' else config.get('BLOCKING_NOTIFIER_ID')
                self.tautulli_manager.manage_block_unblock(plex_user['email'], username, 'remove', notifier_id=notifier_id_to_unblock)
                logger.info(_("Comando de desbloqueio enviado para o utilizador '%(username)s'.", username=username))
            else:
                logger.warning(_("Não foi possível encontrar os detalhes do utilizador Plex para '%(username)s' para o desbloquear.", username=username))

        return new_expiration_date
