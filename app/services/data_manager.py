# app/services/data_manager.py

import os
import hashlib
import json
import logging
import secrets
import calendar
import pytz
from datetime import datetime, timedelta, timezone
from functools import wraps

from ..extensions import db
from ..models import (
    Invitation, BlockedUser, UserProfile, PixPayment, Notification, PasswordReset,
    UnlockedAchievement, ShortLink, Coupon, CouponUsage, Task, StreamTerminationLog,
    PushSubscription
)
from sqlalchemy import func, String
from sqlalchemy.exc import IntegrityError
from flask_babel import gettext as _, ngettext
from collections import defaultdict
from tzlocal import get_localzone_name
from ..utils.identity import normalize_user_id
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


# Quantos dias vale um link de pagamento, quando o config não diz outra coisa.
DIAS_DE_VALIDADE_DO_PAYMENT_TOKEN = 30


def _validade_do_payment_token():
    """A partir de quando um link de pagamento deixa de servir.

    `0` (ou um valor sem sentido) quer dizer SEM VALIDADE, que é o
    comportamento antigo — fica disponível para quem dependa de links de vida
    longa, mas não é o padrão: um token que nunca expira é uma credencial
    portadora eterna a viajar por Telegram e WhatsApp.
    """
    try:
        from ..config import load_or_create_config
        dias = int(load_or_create_config().get(
            'PAYMENT_TOKEN_VALIDITY_DAYS', DIAS_DE_VALIDADE_DO_PAYMENT_TOKEN))
    except (TypeError, ValueError):
        dias = DIAS_DE_VALIDADE_DO_PAYMENT_TOKEN

    if dias <= 0:
        return None
    # Guardada sem fuso, como o resto das colunas `DateTime` desta base de dados.
    return (datetime.now(timezone.utc) + timedelta(days=dias)).replace(tzinfo=None)


