# app/services/data_manager.py

import os
import json
import logging
import secrets
import calendar
import pytz
from datetime import datetime, timedelta, timezone
from functools import wraps

from ..extensions import db
from ..models import (
    Invitation, BlockedUser, UserProfile, PixPayment, Notification, 
    UnlockedAchievement, ShortLink, Coupon, CouponUsage, Task, StreamTerminationLog
)
from sqlalchemy import func, String
from sqlalchemy.exc import IntegrityError
from flask_babel import gettext as _, ngettext
from collections import defaultdict
from tzlocal import get_localzone_name
from ..utils.log_sanitizer import mask_code

logger = logging.getLogger(__name__)

# Janela em que uma cobrança por pagar ainda "segura" o crédito de indicações
# que reservou. Passado este tempo o PIX está, na prática, abandonado (os QR
# Codes expiram muito antes) e o saldo volta a ficar disponível.
RESERVED_CREDIT_MAX_AGE_HOURS = 24

# Mesma ideia para os cupões: uma cobrança gerada e ainda por pagar "segura" o
# uso do cupão que reservou. Sem isto, o limite de utilizações só era verificado
# na criação da cobrança e só descontado na confirmação — pelo meio, dezenas de
# pessoas podiam gerar cobranças com o mesmo cupão de uso único e pagá-las todas.
#
# A janela é curta de propósito: todos os provedores geram PIX com 20 minutos de
# validade, por isso uma cobrança mais antiga já não é pagável e não faz sentido
# continuar a segurar o cupão. Assim o limite fica protegido sem que um QR Code
# abandonado bloqueie o cupão (ou o próprio utilizador) durante horas.
RESERVED_COUPON_MAX_AGE_MINUTES = 30

# Quantas transações são trazidas de cada vez ao exportar o relatório CSV.
EXPORT_BATCH_SIZE = 500


def normalize_coupon_code(code):
    """
    Forma canónica de um código de cupão: sem espaços à volta e em maiúsculas.

    A interface já enviava tudo em maiúsculas, mas só no JavaScript — quem
    chamasse a API diretamente conseguia criar 'promo25' e 'PROMO25' como cupões
    distintos, e um código guardado em maiúsculas nunca era encontrado quando o
    utilizador o escrevia em minúsculas.
    """
    if code is None:
        return None
    return str(code).strip().upper()

