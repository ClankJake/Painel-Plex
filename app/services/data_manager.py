# app/services/data_manager.py
import os
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from ..extensions import db
from ..models import (
    Invitation, BlockedUser, UserProfile, PixPayment, Notification, 
    UnlockedAchievement, ShortLink, Coupon, CouponUsage, Task, StreamTerminationLog
)
from sqlalchemy import func, extract, not_
from sqlalchemy.exc import IntegrityError
from tzlocal import get_localzone
from flask_babel import gettext as _, ngettext

logger = logging.getLogger(__name__)

class DataManager:
    """Responsável por carregar e salvar dados da aplicação usando SQLAlchemy ORM."""
    
    def __init__(self):
        pass

    # --- MÉTODOS DE TAREFAS ---
    def create_task(self, name, payload):
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
        task = Task.query.filter_by(name=name, status='pending').order_by(Task.created_at).with_for_update().first()
        return task

    def update_task(self, task_id, updates):
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
            return self._row_to_dict(new_coupon)
        except Exception as e:
            db.session.rollback()
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

    def record_coupon_usage(self, code, plex_user_id):
        """
        Incrementa a contagem de uso de um cupão e regista quem o usou.
        Esta função NÃO faz commit da transação. O chamador é responsável
        por fazer o commit ou rollback da sessão da base de dados.
        """
        coupon = Coupon.query.filter_by(code=code).first()
        if not coupon or not plex_user_id:
            logger.warning(f"Tentativa de registar o uso de um cupão inválido ('{code}') ou para um utilizador inválido.")
            return False
        
        try:
            coupon.use_count += 1
            new_usage = CouponUsage(user_plex_id=plex_user_id, coupon_id=coupon.id)
            db.session.add(new_usage)
            logger.info(f"Uso do cupão '{code}' registado para o utilizador ID {plex_user_id}. Contagem de uso atual: {coupon.use_count}.")
            return True
        except Exception as e:
            # Re-lança a exceção para que o chamador possa tratar o rollback da transação.
            logger.error(f"Erro de base de dados ao registar o uso do cupão '{code}' para o utilizador ID {plex_user_id}: {e}", exc_info=True)
            raise

    def has_user_used_coupon(self, plex_user_id, code):
        usage = db.session.query(CouponUsage).join(Coupon).filter(
            Coupon.code == code,
            CouponUsage.user_plex_id == plex_user_id
        ).first()
        return usage is not None

    # --- MÉTODOS DE GAMIFICAÇÃO (CONQUISTAS) ---
    def get_unlocked_achievements(self, plex_user_id):
        achievements = UnlockedAchievement.query.filter_by(user_plex_id=plex_user_id).all()
        return {ach.achievement_id for ach in achievements}

    def add_unlocked_achievements(self, plex_user_id, username, achievements_to_add):
        try:
            for ach_data in achievements_to_add:
                new_achievement = UnlockedAchievement(
                    user_plex_id=plex_user_id,
                    username=username,
                    achievement_id=ach_data['id']
                )
                db.session.add(new_achievement)
            db.session.commit()
        except Exception as e:
            db.session.rollback()

    # --- MÉTODOS DE NOTIFICAÇÃO ---
    def create_notification(self, message, category='info', link=None, user_plex_id=None):
        from .. import extensions
        try:
            notification = Notification(
                message=message, category=category, link=link,
                user_plex_id=user_plex_id, timestamp=datetime.utcnow()
            )
            db.session.add(notification)
            db.session.commit()
            
            if extensions.socketio:
                logger.info(f"Emitindo evento 'new_notification' via Socket.IO para acionar a atualização da UI.")
                extensions.socketio.emit('new_notification', namespace='/')
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao criar notificação: {e}", exc_info=True)

    def get_notifications(self, user_plex_id=None, limit=10, include_read=False):
        query = Notification.query.filter_by(user_plex_id=user_plex_id).order_by(Notification.timestamp.desc())
        if not include_read:
            query = query.filter_by(is_read=False)
        notifications = query.limit(limit).all()
        return [self._row_to_dict(n) for n in notifications]

    def get_unread_notification_count(self, user_plex_id=None):
        return Notification.query.filter_by(user_plex_id=user_plex_id, is_read=False).count()

    def mark_all_as_read(self, user_plex_id=None):
        try:
            updated_rows = Notification.query.filter_by(user_plex_id=user_plex_id, is_read=False).update({'is_read': True})
            db.session.commit()
            return updated_rows
        except Exception as e:
            db.session.rollback()
            return 0
            
    def delete_all_notifications(self, user_plex_id=None):
        try:
            num_rows_deleted = db.session.query(Notification).filter_by(user_plex_id=user_plex_id).delete()
            db.session.commit()
            return num_rows_deleted
        except Exception as e:
            db.session.rollback()
            return 0

    def update_user_notification_timestamp(self, plex_user_id):
        try:
            profile = UserProfile.query.get(plex_user_id)
            if profile:
                profile.last_notification_sent = datetime.now(timezone.utc).isoformat()
                db.session.commit()
                return True
        except Exception as e:
            db.session.rollback()
        return False

    # --- MÉTODOS DE AUDITORIA ---
    def log_stream_termination(self, plex_user_id, username, media_title, platform, reason):
        try:
            log_entry = StreamTerminationLog(
                user_plex_id=plex_user_id, username=username, media_title=media_title,
                platform=platform, reason=reason, timestamp=datetime.utcnow()
            )
            db.session.add(log_entry)
            db.session.commit()
            from .. import extensions
            if extensions.socketio:
                extensions.socketio.emit('new_termination_log', self._row_to_dict(log_entry), namespace='/dashboard')
        except Exception as e:
            db.session.rollback()

    def get_stream_termination_logs(self, limit=20):
        logs = StreamTerminationLog.query.order_by(StreamTerminationLog.timestamp.desc()).limit(limit).all()
        return [self._row_to_dict(log) for log in logs]

    def delete_stream_termination_log(self, log_id):
        """Apaga um único log de término pelo seu ID."""
        try:
            log_entry = StreamTerminationLog.query.get(log_id)
            if log_entry:
                db.session.delete(log_entry)
                db.session.commit()
                return True
            return False
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao apagar o log de término {log_id}: {e}", exc_info=True)
            raise

    def clear_all_stream_termination_logs(self):
        """Apaga todos os logs de término da tabela."""
        try:
            num_rows_deleted = db.session.query(StreamTerminationLog).delete()
            db.session.commit()
            return num_rows_deleted
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao limpar todos os logs de término: {e}", exc_info=True)
            raise

    # --- MÉTODOS FINANCEIROS ---
    def get_financial_summary(self, year, month, renewal_days=7):
        summary_query = db.session.query(func.sum(PixPayment.value), func.count(PixPayment.txid)).filter(
            extract('year', PixPayment.created_at) == year, extract('month', PixPayment.created_at) == month, PixPayment.status == 'CONCLUIDA'
        ).first()
        total_revenue, sales_count = (summary_query[0] or 0.0, summary_query[1] or 0)
        daily_revenue_query = db.session.query(extract('day', PixPayment.created_at).label('day'), func.sum(PixPayment.value).label('total')).filter(
            extract('year', PixPayment.created_at) == year, extract('month', PixPayment.created_at) == month, PixPayment.status == 'CONCLUIDA'
        ).group_by('day').all()
        daily_revenue_dict = {day: total for day, total in daily_revenue_query}
        weekly_revenue_query = db.session.query(func.strftime('%W', PixPayment.created_at).label('week_num'), func.sum(PixPayment.value).label('total')).filter(
            extract('year', PixPayment.created_at) == year, extract('month', PixPayment.created_at) == month, PixPayment.status == 'CONCLUIDA'
        ).group_by('week_num').order_by('week_num').all()
        weekly_revenue_dict = {f"Semana {i+1}": total for i, (_, total) in enumerate(weekly_revenue_query)}
        recent_transactions = PixPayment.query.filter(
            extract('year', PixPayment.created_at) == year, extract('month', PixPayment.created_at) == month, PixPayment.status == 'CONCLUIDA'
        ).order_by(PixPayment.created_at.desc()).limit(10).all()

        today = datetime.now(get_localzone()).date()
        end_date = today + timedelta(days=renewal_days)
        today_str, end_date_str = today.isoformat(), (end_date + timedelta(days=1)).isoformat()
        
        blocked_user_ids = [u.user_plex_id for u in BlockedUser.query.all()]
        expiring_users_query = db.session.query(UserProfile).filter(
            UserProfile.expiration_date.isnot(None), UserProfile.expiration_date != '',
            UserProfile.expiration_date >= today_str, UserProfile.expiration_date < end_date_str,
            not_(UserProfile.plex_user_id.in_(blocked_user_ids))
        ).order_by(UserProfile.expiration_date.asc()).all()
        
        upcoming_expirations = []
        for p in expiring_users_query:
            try:
                exp_date = datetime.fromisoformat(p.expiration_date).date()
                days_left = (exp_date - today).days
                days_text = ngettext('%(num)d dia restante', '%(num)d dias restantes', days_left) % {'num': days_left} if days_left > 0 else (_("Hoje") if days_left == 0 else _("Expirado"))
                upcoming_expirations.append({'username': p.username, 'expiration_date': exp_date.strftime('%d/%m/%Y'), 'days_left': days_left, 'days_left_text': days_text, 'screen_limit': p.screen_limit})
            except (ValueError, TypeError): continue
        
        return {"total_revenue": total_revenue, "sales_count": sales_count, "recent_transactions": [self._row_to_dict(p) for p in recent_transactions], "daily_revenue": daily_revenue_dict, "weekly_revenue": weekly_revenue_dict, "upcoming_expirations": upcoming_expirations}

    def get_payments_for_export(self, start_date_iso, end_date_iso):
        try:
            payments = PixPayment.query.filter(PixPayment.status == 'CONCLUIDA', PixPayment.created_at >= start_date_iso, PixPayment.created_at <= end_date_iso).order_by(PixPayment.created_at.asc()).all()
            return [self._row_to_dict(p) for p in payments]
        except Exception: return []

    def get_latest_completed_payment(self, plex_user_id):
        """Busca o pagamento concluído mais recente para um utilizador."""
        payment = PixPayment.query.filter_by(
            user_plex_id=plex_user_id,
            status='CONCLUIDA'
        ).order_by(PixPayment.created_at.desc()).first()
        return self._row_to_dict(payment) if payment else None
        
    # --- MÉTODOS para Perfis de Utilizador ---
    def get_user_profile(self, plex_user_id):
        profile = UserProfile.query.get(plex_user_id)
        if profile:
            return self._row_to_dict(profile)
        
        from ..extensions import plex_manager
        user_info = plex_manager.get_user_by_id(plex_user_id)
        if user_info:
            logger.info(f"Perfil de utilizador para '{user_info['username']}' (ID: {plex_user_id}) não encontrado. A criar um perfil básico.")
            new_profile_data = {
                'username': user_info['username'],
                'email': user_info.get('email'),
                'screen_limit': 0,
                'allow_downloads': False,
                'overseerr_access': False,
                'hide_from_leaderboard': False
            }
            self.set_user_profile(plex_user_id, new_profile_data)
            profile = UserProfile.query.get(plex_user_id)
            return self._row_to_dict(profile)
            
        return {}

    def get_user_profile_by_username(self, username):
        profile = UserProfile.query.filter(func.lower(UserProfile.username) == username.lower()).first()
        if profile:
            return self._row_to_dict(profile)

        from ..extensions import plex_manager
        all_users = plex_manager.get_all_plex_users()
        if all_users:
            user_info = next((u for u in all_users if u['username'].lower() == username.lower()), None)
            if user_info:
                plex_user_id = user_info['id']
                logger.info(f"Perfil de utilizador para '{username}' (ID: {plex_user_id}) não encontrado. A criar um perfil básico.")
                new_profile_data = {
                    'username': user_info['username'],
                    'email': user_info.get('email'),
                    'screen_limit': 0,
                    'allow_downloads': False,
                    'overseerr_access': False,
                    'hide_from_leaderboard': False
                }
                self.set_user_profile(plex_user_id, new_profile_data)
                profile = UserProfile.query.get(plex_user_id)
                return self._row_to_dict(profile)
        return {}

    def get_user_profiles_by_username(self, usernames):
        if not usernames: return {}
        try:
            profiles = UserProfile.query.filter(func.lower(UserProfile.username).in_([u.lower() for u in usernames])).all()
            return {p.username: self._row_to_dict(p) for p in profiles}
        except Exception: return {}

    def get_all_user_profiles(self):
        profiles = UserProfile.query.all()
        return [self._row_to_dict(p) for p in profiles]

    def get_user_profiles_by_id(self, plex_user_ids):
        if not plex_user_ids: return {}
        try:
            profiles = UserProfile.query.filter(UserProfile.plex_user_id.in_(plex_user_ids)).all()
            return {p.plex_user_id: self._row_to_dict(p) for p in profiles}
        except Exception: return {}

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
        db.session.commit()
    
    def delete_user_profile(self, plex_user_id):
        profile = UserProfile.query.get(plex_user_id)
        if profile:
            db.session.delete(profile)
            db.session.commit()

    def get_all_user_expirations(self):
        profiles = UserProfile.query.filter(UserProfile.expiration_date.isnot(None), UserProfile.expiration_date != '').all()
        return {p.plex_user_id: self._row_to_dict(p) for p in profiles}

    def get_all_trial_users(self):
        profiles = UserProfile.query.filter(UserProfile.trial_end_date.isnot(None), UserProfile.trial_end_date != '').all()
        return {p.plex_user_id: self._row_to_dict(p) for p in profiles}

    # --- MÉTODOS de Pagamento PIX ---
    def get_and_lock_pix_payment(self, txid):
        try:
            return self._row_to_dict(db.session.query(PixPayment).filter_by(txid=txid).with_for_update().first())
        except Exception: raise

    def create_pix_payment(self, txid, plex_user_id, username, value, provider, screens, external_reference, coupon_code=None):
        payment = PixPayment.query.get(txid) or PixPayment(txid=txid)
        payment.user_plex_id, payment.username, payment.value, payment.provider, payment.created_at, payment.status, payment.screens, payment.external_reference, payment.coupon_code = \
            plex_user_id, username, value, provider, datetime.now().isoformat(), 'ATIVA', screens, external_reference, coupon_code
        db.session.add(payment)
        db.session.commit()

    def get_pix_payment(self, txid):
        return self._row_to_dict(PixPayment.query.get(txid))

    def update_pix_payment_status(self, txid, status):
        payment = PixPayment.query.get(txid)
        if payment: payment.status = status

    def add_manual_payment(self, plex_user_id, username, value, description, payment_date_str):
        txid = f"manual_{secrets.token_hex(12)}"
        payment = PixPayment(txid=txid, user_plex_id=plex_user_id, username=username, value=float(value), status='CONCLUIDA', provider='Manual', description=description, created_at=payment_date_str, screens=0, external_reference=None)
        db.session.add(payment)
        # O commit agora é da responsabilidade do chamador
        return self._row_to_dict(payment)

    def get_payments_by_user(self, plex_user_id):
        try:
            return [self._row_to_dict(p) for p in PixPayment.query.filter_by(user_plex_id=plex_user_id, status='CONCLUIDA').order_by(PixPayment.created_at.desc()).all()]
        except Exception: return []

    def delete_pix_payment(self, txid):
        payment = PixPayment.query.get(txid)
        if payment:
            try:
                db.session.delete(payment)
                db.session.commit()
                return True
            except Exception:
                db.session.rollback()
                raise
        return False

    # --- MÉTODOS de Limpeza de Dados ---
    def delete_old_pending_payments(self, days_old):
        if not isinstance(days_old, int) or days_old <= 0: return 0
        try:
            cutoff_date_str = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
            num_deleted = PixPayment.query.filter(PixPayment.status != 'CONCLUIDA', PixPayment.created_at < cutoff_date_str).delete(synchronize_session=False)
            db.session.commit()
            if num_deleted > 0: logger.info(f"{num_deleted} cobranças PIX pendentes com mais de {days_old} dias foram apagadas.")
            return num_deleted
        except Exception:
            db.session.rollback()
            return 0

    def delete_old_short_links(self, days_old):
        """Apaga links curtos mais antigos que o número de dias especificado."""
        if not isinstance(days_old, int) or days_old <= 0:
            return 0
        try:
            # Define a data de corte em UTC
            cutoff_date_utc = datetime.now(timezone.utc) - timedelta(days=days_old)
            
            # Apaga os links onde created_at é mais antigo que a data de corte
            num_deleted = ShortLink.query.filter(
                ShortLink.created_at < cutoff_date_utc
            ).delete(synchronize_session=False)
            
            db.session.commit()
            if num_deleted > 0:
                logger.info(f"{num_deleted} links curtos com mais de {days_old} dias foram apagados.")
            return num_deleted
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao apagar links curtos antigos: {e}", exc_info=True)
            return 0

    # --- MÉTODOS de Convites ---
    def add_invitation(self, code, details):
        invitation = Invitation(code=code, libraries=json.dumps(details.get('libraries', [])), screen_limit=details.get('screen_limit', 0), allow_downloads=details.get('allow_downloads', False), created_at=details.get('created_at'), expires_at=details.get('expires_at'), trial_duration_minutes=details.get('trial_duration_minutes', 0), overseerr_access=details.get('overseerr_access', False), max_uses=details.get('max_uses', 1), use_count=details.get('use_count', 0), claimed_by_users=json.dumps(details.get('claimed_by_users', [])))
        db.session.add(invitation)
        db.session.commit()

    def get_invitation(self, code):
        invitation = Invitation.query.get(code)
        return self._row_to_dict(invitation, process_json=True) if invitation else None

    def get_all_pending_invitations(self):
        invitations = Invitation.query.filter(Invitation.use_count < Invitation.max_uses).all()
        return [self._row_to_dict(invite, process_json=True) for invite in invitations]

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

    def add_blocked_user(self, plex_user_id, username, reason='manual'):
        """Adiciona ou atualiza um utilizador na lista de bloqueados de forma segura."""
        try:
            user = BlockedUser.query.get(plex_user_id)
            if not user:
                user = BlockedUser(user_plex_id=plex_user_id, username=username)

            user.blocked_at = datetime.now(get_localzone()).isoformat()
            
            user.block_reason = reason
            db.session.add(user)
            db.session.commit()
            logger.info(f"Utilizador '{username}' (ID: {plex_user_id}) adicionado/atualizado na lista de bloqueados com o motivo '{reason}'.")
        except IntegrityError: 
            db.session.rollback()
            logger.error(f"Falha de integridade ao bloquear o utilizador '{username}'.")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro inesperado ao adicionar utilizador bloqueado '{username}': {e}", exc_info=True)
            raise

    def remove_blocked_user(self, plex_user_id):
        user = BlockedUser.query.get(plex_user_id)
        if user:
            db.session.delete(user)
            db.session.commit()
            
    def _row_to_dict(self, row, process_json=False):
        if not row: return None
        d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        if process_json:
            if 'libraries' in d and d['libraries']: d['libraries'] = json.loads(d['libraries'])
            if 'claimed_by_users' in d and d['claimed_by_users']: d['claimed_by_users'] = json.loads(d['claimed_by_users'])
        return d
