# app/services/data_manager.py

import os
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from ..extensions import db
from ..models import (
    Invitation, BlockedUser, UserProfile, PixPayment, Notification, 
    UnlockedAchievement, ShortLink, Coupon, CouponUsage, Task, StreamTerminationLog
)
from sqlalchemy import func, extract, not_
from sqlalchemy.exc import IntegrityError
from flask_babel import gettext as _, ngettext
from collections import defaultdict

logger = logging.getLogger(__name__)

# --- DECORADOR PARA TRANSAÇÕES DA BASE DE DADOS ---
def db_transaction(f):
    """
    Decorador para gerir transações da base de dados de forma automática.
    Efetua o commit em caso de sucesso ou o rollback em caso de erro.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            result = f(*args, **kwargs)
            db.session.commit()
            return result
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro na transação da base de dados em {f.__name__}: {e}", exc_info=True)
            raise
    return wrapper


class DataManager:
    """Responsável por carregar e salvar dados da aplicação usando SQLAlchemy ORM."""
    
    def __init__(self):
        pass

    # --- MÉTODOS DE TAREFAS ---
    @db_transaction
    def create_task(self, name, payload):
        task = Task(name=name, payload=json.dumps(payload))
        db.session.add(task)
        logger.info(f"Tarefa '{name}' criada na base de dados.")
        return self._row_to_dict(task)

    def get_next_pending_task(self, name):
        # with_for_update não deve estar num wrapper genérico, por isso lidamos com ele diretamente
        return Task.query.filter_by(name=name, status='pending').order_by(Task.created_at).with_for_update().first()

    @db_transaction
    def update_task(self, task_id, updates):
        task = Task.query.get(task_id)
        if task:
            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            return self._row_to_dict(task)
        return None

    # --- MÉTODOS DE CUPÕES ---
    @db_transaction
    def create_coupon(self, details):
        new_coupon = Coupon(**details)
        db.session.add(new_coupon)
        return self._row_to_dict(new_coupon)

    def get_coupon_by_code(self, code):
        coupon = Coupon.query.filter_by(code=code).first()
        return self._row_to_dict(coupon) if coupon else None

    def get_all_coupons(self):
        coupons = Coupon.query.order_by(Coupon.created_at.desc()).all()
        return [self._row_to_dict(c) for c in coupons]

    @db_transaction
    def delete_coupon(self, coupon_id):
        coupon = Coupon.query.get(coupon_id)
        if coupon:
            db.session.delete(coupon)
            return True
        return False

    @db_transaction
    def toggle_coupon_active(self, coupon_id):
        coupon = Coupon.query.get(coupon_id)
        if coupon:
            coupon.is_active = not coupon.is_active
            return self._row_to_dict(coupon)
        return None

    @db_transaction
    def record_coupon_usage(self, code, plex_user_id):
        coupon = Coupon.query.filter_by(code=code).first()
        if not coupon or not plex_user_id:
            logger.warning(f"Tentativa de registar o uso de um cupão inválido ('{code}') ou para um utilizador inválido.")
            return False
        
        coupon.use_count += 1
        new_usage = CouponUsage(user_plex_id=plex_user_id, coupon_id=coupon.id)
        db.session.add(new_usage)
        logger.info(f"Uso do cupão '{code}' registado para o utilizador ID {plex_user_id}. Contagem: {coupon.use_count}.")
        return True

    def has_user_used_coupon(self, plex_user_id, code):
        usage = db.session.query(CouponUsage).join(Coupon).filter(
            Coupon.code == code,
            CouponUsage.user_plex_id == plex_user_id
        ).first()
        return usage is not None

    # --- MÉTODOS DE GAMIFICAÇÃO ---
    def get_unlocked_achievements(self, plex_user_id):
        achievements = UnlockedAchievement.query.filter_by(user_plex_id=plex_user_id).all()
        return {ach.achievement_id for ach in achievements}

    @db_transaction
    def add_unlocked_achievements(self, plex_user_id, username, achievements_to_add):
        for ach_data in achievements_to_add:
            new_achievement = UnlockedAchievement(
                user_plex_id=plex_user_id,
                username=username,
                achievement_id=ach_data['id']
            )
            db.session.add(new_achievement)

    # --- MÉTODOS DE NOTIFICAÇÃO ---
    @db_transaction
    def create_notification(self, message, category='info', link=None, user_plex_id=None):
        """
        Guarda a notificação. A emissão de WebSockets deve ser feita pela Rota/Serviço que chamou este método.
        """
        notification = Notification(
            message=message, category=category, link=link,
            user_plex_id=user_plex_id, timestamp=datetime.now(timezone.utc)
        )
        db.session.add(notification)
        return self._row_to_dict(notification)

    def get_notifications(self, user_plex_id=None, limit=10, include_read=False):
        query = Notification.query.filter_by(user_plex_id=user_plex_id).order_by(Notification.timestamp.desc())
        if not include_read:
            query = query.filter_by(is_read=False)
        notifications = query.limit(limit).all()
        return [self._row_to_dict(n) for n in notifications]

    def get_unread_notification_count(self, user_plex_id=None):
        return Notification.query.filter_by(user_plex_id=user_plex_id, is_read=False).count()

    @db_transaction
    def mark_all_as_read(self, user_plex_id=None):
        updated_rows = Notification.query.filter_by(user_plex_id=user_plex_id, is_read=False).update({'is_read': True})
        return updated_rows
            
    @db_transaction
    def delete_all_notifications(self, user_plex_id=None):
        num_rows_deleted = db.session.query(Notification).filter_by(user_plex_id=user_plex_id).delete()
        return num_rows_deleted

    @db_transaction
    def update_user_notification_timestamp(self, plex_user_id):
        profile = UserProfile.query.get(plex_user_id)
        if profile:
            profile.last_notification_sent = datetime.now(timezone.utc).isoformat()
            return True
        return False

    # --- MÉTODOS DE AUDITORIA ---
    @db_transaction
    def log_stream_termination(self, plex_user_id, username, media_title, platform, reason):
        """
        Guarda o log de términ. A emissão via socketio deve ser tratada no serviço correspondente.
        """
        log_entry = StreamTerminationLog(
            user_plex_id=plex_user_id, username=username, media_title=media_title,
            platform=platform, reason=reason, timestamp=datetime.now(timezone.utc)
        )
        db.session.add(log_entry)
        return self._row_to_dict(log_entry)

    def get_stream_termination_logs(self, limit=20):
        logs = StreamTerminationLog.query.order_by(StreamTerminationLog.timestamp.desc()).limit(limit).all()
        return [self._row_to_dict(log) for log in logs]

    @db_transaction
    def delete_stream_termination_log(self, log_id):
        log_entry = StreamTerminationLog.query.get(log_id)
        if log_entry:
            db.session.delete(log_entry)
            return True
        return False

    @db_transaction
    def clear_all_stream_termination_logs(self):
        num_rows_deleted = db.session.query(StreamTerminationLog).delete()
        return num_rows_deleted

    # --- MÉTODOS FINANCEIROS OTIMIZADOS ---
    def get_financial_summary(self, year, month, renewal_days=7):
        # 1. Busca TODOS os pagamentos concluídos do mês em UMA ÚNICA consulta
        payments_in_month = db.session.query(PixPayment).filter(
            extract('year', PixPayment.created_at) == year, 
            extract('month', PixPayment.created_at) == month, 
            PixPayment.status == 'CONCLUIDA'
        ).order_by(PixPayment.created_at.desc()).all()

        # 2. Agregação em Memória
        total_revenue = 0.0
        sales_count = 0
        daily_revenue_map = defaultdict(float)
        weekly_revenue_map = defaultdict(float)
        recent_transactions = []
        
        for i, payment in enumerate(payments_in_month):
            total_revenue += payment.value
            sales_count += 1
            
            if i < 10:
                recent_transactions.append(self._row_to_dict(payment))
            
            try:
                dt = datetime.fromisoformat(payment.created_at)
                day = dt.day
                week_num = int(dt.strftime('%W'))
                
                daily_revenue_map[day] += payment.value
                weekly_revenue_map[week_num] += payment.value
            except (ValueError, TypeError):
                continue
        
        sorted_weeks = sorted(weekly_revenue_map.keys())
        weekly_revenue_dict = {f"Semana {idx + 1}": weekly_revenue_map[w] for idx, w in enumerate(sorted_weeks)}

        # 3. Otimização de Utilizadores a Expirar (Query Única com JOIN)
        today = datetime.now(timezone.utc).date()
        end_date = today + timedelta(days=renewal_days)
        today_str = today.isoformat()
        end_date_str = (end_date + timedelta(days=1)).isoformat()
        
        expiring_users_query = db.session.query(UserProfile).outerjoin(BlockedUser).filter(
            BlockedUser.user_plex_id == None, 
            UserProfile.expiration_date.isnot(None), 
            UserProfile.expiration_date != '',
            UserProfile.expiration_date >= today_str, 
            UserProfile.expiration_date < end_date_str
        ).order_by(UserProfile.expiration_date.asc()).all()
        
        upcoming_expirations = []
        for p in expiring_users_query:
            try:
                exp_date = datetime.fromisoformat(p.expiration_date).date()
                days_left = (exp_date - today).days
                days_text = ngettext('%(num)d dia restante', '%(num)d dias restantes', days_left) % {'num': days_left} if days_left > 0 else (_("Hoje") if days_left == 0 else _("Expirado"))
                upcoming_expirations.append({
                    'username': p.username, 
                    'expiration_date': exp_date.strftime('%d/%m/%Y'), 
                    'days_left': days_left, 
                    'days_left_text': days_text, 
                    'screen_limit': p.screen_limit
                })
            except (ValueError, TypeError): continue
        
        return {
            "total_revenue": total_revenue, 
            "sales_count": sales_count, 
            "recent_transactions": recent_transactions, 
            "daily_revenue": dict(daily_revenue_map), 
            "weekly_revenue": weekly_revenue_dict, 
            "upcoming_expirations": upcoming_expirations
        }

    def get_payments_for_export(self, start_date_iso, end_date_iso):
        try:
            payments = PixPayment.query.filter(PixPayment.status == 'CONCLUIDA', PixPayment.created_at >= start_date_iso, PixPayment.created_at <= end_date_iso).order_by(PixPayment.created_at.asc()).all()
            return [self._row_to_dict(p) for p in payments]
        except Exception: 
            return []

    def get_latest_completed_payment(self, plex_user_id):
        payment = PixPayment.query.filter_by(
            user_plex_id=plex_user_id,
            status='CONCLUIDA'
        ).order_by(PixPayment.created_at.desc()).first()
        return self._row_to_dict(payment) if payment else None
        
    # --- MÉTODOS para Perfis de Utilizador ---
    def get_user_profile(self, plex_user_id):
        """
        Retorna o perfil da base de dados. Se não existir, retorna None.
        NOTA: A criação do perfil se ele não existir deve agora ser tratada no user_manager.py ou nas rotas.
        """
        profile = UserProfile.query.get(plex_user_id)
        return self._row_to_dict(profile) if profile else None

    def get_user_profile_by_username(self, username):
        """Retorna o perfil pelo username. Retorna None se não existir."""
        profile = UserProfile.query.filter(func.lower(UserProfile.username) == username.lower()).first()
        return self._row_to_dict(profile) if profile else None
    
    def get_user_profile_by_telegram(self, telegram_id):
        profile = UserProfile.query.filter_by(telegram_user=telegram_id).first()
        return self._row_to_dict(profile) if profile else None

    def get_user_profiles_by_username(self, usernames):
        if not usernames: return {}
        try:
            profiles = UserProfile.query.filter(func.lower(UserProfile.username).in_([u.lower() for u in usernames])).all()
            return {p.username: self._row_to_dict(p) for p in profiles}
        except Exception: return {}

    def get_all_user_profiles(self):
        # NOTA: Considere adicionar paginação no futuro se tiver +10k utilizadores
        profiles = UserProfile.query.all()
        return [self._row_to_dict(p) for p in profiles]

    def get_user_profiles_by_id(self, plex_user_ids):
        if not plex_user_ids: return {}
        try:
            profiles = UserProfile.query.filter(UserProfile.plex_user_id.in_(plex_user_ids)).all()
            return {p.plex_user_id: self._row_to_dict(p) for p in profiles}
        except Exception: return {}

    @db_transaction
    def set_user_profile(self, plex_user_id, profile_data):
        profile = UserProfile.query.get(plex_user_id)
        if not profile:
            profile = UserProfile(plex_user_id=plex_user_id)
        if not profile.payment_token:
            profile.payment_token = secrets.token_urlsafe(16)
        
        for key, value in profile_data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        
        db.session.add(profile)
        return self._row_to_dict(profile)
    
    @db_transaction
    def delete_user_profile(self, plex_user_id):
        profile = UserProfile.query.get(plex_user_id)
        if profile:
            db.session.delete(profile)
            return True
        return False

    def get_all_user_expirations(self):
        profiles = UserProfile.query.filter(UserProfile.expiration_date.isnot(None), UserProfile.expiration_date != '').all()
        return {p.plex_user_id: self._row_to_dict(p) for p in profiles}

    def get_all_trial_users(self):
        profiles = UserProfile.query.filter(UserProfile.trial_end_date.isnot(None), UserProfile.trial_end_date != '').all()
        return {p.plex_user_id: self._row_to_dict(p) for p in profiles}

    # --- MÉTODOS de Pagamento PIX ---
    def get_and_lock_pix_payment(self, txid):
        # Não usa o wrapper db_transaction por causa do with_for_update
        try:
            return self._row_to_dict(db.session.query(PixPayment).filter_by(txid=txid).with_for_update().first())
        except Exception: 
            db.session.rollback()
            raise

    @db_transaction
    def create_pix_payment(self, txid, plex_user_id, username, value, provider, screens, external_reference, coupon_code=None):
        payment = PixPayment.query.get(txid) or PixPayment(txid=txid)
        payment.user_plex_id = plex_user_id
        payment.username = username
        payment.value = value
        payment.provider = provider
        payment.created_at = datetime.now(timezone.utc).isoformat()
        payment.status = 'ATIVA'
        payment.screens = screens
        payment.external_reference = external_reference
        payment.coupon_code = coupon_code
        db.session.add(payment)
        return self._row_to_dict(payment)

    def get_pix_payment(self, txid):
        return self._row_to_dict(PixPayment.query.get(txid))

    @db_transaction
    def update_pix_payment_status(self, txid, status):
        payment = PixPayment.query.get(txid)
        if payment: 
            payment.status = status
            return True
        return False

    def add_manual_payment(self, plex_user_id, username, value, description, payment_date_str):
        txid = f"manual_{secrets.token_hex(12)}"
        payment = PixPayment(txid=txid, user_plex_id=plex_user_id, username=username, value=float(value), status='CONCLUIDA', provider='Manual', description=description, created_at=payment_date_str, screens=0, external_reference=None)
        db.session.add(payment)
        # O commit continua a ser da responsabilidade do chamador como indicado originalmente
        return self._row_to_dict(payment)

    def get_payments_by_user(self, plex_user_id):
        try:
            return [self._row_to_dict(p) for p in PixPayment.query.filter_by(user_plex_id=plex_user_id, status='CONCLUIDA').order_by(PixPayment.created_at.desc()).all()]
        except Exception: return []

    @db_transaction
    def delete_pix_payment(self, txid):
        payment = PixPayment.query.get(txid)
        if payment:
            db.session.delete(payment)
            return True
        return False

    # --- MÉTODOS de Limpeza de Dados ---
    @db_transaction
    def delete_old_pending_payments(self, days_old):
        if not isinstance(days_old, int) or days_old <= 0: return 0
        cutoff_date_str = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        num_deleted = PixPayment.query.filter(PixPayment.status != 'CONCLUIDA', PixPayment.created_at < cutoff_date_str).delete(synchronize_session=False)
        if num_deleted > 0: 
            logger.info(f"{num_deleted} cobranças PIX pendentes com mais de {days_old} dias foram apagadas.")
        return num_deleted

    @db_transaction
    def delete_old_short_links(self, days_old):
        if not isinstance(days_old, int) or days_old <= 0: return 0
        cutoff_date_utc = datetime.now(timezone.utc) - timedelta(days=days_old)
        num_deleted = ShortLink.query.filter(ShortLink.created_at < cutoff_date_utc).delete(synchronize_session=False)
        if num_deleted > 0:
            logger.info(f"{num_deleted} links curtos com mais de {days_old} dias foram apagados.")
        return num_deleted

    # --- MÉTODOS de Convites ---
    @db_transaction
    def add_invitation(self, code, details):
        invitation = Invitation(
            code=code, 
            libraries=json.dumps(details.get('libraries', [])), 
            screen_limit=details.get('screen_limit', 0), 
            allow_downloads=details.get('allow_downloads', False), 
            created_at=details.get('created_at'), 
            expires_at=details.get('expires_at'), 
            trial_duration_minutes=details.get('trial_duration_minutes', 0), 
            overseerr_access=details.get('overseerr_access', False), 
            max_uses=details.get('max_uses', 1), 
            use_count=details.get('use_count', 0), 
            claimed_by_users=json.dumps(details.get('claimed_by_users', [])),
            telegram_id=details.get('telegram_id')
        )
        db.session.add(invitation)
        return self._row_to_dict(invitation)

    def get_invitation(self, code):
        invitation = Invitation.query.get(code)
        return self._row_to_dict(invitation, process_json=True) if invitation else None

    def get_all_pending_invitations(self):
        invitations = Invitation.query.filter(Invitation.use_count < Invitation.max_uses).all()
        return [self._row_to_dict(invite, process_json=True) for invite in invitations]

    def get_all_invitations(self):
        invitations = Invitation.query.order_by(Invitation.created_at.desc()).all()
        return [self._row_to_dict(invite, process_json=True) for invite in invitations]

    def check_telegram_id_exists_in_invites(self, telegram_id):
        if not telegram_id: return False
        now_str = datetime.now(timezone.utc).isoformat()
        invitation = Invitation.query.filter(
            Invitation.telegram_id == telegram_id,
            Invitation.use_count < Invitation.max_uses
        ).filter(
            (Invitation.expires_at == None) | (Invitation.expires_at > now_str)
        ).first()
        return invitation is not None

    @db_transaction
    def increment_invitation_use(self, code, username):
        invitation = Invitation.query.get(code)
        if invitation:
            invitation.use_count += 1
            invitation.claimed_at = datetime.now(timezone.utc).isoformat()
            claimed_users = json.loads(invitation.claimed_by_users or '[]')
            if username not in claimed_users: 
                claimed_users.append(username)
            invitation.claimed_by_users = json.dumps(claimed_users)
            return True
        return False
            
    @db_transaction
    def reset_invitation_usage(self, code):
        invitation = Invitation.query.get(code)
        if invitation:
            invitation.use_count = 0
            if invitation.expires_at:
                try:
                    if datetime.fromisoformat(invitation.expires_at) < datetime.now(timezone.utc):
                        invitation.expires_at = None
                except (ValueError, TypeError):
                     invitation.expires_at = None
            logger.info(f"Convite '{code}' reativado manualmente (contagem resetada).")
            return True
        return False
    
    @db_transaction
    def delete_invitation(self, code):
        invitation = Invitation.query.get(code)
        if invitation:
            db.session.delete(invitation)
            return True
        return False

    def get_user_claim_date(self, plex_user_id):
        profile = UserProfile.query.get(plex_user_id)
        if not profile: return None
        invitation = Invitation.query.filter(Invitation.claimed_by_users.contains(profile.username)).order_by(Invitation.claimed_at.desc()).first()
        return invitation.claimed_at if invitation else None

    # --- MÉTODOS de Utilizadores Bloqueados ---
    def get_blocked_user(self, plex_user_id):
        return self._row_to_dict(BlockedUser.query.get(plex_user_id))

    def get_blocked_users_list(self):
        return [self._row_to_dict(u) for u in BlockedUser.query.all()]

    def get_blocked_users_dict(self):
        return {u.user_plex_id: self._row_to_dict(u) for u in BlockedUser.query.all()}

    @db_transaction
    def add_blocked_user(self, plex_user_id, username, reason='manual'):
        user = BlockedUser.query.get(plex_user_id)
        if not user:
            user = BlockedUser(user_plex_id=plex_user_id, username=username)

        user.blocked_at = datetime.now(timezone.utc).isoformat()
        user.block_reason = reason
        db.session.add(user)
        logger.info(f"Utilizador '{username}' (ID: {plex_user_id}) adicionado/atualizado na lista de bloqueados.")
        return self._row_to_dict(user)

    @db_transaction
    def remove_blocked_user(self, plex_user_id):
        user = BlockedUser.query.get(plex_user_id)
        if user:
            db.session.delete(user)
            return True
        return False
            
    def _row_to_dict(self, row, process_json=False):
        if not row: return None
        d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        if process_json:
            if 'libraries' in d and d['libraries']: d['libraries'] = json.loads(d['libraries'])
            if 'claimed_by_users' in d and d['claimed_by_users']: d['claimed_by_users'] = json.loads(d['claimed_by_users'])
        return d
