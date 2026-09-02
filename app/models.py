# app/models.py
from .extensions import db
from flask_login import UserMixin
import json
import uuid
from datetime import datetime

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
        self.role = role

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

class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String, nullable=False, index=True)
    payload = db.Column(db.Text, nullable=True)
    status = db.Column(db.String, default='pending', nullable=False, index=True)
    progress_current = db.Column(db.Integer, default=0)
    progress_total = db.Column(db.Integer, default=0)
    result = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

class Coupon(db.Model):
    __tablename__ = 'coupons'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String, unique=True, nullable=False, index=True)
    discount_type = db.Column(db.String, nullable=False)
    value = db.Column(db.Float, nullable=False)
    max_uses = db.Column(db.Integer, nullable=False, default=1)
    use_count = db.Column(db.Integer, nullable=False, default=0)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    usages = db.relationship('CouponUsage', backref='coupon', lazy=True, cascade="all, delete-orphan")

class CouponUsage(db.Model):
    __tablename__ = 'coupon_usages'
    id = db.Column(db.Integer, primary_key=True)
    user_plex_id = db.Column(db.Integer, db.ForeignKey('user_profiles.plex_user_id'), nullable=False, index=True)
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=False)
    used_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_plex_id', 'coupon_id', name='_user_coupon_uc'),)

class Invitation(db.Model):
    __tablename__ = 'invitations'
    code = db.Column(db.String, primary_key=True)
    libraries = db.Column(db.String, nullable=False)
    screen_limit = db.Column(db.Integer, nullable=False)
    allow_downloads = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.String, nullable=False)
    expires_at = db.Column(db.String)
    claimed_by_users = db.Column(db.Text)
    claimed_at = db.Column(db.String)
    trial_duration_minutes = db.Column(db.Integer, nullable=False, default=0)
    overseerr_access = db.Column(db.Boolean, default=False)
    max_uses = db.Column(db.Integer, nullable=False, default=1)
    use_count = db.Column(db.Integer, nullable=False, default=0)
    telegram_id = db.Column(db.String, nullable=True)  # Novo campo

class BlockedUser(db.Model):
    __tablename__ = 'blocked_users'
    user_plex_id = db.Column(db.Integer, db.ForeignKey('user_profiles.plex_user_id'), primary_key=True)
    username = db.Column(db.String, nullable=False)
    blocked_at = db.Column(db.String)
    block_reason = db.Column(db.String(50), nullable=True)

class UserProfile(db.Model):
    __tablename__ = 'user_profiles'
    plex_user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, unique=True, nullable=False, index=True)
    email = db.Column(db.String, nullable=True)
    name = db.Column(db.String)
    telegram_user = db.Column(db.String)
    discord_user_id = db.Column(db.String)
    phone_number = db.Column(db.String)
    expiration_date = db.Column(db.String)
    # Dia do mês em que a assinatura "faz aniversário" (1-31), guardado tal como foi
    # contratado originalmente. Serve de âncora para as renovações: sem ele, um
    # vencimento a dia 31 era truncado para 28 ao passar por fevereiro e ficava
    # preso nesse dia para sempre, fazendo o utilizador perder dias a cada ano.
    billing_day = db.Column(db.Integer, nullable=True)
    last_notification_sent = db.Column(db.String)
    trial_end_date = db.Column(db.String)
    trial_job_id = db.Column(db.String)
    expiration_job_id = db.Column(db.String)
    overseerr_access = db.Column(db.Boolean, default=False)
    screen_limit = db.Column(db.Integer, default=0, nullable=False)
    hide_from_leaderboard = db.Column(db.Boolean, default=False, nullable=False)
    libraries = db.Column(db.Text, nullable=True)
    payment_token = db.Column(db.String, unique=True, nullable=True)
    status = db.Column(db.String(20), default='active', nullable=False, index=True)
    pending_invite_link = db.Column(db.String, nullable=True)
    last_reactivation_time = db.Column(db.Float, nullable=True)
    xp = db.Column(db.Integer, default=0, nullable=False)
    xp_last_sync_at = db.Column(db.Float, nullable=True)
    lifetime_xp = db.Column(db.Integer, default=0, nullable=False)
    # --- Sistema de Referência ("Indique e Ganhe") ---
    # 'referral_code' é o código público que o utilizador partilha.
    # 'referred_by' guarda o plex_user_id de quem o indicou (a coluna já existia na
    # base de dados desde a migração 'c92625823728', mas nunca chegou a ser mapeada
    # aqui nem usada por qualquer código — agora passa a ser utilizada de facto).
    # 'referral_rewarded' evita pagar a recompensa mais do que uma vez pelo mesmo
    # indicado, mesmo que ele renove várias vezes.
    referral_code = db.Column(db.String(16), unique=True, nullable=True, index=True)
    referred_by = db.Column(db.Integer, nullable=True, index=True)
    referral_rewarded = db.Column(db.Boolean, default=False, nullable=False)
    referral_credit = db.Column(db.Float, default=0.0, nullable=False)
    coupon_usages = db.relationship('CouponUsage', backref='user', lazy=True, cascade="all, delete-orphan")
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade="all, delete-orphan")
    unlocked_achievements = db.relationship('UnlockedAchievement', backref='user', lazy=True, cascade="all, delete-orphan")

