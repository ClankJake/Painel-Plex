# app/services/data_manager.py
import os
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from ..extensions import db
from ..models import Invitation, BlockedUser, UserProfile, PixPayment, Notification, UnlockedAchievement, ShortLink, Coupon, CouponUsage, Task
from sqlalchemy import func, extract, not_
from tzlocal import get_localzone
from flask_babel import gettext as _, ngettext

logger = logging.getLogger(__name__)

class DataManager:
    """Responsável por carregar e salvar dados da aplicação usando SQLAlchemy ORM."""
    
    def __init__(self):
        pass

    # --- MÉTODOS DE TAREFAS ---
    def create_task(self, name, payload):
        """Cria uma nova tarefa na base de dados."""
        try:
            task = Task(name=name, payload=json.dumps(payload))
            db.session.add(task)
            db.session.commit()
            logger.info(f"Tarefa '{name}' criada com ID: {task.id}")
            return self._row_to_dict(task)
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao criar tarefa '{name}': {e}")
            raise

    def get_next_pending_task(self, name):
        """Busca a próxima tarefa pendente de um tipo específico."""
        # Usa 'with_for_update' para bloquear a linha e evitar que múltiplos workers a peguem ao mesmo tempo.
        task = Task.query.filter_by(name=name, status='pending').order_by(Task.created_at).with_for_update().first()
        return task

    def update_task(self, task_id, updates):
        """Atualiza os campos de uma tarefa."""
        try:
            task = Task.query.get(task_id)
            if task:
                for key, value in updates.items():
                    if hasattr(task, key):
                        setattr(task, key, value)
                db.session.commit()
                return self._row_to_dict(task)
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao atualizar a tarefa {task_id}: {e}")
        return None

    # --- MÉTODOS DE CUPÕES ---
    def create_coupon(self, details):
        try:
            new_coupon = Coupon(**details)
            db.session.add(new_coupon)
            db.session.commit()
            logger.info(f"Cupão '{details['code']}' criado com sucesso.")
            return self._row_to_dict(new_coupon)
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao criar cupão: {e}")
            raise

    def get_coupon_by_code(self, code):
        coupon = Coupon.query.filter_by(code=code).first()
        return self._row_to_dict(coupon) if coupon else None

    def get_all_coupons(self):
        coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
        return [self._row_to_dict(c) for c in coupons]

    def delete_coupon(self, coupon_id):
        coupon = Coupon.query.get(coupon_id)
        if coupon:
            db.session.delete(coupon)
            db.session.commit()
            return True
        return False

    def toggle_coupon_active(self, coupon_id):
        coupon = Coupon.query.get(coupon_id)
        if coupon:
            coupon.is_active = not coupon.is_active
            db.session.commit()
            return self._row_to_dict(coupon)
        return None

    def record_coupon_usage(self, code, username):
        """Regista o uso de um cupão por um utilizador e incrementa a contagem global."""
        coupon = Coupon.query.filter_by(code=code).first()
        user_profile = UserProfile.query.get(username)
        if coupon and user_profile:
            try:
                # Incrementa a contagem global
                coupon.use_count += 1
                # Regista o uso individual
                new_usage = CouponUsage(user_username=username, coupon_id=coupon.id)
                db.session.add(new_usage)
                db.session.commit()
                logger.info(f"Uso do cupão '{code}' registado para o utilizador '{username}'. Contagem total: {coupon.use_count}.")
                return True
            except Exception as e:
                db.session.rollback()
                # Evita erro de constraint duplicada caso a lógica seja chamada mais de uma vez
                if 'UNIQUE constraint failed' in str(e):
                    logger.warning(f"Tentativa de registar uso duplicado do cupão '{code}' para '{username}'. Ignorando.")
                    return True
                logger.error(f"Erro ao registar o uso do cupão '{code}' para '{username}': {e}")
                return False
        return False

    def has_user_used_coupon(self, username, code):
        """Verifica se um utilizador específico já utilizou um determinado cupão."""
        usage = db.session.query(CouponUsage).join(Coupon).filter(
            Coupon.code == code,
            CouponUsage.user_username == username
        ).first()
        return usage is not None

    # --- MÉTODOS DE GAMIFICAÇÃO (CONQUISTAS) ---
    def get_unlocked_achievements(self, username):
        """Busca os IDs de todas as conquistas desbloqueadas por um utilizador."""
        achievements = UnlockedAchievement.query.filter_by(username=username).all()
        return {ach.achievement_id for ach in achievements}

    def add_unlocked_achievements(self, username, achievements_to_add):
        """Adiciona novas conquistas desbloqueadas para um utilizador."""
        try:
            for ach_data in achievements_to_add:
                new_achievement = UnlockedAchievement(
                    username=username,
                    achievement_id=ach_data['id']
                )
                db.session.add(new_achievement)
            db.session.commit()
            logger.info(f"{len(achievements_to_add)} nova(s) conquista(s) adicionada(s) para o utilizador '{username}'.")
        except Exception as e:
            logger.error(f"Falha ao adicionar conquistas para '{username}': {e}")
            db.session.rollback()

    # --- MÉTODOS DE NOTIFICAÇÃO ---
    def create_notification(self, message, category='info', link=None, username=None):
        """Cria uma nova notificação no sistema."""
        try:
            notification = Notification(
                message=message,
                category=category,
                link=link,
                username=username, # Pode ser None para notificações de admin
                timestamp=datetime.utcnow()
            )
            db.session.add(notification)
            db.session.commit()
            logger.info(f"Notificação criada para '{username or 'Admin'}': '{message}'")
        except Exception as e:
            logger.error(f"Falha ao criar notificação: {e}")
            db.session.rollback()

    def get_notifications(self, username, limit=10, include_read=False):
        """Busca as notificações mais recentes para um utilizador específico ou para o admin."""
        query = Notification.query.filter_by(username=username).order_by(Notification.timestamp.desc())
        if not include_read:
            query = query.filter_by(is_read=False)
        
        notifications = query.limit(limit).all()
        return [self._row_to_dict(n) for n in notifications]

    def get_unread_notification_count(self, username):
        """Retorna o número de notificações não lidas para um utilizador."""
        return Notification.query.filter_by(username=username, is_read=False).count()

    def mark_all_as_read(self, username):
        """Marca todas as notificações de um utilizador como lidas."""
        try:
            updated_rows = Notification.query.filter_by(username=username, is_read=False).update({'is_read': True})
            db.session.commit()
            logger.info(f"{updated_rows} notificações marcadas como lidas para '{username}'.")
            return updated_rows
        except Exception as e:
            logger.error(f"Falha ao marcar todas as notificações como lidas para '{username}': {e}")
            db.session.rollback()
            return 0
            
    def delete_all_notifications(self, username):
        """Apaga todas as notificações de um utilizador da base de dados."""
        try:
            num_rows_deleted = db.session.query(Notification).filter_by(username=username).delete()
            db.session.commit()
            logger.info(f"{num_rows_deleted} notificações foram apagadas para '{username}'.")
            return num_rows_deleted
        except Exception as e:
            logger.error(f"Falha ao apagar todas as notificaçõess para '{username}': {e}")
            db.session.rollback()
            return 0

    # --- MÉTODOS FINANCEIROS ---
    def get_financial_summary(self, year, month, renewal_days=7):
        """
        Obtém um resumo financeiro para um determinado mês e ano, com consultas otimizadas.
        """
        # --- OTIMIZAÇÃO: Consultas agregadas diretamente na base de dados ---

        # 1. Busca o total de receita e o número de vendas com uma única consulta.
        summary_query = db.session.query(
            func.sum(PixPayment.value),
            func.count(PixPayment.txid)
        ).filter(
            extract('year', PixPayment.created_at) == year,
            extract('month', PixPayment.created_at) == month,
            PixPayment.status == 'CONCLUIDA'
        ).first()
        total_revenue = summary_query[0] or 0.0
        sales_count = summary_query[1] or 0

        # 2. Agrega a receita por dia.
        daily_revenue_query = db.session.query(
            extract('day', PixPayment.created_at).label('day'),
            func.sum(PixPayment.value).label('total')
        ).filter(
            extract('year', PixPayment.created_at) == year,
            extract('month', PixPayment.created_at) == month,
            PixPayment.status == 'CONCLUIDA'
        ).group_by('day').all()
        daily_revenue_dict = {day: total for day, total in daily_revenue_query}

        # 3. Agrega a receita por semana (específico para SQLite com strftime).
        # Para outros bancos de dados, pode ser necessário usar funções diferentes.
        weekly_revenue_query = db.session.query(
            func.strftime('%W', PixPayment.created_at).label('week_num'),
            func.sum(PixPayment.value).label('total')
        ).filter(
            extract('year', PixPayment.created_at) == year,
            extract('month', PixPayment.created_at) == month,
            PixPayment.status == 'CONCLUIDA'
        ).group_by('week_num').order_by('week_num').all()
        # Converte o número da semana do ano para uma chave relativa ao mês (Semana 1, Semana 2, etc.)
        weekly_revenue_dict = {f"Semana {i+1}": total for i, (week_num, total) in enumerate(weekly_revenue_query)}

        # 4. Busca as transações mais recentes (já era uma consulta otimizada).
        recent_transactions = PixPayment.query.filter(
            extract('year', PixPayment.created_at) == year,
            extract('month', PixPayment.created_at) == month,
            PixPayment.status == 'CONCLUIDA'
        ).order_by(PixPayment.created_at.desc()).limit(10).all()

        # --- FIM DA OTIMIZAÇÃO ---

        # A consulta de renovações futuras permanece, pois é em outra tabela.
        today = datetime.now(get_localzone()).date()
        end_date = today + timedelta(days=renewal_days)
        today_str = today.isoformat()
        end_date_str = (end_date + timedelta(days=1)).isoformat()
        blocked_usernames = [u.username for u in BlockedUser.query.all()]
        expiring_users_query = db.session.query(UserProfile).filter(
            UserProfile.expiration_date.isnot(None),
            UserProfile.expiration_date != '',
            UserProfile.expiration_date >= today_str,
            UserProfile.expiration_date < end_date_str,
            not_(UserProfile.username.in_(blocked_usernames))
        ).order_by(UserProfile.expiration_date.asc()).all()
        
        upcoming_expirations = []
        for user_profile in expiring_users_query:
            try:
                exp_date = datetime.fromisoformat(user_profile.expiration_date).date()
                days_left = (exp_date - today).days
                if days_left < 0: days_left_text = _("Expirado")
                elif days_left == 0: days_left_text = _("Hoje")
                else: days_left_text = ngettext('%(num)d dia restante', '%(num)d dias restantes', days_left) % {'num': days_left}
                
                upcoming_expirations.append({
                    'username': user_profile.username,
                    'expiration_date': exp_date.strftime('%d/%m/%Y'),
                    'days_left': days_left,
                    'days_left_text': days_left_text,
                    'screen_limit': user_profile.screen_limit
                })
            except (ValueError, TypeError): continue
        
        return { 
            "total_revenue": total_revenue, 
            "sales_count": sales_count, 
            "recent_transactions": [self._row_to_dict(p) for p in recent_transactions], 
            "daily_revenue": daily_revenue_dict, 
            "weekly_revenue": weekly_revenue_dict, 
            "upcoming_expirations": upcoming_expirations 
        }

    def get_payments_for_export(self, start_date_iso, end_date_iso):
        """Busca todas as transações concluídas dentro de um intervalo de datas para exportação."""
        try:
            payments = PixPayment.query.filter(
                PixPayment.status == 'CONCLUIDA',
                PixPayment.created_at >= start_date_iso,
                PixPayment.created_at <= end_date_iso
            ).order_by(PixPayment.created_at.asc()).all()
            return [self._row_to_dict(p) for p in payments]
        except Exception as e:
            logger.error(f"Erro ao buscar pagamentos para exportação: {e}", exc_info=True)
            return []

    # --- Métodos para Perfis de Utilizador ---
    def get_user_profile(self, username):
        profile = UserProfile.query.get(username)
        return self._row_to_dict(profile) if profile else {}

    def get_all_user_profiles(self):
        profiles = UserProfile.query.all()
        return [self._row_to_dict(p) for p in profiles]

    def get_user_profiles_by_username(self, usernames):
        """
        Retorna um dicionário de perfis de utilizador para uma lista de nomes de utilizador.
        """
        if not usernames:
            return {}
        try:
            profiles = UserProfile.query.filter(UserProfile.username.in_(usernames)).all()
            return {
                p.username: self._row_to_dict(p)
                for p in profiles
            }
        except Exception as e:
            logger.error(f"Erro ao obter perfis para os utilizadores {usernames}: {e}", exc_info=True)
            return {}

    def set_user_profile(self, username, profile_data):
        profile = UserProfile.query.get(username)
        if not profile:
            profile = UserProfile(username=username)
        if not profile.payment_token:
            profile.payment_token = secrets.token_urlsafe(16)
        for key, value in profile_data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        db.session.add(profile)
        db.session.commit()
    
    def delete_user_profile(self, username):
        profile = UserProfile.query.get(username)
        if profile:
            db.session.delete(profile)
            db.session.commit()
            logger.info(f"Perfil do utilizador '{username}' apagado da base de dados.")

    def get_all_user_expirations(self):
        profiles = UserProfile.query.filter(UserProfile.expiration_date.isnot(None), UserProfile.expiration_date != '').all()
        return {p.username: self._row_to_dict(p) for p in profiles}

    def get_all_trial_users(self):
        profiles = UserProfile.query.filter(UserProfile.trial_end_date.isnot(None), UserProfile.trial_end_date != '').all()
        return {p.username: self._row_to_dict(p) for p in profiles}

    # --- Métodos de Pagamento PIX ---
    def create_pix_payment(self, txid, username, value, provider, screens, external_reference, coupon_code=None):
        payment = PixPayment.query.get(txid)
        if not payment:
            payment = PixPayment(txid=txid)
        payment.username = username
        payment.value = value
        payment.provider = provider
        payment.created_at = datetime.now().isoformat()
        payment.status = 'ATIVA'
        payment.screens = screens
        payment.external_reference = external_reference
        payment.coupon_code = coupon_code
        db.session.add(payment)
        db.session.commit()

    def get_pix_payment(self, txid):
        payment = PixPayment.query.get(txid)
        return self._row_to_dict(payment) if payment else None

    def update_pix_payment_status(self, txid, status):
        payment = PixPayment.query.get(txid)
        if payment:
            payment.status = status
            db.session.commit()

    def add_manual_payment(self, username, value, description, payment_date_str):
        txid = f"manual_{secrets.token_hex(12)}"
        payment = PixPayment(txid=txid, username=username, value=float(value), status='CONCLUIDA', provider='Manual', description=description, created_at=payment_date_str, screens=0, external_reference=None)
        db.session.add(payment)
        db.session.commit()
        logger.info(f"Pagamento manual de {value} para '{username}' registado com sucesso (TXID: {txid}).")
        return self._row_to_dict(payment)

    def get_payments_by_user(self, username):
        try:
            payments = PixPayment.query.filter_by(username=username, status='CONCLUIDA').order_by(PixPayment.created_at.desc()).all()
            return [self._row_to_dict(p) for p in payments]
        except Exception as e:
            logger.error(f"Erro ao buscar pagamentos para o utilizador '{username}': {e}")
            return []

    def delete_pix_payment(self, txid):
        """Apaga um registo de pagamento da base de dados."""
        payment = PixPayment.query.get(txid)
        if payment:
            try:
                db.session.delete(payment)
                db.session.commit()
                logger.info(f"Pagamento com TXID '{txid}' apagado com sucesso.")
                return True
            except Exception as e:
                db.session.rollback()
                logger.error(f"Erro ao apagar o pagamento com TXID '{txid}': {e}")
                raise
        return False

    # --- Métodos de Limpeza de Dados ---
    def delete_old_pending_payments(self, days_old):

        if not isinstance(days_old, int) or days_old <= 0:
            logger.warning("A limpeza de pagamentos pendentes foi ignorada devido a um número de dias inválido.")
            return 0
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
            cutoff_date_str = cutoff_date.isoformat()
            payments_to_delete = PixPayment.query.filter(PixPayment.status != 'CONCLUIDA', PixPayment.created_at < cutoff_date_str)
            num_deleted = payments_to_delete.delete(synchronize_session=False)
            db.session.commit()
            if num_deleted > 0: logger.info(f"{num_deleted} cobranças PIX pendentes com mais de {days_old} dias foram apagadas.")
            return num_deleted
        except Exception as e:
            logger.error(f"Erro ao apagar cobranças PIX pendentes antigas: {e}", exc_info=True)
            db.session.rollback()
            return 0

    # --- Métodos de Convites ---
    def add_invitation(self, code, details):
        invitation = Invitation(code=code, libraries=json.dumps(details.get('libraries', [])), screen_limit=details.get('screen_limit', 0), allow_downloads=details.get('allow_downloads', False), created_at=details.get('created_at'), expires_at=details.get('expires_at'), trial_duration_minutes=details.get('trial_duration_minutes', 0), overseerr_access=details.get('overseerr_access', False), max_uses=details.get('max_uses', 1), use_count=details.get('use_count', 0), claimed_by_users=json.dumps(details.get('claimed_by_users', [])))
        db.session.add(invitation)
        db.session.commit()

    def get_invitation(self, code):
        invitation = Invitation.query.get(code)
        if invitation:
            inv_dict = self._row_to_dict(invitation)
            inv_dict['libraries'] = json.loads(inv_dict['libraries'])
            inv_dict['claimed_by_users'] = json.loads(inv_dict['claimed_by_users'] or '[]')
            return inv_dict
        return None

    def get_all_pending_invitations(self):
        invitations = Invitation.query.filter(Invitation.use_count < Invitation.max_uses).all()
        invites = {}
        for invite in invitations:
            inv_dict = self._row_to_dict(invite)
            inv_dict['libraries'] = json.loads(inv_dict['libraries'])
            inv_dict['claimed_by_users'] = json.loads(inv_dict['claimed_by_users'] or '[]')
            invites[invite.code] = inv_dict
        return invites

    def increment_invitation_use(self, code, username):
        invitation = Invitation.query.get(code)
        if invitation:
            invitation.use_count += 1
            invitation.claimed_at = datetime.now(timezone.utc).isoformat()
            claimed_users = json.loads(invitation.claimed_by_users or '[]')
            if username not in claimed_users: claimed_users.append(username)
            invitation.claimed_by_users = json.dumps(claimed_users)
            db.session.commit()
    
    def delete_invitation(self, code):
        invitation = Invitation.query.get(code)
        if invitation:
            db.session.delete(invitation)
            db.session.commit()

    def get_user_claim_date(self, username):
        invitation = Invitation.query.filter(Invitation.claimed_by_users.contains(username)).order_by(Invitation.claimed_at.desc()).first()
        return invitation.claimed_at if invitation else None

    # --- Métodos de Utilizadores Bloqueados ---
    def get_blocked_user(self, username):
        """Busca um único utilizador bloqueado pelo nome."""
        user = BlockedUser.query.get(username)
        return self._row_to_dict(user) if user else None

    def get_blocked_users_list(self):
        """Retorna uma LISTA de todos os utilizadores bloqueados. Ideal para a API /api/users/status."""
        users = BlockedUser.query.all()
        return [self._row_to_dict(u) for u in users]

    def get_blocked_users_dict(self):
        """Retorna um DICIONÁRIO de utilizadores bloqueados para performance otimizada (O(1)) no StreamManager."""
        users = BlockedUser.query.all()
        return {u.username: self._row_to_dict(u) for u in users}

    def add_blocked_user(self, username, reason='manual'):
        user = BlockedUser.query.get(username)
        if not user:
            user = BlockedUser(username=username)
        user.blocked_at = datetime.now().isoformat()
        user.block_reason = reason
        db.session.add(user)
        db.session.commit()

    def remove_blocked_user(self, username):
        user = BlockedUser.query.get(username)
        if user:
            db.session.delete(user)
            db.session.commit()
            
    def _row_to_dict(self, row):
        if not row:
            return None
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}