def tipo_de_servidor_configurado():
    """Que servidor de média este painel administra, para marcar o que grava.

    Lê-se do config e não do manager: isto corre em jobs de fundo e em rotas,
    e a camada de dados não deve depender de um objeto que pode ainda não ter
    sido construído.
    """
    try:
        from ..config import load_or_create_config
        from .media_server import resolve_media_server_type

        return resolve_media_server_type(load_or_create_config().get('MEDIA_SERVER_TYPE'))
    except Exception as e:  # pragma: no cover - defensivo
        logger.debug(f"Não foi possível determinar o tipo de servidor: {e}")
        return None


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
        coupon = Coupon.query.filter(
            func.upper(Coupon.code) == normalizado, Coupon.deleted_at.is_(None)
        ).first()
        return self._row_to_dict(coupon) if coupon else None

    def get_coupon_by_id(self, coupon_id, incluir_apagados=False):
        consulta = Coupon.query.filter(Coupon.id == coupon_id)
        if not incluir_apagados:
            consulta = consulta.filter(Coupon.deleted_at.is_(None))
        return self._row_to_dict(consulta.first())

    def get_all_coupons(self):
        coupons = (Coupon.query.filter(Coupon.deleted_at.is_(None))
                   .order_by(Coupon.created_at.desc()).all())
        return [self._row_to_dict(c) for c in coupons]

    @db_transaction
    def delete_coupon(self, coupon_id):
        """Marca um cupão como apagado, sem apagar quem o usou.

        🛡️ O DELETE de antes arrastava consigo, por `cascade='all,
        delete-orphan'`, todas as linhas de `coupon_usages` — ou seja, o
        registo de QUEM já tinha usado aquele cupão. E é esse registo que
        impede a mesma pessoa de o usar outra vez. Apagar um cupão para o
        recriar a seguir (que é o que se faz para lhe corrigir o valor) dava a
        toda a gente um segundo desconto.
        """
        coupon = Coupon.query.filter(
            Coupon.id == coupon_id, Coupon.deleted_at.is_(None)
        ).first()
        if coupon:
            coupon.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            # Um cupão apagado também não pode continuar a ser aceite: a
            # procura por código ignora os apagados, mas desativá-lo torna a
            # intenção explícita para quem olhe direto para a tabela.
            coupon.is_active = False
            return True
        return False

    @db_transaction
    def toggle_coupon_active(self, coupon_id):
        coupon = Coupon.query.filter(
            Coupon.id == coupon_id, Coupon.deleted_at.is_(None)
        ).first()
        if coupon:
            coupon.is_active = not coupon.is_active
            return self._row_to_dict(coupon)
        return None

    @db_transaction
    def record_coupon_usage(self, code, media_user_id):
        """
        Regista o uso de um cupão. É IDEMPOTENTE: registar duas vezes o mesmo par
        (utilizador, cupão) não faz nada e não levanta erro.

        ⚠️ Isto não é um detalhe: 'coupon_usages' tem uma restrição de unicidade
        em (media_user_id, coupon_id) e este método é chamado ao confirmar um
        pagamento. Uma segunda inserção levantava IntegrityError, que subia até ao
        processamento do pagamento e o marcava como 'FALHOU' — DEPOIS de a
        assinatura já ter sido renovada. O cliente pagava, era renovado, e a
        transação desaparecia do relatório financeiro e do CSV.
        """
        normalizado = normalize_coupon_code(code)
        coupon = Coupon.query.filter(
            func.upper(Coupon.code) == normalizado, Coupon.deleted_at.is_(None)
        ).first() if normalizado else None
        if not coupon or not media_user_id:
            logger.warning(f"Tentativa de registar o uso de um cupão inválido ('{code}') ou para um utilizador inválido.")
            return False

        ja_registado = db.session.query(CouponUsage.id).filter(
            CouponUsage.coupon_id == coupon.id,
            CouponUsage.media_user_id == media_user_id
        ).first()
        if ja_registado:
            logger.info(
                f"Uso do cupão '{coupon.code}' pelo utilizador ID {media_user_id} já estava registado. "
                "Nada a fazer (registo idempotente)."
            )
            return False

        coupon.use_count += 1
        new_usage = CouponUsage(media_user_id=media_user_id, coupon_id=coupon.id)
        db.session.add(new_usage)
        logger.info(f"Uso do cupão '{coupon.code}' registado para o utilizador ID {media_user_id}. Contagem: {coupon.use_count}.")
        return True

    def has_user_used_coupon(self, media_user_id, code):
        # 🚀 OTIMIZAÇÃO: Busca apenas o ID para ser instantâneo, em vez de carregar a linha toda
        normalizado = normalize_coupon_code(code)
        if not normalizado:
            return False
        usage_exists = db.session.query(CouponUsage.id).join(Coupon).filter(
            func.upper(Coupon.code) == normalizado,
            CouponUsage.media_user_id == media_user_id
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
                PixPayment.created_at >= cutoff,
                PixPayment.deleted_at.is_(None),
            )
            return int(query.scalar() or 0)
        except Exception:
            return 0

    def has_user_pending_coupon_charge(self, media_user_id, code,
                                       max_age_minutes=RESERVED_COUPON_MAX_AGE_MINUTES):
        """
        Indica se o utilizador já tem uma cobrança aberta com este cupão.

        Sem esta verificação, a mesma pessoa gerava duas cobranças com o mesmo
        cupão (o 'já usou' só é registado na confirmação) e pagava ambas com
        desconto. O PIX que ela já tem continua válido e pagável — só não pode
        gerar um segundo em paralelo enquanto o primeiro não expira.
        """
        normalizado = normalize_coupon_code(code)
        if not normalizado or not media_user_id:
            return False
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=int(max_age_minutes))).isoformat()
            existe = db.session.query(PixPayment.txid).filter(
                PixPayment.media_user_id == media_user_id,
                func.upper(PixPayment.coupon_code) == normalizado,
                PixPayment.status.in_(('ATIVA', 'PROCESSANDO')),
                PixPayment.created_at >= cutoff,
                PixPayment.deleted_at.is_(None),
            ).first()
            return existe is not None
        except Exception:
            return False

    # --- MÉTODOS DE GAMIFICAÇÃO ---
    def get_unlocked_achievements(self, media_user_id):
        achievements = UnlockedAchievement.query.filter_by(media_user_id=media_user_id).all()
        return {ach.achievement_id for ach in achievements}

    @db_transaction
    def add_unlocked_achievements(self, media_user_id, username, achievements_to_add):
        for ach_data in achievements_to_add:
            new_achievement = UnlockedAchievement(
                media_user_id=media_user_id,
                username=username,
                achievement_id=ach_data['id']
            )
            db.session.add(new_achievement)

    # --- MÉTODOS DE NOTIFICAÇÃO ---
    @db_transaction
    def create_notification(self, message, category='info', link=None, media_user_id=None):
        notification = Notification(
            message=message, category=category, link=link,
            media_user_id=media_user_id, timestamp=datetime.now(timezone.utc)
        )
        db.session.add(notification)
        db.session.flush() 
        return self._row_to_dict(notification)

    def get_notifications(self, media_user_id=None, limit=10, include_read=False):
        query = Notification.query.filter_by(media_user_id=media_user_id).order_by(Notification.timestamp.desc())
        if not include_read:
            query = query.filter_by(is_read=False)
        notifications = query.limit(limit).all()
        return [self._row_to_dict(n) for n in notifications]

    def get_unread_notification_count(self, media_user_id=None):
        return Notification.query.filter_by(media_user_id=media_user_id, is_read=False).count()

    @db_transaction
    def mark_all_as_read(self, media_user_id=None):
        # 🚀 OTIMIZAÇÃO: synchronize_session=False previne consumo excessivo de RAM
        updated_rows = Notification.query.filter_by(media_user_id=media_user_id, is_read=False).update({'is_read': True}, synchronize_session=False)
        return updated_rows
            
    @db_transaction
    def delete_all_notifications(self, media_user_id=None):
        num_rows_deleted = db.session.query(Notification).filter_by(media_user_id=media_user_id).delete(synchronize_session=False)
        return num_rows_deleted

    @db_transaction
    def update_user_notification_timestamp(self, media_user_id):
        profile = UserProfile.query.get(media_user_id)
        if profile:
            profile.last_notification_sent = datetime.now(timezone.utc).isoformat()
            return True
        return False

    # --- MÉTODOS DE NOTIFICAÇÕES PUSH ---
    # ⚠️ `media_user_id=None` é o ADMINISTRADOR, a mesma convenção das
    # notificações do sino. Não é "toda a gente": para isso há
    # `get_all_push_subscriptions`.

    @db_transaction
    def registar_push_subscription(self, media_user_id, endpoint, p256dh, auth,
                                   device_label=None):
        """Grava (ou atualiza) a subscrição de um aparelho.

        ⚠️ A chave é o ENDEREÇO, não a pessoa. O mesmo navegador devolve sempre
        o mesmo endereço, e o que muda é de quem ele é agora — a sessão do
        administrador e a de um usuário comum no mesmo computador partilham-no.
        Sem esta atualização, as notificações continuavam a ir para o dono
        anterior.
        """
        subscricao = PushSubscription.query.filter_by(endpoint=endpoint).first()
        if subscricao is None:
            subscricao = PushSubscription(endpoint=endpoint)
            db.session.add(subscricao)

        subscricao.media_user_id = media_user_id
        subscricao.p256dh = p256dh
        subscricao.auth = auth
        if device_label:
            subscricao.device_label = device_label
        db.session.flush()
        return self._row_to_dict(subscricao)

    @db_transaction
    def remover_push_subscription(self, endpoint):
        """Apaga a subscrição de um aparelho. Devolve se alguma foi apagada."""
        apagadas = PushSubscription.query.filter_by(endpoint=endpoint).delete(
            synchronize_session=False)
        return apagadas > 0

    def get_push_subscriptions(self, media_user_id=None):
        """Os aparelhos de uma pessoa (ou do administrador, com `None`)."""
        linhas = PushSubscription.query.filter_by(media_user_id=media_user_id).all()
        return [self._row_to_dict(linha) for linha in linhas]

    def get_all_push_subscriptions(self):
        """Todos os aparelhos subscritos, de toda a gente."""
        return [self._row_to_dict(linha) for linha in PushSubscription.query.all()]

    @db_transaction
    def marcar_push_entregue(self, endpoint):
        """Regista que a última entrega a este aparelho foi aceite."""
        return PushSubscription.query.filter_by(endpoint=endpoint).update(
            {'last_success_at': datetime.now(timezone.utc)}, synchronize_session=False)

    # --- MÉTODOS DE AUDITORIA ---
    @db_transaction
    def log_stream_termination(self, media_user_id, username, media_title, platform, reason,
                               timestamp=None):
        """Regista um corte na auditoria.

        `timestamp` existe para os cortes que NÃO foram do painel: os que o
        plugin StreamLimiter deu sozinho são lidos do log do Jellyfin minutos
        depois de acontecerem, e gravá-los com a hora da leitura punha-os todos
        empilhados no mesmo instante, fora de ordem com os restantes.
        """
        log_entry = StreamTerminationLog(
            media_user_id=media_user_id, username=username, media_title=media_title,
            platform=platform, reason=reason,
            timestamp=timestamp or datetime.now(timezone.utc)
        )
        db.session.add(log_entry)
        return self._row_to_dict(log_entry)

    def get_last_termination_timestamp(self, reason):
        """Quando foi o último corte registado com esta razão (None se nenhum).

        É a marca de água de quem importa cortes de fora: sem ela, cada leitura
        do log do servidor voltava a gravar o que já lá estava.
        """
        # ⚠️ A marca de água conta os cortes APAGADOS também, de propósito: ela
        # não serve para mostrar nada, serve para não voltar a importar o que já
        # foi importado. Ignorar os apagados fazia a importação seguinte reler
        # tudo desde o início e duplicar o que tinha sido escondido.
        ultimo = (StreamTerminationLog.query
                  .filter(StreamTerminationLog.reason == reason)
                  .order_by(StreamTerminationLog.timestamp.desc())
                  .first())
        return ultimo.timestamp if ultimo else None

    def get_stream_termination_logs(self, limit=20):
        logs = (StreamTerminationLog.query
                .filter(StreamTerminationLog.deleted_at.is_(None))
                .order_by(StreamTerminationLog.timestamp.desc())
                .limit(limit).all())
        return [self._row_to_dict(log) for log in logs]

    @db_transaction
    def delete_stream_termination_log(self, log_id):
        """Tira um corte da Auditoria de Cortes, sem o apagar da base de dados.

        🛡️ A auditoria de cortes é o único registo de que alguém foi
        interrompido, e porquê — e tinha um botão que a apagava para sempre,
        linha a linha ou toda de uma vez. Uma auditoria que se apaga com um
        clique não responde à pergunta para que existe.
        """
        log_entry = StreamTerminationLog.query.filter(
            StreamTerminationLog.id == log_id,
            StreamTerminationLog.deleted_at.is_(None),
        ).first()
        if log_entry:
            log_entry.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            return True
        return False

    @db_transaction
    def clear_all_stream_termination_logs(self):
        """Limpa a Auditoria de Cortes da VISTA. Nada sai da base de dados.

        🛡️ Era um `DELETE` sem cláusula nenhuma: um botão que apagava para
        sempre, de uma vez, todo o registo de quem tinha sido interrompido e
        porquê. Continua a limpar o ecrã — é para isso que serve — mas as
        linhas ficam, marcadas com a data.
        """
        return (db.session.query(StreamTerminationLog)
                .filter(StreamTerminationLog.deleted_at.is_(None))
                .update({'deleted_at': datetime.now(timezone.utc).replace(tzinfo=None)},
                        synchronize_session=False))

    # --- MÉTODOS FINANCEIROS OTIMIZADOS ---
    def get_financial_summary(self, year, month, renewal_days=7):
        local_tz = get_app_timezone()

        # 1. Delimitar o mês atual usando o fuso horário local
        # 🐛 Era `_, last_day = ...`: o `_` do gettext ficava a valer um INTEIRO
        # para o resto da função, e a linha que escreve "Hoje" (um vencimento
        # que cai no próprio dia) rebentava com
        # `TypeError: 'int' object is not callable`.
        _dia_da_semana, last_day = calendar.monthrange(year, month)
        local_start = local_tz.localize(datetime(year, month, 1, 0, 0, 0))
        local_end = local_tz.localize(datetime(year, month, last_day, 23, 59, 59, 999999))

        # Converter para as horas UTC exatas para a consulta na base de dados
        utc_start_str = local_start.astimezone(timezone.utc).isoformat()
        utc_end_str = local_end.astimezone(timezone.utc).isoformat()

        payments_in_month = db.session.query(PixPayment).filter(
            PixPayment.created_at >= utc_start_str,
            PixPayment.created_at <= utc_end_str,
            PixPayment.status == 'CONCLUIDA',
            PixPayment.deleted_at.is_(None),
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
            BlockedUser.media_user_id == None, 
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
            PixPayment.created_at <= end_date_iso,
            PixPayment.deleted_at.is_(None),
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

    def get_latest_completed_payment(self, media_user_id):
        payment = PixPayment.query.filter_by(
            media_user_id=media_user_id,
            status='CONCLUIDA'
        ).filter(PixPayment.deleted_at.is_(None)).order_by(PixPayment.created_at.desc()).first()
        return self._row_to_dict(payment) if payment else None
        
    # --- MÉTODOS para Perfis de Utilizador ---
    def get_user_profile(self, media_user_id):
        profile = UserProfile.query.get(media_user_id)
        return self._row_to_dict(profile) if profile else None

    def get_user_profile_by_username(self, username):
        profile = UserProfile.query.filter(func.lower(UserProfile.username) == username.lower()).first()
        return self._row_to_dict(profile) if profile else None
    
    def get_user_profile_by_contacto(self, canal, valor):
        """Localiza o utilizador vinculado a um ID de `canal` ('telegram', 'discord').

        🐛 NOTA: os nomes das colunas DIVERGEM entre o convite e o perfil — em
        `user_profiles` são `telegram_user` e `discord_user_id`, em
        `invitations` são `telegram_id` e `discord_id`. Já houve código a ler
        `profile.get("telegram_id")`, que devolve SEMPRE None por essa coluna
        não existir no perfil, e a parecer funcionar por causa de um `or` à
        frente. Quem faz a ponte é o mapa `CONTACTOS`, em
        `media_server/invitations.py`.

        A comparação é feita como texto e sem espaços, porque o ID pode chegar
        como número (de um bot) ou como string (de um formulário).
        """
        colunas = {
            'telegram': UserProfile.telegram_user,
            'discord': UserProfile.discord_user_id,
        }
        coluna = colunas.get(canal)
        if coluna is None or valor is None or str(valor).strip() == "":
            return None

        profile = UserProfile.query.filter(
            func.trim(func.cast(coluna, String)) == str(valor).strip()
        ).first()
        return self._row_to_dict(profile) if profile else None

    def get_user_profile_by_telegram(self, telegram_id):
        """O nome antigo, que continua a ser chamado de vários sítios."""
        return self.get_user_profile_by_contacto('telegram', telegram_id)

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

    def get_users_referred_by(self, media_user_id):
        """Lista os utilizadores indicados por alguém."""
        try:
            profiles = UserProfile.query.filter(UserProfile.referred_by == media_user_id).all()
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
    def set_user_referral_code(self, media_user_id, code):
        """
        Atribui um código de indicação, mas só se o utilizador ainda não tiver um.
        Devolve o código que ficou efetivamente em vigor — o novo, ou o que já lá
        estava caso outro pedido em paralelo se tenha antecipado.

        Propaga IntegrityError se o código colidir com o de outro utilizador
        (há um índice único na coluna); quem chama deve gerar outro e tentar de novo.
        """
        uid = media_user_id
        updated = UserProfile.query.filter(
            UserProfile.media_user_id == uid,
            (UserProfile.referral_code.is_(None)) | (UserProfile.referral_code == '')
        ).update({UserProfile.referral_code: code}, synchronize_session=False)

        if updated:
            return code

        # Já tinha código (ou o perfil não existe): devolve o que está gravado.
        row = db.session.query(UserProfile.referral_code).filter(
            UserProfile.media_user_id == uid
        ).first()
        return row[0] if row else None

    @db_transaction
    def add_referral_credit(self, media_user_id, amount):
        """Soma crédito de indicações ao saldo. Devolve o valor somado."""
        value = round(float(amount or 0), 2)
        if value <= 0:
            return 0.0
        updated = UserProfile.query.filter(
            UserProfile.media_user_id == media_user_id
        ).update(
            {UserProfile.referral_credit: func.coalesce(UserProfile.referral_credit, 0.0) + value},
            synchronize_session=False
        )
        return value if updated else 0.0

    @db_transaction
    def consume_referral_credit(self, media_user_id, amount):
        """
        Abate crédito do saldo e devolve o valor efetivamente consumido. O saldo
        nunca fica negativo: se o pedido exceder o disponível, consome só o resto.
        """
        uid = media_user_id
        wanted = round(max(0.0, float(amount or 0)), 2)
        if wanted <= 0:
            return 0.0

        # Caso normal: há saldo suficiente. O WHERE garante que dois abatimentos
        # simultâneos nunca gastam o mesmo crédito duas vezes.
        if UserProfile.query.filter(
            UserProfile.media_user_id == uid,
            UserProfile.referral_credit >= wanted
        ).update(
            {UserProfile.referral_credit: UserProfile.referral_credit - wanted},
            synchronize_session=False
        ):
            return wanted

        # Saldo insuficiente: leva o que restar e deixa o saldo a zero.
        row = db.session.query(UserProfile.referral_credit).filter(
            UserProfile.media_user_id == uid
        ).first()
        available = round(float(row[0] or 0), 2) if row else 0.0
        if available <= 0:
            return 0.0

        if UserProfile.query.filter(
            UserProfile.media_user_id == uid,
            UserProfile.referral_credit > 0,
            UserProfile.referral_credit <= wanted
        ).update({UserProfile.referral_credit: 0.0}, synchronize_session=False):
            return available
        return 0.0

    def get_reserved_referral_credit(self, media_user_id, exclude_txid=None,
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
                PixPayment.media_user_id == media_user_id,
                PixPayment.status.in_(('ATIVA', 'PROCESSANDO')),
                PixPayment.referral_credit_used > 0,
                PixPayment.created_at >= cutoff,
                PixPayment.deleted_at.is_(None),
            )
            if exclude_txid:
                query = query.filter(PixPayment.txid != exclude_txid)
            return round(float(query.scalar() or 0.0), 2)
        except Exception:
            return 0.0

    @db_transaction
    def claim_referral_reward(self, media_user_id):
        """
        Marca — de forma atómica — que a recompensa pela indicação DESTE utilizador
        já foi paga, e devolve True apenas a quem 'ganhou a corrida'.

        É esta condição no UPDATE (e não uma leitura prévia do campo) que garante
        que a recompensa é entregue exatamente uma vez, mesmo que o pagamento seja
        processado duas vezes em simultâneo.
        """
        return bool(UserProfile.query.filter(
            UserProfile.media_user_id == media_user_id,
            UserProfile.referred_by.isnot(None),
            UserProfile.referral_rewarded.is_(False)
        ).update({UserProfile.referral_rewarded: True}, synchronize_session=False))

    @db_transaction
    def release_referral_reward(self, media_user_id):
        """
        Desfaz a marca de recompensa paga. Usado quando a entrega falha a meio:
        sem isto, o indicado ficava marcado como 'já recompensado' e quem o
        indicou nunca receberia nada.
        """
        return bool(UserProfile.query.filter(
            UserProfile.media_user_id == media_user_id
        ).update({UserProfile.referral_rewarded: False}, synchronize_session=False))

    def count_rewarded_referrals(self, media_user_id):
        """Quantas indicações deste utilizador já foram efetivamente recompensadas."""
        try:
            return UserProfile.query.filter(
                UserProfile.referred_by == media_user_id,
                UserProfile.referral_rewarded.is_(True)
            ).count()
        except Exception:
            return 0

    def user_has_completed_payment(self, media_user_id):
        """Indica se o utilizador já tem algum pagamento confirmado no histórico."""
        try:
            return bool(PixPayment.query.filter_by(
                media_user_id=media_user_id, status='CONCLUIDA'
            ).filter(PixPayment.deleted_at.is_(None)).first())
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

    def get_user_profiles_by_id(self, media_user_ids):
        if not media_user_ids: return {}
        try:
            profiles = UserProfile.query.filter(UserProfile.media_user_id.in_(media_user_ids)).all()
            return {p.media_user_id: self._row_to_dict(p) for p in profiles}
        except Exception: return {}

    @db_transaction
    def set_user_profile(self, media_user_id, profile_data):
        """Cria ou atualiza o perfil local de um utilizador.

        ⚠️ **Criar exige um `username`** — a coluna é NOT NULL. Quem chama isto
        com um dicionário PARCIAL (só o XP, só o estado) está a contar que o
        perfil já exista; quando não existia, o que aparecia no log era um
        `IntegrityError` sobre um INSERT de trinta colunas, que não diz a
        ninguém o que faltava. Diz-se aqui.
        """
        profile = UserProfile.query.get(media_user_id)
        if not profile:
            if not (profile_data or {}).get('username'):
                raise ValueError(
                    f"Não se cria um perfil sem 'username' (media_user_id={media_user_id})."
                )
            profile = UserProfile(media_user_id=media_user_id)
            # Que servidor de média criou este perfil. Sem isto, todos os
            # perfis criados pelo painel ficavam com a coluna a NULL — e a
            # coluna existe precisamente para não confundir um ID do Plex com
            # um GUID do Jellyfin que por acaso coincida. Quem passa o valor
            # explicitamente (o registo de contas do Jellyfin) manda.
            profile.media_server_type = (
                (profile_data or {}).get('media_server_type') or tipo_de_servidor_configurado()
            )
        if not profile.payment_token:
            profile.payment_token = secrets.token_urlsafe(32)
            profile.payment_token_expires_at = _validade_do_payment_token()
        
        for key, value in profile_data.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        
        db.session.add(profile)
        return self._row_to_dict(profile)
    
    # As tabelas que guardam a identidade de um utilizador, e a coluna onde a
    # guardam. Uma coluna nova que cite `user_profiles.media_user_id` tem de
    # entrar aqui, ou uma migração de identidade deixa-a a apontar para um
    # perfil que já não existe.
    _TABELAS_COM_IDENTIDADE = (
        ('coupon_usages', 'media_user_id'),
        ('blocked_users', 'media_user_id'),
        ('pix_payments', 'media_user_id'),
        ('notifications', 'media_user_id'),
        ('unlocked_achievements', 'media_user_id'),
        ('stream_termination_logs', 'media_user_id'),
        # Um pedido de reposição em curso quando a conta é recriada: sem isto
        # ficava a apontar para um perfil que já não existe, e o link que a
        # pessoa tinha acabado de receber deixava de funcionar sem explicação.
        ('password_resets', 'media_user_id'),
        # Quem indicou quem: aponta para um perfil sem ser chave estrangeira.
        ('user_profiles', 'referred_by'),
    )

    @db_transaction
    def migrar_identidade(self, antigo, novo):
        """Muda o `media_user_id` de um perfil e de tudo o que lhe aponta.

        ⚠️ **Isto existe por uma razão só**: num servidor de contas locais, uma
        conta apagada e recriada volta com um identificador NOVO — o Jellyfin
        atribui um GUID ao criar e não aceita que se lhe imponha um. Sem migrar,
        a pessoa reaparecia como um estranho: sem pagamentos, sem XP, sem
        conquistas e sem a data de vencimento que acabou de pagar.

        Não é uma fusão de perfis: se já existir um perfil com o identificador
        novo, recusa-se. Juntar dois históricos é uma decisão de quem administra,
        não de uma rotina automática.

        ⚠️ **As chaves estrangeiras são agora IMPOSTAS** (`e1c7a4f92db6` ligou o
        `PRAGMA foreign_keys`), e isso muda o que este método pode fazer. Mudar
        a chave primária do perfil com filhas a apontar-lhe é, para o SQLite,
        uma violação — a não ser que a chave diga `ON UPDATE CASCADE`, que é o
        que as tabelas de ESTADO dizem. Elas seguem sozinhas; o ciclo abaixo
        passa por lá e não encontra nada para mudar, o que está certo.

        Quem PRECISA mesmo do ciclo são as tabelas de HISTÓRICO — os pagamentos
        e a auditoria de cortes — que não têm chave estrangeira de propósito
        (têm de sobreviver ao perfil) e por isso ninguém as arrasta.

        Tudo corre na MESMA transação: ou muda tudo, ou não muda nada. Metade
        migrada seria pior do que não migrar.
        """
        from sqlalchemy import text

        antigo = normalize_user_id(antigo)
        novo = normalize_user_id(novo)

        if not antigo or not novo:
            raise ValueError("Migrar identidade exige os dois identificadores.")
        if antigo == novo:
            return False

        if not UserProfile.query.get(antigo):
            raise ValueError(f"Não há perfil com o identificador '{antigo}'.")
        if UserProfile.query.get(novo):
            raise ValueError(
                f"Já existe um perfil com o identificador '{novo}': migrar juntaria "
                "dois históricos numa só pessoa."
            )

        # O perfil primeiro: as outras tabelas citam-no.
        db.session.execute(
            text('UPDATE user_profiles SET media_user_id = :novo WHERE media_user_id = :antigo'),
            {'novo': novo, 'antigo': antigo},
        )

        for tabela, coluna in self._TABELAS_COM_IDENTIDADE:
            db.session.execute(
                text(f'UPDATE "{tabela}" SET "{coluna}" = :novo WHERE "{coluna}" = :antigo'),
                {'novo': novo, 'antigo': antigo},
            )

        self._migrar_identidade_nos_convites(antigo, novo)

        logger.info(f"Identidade migrada: '{antigo}' passa a ser '{novo}'.")
        return True

    def _migrar_identidade_nos_convites(self, antigo, novo):
        """`invitations.claimed_by_ids` é uma lista JSON, não uma coluna de ID.

        É por ela que se sabe quem já resgatou um convite — deixá-la para trás
        deixava a porta aberta a resgatar de novo um convite já usado.
        """
        for convite in Invitation.query.all():
            try:
                ids = json.loads(convite.claimed_by_ids or '[]')
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(ids, list) or not any(normalize_user_id(i) == antigo for i in ids):
                continue

            convite.claimed_by_ids = json.dumps(
                [novo if normalize_user_id(i) == antigo else i for i in ids]
            )
            db.session.add(convite)

    # =========================================================================
    # REPOSIÇÃO DE PALAVRA-PASSE
    # =========================================================================

    # Meia hora: tempo de sobra para ler uma notificação e escrever uma
    # palavra-passe nova, e curto o suficiente para um link esquecido num
    # histórico de Telegram deixar de servir.
    VALIDADE_DA_REPOSICAO_MINUTOS = 30

    @staticmethod
    def _resumo_do_token(token):
        return hashlib.sha256(str(token or '').encode('utf-8')).hexdigest()

    @db_transaction
    def criar_pedido_de_reposicao(self, media_user_id):
        """Cria um pedido e devolve o token EM CLARO, uma única vez.

        🛡️ O que fica guardado é o resumo: a partir daqui, só quem recebeu a
        notificação tem o token. Nem o administrador o consegue ler na base de
        dados — o que é o objetivo.

        Pedir de novo invalida o pedido anterior: dois links válidos ao mesmo
        tempo é uma porta a mais aberta, e quem pediu outra vez está a usar o
        último que recebeu.
        """
        media_user_id = normalize_user_id(media_user_id)
        if not media_user_id:
            raise ValueError("Um pedido de reposição precisa de um utilizador.")

        PasswordReset.query.filter_by(media_user_id=media_user_id, used_at=None).delete()

        token = secrets.token_urlsafe(32)
        agora = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.add(PasswordReset(
            token_hash=self._resumo_do_token(token),
            media_user_id=media_user_id,
            created_at=agora,
            expires_at=agora + timedelta(minutes=self.VALIDADE_DA_REPOSICAO_MINUTOS),
        ))
        return token

    def ler_pedido_de_reposicao(self, token):
        """O pedido correspondente a este token, sem o consumir.

        Serve a página do formulário, que precisa de saber se vale a pena
        mostrá-lo. Devolve `(media_user_id, motivo)`: o motivo diz `'expirado'`,
        `'usado'` ou `'invalido'` — são três coisas diferentes para quem está do
        outro lado, e um "link inválido" para todas seria mentira em duas delas.
        """
        if not token:
            return None, 'invalido'

        pedido = PasswordReset.query.get(self._resumo_do_token(token))
        if not pedido:
            return None, 'invalido'
        if pedido.used_at is not None:
            return None, 'usado'
        if pedido.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
            return None, 'expirado'
        return pedido.media_user_id, ''

    @db_transaction
    def consumir_pedido_de_reposicao(self, token):
        """Marca o pedido como usado e devolve `(media_user_id, motivo)`.

        ⚠️ Marcar ANTES de mudar a palavra-passe seria perder o pedido se o
        servidor de média recusasse; marcar DEPOIS deixava a janela para dois
        pedidos em paralelo usarem o mesmo token. Quem chama marca aqui e, se o
        servidor recusar, diz à pessoa que peça outro — é o lado seguro do erro.
        """
        media_user_id, motivo = self.ler_pedido_de_reposicao(token)
        if not media_user_id:
            return None, motivo

        pedido = PasswordReset.query.get(self._resumo_do_token(token))
        pedido.used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.add(pedido)
        return media_user_id, ''

    @db_transaction
    def limpar_pedidos_de_reposicao_antigos(self, dias=7):
        """Os pedidos usados e os expirados não têm de ficar para sempre."""
        limite = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
        apagados = PasswordReset.query.filter(PasswordReset.created_at < limite).delete()
        if apagados:
            logger.info(f"Limpeza: {apagados} pedido(s) de reposição de palavra-passe antigos removidos.")
        return apagados

    @db_transaction
    def delete_user_profile(self, media_user_id):
        profile = UserProfile.query.get(media_user_id)
        if profile:
            db.session.delete(profile)
            return True
        return False

    # --- MÉTODOS do token de pagamento ---
    #
    # 🛡️ O `payment_token` é uma credencial PORTADORA: quem tiver o link
    # `/pay/<token>` vê o nome e o vencimento de quem lá está, e pode gerar uma
    # cobrança. Ele viaja por Telegram, Discord e WhatsApp — canais que a pessoa
    # pode reencaminhar sem pensar, e que ficam no histórico do telemóvel para
    # sempre.
    #
    # Até aqui esse token **nunca expirava e nunca mudava**: o primeiro link
    # enviado a alguém continuava a funcionar anos depois, e um dump da base de
    # dados entregava o de toda a gente, em texto puro e prontos a usar. Agora
    # tem validade, é renovado quando se manda um link novo, e é trocado depois
    # de um pagamento ser confirmado.

    def perfil_por_payment_token(self, token):
        """O perfil dono deste token, ou None se ele não existir ou ter expirado.

        ⚠️ **Todas as rotas públicas de pagamento passam por aqui.** Enquanto
        cada uma fazia o seu `filter_by(payment_token=...)`, acrescentar a
        verificação de validade obrigava a lembrar-se dela em cinco sítios — e
        o sítio esquecido não dava erro, dava um token eterno.
        """
        if not token:
            return None

        perfil = UserProfile.query.filter_by(payment_token=token).first()
        if not perfil:
            return None

        validade = perfil.payment_token_expires_at
        # Sem validade gravada é um perfil anterior a esta mudança: vale, e é
        # renovado no próximo link que lhe for enviado. Invalidar de repente os
        # links que já estão no telemóvel de toda a gente seria uma migração a
        # cortar o acesso a quem quer pagar.
        if validade and validade < datetime.now(timezone.utc).replace(tzinfo=None):
            logger.info(
                f"Token de pagamento expirado para '{perfil.username}': "
                "é preciso um link novo."
            )
            return None

        return perfil

    @db_transaction
    def garantir_payment_token(self, media_user_id):
        """Um token VÁLIDO para este perfil, renovando-o se for preciso.

        É por aqui que passa quem vai ENVIAR um link. Renovar no envio (e não
        num relógio qualquer) é o que faz a validade ser útil sem ser um
        incómodo: o link que a pessoa acabou de receber está sempre bom, e o que
        ela recebeu há três meses já não.
        """
        perfil = UserProfile.query.get(media_user_id)
        if not perfil:
            return None

        agora = datetime.now(timezone.utc).replace(tzinfo=None)
        expirado = perfil.payment_token_expires_at and perfil.payment_token_expires_at < agora

        if not perfil.payment_token or expirado:
            perfil.payment_token = secrets.token_urlsafe(32)

        # A validade é sempre estendida, mesmo quando o token se mantém: o que
        # conta é a data do ÚLTIMO link enviado.
        perfil.payment_token_expires_at = _validade_do_payment_token()
        return perfil.payment_token

    @db_transaction
    def rodar_payment_token(self, media_user_id):
        """Troca o token depois de um pagamento confirmado.

        🛡️ O link já cumpriu o que tinha a fazer. Deixá-lo a funcionar era
        manter viva, indefinidamente, uma credencial que passou por três
        aplicações de mensagens — e que qualquer pessoa com acesso ao telemóvel
        ou ao histórico da conversa podia voltar a abrir.

        Quem recarregar a página de pagamento a seguir vê a mensagem de link
        inválido, que já diz o que fazer ("solicite um novo link").
        """
        perfil = UserProfile.query.get(media_user_id)
        if not perfil:
            return None

        perfil.payment_token = secrets.token_urlsafe(32)
        perfil.payment_token_expires_at = _validade_do_payment_token()
        return perfil.payment_token

    def get_all_user_expirations(self):
        profiles = UserProfile.query.filter(UserProfile.expiration_date.isnot(None), UserProfile.expiration_date != '').all()
        return {p.media_user_id: self._row_to_dict(p) for p in profiles}

    def get_all_trial_users(self):
        profiles = UserProfile.query.filter(UserProfile.trial_end_date.isnot(None), UserProfile.trial_end_date != '').all()
        return {p.media_user_id: self._row_to_dict(p) for p in profiles}

    # --- MÉTODOS de Pagamento PIX ---
    def get_and_lock_pix_payment(self, txid):
        try:
            return self._row_to_dict(db.session.query(PixPayment).filter_by(txid=txid).with_for_update().first())
        except Exception: 
            db.session.rollback()
            raise

    @db_transaction
    def create_pix_payment(self, txid, media_user_id, username, value, provider, screens, external_reference, coupon_code=None):
        payment = PixPayment.query.get(txid) or PixPayment(txid=txid)
        payment.media_user_id = media_user_id
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

    def get_pix_payment(self, txid, incluir_apagados=False):
        """A cobrança com este txid, ou None.

        `incluir_apagados` existe para quem precisa mesmo de lá chegar — a
        auditoria, uma reposição — e não para as consultas normais. Sem ele, um
        pagamento apagado por engano continuava a aparecer em todo o lado.
        """
        consulta = PixPayment.query.filter(PixPayment.txid == txid)
        if not incluir_apagados:
            consulta = consulta.filter(PixPayment.deleted_at.is_(None))
        return self._row_to_dict(consulta.first())

    @db_transaction
    def update_pix_payment_status(self, txid, status):
        payment = PixPayment.query.get(txid)
        if payment: 
            payment.status = status
            return True
        return False

    def add_manual_payment(self, media_user_id, username, value, description, payment_date_str):
        txid = f"manual_{secrets.token_hex(12)}"
        payment = PixPayment(txid=txid, media_user_id=media_user_id, username=username, value=float(value), status='CONCLUIDA', provider='Manual', description=description, created_at=payment_date_str, screens=0, external_reference=None)
        db.session.add(payment)
        return self._row_to_dict(payment)

    def get_payments_by_user(self, media_user_id):
        try:
            return [self._row_to_dict(p) for p in PixPayment.query
                    .filter_by(media_user_id=media_user_id, status='CONCLUIDA')
                    .filter(PixPayment.deleted_at.is_(None))
                    .order_by(PixPayment.created_at.desc()).all()]
        except Exception: return []

    @db_transaction
    def delete_pix_payment(self, txid):
        """Marca uma cobrança como apagada, sem a apagar.

        🛡️ **Era um DELETE sem volta, sobre a tabela onde estão os pagamentos
        recebidos.** Um clique enganado na página financeira e a transação
        desaparecia do relatório, do CSV e do histórico da pessoa — sem
        recuperação nenhuma tirando o backup da noite anterior, que traz de
        volta tudo o resto junto.

        Agora fica: sai de todas as leituras (que filtram `deleted_at IS NULL`)
        e continua na base de dados, com a data em que saiu. A auditoria guarda
        quem a mandou apagar.
        """
        payment = PixPayment.query.filter(
            PixPayment.txid == txid, PixPayment.deleted_at.is_(None)
        ).first()
        if payment:
            payment.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            return True
        return False

    @db_transaction
    def restaurar_pix_payment(self, txid):
        """Devolve às contas uma cobrança apagada por engano.

        É a outra metade da remoção suave: sem uma forma de voltar atrás, a
        coluna `deleted_at` era só um DELETE mais lento.
        """
        payment = PixPayment.query.get(txid)
        if not payment or payment.deleted_at is None:
            return False
        payment.deleted_at = None
        return True

    # --- MÉTODOS de Limpeza de Dados ---
    @db_transaction
    def delete_old_pending_payments(self, days_old):
        if not isinstance(days_old, int) or days_old <= 0: return 0
        cutoff_date_str = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
        # ⚠️ Estas são apagadas MESMO, e continua certo: uma cobrança nunca
        # concluída e abandonada há semanas não é histórico financeiro nenhum —
        # é lixo de QR codes que ninguém chegou a pagar. A remoção suave existe
        # para o que tem valor, e guardar isto para sempre só faria a tabela
        # crescer sem que nada a fosse ler.
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
            telegram_id=details.get('telegram_id'),
            discord_id=details.get('discord_id'),
            note=details.get('note'),
        )
        db.session.add(invitation)
        return self._row_to_dict(invitation)

    def get_invitation(self, code, incluir_apagados=False):
        """O convite, ou None.

        `incluir_apagados` existe para UM caso: decidir se um código
        personalizado pode voltar a ser usado. Um convite removido continua na
        tabela (é ele que guarda o "membro desde" de quem entrou por ele) e o
        `code` é a chave primária — sem olhar para as linhas apagadas, criar
        outro convite com o mesmo código passava na validação e rebentava com
        um IntegrityError.
        """
        invitation = Invitation.query.get(code)
        if not invitation:
            return None
        if invitation.deleted_at is not None and not incluir_apagados:
            return None
        return self._row_to_dict(invitation, process_json=True)

    def get_all_pending_invitations(self):
        invitations = Invitation.query.filter(
            Invitation.use_count < Invitation.max_uses,
            Invitation.deleted_at.is_(None),
        ).all()
        return [self._row_to_dict(invite, process_json=True) for invite in invitations]

    def get_all_invitations(self):
        invitations = (Invitation.query
                       .filter(Invitation.deleted_at.is_(None))
                       .order_by(Invitation.created_at.desc()).all())
        return [self._row_to_dict(invite, process_json=True) for invite in invitations]

    # ⚠️ **As datas dos convites são comparadas como TEXTO.** `created_at` e
    # `expires_at` são colunas de texto, e tudo o que o painel lá escreve vem de
    # `datetime.now(timezone.utc).isoformat()` — sempre UTC, sempre com o mesmo
    # `+00:00` no fim. Duas datas nesse formato ordenam-se lexicograficamente na
    # mesma ordem em que ordenam no tempo, e é por isso que isto funciona (é a
    # mesma comparação que `check_telegram_id_exists_in_invites` já fazia).
    #
    # O que pode escapar ao formato é uma data escrita à mão na base de dados ou
    # vinda de uma importação antiga. O pior que acontece é a linha cair na aba
    # errada: quem decide mesmo se um convite vale é `convite_expirado`, em
    # Python, que trata uma data ilegível como expirada.
    def _convite_esta_ativo(self):
        """A condição SQL de "ainda dá para usar"."""
        agora = datetime.now(timezone.utc).isoformat()
        return db.and_(
            Invitation.use_count < Invitation.max_uses,
            db.or_(Invitation.expires_at.is_(None), Invitation.expires_at > agora),
        )

    def contar_convites(self):
        """(ativos, total) — o que o polling do painel precisa de saber.

        ⚡ A página perguntava isto trazendo a TABELA INTEIRA de dez em dez
        segundos, com o histórico de resgates de cada convite, só para contar
        quantos ainda estavam abertos e avisar quando um tinha sido usado. Dois
        `COUNT(*)` respondem à mesma pergunta.
        """
        vivos = Invitation.query.filter(Invitation.deleted_at.is_(None))
        return (vivos.filter(self._convite_esta_ativo()).count(), vivos.count())

    # ⚠️ Pedir 100000 por página era pedir a tabela inteira por outro caminho —
    # exatamente o que a paginação existe para impedir.
    POR_PAGINA_MAXIMO = 100
    POR_PAGINA_PADRAO = 20

    @classmethod
    def normalizar_paginacao(cls, pagina, por_pagina):
        """(pagina, por_pagina) dentro do que é servido.

        Fica aqui, e não na rota, porque a resposta tem de ECOAR os valores
        efetivos: devolver os pedidos fazia a interface calcular o número de
        páginas sobre um tamanho que não foi o usado, e o botão "seguinte"
        levava a uma página vazia.
        """
        try:
            pagina = int(pagina) if pagina not in (None, '') else 1
            por_pagina = int(por_pagina) if por_pagina not in (None, '') else cls.POR_PAGINA_PADRAO
        except (TypeError, ValueError):
            raise ValueError("paginação inválida")

        # ⚠️ Um valor não positivo é um engano, não um pedido. `por_pagina=0`
        # cair em "uma linha por página" era pior do que voltar ao padrão: quem
        # escreve um zero quer o comportamento normal, não vinte vezes mais
        # pedidos.
        if pagina < 1:
            pagina = 1
        if por_pagina < 1:
            por_pagina = cls.POR_PAGINA_PADRAO

        return pagina, min(por_pagina, cls.POR_PAGINA_MAXIMO)

    def get_invitations_page(self, estado=None, pagina=1, por_pagina=20):
        """Uma página de convites, do mais recente para o mais antigo.

        `estado` é 'ativos', 'historico' ou None (todos). Devolve
        `(linhas, total)`, em que o total é o daquele estado — é o que a
        interface precisa para saber quantas páginas há.
        """
        pagina, por_pagina = self.normalizar_paginacao(pagina, por_pagina)

        consulta = Invitation.query.filter(Invitation.deleted_at.is_(None))
        if estado == 'ativos':
            consulta = consulta.filter(self._convite_esta_ativo())
        elif estado == 'historico':
            consulta = consulta.filter(db.not_(self._convite_esta_ativo()))

        total = consulta.count()
        linhas = (consulta.order_by(Invitation.created_at.desc())
                  .limit(por_pagina).offset((pagina - 1) * por_pagina).all())
        return [self._row_to_dict(l, process_json=True) for l in linhas], total

    def contacto_em_convite_ativo(self, canal, valor):
        """Já existe um convite por usar com este ID de `canal`?

        Um convite gasto ou expirado não bloqueia: ele já não vai vincular
        ninguém, e recusar por causa dele impedia o administrador de gerar um
        convite novo para a mesma pessoa.
        """
        colunas = {
            'telegram': Invitation.telegram_id,
            'discord': Invitation.discord_id,
        }
        coluna = colunas.get(canal)
        if coluna is None or not valor:
            return False

        agora = datetime.now(timezone.utc).isoformat()
        return Invitation.query.filter(
            coluna == str(valor).strip(),
            Invitation.use_count < Invitation.max_uses,
            Invitation.deleted_at.is_(None),
        ).filter(
            (Invitation.expires_at.is_(None)) | (Invitation.expires_at > agora)
        ).first() is not None

    def check_telegram_id_exists_in_invites(self, telegram_id):
        """O nome antigo, mantido para não partir quem o chame."""
        return self.contacto_em_convite_ativo('telegram', telegram_id)

    @db_transaction
    def increment_invitation_use(self, code, username, media_user_id=None):
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

            if media_user_id is not None:
                claimed_ids = json.loads(invitation.claimed_by_ids or '[]')
                if str(media_user_id) not in claimed_ids:
                    claimed_ids.append(str(media_user_id))
                    invitation.claimed_by_ids = json.dumps(claimed_ids)
            return True
        return False
            
    @db_transaction
    def reserve_invitation_use(self, code, username, media_user_id=None):
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
            if media_user_id is not None:
                claimed_ids = json.loads(invitation.claimed_by_ids or '[]')
                if str(media_user_id) not in claimed_ids:
                    claimed_ids.append(str(media_user_id))
                    invitation.claimed_by_ids = json.dumps(claimed_ids)
        return True

    @db_transaction
    def registar_identidade_no_convite(self, code, username, media_user_id):
        """Acrescenta ao convite o ID de quem o resgatou, SEM mexer nas vagas.

        🐛 Onde as contas são LOCAIS, a vaga tem de ser reservada antes de a
        conta existir — e por isso sem ID. O backend do Jellyfin corrigia isso
        a seguir com um `release` seguido de um `reserve`, o que abre uma
        janela em que a vaga fica LIVRE: com o worker gevent, outro resgate
        podia ficar com ela, e o `reserve` seguinte devolvia False — que ninguém
        verificava. A conta ficava criada e o ID nunca entrava no convite, e sem
        ele nem a verificação de resgate duplicado nem a de abuso de período de
        teste voltavam a reconhecer aquela pessoa.

        Acrescentar o ID não é uma operação sobre vagas: não precisa de as
        libertar para lhes tocar.
        """
        invitation = Invitation.query.get(code)
        if not invitation:
            return False

        if username:
            claimed_users = json.loads(invitation.claimed_by_users or '[]')
            if username not in claimed_users:
                claimed_users.append(username)
                invitation.claimed_by_users = json.dumps(claimed_users)

        if media_user_id is not None:
            claimed_ids = json.loads(invitation.claimed_by_ids or '[]')
            if str(media_user_id) not in claimed_ids:
                claimed_ids.append(str(media_user_id))
                invitation.claimed_by_ids = json.dumps(claimed_ids)
        return True

    @db_transaction
    def release_invitation_use(self, code, username, media_user_id=None):
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

        if media_user_id is not None:
            claimed_ids = json.loads(invitation.claimed_by_ids or '[]')
            if str(media_user_id) in claimed_ids:
                claimed_ids.remove(str(media_user_id))
                invitation.claimed_by_ids = json.dumps(claimed_ids)
        return True

    # Quando um convite reativado já tinha expirado, ganha outra vez a MESMA
    # janela que teve à partida. Só quando essa janela não é legível (uma data
    # corrompida, um convite importado sem `created_at`) é preciso escolher um
    # número, e uma semana é o menor prazo que serve para o que a reativação é:
    # dar outra oportunidade a um convite concreto, não abrir um permanente.
    JANELA_DE_REATIVACAO_EM_FALTA = timedelta(days=7)

    @db_transaction
    def reset_invitation_usage(self, code):
        """Zera o contador de usos e, se já tiver expirado, dá-lhe validade nova.

        🐛 A validade não era estendida — era APAGADA. `expires_at = None`
        quer dizer "não expira", por isso um convite promocional de 24 horas
        reativado por engano passava a valer para sempre, e a mensagem dizia
        "validade estendida" a quem tinha acabado de remover a validade. Agora
        recebe outra vez a janela que teve à partida, contada de agora.
        """
        invitation = Invitation.query.get(code)
        if not invitation:
            return False

        invitation.use_count = 0

        if invitation.expires_at:
            agora = datetime.now(timezone.utc)
            try:
                expirava_em = datetime.fromisoformat(invitation.expires_at)
                ja_expirou = expirava_em < agora
            except (ValueError, TypeError):
                # A data não é legível: o convite é tratado como expirado, que é
                # o lado seguro do erro (a mesma decisão de `get_invitation_by_code`).
                expirava_em, ja_expirou = None, True

            if ja_expirou:
                invitation.expires_at = (agora + self._janela_original(invitation, expirava_em)).isoformat()

        logger.info(f"Convite '{mask_code(code)}' reativado manualmente (contagem resetada).")
        return True

    def _janela_original(self, invitation, expirava_em):
        """Quanto tempo o convite valeu da primeira vez."""
        if expirava_em is None or not invitation.created_at:
            return self.JANELA_DE_REATIVACAO_EM_FALTA
        try:
            janela = expirava_em - datetime.fromisoformat(invitation.created_at)
        except (ValueError, TypeError):
            return self.JANELA_DE_REATIVACAO_EM_FALTA
        # Uma janela nula ou negativa vem de datas trocadas e não diz nada.
        return janela if janela > timedelta(0) else self.JANELA_DE_REATIVACAO_EM_FALTA
    
    @db_transaction
    def delete_invitation(self, code):
        """Tira o convite da vista. A linha FICA.

        🛡️ Apagá-la de verdade apagava o "membro desde" de toda a gente que
        entrou por este código: `get_user_claim_date` procura o username dentro
        de `claimed_by_users` e não há outra fonte para essa data. O botão que
        existe para arrumar a lista de convites gastos destruía em silêncio o
        histórico de entrada de cada pessoa que os tinha resgatado.

        O link deixa de funcionar na mesma: todas as leituras filtram
        `deleted_at IS NULL`.
        """
        invitation = Invitation.query.get(code)
        if not invitation or invitation.deleted_at is not None:
            return False
        invitation.deleted_at = datetime.now(timezone.utc)
        return True

    @db_transaction
    def libertar_codigo_apagado(self, code):
        """Apaga DE VERDADE um convite removido, para o código poder ser reusado.

        Só se chama quando não há nada a perder — um convite que ninguém chegou
        a resgatar não guarda o "membro desde" de pessoa nenhuma, e a única
        razão para a linha ficar era essa. Quem decide é `create_invitation`.
        """
        invitation = Invitation.query.get(code)
        if not invitation or invitation.deleted_at is None:
            return False
        db.session.delete(invitation)
        return True

    @db_transaction
    def limpar_convites_antigos(self, dias):
        """Apaga os convites gastos ou expirados há mais de `dias` que NINGUÉM resgatou.

        ⚠️ Um convite que foi resgatado nunca é apagado aqui, tenha a idade que
        tiver: é ele que responde ao "membro desde" de quem entrou por ele. O
        que sai são os outros — um código que expirou sem ninguém lhe tocar é
        lixo, como uma cobrança PIX que ninguém pagou, e guardá-lo para sempre
        só fazia a tabela crescer sem que nada a fosse ler.
        """
        if not isinstance(dias, int) or dias <= 0:
            return 0

        limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        agora = datetime.now(timezone.utc).isoformat()

        apagados = Invitation.query.filter(
            Invitation.created_at < limite,
            # Ninguém entrou por ele. As duas colunas porque os convites
            # anteriores ao `claimed_by_ids` só têm a primeira preenchida.
            Invitation.use_count == 0,
            (Invitation.claimed_by_users.is_(None)) | (Invitation.claimed_by_users.in_(('', '[]'))),
            # E já não serve para nada: foi removido, ou a validade passou.
            (Invitation.deleted_at.isnot(None)) | (Invitation.expires_at < agora),
        ).delete(synchronize_session=False)

        if apagados:
            logger.info(f"{apagados} convites sem uso e com mais de {dias} dias foram apagados.")
        return apagados

    def get_user_claim_date(self, media_user_id):
        """Desde quando é que esta pessoa está aqui — o "membro desde".

        ⚠️ Esta é a leitura que NÃO filtra `deleted_at`, e é a razão de a
        remoção suave existir nesta tabela. O convite pode ter sido removido há
        muito tempo; a data em que a pessoa entrou por ele não muda por isso.
        """
        profile = UserProfile.query.get(media_user_id)
        if not profile: return None
        invitation = Invitation.query.filter(Invitation.claimed_by_users.contains(profile.username)).order_by(Invitation.claimed_at.desc()).first()
        return invitation.claimed_at if invitation else None

    # --- MÉTODOS de Utilizadores Bloqueados ---
    def get_blocked_user(self, media_user_id):
        return self._row_to_dict(BlockedUser.query.get(media_user_id))

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
        return {u.media_user_id: self._row_to_dict(u) for u in BlockedUser.query.all()}

    def add_blocked_user(self, media_user_id, username, reason='manual'):
        """Adiciona ou atualiza um utilizador bloqueado. Protegido contra colisões de threads.

        ⚠️ **O perfil local tem de existir primeiro.** Um bloqueio é ESTADO de
        alguém, e desde que as chaves estrangeiras passaram a ser impostas
        (`e1c7a4f92db6`) não há como gravar um sobre um perfil que não existe.
        Isso acontece de verdade: o administrador pode bloquear, na página de
        utilizadores, alguém que está no servidor de média mas nunca entrou no
        painel — antes disto a conta era suspensa no servidor e o registo do
        bloqueio desaparecia pelo `except IntegrityError` abaixo, devolvendo
        `None` sem uma linha de log. A conta ficava bloqueada e o painel não
        sabia porquê nem desde quando.
        """
        try:
            self._garantir_perfil_para_bloqueio(media_user_id, username)

            user = BlockedUser.query.get(media_user_id)
            if not user:
                user = BlockedUser(media_user_id=media_user_id, username=username)

            user.blocked_at = datetime.now(timezone.utc).isoformat()
            user.block_reason = reason
            db.session.add(user)
            db.session.commit()
            
            logger.info(f"Utilizador '{username}' (ID: {media_user_id}) adicionado/atualizado na lista de bloqueados.")
            return self._row_to_dict(user)
            
        except IntegrityError:
            db.session.rollback()
            user = BlockedUser.query.get(media_user_id)
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

    def _garantir_perfil_para_bloqueio(self, media_user_id, username):
        """Cria o perfil mínimo de quem vai ser bloqueado, se ainda não houver.

        Mínimo é mesmo mínimo: o identificador e o nome. Tudo o resto —
        vencimento, plano, bibliotecas — é escolha de quem administra e não se
        inventa aqui. Um perfil que já exista não é tocado.
        """
        if UserProfile.query.get(media_user_id):
            return

        if not username:
            # Sem nome não há perfil (a coluna é NOT NULL) e o bloqueio local
            # não se consegue gravar. Dizê-lo é melhor do que o IntegrityError.
            raise ValueError(
                f"Não se regista um bloqueio sem nome de utilizador "
                f"(media_user_id={media_user_id})."
            )

        db.session.add(UserProfile(
            media_user_id=media_user_id,
            username=username,
            media_server_type=tipo_de_servidor_configurado(),
        ))
        db.session.flush()
        logger.info(
            f"Perfil local criado para '{username}' (ID: {media_user_id}) por não "
            "existir no momento do bloqueio."
        )

    @db_transaction
    def remove_blocked_user(self, media_user_id):
        user = BlockedUser.query.get(media_user_id)
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