class PixPayment(db.Model):
    __tablename__ = 'pix_payments'
    txid = db.Column(db.String, primary_key=True)
    user_plex_id = db.Column(db.Integer, db.ForeignKey('user_profiles.plex_user_id'), nullable=False, index=True)
    username = db.Column(db.String, nullable=False)
    value = db.Column(db.Float, nullable=False)
    status = db.Column(db.String, nullable=False, default='ATIVA')
    provider = db.Column(db.String)
    created_at = db.Column(db.String, nullable=False)
    screens = db.Column(db.Integer, nullable=True)
    external_reference = db.Column(db.String, unique=True, nullable=True)
    description = db.Column(db.String(100), nullable=True)
    # 📌 REGISTO HISTÓRICO, não uma referência viva: guarda o código do cupão tal
    # como foi usado nesta cobrança. Deixou de ser uma chave estrangeira para
    # 'coupons.code' porque apagar um cupão antigo não pode apagar nem invalidar o
    # histórico financeiro que já o citou (e o relatório CSV precisa do código).
    coupon_code = db.Column(db.String, nullable=True, index=True)
    # Crédito de indicações RESERVADO nesta cobrança. Fica apenas registado aqui até
    # o pagamento ser confirmado — só nessa altura é debitado do saldo do utilizador.
    # Assim, um PIX gerado e abandonado nunca consome o crédito de ninguém.
    referral_credit_used = db.Column(db.Float, default=0.0, nullable=False)
    # Marca uma cobrança como UPGRADE PRO-RATA (troca de plano a meio do ciclo).
    # É essencial distinguir: numa renovação normal o vencimento é estendido, mas
    # num upgrade pro-rata o utilizador paga só a diferença e o vencimento fica
    # EXATAMENTE onde estava — sem esta marca, o webhook daria um mês grátis.
    is_proration = db.Column(db.Boolean, default=False, nullable=False)

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_plex_id = db.Column(db.Integer, db.ForeignKey('user_profiles.plex_user_id'), nullable=True, index=True)
    message = db.Column(db.String, nullable=False)
    category = db.Column(db.String(20), nullable=False, default='info')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    link = db.Column(db.String, nullable=True)

class ShortLink(db.Model):
    __tablename__ = 'short_links'
    id = db.Column(db.Integer, primary_key=True)
    short_code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    original_url = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UnlockedAchievement(db.Model):
    __tablename__ = 'unlocked_achievements'
    id = db.Column(db.Integer, primary_key=True)
    user_plex_id = db.Column(db.Integer, db.ForeignKey('user_profiles.plex_user_id'), nullable=False, index=True)
    username = db.Column(db.String, nullable=False)
    achievement_id = db.Column(db.String, nullable=False)
    unlocked_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_plex_id', 'achievement_id', name='_user_achievement_uc'),)

class StreamTerminationLog(db.Model):
    __tablename__ = 'stream_termination_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_plex_id = db.Column(db.Integer, db.ForeignKey('user_profiles.plex_user_id'), nullable=False, index=True)
    username = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    media_title = db.Column(db.String, nullable=False)
    platform = db.Column(db.String, nullable=True)
    reason = db.Column(db.String, nullable=False)
