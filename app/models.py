# app/models.py
from .extensions import db
from flask_login import UserMixin
import json
from datetime import datetime

# O seu modelo User existente
class User(UserMixin):
    """
    Representa um usuário genérico da aplicação, que pode ser tanto um 
    administrador quanto um usuário comum do Plex.
    """
    def __init__(self, id, username, email=None, thumb=None, role='user'):
        self.id = id
        self.username = username
        self.email = email
        self.thumb = thumb
        self.role = role  # 'admin' ou 'user'

    def is_admin(self):
        """Verifica se o usuário tem permissões de administrador."""
        return self.role == 'admin'

    def to_dict(self):
        """Retorna uma representação do usuário como dicionário."""
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'thumb': self.thumb,
            'role': self.role
        }
    
    def to_json(self):
        """Retorna uma representação JSON do usuário."""
        return json.dumps(self.to_dict())

# --- NOVOS MODELOS PARA O FLASK-MIGRATE ---

class Invitation(db.Model):
    __tablename__ = 'invitations'
    code = db.Column(db.String, primary_key=True)
    libraries = db.Column(db.String, nullable=False)
    screen_limit = db.Column(db.Integer, nullable=False)
    allow_downloads = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.String, nullable=False)
    expires_at = db.Column(db.String)
    claimed_by = db.Column(db.String)
    claimed_at = db.Column(db.String)
    trial_duration_minutes = db.Column(db.Integer, nullable=False, default=0)
    overseerr_access = db.Column(db.Boolean, default=False)

class BlockedUser(db.Model):
    __tablename__ = 'blocked_users'
    username = db.Column(db.String, primary_key=True)
    blocked_at = db.Column(db.String)
    block_reason = db.Column(db.String(50), nullable=True) # 'expired', 'trial_expired', 'manual'

class UserProfile(db.Model):
    __tablename__ = 'user_profiles'
    username = db.Column(db.String, primary_key=True)
    name = db.Column(db.String)
    telegram_user = db.Column(db.String)
    phone_number = db.Column(db.String)
    expiration_date = db.Column(db.String)
    last_notification_sent = db.Column(db.String)
    trial_end_date = db.Column(db.String)
    trial_job_id = db.Column(db.String)
    expiration_job_id = db.Column(db.String)
    overseerr_access = db.Column(db.Boolean, default=False)
    screen_limit = db.Column(db.Integer, default=0, nullable=False)
    hide_from_leaderboard = db.Column(db.Boolean, default=False, nullable=False)
    libraries = db.Column(db.Text, nullable=True)

class PixPayment(db.Model):
    __tablename__ = 'pix_payments'
    txid = db.Column(db.String, primary_key=True)
    username = db.Column(db.String, nullable=False)
    value = db.Column(db.Float, nullable=False)
    status = db.Column(db.String, nullable=False, default='ATIVA')
    provider = db.Column(db.String)
    created_at = db.Column(db.String, nullable=False)
    screens = db.Column(db.Integer, nullable=True)
    external_reference = db.Column(db.String, unique=True, nullable=True)
    description = db.Column(db.String(100), nullable=True)


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String, nullable=False)
    category = db.Column(db.String(20), nullable=False, default='info')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    link = db.Column(db.String, nullable=True) 
