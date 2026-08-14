import logging
import secrets
import calendar
import time
from datetime import datetime, date, timedelta
from tzlocal import get_localzone

from flask import current_app
from flask_babel import gettext as _
from apscheduler.jobstores.base import JobLookupError
from sqlalchemy.exc import OperationalError

from ...config import load_or_create_config

logger = logging.getLogger(__name__)

class PlexSubscriptionManager:
    """
    Gere as tarefas agendadas relacionadas com as subscrições dos utilizadores,
    como renovações, notificações de expiração e o fim dos períodos de teste.
    """
    def __init__(self, data_manager, user_manager=None, scheduler=None):
        self.data_manager = data_manager
        self.user_manager = user_manager
        # Injeção direta da dependência para evitar importações circulares e melhorar o uso em threads
        self.scheduler = scheduler 
        self.plex_manager = None # Injetado pelo PlexManager após a inicialização

    def renew_subscription(self, plex_user_id, months_to_add, screens=None, base_mode='today', base_date_str=None, expiration_time_str=None, is_reactivation=False):
        """
        Renova a subscrição de um utilizador, calcula a nova data de vencimento
        e reagenda a sua tarefa de expiração.
        """
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile:
            raise ValueError("Perfil de utilizador não encontrado.")

        # Deteção automática de reativação caso a flag não venha explícita do sistema de pagamentos
        was_blocked = self.data_manager.get_blocked_user(plex_user_id) is not None
        if profile.get('status') == 'inactive' or was_blocked:
            is_reactivation = True

        # 1. Atualizar o estado básico do perfil (Status e Limite de Telas)
        self._update_basic_profile_state(profile, plex_user_id, screens, is_reactivation)

        # 2. Calcular as Datas de Renovação
        now = datetime.now(get_localzone())
        base_date = self._calculate_base_date(profile, base_mode, base_date_str, now)
        new_expiration_date = self._calculate_new_expiration_date(base_date, months_to_add, expiration_time_str)
        profile['expiration_date'] = new_expiration_date.isoformat()

        # 3. Limpar Testes (Trials) Anteriores e Reagendar Expiração
        self._clear_trial_data(profile, plex_user_id)
        self._replace_expiration_job(profile, plex_user_id, new_expiration_date)

        # 4. Desbloquear Utilizador (Se estava bloqueado por falta de pagamento)
        self._unblock_user_if_needed(plex_user_id)

        # 🛡️ ANTI-DUPLICAÇÃO: Marca o timestamp exato da reativação no perfil do utilizador
        if is_reactivation:
            profile['last_reactivation_time'] = time.time()

        # 5. Salvar na Base de Dados
        self.data_manager.set_user_profile(plex_user_id, profile)

        # 6. Restauro de Acesso Seguro e Notificação de Reativação
        if is_reactivation and self.plex_manager and self.plex_manager.notifier_manager:
            try:
                user_info = self.plex_manager.get_user_by_id(plex_user_id) or {'id': plex_user_id, 'username': profile.get('username')}
                email = profile.get('email') or user_info.get('email')
                
                # Utiliza o método robusto do InviteManager para restaurar o acesso e readicionar o utilizador
                invite_result = {}
                if email:
                    import json
                    libraries = profile.get('libraries', '[]')
                    if isinstance(libraries, str):
                        try:
                            libraries = json.loads(libraries)
                        except:
                            libraries = []
                            
                    invite_result = self.plex_manager.invites.send_plex_invite(
                        identifier=email,
                        library_titles=libraries,
                        plex_user_id=plex_user_id,
                        allow_sync=profile.get('allow_downloads', False)
                    )
                
                # Resgata o token escondido e constrói o Link Direto de Aceite
                invite_token = invite_result.get('invite_token')
                
                if invite_token and invite_token != "ACCEPTED":
                    invite_link = f"https://clients.plex.tv/servers/shared_servers/accept?invite_token={invite_token}"
                else:
                    invite_link = "https://app.plex.tv/desktop"

                # 🔗 Persiste o link no perfil para que a página de pagamento consiga
                # exibir um botão de confirmação manual caso a ativação automática falhe.
                # Só guardamos um link "real" quando temos um token válido (convite pendente);
                # caso contrário, limpamos para não mostrar o botão à toa.
                latest_profile = self.data_manager.get_user_profile(plex_user_id) or profile
                latest_profile['pending_invite_link'] = invite_link if (invite_token and invite_token != "ACCEPTED") else None
                self.data_manager.set_user_profile(plex_user_id, latest_profile)
                
                # Dispara a notificação de reativação com o link embutido
                self.plex_manager.notifier_manager.send_reactivation_notification(user_info, new_expiration_date, profile, invite_link)
            except Exception as e:
                logger.error(f"Falha ao restaurar acesso ou enviar notificação de reativação para o usuário {plex_user_id}: {e}")

        return new_expiration_date

    # ====================================================================
    # --- MÉTODOS AUXILIARES (SRP) ---
    # ====================================================================

    def _update_basic_profile_state(self, profile, plex_user_id, screens, is_reactivation):
        """Atualiza estado e limites de ecrã do perfil do utilizador."""
        if is_reactivation:
            logger.info(f"A reativar o perfil do utilizador '{profile.get('username')}' (ID: {plex_user_id}).")
            profile['status'] = 'active'

        if screens is not None and screens >= 0:
            profile['screen_limit'] = screens
            logger.info(f"Limite de telas para '{profile.get('username')}' definido para {screens} durante a renovação.")

    def _calculate_base_date(self, profile, base_mode, base_date_str, now):
        """Determina a data de início (data base) para adicionar os meses da renovação."""
        base_date = now

        if base_mode == 'expiry_date':
            current_expiration_str = profile.get('expiration_date')
            if current_expiration_str:
                try:
                    expiration_date = datetime.fromisoformat(current_expiration_str)
                    # Se a data de expiração não passou, adiciona a partir dessa data (Acumula meses)
                    if expiration_date >= now:
                        base_date = expiration_date
                except (ValueError, TypeError):
                    logger.warning(f"Formato de data de expiração inválido '{current_expiration_str}'. A renovar a partir de hoje.")

        if base_date_str:
            try:
                base_time = base_date.time()
                parsed_base = datetime.fromisoformat(base_date_str)
                # Preserva a hora calculada, mas atualiza os componentes da data com o timezone local
                base_date = parsed_base.replace(
                    hour=base_time.hour, minute=base_time.minute, 
                    second=base_time.second, microsecond=0, tzinfo=get_localzone()
                )
                if base_date < now:
                    base_date = now
            except (ValueError, TypeError):
                 logger.warning(f"Formato de data base inválido '{base_date_str}'. A renovar a partir de hoje.")

        return base_date

    def _calculate_new_expiration_date(self, base_date, months_to_add, expiration_time_str):
        """Adiciona os meses de forma precisa usando o calendário e aplica a hora de expiração."""
        try:
            months_to_add = int(months_to_add)
        except (ValueError, TypeError):
            months_to_add = 0
            
        # 1. Adicionar Meses Precisamente (Lida com anos bissextos e fins de mês)
        months_total = base_date.month - 1 + months_to_add
        new_year = base_date.year + months_total // 12
        new_month = months_total % 12 + 1
        new_day = min(base_date.day, calendar.monthrange(new_year, new_month)[1])
        new_expiration_date = base_date.replace(year=new_year, month=new_month, day=new_day)

        # 2. Aplicar a Hora de Vencimento
        config = load_or_create_config()
        universal_enabled = config.get("UNIVERSAL_EXPIRATION_ENABLED", False)
        universal_time_str = config.get("UNIVERSAL_EXPIRATION_TIME", "23:59")

        final_time_str = universal_time_str if universal_enabled else expiration_time_str

        # Conversão robusta de Time Strings
        if final_time_str:
            try:
                time_parts = list(map(int, final_time_str.split(':')))
                if len(time_parts) >= 2:
                    new_expiration_date = new_expiration_date.replace(
                        hour=time_parts[0], minute=time_parts[1], second=0, microsecond=0
                    )
            except (ValueError, IndexError, TypeError):
                logger.warning(f"Formato de hora de expiração inválido '{final_time_str}'. A ignorar formatação forçada de horas.")

        return new_expiration_date

    def _clear_trial_data(self, profile, plex_user_id):
        """Remove rastros de um período de teste (trial) anterior e cancela a sua tarefa."""
        if not profile.get('trial_end_date') and not profile.get('trial_job_id'):
            return

        profile['trial_end_date'] = None
        logger.info(f"Data de fim de teste limpa para o utilizador {plex_user_id} devido à renovação.")

        job_id = profile.get('trial_job_id')
        if job_id and self.scheduler:
            # Proteção anti-deadlock ao remover a tarefa de trial do Scheduler
            for attempt in range(3):
                try:
                    self.scheduler.remove_job(job_id)
                    logger.info(f"Tarefa de teste '{job_id}' removida.")
                    break
                except JobLookupError:
                    break
                except OperationalError as e:
                    if "database is locked" in str(e):
                        if attempt < 2:
                            logger.warning(f"Base de dados bloqueada ao tentar remover tarefa de teste '{job_id}'. A tentar novamente...")
                            time.sleep(1)
                        else:
                            logger.error(f"Falha persistente ao remover tarefa de teste '{job_id}' por bloqueio de BD. A ignorar.")
                    else:
                        raise
            profile['trial_job_id'] = None

    def _replace_expiration_job(self, profile, plex_user_id, new_expiration_date):
        """Cancela a tarefa de suspensão antiga e agenda uma nova no APScheduler."""
        from ...scheduler import end_subscription_job

        if not self.scheduler:
            logger.error("O agendador (scheduler) não foi injetado. A tarefa de expiração não pode ser agendada.")
            return

        # 1. Remover a tarefa antiga com proteção anti-deadlock (retry)
        old_job_id = profile.get('expiration_job_id')
        if old_job_id:
            for attempt in range(3):
                try:
                    self.scheduler.remove_job(old_job_id)
                    break # Sucesso
                except JobLookupError:
                    break # Não existe, tudo bem
                except OperationalError as e:
                    if "database is locked" in str(e):
                        if attempt < 2:
                            logger.warning(f"A base de dados estava bloqueada ao tentar remover a tarefa antiga '{old_job_id}'. A tentar novamente...")
                            time.sleep(1)
                        else:
                            logger.error(f"Falha persistente ao remover tarefa antiga '{old_job_id}' por bloqueio de BD. A ignorar.")
                    else:
                        raise
            profile['expiration_job_id'] = None

        # 2. Agendar a nova tarefa
        new_job_id = f"sub_end_{plex_user_id}_{secrets.token_hex(4)}"
        try:
            self.scheduler.add_job(
                id=new_job_id,
                func=end_subscription_job,
                args=[plex_user_id],
                trigger='date',
                run_date=new_expiration_date,
                misfire_grace_time=3600 # 1 hora de tolerância
            )
            profile['expiration_job_id'] = new_job_id
            logger.info(f"Tarefa de expiração '{new_job_id}' agendada para {new_expiration_date.strftime('%Y-%m-%d %H:%M:%S')}.")
        except Exception as e:
            logger.error(f"Falha ao agendar tarefa de expiração para o utilizador {plex_user_id}: {e}")

    def _unblock_user_if_needed(self, plex_user_id):
        """Desbloqueia o utilizador no Plex se ele estivesse inativo devido a falta de pagamento."""
        blocked_user_info = self.data_manager.get_blocked_user(plex_user_id)
        if blocked_user_info and self.plex_manager:
            block_reason = blocked_user_info.get('block_reason')
            if block_reason in ['expired', 'trial_expired']:
                self.plex_manager.unblock_user(plex_user_id)

    # --- MÉTODOS PENDENTES (Mantidos para evitar quebrar interfaces externas) ---
    def schedule_user_expiration(self, plex_user_id, expiration_date):
        pass

    def end_user_trial(self, plex_user_id):
        pass

    def check_user_expiration(self, plex_user_id):
        pass
