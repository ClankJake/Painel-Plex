# app/services/data_manager.py
import os
import json
import logging
import secrets
from datetime import datetime, timedelta
from ..extensions import db
from ..models import Invitation, BlockedUser, UserProfile, PixPayment, Notification
from sqlalchemy import func, extract, not_
from tzlocal import get_localzone
from flask_babel import gettext as _, ngettext

logger = logging.getLogger(__name__)

class DataManager:
    """Responsável por carregar e salvar dados da aplicação usando SQLAlchemy ORM."""
    
    def __init__(self):
        # O caminho da base de dados é agora gerido centralmente no __init__.py da app
        # e a instância do db é injetada através das extensões.
        pass

    # --- MÉTODOS DE NOTIFICAÇÃO ---
    def create_notification(self, message, category='info', link=None):
        """Cria uma nova notificação no sistema."""
        try:
            notification = Notification(
                message=message,
                category=category,
                link=link,
                timestamp=datetime.utcnow()
            )
            db.session.add(notification)
            db.session.commit()
            logger.info(f"Notificação criada: '{message}' (Categoria: {category})")
        except Exception as e:
            logger.error(f"Falha ao criar notificação: {e}")
            db.session.rollback()

    def get_notifications(self, limit=10, include_read=False):
        """Busca as notificações mais recentes."""
        query = Notification.query.order_by(Notification.timestamp.desc())
        if not include_read:
            query = query.filter_by(is_read=False)
        
        notifications = query.limit(limit).all()
        return [self._row_to_dict(n) for n in notifications]

    def get_unread_notification_count(self):
        """Retorna o número de notificações não lidas."""
        return Notification.query.filter_by(is_read=False).count()

    def mark_all_as_read(self):
        """Marca todas as notificações como lidas."""
        try:
            updated_rows = Notification.query.filter_by(is_read=False).update({'is_read': True})
            db.session.commit()
            logger.info(f"{updated_rows} notificações marcadas como lidas.")
            return updated_rows
        except Exception as e:
            logger.error(f"Falha ao marcar todas as notificações como lidas: {e}")
            db.session.rollback()
            return 0
            
    def delete_all_notifications(self):
        """Apaga todas as notificações da base de dados."""
        try:
            num_rows_deleted = db.session.query(Notification).delete()
            db.session.commit()
            logger.info(f"{num_rows_deleted} notificações foram apagadas.")
            return num_rows_deleted
        except Exception as e:
            logger.error(f"Falha ao apagar todas as notificações: {e}")
            db.session.rollback()
            return 0
            
    def get_financial_summary(self, year, month, renewal_days=7):
        """Coleta um resumo financeiro para um determinado mês e ano."""
        
        confirmed_payments_query = db.session.query(
            PixPayment
        ).filter(
            extract('year', PixPayment.created_at) == year,
            extract('month', PixPayment.created_at) == month,
            PixPayment.status == 'CONCLUIDA'
        )

        confirmed_payments = confirmed_payments_query.order_by(PixPayment.created_at.desc()).all()
        
        total_revenue = sum(p.value for p in confirmed_payments)
        sales_count = len(confirmed_payments)

        daily_revenue_data = db.session.query(
            extract('day', PixPayment.created_at).label('day'),
            func.sum(PixPayment.value).label('total')
        ).filter(
            extract('year', PixPayment.created_at) == year,
            extract('month', PixPayment.created_at) == month,
            PixPayment.status == 'CONCLUIDA'
        ).group_by('day').order_by('day').all()

        weekly_revenue_data = db.session.query(
            func.strftime('%W', PixPayment.created_at).label('week_number'),
            func.sum(PixPayment.value).label('total')
        ).filter(
            extract('year', PixPayment.created_at) == year,
            extract('month', PixPayment.created_at) == month,
            PixPayment.status == 'CONCLUIDA'
        ).group_by('week_number').order_by('week_number').all()

        weekly_revenue_dict = {}
        if weekly_revenue_data:
            first_week_num = int(weekly_revenue_data[0].week_number)
            for row in weekly_revenue_data:
                relative_week = int(row.week_number) - first_week_num + 1
                weekly_revenue_dict[f"Semana {relative_week}"] = row.total

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

                # --- INÍCIO DA ALTERAÇÃO ---
                # Adiciona um campo de texto formatado para a exibição
                if days_left < 0:
                    days_left_text = _("Expirado")
                elif days_left == 0:
                    days_left_text = _("Hoje")
                else:
                    # Usando ngettext para pluralização correta (dia/dias)
                    days_left_text = ngettext('%(num)d dia restante', '%(num)d dias restantes', days_left) % {'num': days_left}
                
                upcoming_expirations.append({
                    'username': user_profile.username,
                    'expiration_date': exp_date.strftime('%d/%m/%Y'),
                    'days_left': days_left,
                    'days_left_text': days_left_text, # Adiciona o novo campo
                    'screen_limit': user_profile.screen_limit
                })
                # --- FIM DA ALTERAÇÃO ---
            except (ValueError, TypeError):
                continue
        
        return {
            "total_revenue": total_revenue,
            "sales_count": sales_count,
            "recent_transactions": [self._row_to_dict(p) for p in confirmed_payments[:10]],
            "daily_revenue": {day: total for day, total in daily_revenue_data},
            "weekly_revenue": weekly_revenue_dict,
            "upcoming_expirations": upcoming_expirations
        }

    # --- Métodos para Perfis de Utilizador ---
    def get_user_profile(self, username):
        profile = UserProfile.query.get(username)
        return self._row_to_dict(profile) if profile else {}

    def get_all_user_profiles(self):
        profiles = UserProfile.query.all()
        return [self._row_to_dict(p) for p in profiles]

    def set_user_profile(self, username, profile_data):
        profile = UserProfile.query.get(username)
        if not profile:
            profile = UserProfile(username=username)
        
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
    def create_pix_payment(self, txid, username, value, provider, screens, external_reference):
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
        """Adiciona um registro de pagamento manual."""
        txid = f"manual_{secrets.token_hex(12)}"
        payment = PixPayment(
            txid=txid,
            username=username,
            value=float(value),
            status='CONCLUIDA',
            provider='Manual',
            description=description,
            created_at=payment_date_str,
            screens=0, 
            external_reference=None
        )
        db.session.add(payment)
        db.session.commit()
        logger.info(f"Pagamento manual de {value} para '{username}' registado com sucesso (TXID: {txid}).")
        return self._row_to_dict(payment)

    def get_payments_by_user(self, username):
        """Busca todos os pagamentos (manuais e PIX) para um utilizador específico."""
        try:
            payments = PixPayment.query.filter_by(username=username).order_by(PixPayment.created_at.desc()).all()
            return [self._row_to_dict(p) for p in payments]
        except Exception as e:
            logger.error(f"Erro ao buscar pagamentos para o utilizador '{username}': {e}")
            return []

    # --- Métodos de Convites ---
    def add_invitation(self, code, details):
        invitation = Invitation(
            code=code,
            libraries=json.dumps(details.get('libraries', [])),
            screen_limit=details.get('screen_limit', 0),
            allow_downloads=details.get('allow_downloads', False),
            created_at=details.get('created_at'),
            expires_at=details.get('expires_at'),
            trial_duration_minutes=details.get('trial_duration_minutes', 0),
            overseerr_access=details.get('overseerr_access', False)
        )
        db.session.add(invitation)
        db.session.commit()

    def get_invitation(self, code):
        invitation = Invitation.query.get(code)
        if invitation:
            inv_dict = self._row_to_dict(invitation)
            inv_dict['libraries'] = json.loads(inv_dict['libraries'])
            return inv_dict
        return None

    def get_all_pending_invitations(self):
        invitations = Invitation.query.filter_by(claimed_by=None).all()
        invites = {}
        for invite in invitations:
            inv_dict = self._row_to_dict(invite)
            inv_dict['libraries'] = json.loads(inv_dict['libraries'])
            invites[invite.code] = inv_dict
        return invites

    def update_invitation_claim(self, code, username, claim_time):
        invitation = Invitation.query.get(code)
        if invitation:
            invitation.claimed_by = username
            invitation.claimed_at = claim_time
            db.session.commit()
    
    def delete_invitation(self, code):
        invitation = Invitation.query.get(code)
        if invitation:
            db.session.delete(invitation)
            db.session.commit()

    def get_user_claim_date(self, username):
        invitation = Invitation.query.filter_by(claimed_by=username).order_by(Invitation.claimed_at.desc()).first()
        return invitation.claimed_at if invitation else None

    # --- Métodos de Utilizadores Bloqueados ---
    def get_blocked_users(self, username=None):
        """
        Busca utilizadores bloqueados.
        Se um username for fornecido, busca apenas esse utilizador.
        Caso contrário, retorna todos.
        """
        if username:
            user = BlockedUser.query.get(username)
            return self._row_to_dict(user) if user else None
        
        users = BlockedUser.query.all()
        return [self._row_to_dict(u) for u in users]

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
        """Converte uma linha do SQLAlchemy para um dicionário."""
        if not row:
            return None
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}
