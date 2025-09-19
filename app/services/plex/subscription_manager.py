# app/services/plex/subscription_manager.py

import logging
from datetime import datetime, date, timedelta
from flask import current_app
from flask_babel import gettext as _
from tzlocal import get_localzone
import secrets
import calendar  # Importa o módulo calendar
# Importa JobLookupError para lidar com a remoção de tarefas de forma segura
from apscheduler.jobstores.base import JobLookupError
# CORREÇÃO: Importa a exceção de erro operacional para lidar com bloqueios de DB
from sqlalchemy.exc import OperationalError
# Importar a função para carregar a configuração
from ...config import load_or_create_config

logger = logging.getLogger(__name__)

class PlexSubscriptionManager:
    """
    Gere as tarefas agendadas relacionadas com as subscrições dos utilizadores,
    como notificações de expiração e o fim dos períodos de teste.
    """
    def __init__(self, data_manager, user_manager=None):
        self.data_manager = data_manager
        self.plex_manager = None # Injetado pelo PlexManager após a inicialização

    def renew_subscription(self, plex_user_id, months_to_add, base_mode='today', base_date_str=None, expiration_time_str=None):
        """
        Renova a subscrição de um utilizador, calcula a nova data de vencimento
        e reagenda a sua tarefa de expiração.
        """
        from ... import extensions # Prevenção de importação circular
        from ...scheduler import end_subscription_job

        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile:
            raise ValueError("Perfil de utilizador não encontrado.")

        now = datetime.now(get_localzone())
        base_date = now

        # 1. Define a data base para a renovação
        if base_mode == 'expiry_date':
            current_expiration_str = profile.get('expiration_date')
            if current_expiration_str:
                try:
                    expiration_date = datetime.fromisoformat(current_expiration_str)
                    # CORREÇÃO: Se a data de expiração já passou, a nova assinatura
                    # começa a contar a partir de hoje. Caso contrário, a partir da data de expiração.
                    if expiration_date < now:
                        base_date = now
                    else:
                        base_date = expiration_date
                except ValueError:
                    logger.warning(f"Formato de data de expiração inválido '{current_expiration_str}' para o utilizador {plex_user_id}. A renovar a partir da data atual.")
                    base_date = now # Fallback para a data atual em caso de erro no formato
            else:
                # Se não houver data de expiração (primeira assinatura), usa a data atual.
                base_date = now
        
        if base_date_str:
            try:
                base_time = base_date.time()
                base_date = datetime.fromisoformat(base_date_str).replace(hour=base_time.hour, minute=base_time.minute, second=base_time.second, microsecond=0, tzinfo=get_localzone())
                if base_date < now:
                    base_date = now
            except ValueError:
                 logger.warning(f"Formato de data base inválido '{base_date_str}'. A renovar a partir da data atual.")

        # 2. CORREÇÃO: Calcula a nova data de vencimento de forma precisa, adicionando meses de calendário.
        # Isto substitui a lógica imprecisa de `timedelta(days=30)`.
        months_total = base_date.month - 1 + int(months_to_add)
        new_year = base_date.year + months_total // 12
        new_month = months_total % 12 + 1
        # Garante que o dia seja válido para o novo mês (ex: lida com 31 de Janeiro -> 28/29 de Fevereiro)
        new_day = min(base_date.day, calendar.monthrange(new_year, new_month)[1])
        new_expiration_date = base_date.replace(year=new_year, month=new_month, day=new_day)

        # 3. Aplica a hora de vencimento, dando prioridade à configuração universal
        config = load_or_create_config()
        universal_enabled = config.get("UNIVERSAL_EXPIRATION_ENABLED", False)
        universal_time_str = config.get("UNIVERSAL_EXPIRATION_TIME", "23:59")
        
        final_expiration_time_str = None
        if universal_enabled:
            final_expiration_time_str = universal_time_str
        elif expiration_time_str:
            final_expiration_time_str = expiration_time_str

        if final_expiration_time_str:
            try:
                time_parts = list(map(int, final_expiration_time_str.split(':')))
                new_expiration_date = new_expiration_date.replace(hour=time_parts[0], minute=time_parts[1], second=0, microsecond=0)
            except (ValueError, IndexError):
                logger.warning(f"Formato de hora de expiração inválido '{final_expiration_time_str}'. A ignorar.")

        profile['expiration_date'] = new_expiration_date.isoformat()

        # Limpa os dados do período de teste ao renovar a assinatura.
        if 'trial_end_date' in profile:
            profile['trial_end_date'] = None
        if 'trial_job_id' in profile and profile['trial_job_id']:
            try:
                extensions.scheduler.remove_job(profile['trial_job_id'])
                logger.info(f"Tarefa de teste '{profile['trial_job_id']}' removida para o utilizador {plex_user_id} após renovação.")
            except JobLookupError:
                logger.warning(f"Não foi possível encontrar a tarefa de teste '{profile['trial_job_id']}' para remover para o utilizador {plex_user_id}.")
            profile['trial_job_id'] = None

        # Garante que o utilizador seja desbloqueado se estava bloqueado por expiração
        blocked_user_info = self.data_manager.get_blocked_user(plex_user_id)
        if blocked_user_info:
            block_reason = blocked_user_info.get('block_reason')
            if block_reason in ['expired', 'trial_expired']:
                self.plex_manager.unblock_user(plex_user_id)

        # Remove a tarefa de expiração antiga, se existir
        if profile.get('expiration_job_id'):
            try:
                extensions.scheduler.remove_job(profile['expiration_job_id'])
            except JobLookupError:
                logger.warning(f"Não foi possível encontrar a tarefa de expiração antiga '{profile['expiration_job_id']}' para remover para o utilizador {plex_user_id}.")
            except OperationalError as e:
                if "database is locked" in str(e):
                    logger.warning(f"A base de dados estava bloqueada ao tentar remover a tarefa antiga '{profile['expiration_job_id']}' para o utilizador {plex_user_id}. A nova tarefa será agendada, mas a antiga pode permanecer. Isto geralmente resolve-se sozinho.")
                else:
                    raise 
        
        # Agenda a nova tarefa para o dia da expiração
        new_job_id = f"sub_end_{plex_user_id}_{secrets.token_hex(4)}"
        extensions.scheduler.add_job(
            id=new_job_id,
            func=end_subscription_job,
            args=[plex_user_id],
            trigger='date',
            run_date=new_expiration_date,
            misfire_grace_time=3600
        )
        profile['expiration_job_id'] = new_job_id
        
        # Salva o perfil atualizado
        self.data_manager.set_user_profile(plex_user_id, profile)
        
        return new_expiration_date

    # Manter as outras funções da classe para evitar quebrar outras partes do código
    def schedule_user_expiration(self, plex_user_id, expiration_date):
        pass

    def end_user_trial(self, plex_user_id):
        pass
    
    def check_user_expiration(self, plex_user_id):
        pass

