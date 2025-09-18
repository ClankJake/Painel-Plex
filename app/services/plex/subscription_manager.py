# app/services/plex/subscription_manager.py

import logging
from datetime import datetime, date, timedelta
from flask import current_app
from flask_babel import gettext as _

logger = logging.getLogger(__name__)

class PlexSubscriptionManager:
    """
    Gere as tarefas agendadas relacionadas com as subscrições dos utilizadores,
    como notificações de expiração e o fim dos períodos de teste.
    """
    def __init__(self, data_manager, user_manager=None):
        self.data_manager = data_manager

    def schedule_user_expiration(self, plex_user_id, expiration_date):
        """Agenda ou reagenda as tarefas de notificação e expiração para um utilizador."""
        from ... import plex_manager, scheduler # Prevenção de importação circular
        from ...config import load_or_create_config
        config = load_or_create_config()
        
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile:
            logger.warning(f"Tentativa de agendar expiração para o ID {plex_user_id}, mas o perfil não foi encontrado.")
            return

        # Remove a tarefa de expiração antiga, se existir
        if profile.get('expiration_job_id'):
            try:
                scheduler.remove_job(profile['expiration_job_id'])
            except Exception as e:
                logger.warning(f"Não foi possível remover a tarefa de expiração antiga '{profile['expiration_job_id']}': {e}")

        # Agenda a nova tarefa para o dia da expiração
        job = scheduler.add_job(
            func=self.check_user_expiration,
            trigger='date',
            run_date=expiration_date,
            args=[plex_user_id],
            id=f"expiration_{plex_user_id}_{int(expiration_date.timestamp())}"
        )
        profile['expiration_job_id'] = job.id
        self.data_manager.set_user_profile(plex_user_id, profile)
        logger.info(f"Tarefa de verificação de expiração agendada para o utilizador ID {plex_user_id} em {expiration_date.strftime('%Y-%m-%d')}.")

    def end_user_trial(self, plex_user_id):
        """
        Função executada pelo scheduler quando o período de teste de um utilizador termina.
        Bloqueia o utilizador a menos que ele já tenha renovado a subscrição.
        """
        from ... import plex_manager # Prevenção de importação circular
        with current_app.app_context():
            profile = self.data_manager.get_user_profile(plex_user_id)
            if not profile:
                logger.warning(f"Tarefa de fim de teste executada para o ID {plex_user_id}, mas o perfil não foi encontrado.")
                return

            expiration_date_str = profile.get('expiration_date')
            if not expiration_date_str:
                logger.warning(f"Utilizador {plex_user_id} em fim de teste não tem data de expiração. A bloquear por segurança.")
                plex_manager.block_user(plex_user_id, reason='trial_expired')
                plex_manager.notifier_manager.send_trial_end_notification(plex_manager.get_user_by_id(plex_user_id))
                return

            try:
                expiration_datetime = datetime.fromisoformat(expiration_date_str)
            except ValueError:
                logger.error(f"Formato de data de expiração inválido para o utilizador {plex_user_id}: '{expiration_date_str}'. A bloquear por segurança.")
                plex_manager.block_user(plex_user_id, reason='trial_expired')
                plex_manager.notifier_manager.send_trial_end_notification(plex_manager.get_user_by_id(plex_user_id))
                return

            # A tarefa de fim de teste é precisa. Só não bloqueamos se o utilizador claramente renovou,
            # o que significa que a sua data de expiração está significativamente no futuro.
            # Usamos um buffer de 1 dia para evitar problemas com ligeiras diferenças de tempo.
            if expiration_datetime > datetime.now() + timedelta(days=1):
                logger.info(f"O utilizador {profile.get('username')} renovou a subscrição antes do fim do teste (nova expiração: {expiration_datetime}). A tarefa de bloqueio foi cancelada.")
                return
            
            logger.info(f"O período de teste do utilizador {profile.get('username')} terminou. A bloquear o acesso.")
            plex_manager.block_user(plex_user_id, reason='trial_expired')
            plex_manager.notifier_manager.send_trial_end_notification(plex_manager.get_user_by_id(plex_user_id))
    
    def check_user_expiration(self, plex_user_id):
        """
        Função executada pelo scheduler no dia da expiração de um utilizador.
        Verifica se a subscrição realmente expirou e bloqueia se necessário.
        """
        from ... import plex_manager
        with current_app.app_context():
            profile = self.data_manager.get_user_profile(plex_user_id)
            if not profile:
                logger.warning(f"Tarefa de verificação de expiração executada para o ID {plex_user_id}, mas o perfil não foi encontrado.")
                return

            expiration_date_str = profile.get('expiration_date')
            if expiration_date_str:
                try:
                    expiration_date = datetime.fromisoformat(expiration_date_str).date()
                    # Bloqueia apenas se a data de expiração for anterior a hoje
                    if expiration_date < date.today():
                        logger.info(f"A subscrição do utilizador {profile.get('username')} expirou em {expiration_date}. A bloquear o acesso.")
                        plex_manager.block_user(plex_user_id, reason='expired')
                        plex_manager.notifier_manager.send_expiration_notification(plex_manager.get_user_by_id(plex_user_id), expiration_date)
                    else:
                        logger.info(f"A subscrição do utilizador {profile.get('username')} ainda está ativa (expira em {expiration_date}). Nenhuma ação necessária.")
                except ValueError:
                    logger.error(f"Formato de data de expiração inválido para o utilizador {plex_user_id}: '{expiration_date_str}'.")
            else:
                logger.warning(f"Tarefa de verificação de expiração para {plex_user_id} executada, mas não há data de expiração no perfil.")

    def renew_subscription(self, plex_user_id, months, renewal_type):
        """
        Renova a subscrição de um utilizador e reagenda a sua tarefa de expiração.
        """
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile:
            raise ValueError("Perfil de utilizador não encontrado.")

        current_expiration_str = profile.get('expiration_date')
        now = datetime.now()
        
        base_date = now
        if current_expiration_str:
            try:
                current_expiration_date = datetime.fromisoformat(current_expiration_str)
                # Se a subscrição ainda estiver ativa, adiciona tempo à data existente.
                # Caso contrário, renova a partir de hoje.
                if current_expiration_date > now:
                    base_date = current_expiration_date
            except ValueError:
                logger.warning(f"Formato de data de expiração inválido '{current_expiration_str}' para o utilizador {plex_user_id}. A renovar a partir da data atual.")
        
        # Simplificado para sempre adicionar 30 dias por mês
        new_expiration_date = base_date + timedelta(days=30 * months)
        
        profile['expiration_date'] = new_expiration_date.isoformat()
        
        # Garante que o utilizador seja desbloqueado se estava bloqueado por expiração
        if self.data_manager.is_user_blocked(plex_user_id):
            block_info = self.data_manager.is_user_blocked(plex_user_id, get_reason=True)
            if block_info and block_info.get('block_reason') in ['expired', 'trial_expired']:
                from ... import plex_manager
                plex_manager.unblock_user(plex_user_id)

        self.data_manager.set_user_profile(plex_user_id, profile)
        self.schedule_user_expiration(plex_user_id, new_expiration_date)
        
        return new_expiration_date