# --- HELPERS ---
def get_app_timezone():
    """Obtém o fuso horário real do sistema (respeita a variável TZ do Docker)."""
    tz_env = os.environ.get('TZ')
    if tz_env:
        try:
            return pytz.timezone(tz_env)
        except pytz.UnknownTimeZoneError:
            pass
    try: 
        return pytz.timezone(get_localzone_name())
    except Exception: 
        return pytz.UTC

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
        db.session.flush() # 🛡️ Força a BD a gerar o ID antes de devolver o resultado!
        logger.info(f"Tarefa '{name}' criada na base de dados.")
        return self._row_to_dict(task)

    def get_next_pending_task(self, name):
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
        detalhes = dict(details)
        # Guarda sempre na forma canónica, para que a procura por código continue
        # a funcionar independentemente de como o cupão foi criado.
        detalhes['code'] = normalize_coupon_code(detalhes.get('code'))
        new_coupon = Coupon(**detalhes)
        db.session.add(new_coupon)
        db.session.flush()
        return self._row_to_dict(new_coupon)

    def get_coupon_by_code(self, code):
        """Procura um cupão ignorando maiúsculas/minúsculas e espaços à volta."""
        normalizado = normalize_coupon_code(code)
        if not normalizado:
            return None
        coupon = Coupon.query.filter(func.upper(Coupon.code) == normalizado).first()
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
        """
        Regista o uso de um cupão. É IDEMPOTENTE: registar duas vezes o mesmo par
        (utilizador, cupão) não faz nada e não levanta erro.

        ⚠️ Isto não é um detalhe: 'coupon_usages' tem uma restrição de unicidade
        em (user_plex_id, coupon_id) e este método é chamado ao confirmar um
        pagamento. Uma segunda inserção levantava IntegrityError, que subia até ao
        processamento do pagamento e o marcava como 'FALHOU' — DEPOIS de a
        assinatura já ter sido renovada. O cliente pagava, era renovado, e a
        transação desaparecia do relatório financeiro e do CSV.
        """
        normalizado = normalize_coupon_code(code)
        coupon = Coupon.query.filter(func.upper(Coupon.code) == normalizado).first() if normalizado else None
        if not coupon or not plex_user_id:
            logger.warning(f"Tentativa de registar o uso de um cupão inválido ('{code}') ou para um utilizador inválido.")
            return False

        ja_registado = db.session.query(CouponUsage.id).filter(
            CouponUsage.coupon_id == coupon.id,
            CouponUsage.user_plex_id == int(plex_user_id)
        ).first()
        if ja_registado:
            logger.info(
                f"Uso do cupão '{coupon.code}' pelo utilizador ID {plex_user_id} já estava registado. "
                "Nada a fazer (registo idempotente)."
            )
            return False

        coupon.use_count += 1
        new_usage = CouponUsage(user_plex_id=int(plex_user_id), coupon_id=coupon.id)
        db.session.add(new_usage)
        logger.info(f"Uso do cupão '{coupon.code}' registado para o utilizador ID {plex_user_id}. Contagem: {coupon.use_count}.")
        return True

    def has_user_used_coupon(self, plex_user_id, code):
        # 🚀 OTIMIZAÇÃO: Busca apenas o ID para ser instantâneo, em vez de carregar a linha toda
        normalizado = normalize_coupon_code(code)
        if not normalizado:
            return False
        usage_exists = db.session.query(CouponUsage.id).join(Coupon).filter(
            func.upper(Coupon.code) == normalizado,
            CouponUsage.user_plex_id == plex_user_id
        ).first()
        return usage_exists is not None

    def get_reserved_coupon_uses(self, code,
                                 max_age_minutes=RESERVED_COUPON_MAX_AGE_MINUTES):
        """
        Quantas cobranças ainda por pagar já reservaram este cupão.

        O 'use_count' só sobe quando o pagamento é confirmado. Entre gerar a
        cobrança e pagá-la existe uma janela em que o cupão continua a parecer
        disponível: com um cupão de uso único, dez pessoas geravam dez cobranças e
        pagavam as dez com desconto. Contar aqui as cobranças abertas fecha essa
        janela — o mesmo padrão já usado para o crédito de indicações.

        Só contam as cobranças RECENTES: um PIX abandonado fica 'ATIVA' até à
        limpeza automática (dias depois) e, sem esta janela, um cupão de uso único
        ficaria bloqueado todo esse tempo por causa de quem desistiu de pagar.
        """
        normalizado = normalize_coupon_code(code)
        if not normalizado:
            return 0
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=int(max_age_minutes))).isoformat()
            query = db.session.query(func.count(PixPayment.txid)).filter(
                func.upper(PixPayment.coupon_code) == normalizado,
                PixPayment.status.in_(('ATIVA', 'PROCESSANDO')),
                PixPayment.created_at >= cutoff
            )
            return int(query.scalar() or 0)
        except Exception:
            return 0

    def has_user_pending_coupon_charge(self, plex_user_id, code,
                                       max_age_minutes=RESERVED_COUPON_MAX_AGE_MINUTES):
        """
        Indica se o utilizador já tem uma cobrança aberta com este cupão.

        Sem esta verificação, a mesma pessoa gerava duas cobranças com o mesmo
        cupão (o 'já usou' só é registado na confirmação) e pagava ambas com
        desconto. O PIX que ela já tem continua válido e pagável — só não pode
        gerar um segundo em paralelo enquanto o primeiro não expira.
        """
        normalizado = normalize_coupon_code(code)
        if not normalizado or not plex_user_id:
            return False
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=int(max_age_minutes))).isoformat()
            existe = db.session.query(PixPayment.txid).filter(
                PixPayment.user_plex_id == int(plex_user_id),
                func.upper(PixPayment.coupon_code) == normalizado,
                PixPayment.status.in_(('ATIVA', 'PROCESSANDO')),
                PixPayment.created_at >= cutoff
            ).first()
            return existe is not None
        except Exception:
            return False

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
        notification = Notification(
            message=message, category=category, link=link,
            user_plex_id=user_plex_id, timestamp=datetime.now(timezone.utc)
        )
        db.session.add(notification)
        db.session.flush() 
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
        # 🚀 OTIMIZAÇÃO: synchronize_session=False previne consumo excessivo de RAM
        updated_rows = Notification.query.filter_by(user_plex_id=user_plex_id, is_read=False).update({'is_read': True}, synchronize_session=False)
        return updated_rows
            
    @db_transaction
    def delete_all_notifications(self, user_plex_id=None):
        num_rows_deleted = db.session.query(Notification).filter_by(user_plex_id=user_plex_id).delete(synchronize_session=False)
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
        num_rows_deleted = db.session.query(StreamTerminationLog).delete(synchronize_session=False)
        return num_rows_deleted

    # --- MÉTODOS FINANCEIROS OTIMIZADOS ---
    def get_financial_summary(self, year, month, renewal_days=7):
        local_tz = get_app_timezone()

        # 1. Delimitar o mês atual usando o fuso horário local
        _, last_day = calendar.monthrange(year, month)
        local_start = local_tz.localize(datetime(year, month, 1, 0, 0, 0))
        local_end = local_tz.localize(datetime(year, month, last_day, 23, 59, 59, 999999))

        # Converter para as horas UTC exatas para a consulta na base de dados
        utc_start_str = local_start.astimezone(timezone.utc).isoformat()
        utc_end_str = local_end.astimezone(timezone.utc).isoformat()

        payments_in_month = db.session.query(PixPayment).filter(
            PixPayment.created_at >= utc_start_str,
            PixPayment.created_at <= utc_end_str,
            PixPayment.status == 'CONCLUIDA'
        ).order_by(PixPayment.created_at.desc()).all()

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
                # 2. Ao ler a data, converte-a de UTC para a hora local do painel
                dt_utc = datetime.fromisoformat(payment.created_at)
                if dt_utc.tzinfo is None:
                    dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                
                dt_local = dt_utc.astimezone(local_tz)
                
                day = dt_local.day
                week_num = int(dt_local.strftime('%W'))
                
                daily_revenue_map[day] += payment.value
                weekly_revenue_map[week_num] += payment.value
            except (ValueError, TypeError):
                continue
        
        sorted_weeks = sorted(weekly_revenue_map.keys())
        weekly_revenue_dict = {f"Semana {idx + 1}": weekly_revenue_map[w] for idx, w in enumerate(sorted_weeks)}

        # 3. Otimização de Utilizadores a Expirar
        today_local = datetime.now(local_tz).date()
        
        search_start_str = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        search_end_str = (datetime.now(timezone.utc) + timedelta(days=renewal_days + 2)).isoformat()
        
        expiring_users_query = db.session.query(UserProfile).outerjoin(BlockedUser).filter(
            BlockedUser.user_plex_id == None, 
            UserProfile.expiration_date.isnot(None), 
            UserProfile.expiration_date != '',
            UserProfile.expiration_date >= search_start_str, 
            UserProfile.expiration_date <= search_end_str
        ).all()
        
        upcoming_expirations = []
        for p in expiring_users_query:
            try:
                exp_date_utc = datetime.fromisoformat(p.expiration_date)
                if exp_date_utc.tzinfo is None:
                    exp_date_utc = exp_date_utc.replace(tzinfo=timezone.utc)
                
                # Cálculo matemático perfeito (Data Local - Hoje Local = Dias Restantes)
                days_left = (exp_date_utc.astimezone(local_tz).date() - today_local).days
                
                if 0 <= days_left <= renewal_days:
                    days_text = ngettext('%(num)d dia restante', '%(num)d dias restantes', days_left) % {'num': days_left} if days_left > 0 else _("Hoje")
                    
                    upcoming_expirations.append({
                        'username': p.username, 
                        'expiration_date': exp_date_utc.astimezone(local_tz).strftime('%d/%m/%Y'), 
                        'days_left': days_left, 
                        'days_left_text': days_text, 
                        'screen_limit': p.screen_limit
                    })
            except (ValueError, TypeError): continue
        
        # Ordenamos para que os que vencem "Hoje" (0 dias) apareçam sempre no topo da lista
        upcoming_expirations.sort(key=lambda x: x['days_left'])
        
        return {
            "total_revenue": total_revenue, 
            "sales_count": sales_count, 
            "recent_transactions": recent_transactions, 
            "daily_revenue": dict(daily_revenue_map), 
            "weekly_revenue": weekly_revenue_dict, 
            "upcoming_expirations": upcoming_expirations
        }

    def _payments_for_export_query(self, start_date_iso, end_date_iso):
        return PixPayment.query.filter(
            PixPayment.status == 'CONCLUIDA',
            PixPayment.created_at >= start_date_iso,
            PixPayment.created_at <= end_date_iso
        ).order_by(PixPayment.created_at.asc())

    def get_payments_for_export(self, start_date_iso, end_date_iso):
        try:
            payments = self._payments_for_export_query(start_date_iso, end_date_iso).all()
            return [self._row_to_dict(p) for p in payments]
        except Exception:
            return []

    def iter_payments_for_export(self, start_date_iso, end_date_iso, batch_size=EXPORT_BATCH_SIZE):
        """
        Igual a 'get_payments_for_export', mas devolve um ITERADOR que só traz da
        base de dados um lote de cada vez.

        A exportação é servida em streaming, mas isso não servia de nada enquanto
        o primeiro passo era carregar o período inteiro para memória: num
        relatório de vários anos, o painel construía a lista toda (e um dicionário
        por transação) antes de enviar um único byte.
        """
        query = self._payments_for_export_query(start_date_iso, end_date_iso)
        for payment in query.yield_per(int(batch_size)):
            yield self._row_to_dict(payment)

    def get_latest_completed_payment(self, plex_user_id):
        payment = PixPayment.query.filter_by(
            user_plex_id=plex_user_id,
            status='CONCLUIDA'
        ).order_by(PixPayment.created_at.desc()).first()
        return self._row_to_dict(payment) if payment else None
        
    # --- MÉTODOS para Perfis de Utilizador ---
    def get_user_profile(self, plex_user_id):
        profile = UserProfile.query.get(plex_user_id)
        return self._row_to_dict(profile) if profile else None

    def get_user_profile_by_username(self, username):
        profile = UserProfile.query.filter(func.lower(UserProfile.username) == username.lower()).first()
        return self._row_to_dict(profile) if profile else None
    
    def get_user_profile_by_telegram(self, telegram_id):
        """
        Localiza o utilizador vinculado a um Telegram ID.

        🐛 NOTA: a coluna em 'user_profiles' chama-se 'telegram_user' (é em
        'invitations' que o campo se chama 'telegram_id'). Vários pontos do código
        liam 'profile.get("telegram_id")', que devolvia SEMPRE None por essa coluna
        não existir neste modelo — dando a falsa impressão de funcionar graças aos
        fallbacks 'or'. A comparação é feita como texto e sem espaços, porque o ID
        pode chegar como número (de um bot) ou como string (de um formulário).
        """
        if telegram_id is None or str(telegram_id).strip() == "":
            return None
        normalized = str(telegram_id).strip()
        profile = UserProfile.query.filter(
            func.trim(func.cast(UserProfile.telegram_user, String)) == normalized
        ).first()
        return self._row_to_dict(profile) if profile else None

    def get_user_profiles_by_username(self, usernames):
        if not usernames: return {}
        try:
            profiles = UserProfile.query.filter(func.lower(UserProfile.username).in_([u.lower() for u in usernames])).all()
            return {p.username: self._row_to_dict(p) for p in profiles}
        except Exception: return {}

    def get_all_user_profiles(self):
        profiles = UserProfile.query.all()
        return [self._row_to_dict(p) for p in profiles]

    def get_user_profile_by_email(self, email):
        """
        Localiza um utilizador pelo email (comparação sem distinção de maiúsculas).
        Usado para ligar notificações vindas do Overseerr ao utilizador do painel.
        """
        if not email:
            return None
        try:
            profile = UserProfile.query.filter(
                func.lower(UserProfile.email) == str(email).strip().lower()
            ).first()
            return self._row_to_dict(profile) if profile else None
        except Exception:
            return None

    def get_user_profile_by_referral_code(self, code):
        """Localiza o dono de um código de indicação (case-insensitive)."""
        if not code:
            return None
        try:
            profile = UserProfile.query.filter(
                func.upper(UserProfile.referral_code) == str(code).strip().upper()
            ).first()
            return self._row_to_dict(profile) if profile else None
        except Exception:
            return None

    def get_users_referred_by(self, plex_user_id):
        """Lista os utilizadores indicados por alguém."""
        try:
            profiles = UserProfile.query.filter(UserProfile.referred_by == int(plex_user_id)).all()
            return [self._row_to_dict(p) for p in profiles]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # OPERAÇÕES ATÓMICAS DO PROGRAMA DE INDICAÇÕES
    # ------------------------------------------------------------------
    # Estes métodos escrevem APENAS a coluna em causa, com um UPDATE ... WHERE,
    # em vez de ler o perfil inteiro, alterá-lo em memória e voltar a gravá-lo.
    # Duas razões, ambas com consequências reais:
    #   • dinheiro: o mesmo webhook entregue duas vezes (ou duas cobranças a
    #     serem confirmadas ao mesmo tempo) não pode somar nem abater crédito a
    #     dobrar — o que acontecia com o padrão ler-alterar-gravar;
    #   • gravar o perfil completo a partir de uma leitura antiga sobrepõe
    #     campos que outro fluxo (renovação, sincronização com o Plex) acabou de
    #     mudar entre a leitura e a escrita.

    @db_transaction
    def set_user_referral_code(self, plex_user_id, code):
        """
        Atribui um código de indicação, mas só se o utilizador ainda não tiver um.
        Devolve o código que ficou efetivamente em vigor — o novo, ou o que já lá
        estava caso outro pedido em paralelo se tenha antecipado.

        Propaga IntegrityError se o código colidir com o de outro utilizador
        (há um índice único na coluna); quem chama deve gerar outro e tentar de novo.
        """
        uid = int(plex_user_id)
        updated = UserProfile.query.filter(
            UserProfile.plex_user_id == uid,
            (UserProfile.referral_code.is_(None)) | (UserProfile.referral_code == '')
        ).update({UserProfile.referral_code: code}, synchronize_session=False)

        if updated:
            return code

        # Já tinha código (ou o perfil não existe): devolve o que está gravado.
        row = db.session.query(UserProfile.referral_code).filter(
            UserProfile.plex_user_id == uid
        ).first()
        return row[0] if row else None

    @db_transaction
    def add_referral_credit(self, plex_user_id, amount):
        """Soma crédito de indicações ao saldo. Devolve o valor somado."""
        value = round(float(amount or 0), 2)
        if value <= 0:
            return 0.0
        updated = UserProfile.query.filter(
            UserProfile.plex_user_id == int(plex_user_id)
        ).update(
            {UserProfile.referral_credit: func.coalesce(UserProfile.referral_credit, 0.0) + value},
            synchronize_session=False
        )
        return value if updated else 0.0

    @db_transaction
    def consume_referral_credit(self, plex_user_id, amount):
        """
        Abate crédito do saldo e devolve o valor efetivamente consumido. O saldo
        nunca fica negativo: se o pedido exceder o disponível, consome só o resto.
        """
        uid = int(plex_user_id)
        wanted = round(max(0.0, float(amount or 0)), 2)
        if wanted <= 0:
            return 0.0

        # Caso normal: há saldo suficiente. O WHERE garante que dois abatimentos
        # simultâneos nunca gastam o mesmo crédito duas vezes.
        if UserProfile.query.filter(
            UserProfile.plex_user_id == uid,
            UserProfile.referral_credit >= wanted
        ).update(
            {UserProfile.referral_credit: UserProfile.referral_credit - wanted},
            synchronize_session=False
        ):
            return wanted

        # Saldo insuficiente: leva o que restar e deixa o saldo a zero.
        row = db.session.query(UserProfile.referral_credit).filter(
            UserProfile.plex_user_id == uid
        ).first()
        available = round(float(row[0] or 0), 2) if row else 0.0
        if available <= 0:
            return 0.0

        if UserProfile.query.filter(
            UserProfile.plex_user_id == uid,
            UserProfile.referral_credit > 0,
            UserProfile.referral_credit <= wanted
        ).update({UserProfile.referral_credit: 0.0}, synchronize_session=False):
            return available
        return 0.0

    def get_reserved_referral_credit(self, plex_user_id, exclude_txid=None,
                                     max_age_hours=RESERVED_CREDIT_MAX_AGE_HOURS):
        """
        Crédito já comprometido em cobranças geradas e ainda por pagar.

        Sem isto, um utilizador com R$ 20 de saldo podia abrir duas cobranças ao
        mesmo tempo, cada uma com os R$ 20 descontados, e pagar as duas: recebia
        R$ 40 de desconto com R$ 20 de crédito.

        Só contam as cobranças RECENTES: um QR Code PIX abandonado fica 'ATIVA'
        até à limpeza automática (dias depois) e, sem esta janela, o crédito de
        quem desistiu de um pagamento ficaria retido todo esse tempo.
        """
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=int(max_age_hours))).isoformat()
            query = db.session.query(
                func.coalesce(func.sum(PixPayment.referral_credit_used), 0.0)
            ).filter(
                PixPayment.user_plex_id == int(plex_user_id),
                PixPayment.status.in_(('ATIVA', 'PROCESSANDO')),
                PixPayment.referral_credit_used > 0,
                PixPayment.created_at >= cutoff
            )
            if exclude_txid:
                query = query.filter(PixPayment.txid != exclude_txid)
            return round(float(query.scalar() or 0.0), 2)
        except Exception:
            return 0.0

    @db_transaction
    def claim_referral_reward(self, plex_user_id):
        """
        Marca — de forma atómica — que a recompensa pela indicação DESTE utilizador
        já foi paga, e devolve True apenas a quem 'ganhou a corrida'.

        É esta condição no UPDATE (e não uma leitura prévia do campo) que garante
        que a recompensa é entregue exatamente uma vez, mesmo que o pagamento seja
        processado duas vezes em simultâneo.
        """
        return bool(UserProfile.query.filter(
            UserProfile.plex_user_id == int(plex_user_id),
            UserProfile.referred_by.isnot(None),
            UserProfile.referral_rewarded.is_(False)
        ).update({UserProfile.referral_rewarded: True}, synchronize_session=False))

    @db_transaction
    def release_referral_reward(self, plex_user_id):
        """
        Desfaz a marca de recompensa paga. Usado quando a entrega falha a meio:
        sem isto, o indicado ficava marcado como 'já recompensado' e quem o
        indicou nunca receberia nada.
        """
        return bool(UserProfile.query.filter(
            UserProfile.plex_user_id == int(plex_user_id)
        ).update({UserProfile.referral_rewarded: False}, synchronize_session=False))

    def count_rewarded_referrals(self, plex_user_id):
        """Quantas indicações deste utilizador já foram efetivamente recompensadas."""
        try:
            return UserProfile.query.filter(
                UserProfile.referred_by == int(plex_user_id),
                UserProfile.referral_rewarded.is_(True)
            ).count()
        except Exception:
            return 0

    def user_has_completed_payment(self, plex_user_id):
        """Indica se o utilizador já tem algum pagamento confirmado no histórico."""
        try:
            return bool(PixPayment.query.filter_by(
                user_plex_id=int(plex_user_id), status='CONCLUIDA'
            ).first())
        except Exception:
            return False

    @db_transaction
    def reset_all_users_xp(self):
        """
        Repõe a zero o XP da temporada atual de TODOS os utilizadores, preservando
        o 'lifetime_xp' (XP acumulado de sempre) e o 'xp_last_sync_at' — este último
        é essencial: se fosse limpo, a próxima sincronização reprocessaria todo o
        histórico do Tautulli desde o início e o XP voltaria imediatamente ao valor
        anterior, anulando o reset.

        Devolve o número de utilizadores afetados.
        """
        affected = UserProfile.query.update({UserProfile.xp: 0}, synchronize_session=False)
        return affected

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

    @db_transaction
    def set_payment_referral_credit(self, txid, amount):
        """
        Regista quanto crédito de indicações esta cobrança pretende consumir.
        É apenas uma RESERVA — o débito no saldo do utilizador só ocorre quando o
        pagamento for confirmado.
        """
        payment = PixPayment.query.get(txid)
        if not payment:
            return False
        payment.referral_credit_used = float(amount or 0)
        return True

    @db_transaction
    def mark_payment_as_proration(self, txid):
        """Marca uma cobrança como upgrade pro-rata (não estende o vencimento)."""
        payment = PixPayment.query.get(txid)
        if not payment:
            return False
        payment.is_proration = True
        return True

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
    def increment_invitation_use(self, code, username, plex_user_id=None):
        """
        Incremento SEM verificação de limite. O resgate usa
        `reserve_invitation_use`, que valida as vagas de forma atómica; este
        método fica para os casos em que o uso já foi decidido noutro sítio.
        """
        invitation = Invitation.query.get(code)
        if invitation:
            invitation.use_count += 1
            invitation.claimed_at = datetime.now(timezone.utc).isoformat()
            claimed_users = json.loads(invitation.claimed_by_users or '[]')
            if username not in claimed_users: 
                claimed_users.append(username)
            invitation.claimed_by_users = json.dumps(claimed_users)

            if plex_user_id is not None:
                claimed_ids = json.loads(invitation.claimed_by_ids or '[]')
                if str(plex_user_id) not in claimed_ids:
                    claimed_ids.append(str(plex_user_id))
                    invitation.claimed_by_ids = json.dumps(claimed_ids)
            return True
        return False
            
    @db_transaction
    def reserve_invitation_use(self, code, username, plex_user_id=None):
        """
        Reserva ATOMICAMENTE uma utilização do convite. Devolve False se já não
        houver vagas (ou o convite não existir), sem alterar nada.

        🐛 CORREÇÃO DE CONCORRÊNCIA: o resgate validava o convite com
        `get_invitation_by_code` (leitura) e só contabilizava o uso lá no fim,
        com `increment_invitation_use`. Entre as duas coisas há dezenas de
        chamadas de rede à API do Plex (enviar o convite, aceitá-lo, aplicar
        preferências) e o servidor corre com um worker gevent: cada espera de
        rede é um ponto de troca entre greenlets. Dois resgates simultâneos do
        MESMO código liam ambos `use_count = 0 < max_uses = 1`, ambos passavam
        na validação e ambos recebiam acesso — o `use_count` acabava em 2. Um
        link de uso único partilhado num grupo entrava por duas pessoas.

        A condição `use_count < max_uses` vive agora DENTRO do UPDATE, pelo que
        é a própria base de dados a decidir quem fica com a vaga. Só quem
        receber True prossegue; em caso de falha a seguir, `release_invitation_use`
        devolve a vaga.
        """
        atualizadas = db.session.query(Invitation).filter(
            Invitation.code == code,
            Invitation.use_count < Invitation.max_uses,
        ).update(
            {
                Invitation.use_count: Invitation.use_count + 1,
                Invitation.claimed_at: datetime.now(timezone.utc).isoformat(),
            },
            synchronize_session=False,
        )
        if not atualizadas:
            return False

        # `populate_existing` força a releitura da linha: o UPDATE acima passou
        # ao lado da sessão (synchronize_session=False) e o objeto em cache
        # ainda traria o `use_count` antigo.
        invitation = db.session.query(Invitation).populate_existing().filter(
            Invitation.code == code
        ).first()
        if invitation is not None:
            claimed_users = json.loads(invitation.claimed_by_users or '[]')
            if username and username not in claimed_users:
                claimed_users.append(username)
                invitation.claimed_by_users = json.dumps(claimed_users)

            # O ID é a identidade estável: o username do Plex pode ser mudado
            # pelo próprio utilizador e deixaria de servir para reconhecê-lo.
            if plex_user_id is not None:
                claimed_ids = json.loads(invitation.claimed_by_ids or '[]')
                if str(plex_user_id) not in claimed_ids:
                    claimed_ids.append(str(plex_user_id))
                    invitation.claimed_by_ids = json.dumps(claimed_ids)
        return True

    @db_transaction
    def release_invitation_use(self, code, username, plex_user_id=None):
        """
        Devolve a vaga reservada por `reserve_invitation_use`.

        Chamado quando o resgate falha depois da reserva (o Plex recusa o
        convite, o utilizador já é amigo, o aceite não chega a tempo). Sem isto,
        uma tentativa falhada queimava permanentemente uma utilização do convite.
        """
        invitation = Invitation.query.get(code)
        if not invitation:
            return False

        if invitation.use_count > 0:
            invitation.use_count -= 1

        claimed_users = json.loads(invitation.claimed_by_users or '[]')
        if username in claimed_users:
            claimed_users.remove(username)
            invitation.claimed_by_users = json.dumps(claimed_users)

        if plex_user_id is not None:
            claimed_ids = json.loads(invitation.claimed_by_ids or '[]')
            if str(plex_user_id) in claimed_ids:
                claimed_ids.remove(str(plex_user_id))
                invitation.claimed_by_ids = json.dumps(claimed_ids)
        return True

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
            logger.info(f"Convite '{mask_code(code)}' reativado manualmente (contagem resetada).")
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

    def count_blocked_users(self):
        """
        Conta os bloqueados sem os materializar.

        O resumo do dashboard só quer o número, mas usava o
        'get_blocked_users_list()' — que carrega cada linha e a converte em
        dicionário — e essa contagem é refeita a cada 5 segundos pela tarefa de
        tempo real, para todos os painéis abertos.
        """
        return db.session.query(BlockedUser).count()

    def get_blocked_users_dict(self):
        return {u.user_plex_id: self._row_to_dict(u) for u in BlockedUser.query.all()}

    def add_blocked_user(self, plex_user_id, username, reason='manual'):
        """Adiciona ou atualiza um utilizador bloqueado. Protegido contra colisões de threads."""
        try:
            user = BlockedUser.query.get(plex_user_id)
            if not user:
                user = BlockedUser(user_plex_id=plex_user_id, username=username)

            user.blocked_at = datetime.now(timezone.utc).isoformat()
            user.block_reason = reason
            db.session.add(user)
            db.session.commit()
            
            logger.info(f"Utilizador '{username}' (ID: {plex_user_id}) adicionado/atualizado na lista de bloqueados.")
            return self._row_to_dict(user)
            
        except IntegrityError:
            db.session.rollback()
            user = BlockedUser.query.get(plex_user_id)
            if user:
                user.blocked_at = datetime.now(timezone.utc).isoformat()
                user.block_reason = reason
                db.session.commit()
                return self._row_to_dict(user)
                
            return None
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro inesperado em add_blocked_user: {e}", exc_info=True)
            raise

    @db_transaction
    def remove_blocked_user(self, plex_user_id):
        user = BlockedUser.query.get(plex_user_id)
        if user:
            db.session.delete(user)
            return True
        return False
            
    def _row_to_dict(self, row, process_json=False):
        """
        Converte uma linha do SQLAlchemy para um dicionário Python nativo.
        Implementa proteção contra erros de tipo de JSON.
        """
        if not row: return None
        d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        
        if process_json:
            # 🚀 OTIMIZAÇÃO: Proteção Type-Safe. Previne que json.loads quebre se a DB (ex: Postgres) já devolver um List
            if d.get('libraries') and isinstance(d['libraries'], str): 
                try:
                    d['libraries'] = json.loads(d['libraries'])
                except json.JSONDecodeError:
                    d['libraries'] = []
            elif not d.get('libraries'):
                d['libraries'] = []
                
            if d.get('claimed_by_users') and isinstance(d['claimed_by_users'], str):
                try:
                    d['claimed_by_users'] = json.loads(d['claimed_by_users'])
                except json.JSONDecodeError:
                    d['claimed_by_users'] = []
            elif not d.get('claimed_by_users'):
                d['claimed_by_users'] = []

            if d.get('claimed_by_ids') and isinstance(d['claimed_by_ids'], str):
                try:
                    d['claimed_by_ids'] = json.loads(d['claimed_by_ids'])
                except json.JSONDecodeError:
                    d['claimed_by_ids'] = []
            elif 'claimed_by_ids' in d and not d.get('claimed_by_ids'):
                d['claimed_by_ids'] = []
                
        return d
