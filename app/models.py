# app/models.py
from sqlalchemy import text as sa_text

from .dominios import (
    CATEGORIAS_DE_NOTIFICACAO,
    ESTADOS_DE_PAGAMENTO,
    ESTADOS_DE_TAREFA,
    ESTADOS_DO_PERFIL,
    TIPOS_DE_DESCONTO,
    clausula_in,
)
from .extensions import db


def agora_utc():
    """O "agora" na forma que as colunas `DateTime` deste esquema guardam.

    ⚠️ Elas são todas SEM FUSO, e o valor tem de continuar a sê-lo: o SQLite
    guarda-o como texto, e uma linha escrita com fuso ordenaria e compararia
    de forma diferente das que já lá estão. `replace(tzinfo=None)` é o que
    torna esta função um substituto exato do `datetime.utcnow`.

    🐛 E é por isso que ela existe: o `utcnow` está DEPRECIADO desde o Python
    3.12 — que o CI já corre — e o aviso diz "scheduled for removal". Havia
    doze chamadas, onze delas `default=` de colunas. Trocá-las por
    `datetime.now(timezone.utc)` sem o `replace` teria sido pior do que não
    mexer: passaria a escrever com fuso numa coluna que não o tem.

    É o mesmo idioma que o `garantir_payment_token` já usava à mão.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
from .utils.identity import normalize_user_id
from flask_login import UserMixin
from sqlalchemy.orm import validates
import json
import re
import uuid
from datetime import datetime, timezone


class UserId(db.TypeDecorator):
    """A identidade de um utilizador no servidor de média, guardada como texto.

    O Plex identifica as contas por um inteiro e o Jellyfin por um GUID, por
    isso a coluna é texto. O problema é que o ID chega ao painel em formatos
    diferentes conforme a origem — inteiro da API do Plex, string de um URL ou
    da sessão, o que o gateway quiser de um webhook — e no SQLite uma consulta
    feita com o inteiro 123 NÃO encontra a linha guardada como '123'.

    A normalização vive aqui, no tipo da coluna, e não espalhada pelos métodos
    do DataManager: assim aplica-se sozinha a tudo o que é gravado E a tudo o
    que é comparado num WHERE, incluindo em consultas que ainda ninguém
    escreveu. Era o 37.º método a esquecer-se dela que reintroduzia o bug.
    """

    impl = db.String(64)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return normalize_user_id(value)

    def process_result_value(self, value, dialect):
        # Defensivo: uma instalação antiga pode ter linhas que escaparam ao
        # CAST da migração e continuam guardadas como inteiro.
        return normalize_user_id(value)

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
    created_at = db.Column(db.DateTime, default=agora_utc)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint(clausula_in('status', ESTADOS_DE_TAREFA), name='ck_tasks_status'),
        db.CheckConstraint('progress_current >= 0', name='ck_tasks_progress_current'),
        db.CheckConstraint('progress_total >= 0', name='ck_tasks_progress_total'),
    )

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
    created_at = db.Column(db.DateTime, default=agora_utc)
    # 🛡️ Quando esta linha foi tirada da vista, se foi. NULL quer dizer que
    # está viva, que é o caso de quase todas — daí o índice ser PARCIAL.
    # A remoção suave está nestas três tabelas e não em todas de propósito:
    # são as que guardam o que mais custa perder (o histórico financeiro, o
    # registo de quem usou cada cupão, a auditoria de cortes) e as que tinham
    # um botão a apagá-las para sempre, sem confirmação e sem volta.
    deleted_at = db.Column(db.DateTime, nullable=True)
    usages = db.relationship('CouponUsage', backref='coupon', lazy=True, cascade="all, delete-orphan")

    __table_args__ = (
        db.CheckConstraint(clausula_in('discount_type', TIPOS_DE_DESCONTO),
                           name='ck_coupons_discount_type'),
        # Um cupão de -50% multiplicava o preço por 1,5. O schema Pydantic da
        # rota de criação já o recusava; a coluna não, e a rota não é o único
        # caminho até aqui (restauros, migrações, código futuro).
        db.CheckConstraint('value >= 0', name='ck_coupons_value'),
        db.CheckConstraint('max_uses >= 0', name='ck_coupons_max_uses'),
        db.CheckConstraint('use_count >= 0', name='ck_coupons_use_count'),
        # ⚠️ O índice de `code` não servia para nada nas consultas que existem:
        # elas comparam `func.upper(Coupon.code)` com o código escrito, e o
        # SQLite não usa o índice de uma coluna quando a comparação é sobre uma
        # EXPRESSÃO dessa coluna. Validar um cupão era uma varredura da tabela.
        db.Index('ix_coupons_code_upper', db.func.upper(code)),
        db.Index('ix_coupons_deleted_at', deleted_at,
                 sqlite_where=db.text('deleted_at IS NOT NULL')),
    )

class CouponUsage(db.Model):
    __tablename__ = 'coupon_usages'
    id = db.Column(db.Integer, primary_key=True)
    media_user_id = db.Column(
        UserId(),
        db.ForeignKey('user_profiles.media_user_id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True,
    )
    coupon_id = db.Column(db.Integer, db.ForeignKey('coupons.id'), nullable=False)
    used_at = db.Column(db.DateTime, default=agora_utc)
    __table_args__ = (db.UniqueConstraint('media_user_id', 'coupon_id', name='_user_coupon_uc'),)

class Invitation(db.Model):
    __tablename__ = 'invitations'
    code = db.Column(db.String, primary_key=True)
    libraries = db.Column(db.String, nullable=False)
    screen_limit = db.Column(db.Integer, nullable=False)
    allow_downloads = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.String, nullable=False)
    expires_at = db.Column(db.String)
    claimed_by_users = db.Column(db.Text)
    # IDs do Plex de quem resgatou, em paralelo com 'claimed_by_users'.
    # O username do Plex PODE MUDAR (o painel até tem sincronização para isso),
    # por isso não serve como identidade estável para a verificação anti-abuso
    # de períodos de teste. O ID nunca muda.
    claimed_by_ids = db.Column(db.Text)
    claimed_at = db.Column(db.String)
    trial_duration_minutes = db.Column(db.Integer, nullable=False, default=0)
    overseerr_access = db.Column(db.Boolean, default=False)
    max_uses = db.Column(db.Integer, nullable=False, default=1)
    use_count = db.Column(db.Integer, nullable=False, default=0)
    # O contacto pré-atribuído ao convite. São duas colunas e não uma porque
    # são dois canais independentes — a mesma pessoa pode ser convidada pelo
    # Discord e ter o Telegram registado depois. ⚠️ Os nomes DIVERGEM dos do
    # perfil (`telegram_user`, `discord_user_id`) por razões históricas; quem
    # faz a ponte é o mapa `CONTACTOS` em `media_server/invitations.py`.
    telegram_id = db.Column(db.String, nullable=True)
    discord_id = db.Column(db.String, nullable=True)
    # Para quem é este convite. O painel só sabia responder a isso quando havia
    # Telegram; os outros ficavam a ser um código aleatório e mais nada, e um
    # convite gasto só dizia o nome de quem o usou — não o de quem o devia ter
    # usado.
    note = db.Column(db.String(200), nullable=True)
    # 🛡️ Apagar um convite apagava o "membro desde" de quem entrou por ele:
    # `get_user_claim_date` procura o username dentro de `claimed_by_users` e
    # não há outra fonte para essa data. A mesma decisão de `pix_payments`,
    # `coupons` e `stream_termination_logs` — o que custa perder sai das
    # leituras e fica na tabela.
    deleted_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        # PARCIAL: a esmagadora maioria das linhas tem isto a NULL, e um índice
        # completo sobre uma coluna assim indexa sobretudo nada.
        db.Index('ix_invitations_deleted_at', deleted_at,
                 sqlite_where=db.text('deleted_at IS NOT NULL')),
    )

class BlockedUser(db.Model):
    __tablename__ = 'blocked_users'
    media_user_id = db.Column(
        UserId(),
        db.ForeignKey('user_profiles.media_user_id', ondelete='CASCADE', onupdate='CASCADE'),
        primary_key=True,
    )
    username = db.Column(db.String, nullable=False)
    blocked_at = db.Column(db.String)
    block_reason = db.Column(db.String(50), nullable=True)

class UserProfile(db.Model):
    __tablename__ = 'user_profiles'
    # A identidade vem do servidor de média e é TEXTO: o Plex usa um inteiro,
    # o Jellyfin um GUID. Ver app/utils/identity.py — no SQLite uma consulta
    # feita com um inteiro NÃO encontra a linha guardada como texto, e é por
    # isso que o DataManager normaliza tudo o que recebe.
    media_user_id = db.Column(UserId(), primary_key=True)
    # Que servidor de média criou este perfil. Um painel que troque de servidor
    # não pode confundir o histórico de um ID Plex com o de um GUID do Jellyfin
    # que por acaso coincida.
    media_server_type = db.Column(db.String(20), nullable=True)
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
    # 🐛 Andava a ser escrito e descartado em silêncio: o convite e as rotas de
    # administração mandavam `allow_downloads` para `set_user_profile` e não
    # havia coluna. O sintoma era a permissão de download NÃO sobreviver a uma
    # reativação — `restaurar_acesso` lê-a daqui, e lia sempre False. Fica ao
    # lado de `libraries` porque é a mesma decisão: o que esta pessoa pode ver,
    # e se pode levar consigo.
    allow_downloads = db.Column(db.Boolean, default=False, nullable=False,
                                server_default=sa_text('0'))
    payment_token = db.Column(db.String, unique=True, nullable=True)
    # 🛡️ Quando é que este link de pagamento deixa de servir. Até aqui o
    # `payment_token` era uma credencial portadora ETERNA: o primeiro link
    # enviado a alguém continuava a abrir a página anos depois, e um dump da
    # base de dados entregava o de toda a gente pronto a usar. NULL continua a
    # querer dizer "sem validade" — é o que os perfis anteriores a esta coluna
    # têm, para não se cortar de repente o acesso a quem quer pagar.
    payment_token_expires_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='active', nullable=False, index=True)
    pending_invite_link = db.Column(db.String, nullable=True)
    last_reactivation_time = db.Column(db.Float, nullable=True)
    xp = db.Column(db.Integer, default=0, nullable=False)
    xp_last_sync_at = db.Column(db.Float, nullable=True)
    lifetime_xp = db.Column(db.Integer, default=0, nullable=False)
    # --- Sistema de Referência ("Indique e Ganhe") ---
    # 'referral_code' é o código público que o utilizador partilha.
    # 'referred_by' guarda o media_user_id de quem o indicou (a coluna já existia na
    # base de dados desde a migração 'c92625823728', mas nunca chegou a ser mapeada
    # aqui nem usada por qualquer código — agora passa a ser utilizada de facto).
    # 'referral_rewarded' evita pagar a recompensa mais do que uma vez pelo mesmo
    # indicado, mesmo que ele renove várias vezes.
    referral_code = db.Column(db.String(16), unique=True, nullable=True, index=True)
    referred_by = db.Column(UserId(), nullable=True, index=True)
    referral_rewarded = db.Column(db.Boolean, default=False, nullable=False)
    referral_credit = db.Column(db.Float, default=0.0, nullable=False)
    coupon_usages = db.relationship('CouponUsage', backref='user', lazy=True, cascade="all, delete-orphan")
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade="all, delete-orphan")
    unlocked_achievements = db.relationship('UnlockedAchievement', backref='user', lazy=True, cascade="all, delete-orphan")

    @validates('phone_number')
    def _normalizar_telefone(self, _chave, valor):
        """Guarda o telefone só com dígitos.

        🐛 **Não é arrumação: é um bug.** O destinatário do WhatsApp é montado
        como `{phone_number}@s.whatsapp.net`, por isso um número escrito da
        forma natural — `(11) 99999-9999` — produzia um identificador inválido
        e a mensagem não chegava a ninguém. Não havia erro: o painel dizia que
        tinha enviado.

        A normalização vive aqui, e não na rota, para valer em todos os
        caminhos: a edição do administrador, o resgate do convite, a "Minha
        Conta" e o que vier a seguir.
        """
        if valor is None:
            return None
        so_digitos = re.sub(r'\D', '', str(valor))
        return so_digitos or None

    @validates('email')
    def _normalizar_email(self, _chave, valor):
        """Espaços fora, minúsculas dentro.

        ⚠️ **Normaliza, não recusa** — e a diferença é deliberada. Grande parte
        dos emails que aqui entram vêm do SERVIDOR de média (o Plex e o
        Jellyfin são a fonte da verdade do que a conta tem), e recusar um que
        não nos pareça bem partia a sincronização de perfis por causa de um
        campo que o painel nem usa para autenticar. Quem ESCREVE um email à
        mão passa pelos schemas Pydantic das rotas, e esses recusam.

        A normalização importa na mesma: sem ela, 'Ana@X.com' e 'ana@x.com'
        eram duas pessoas diferentes para quem procura no Seerr.
        """
        if valor is None:
            return None
        limpo = str(valor).strip().lower()
        return limpo or None

    __table_args__ = (
        db.CheckConstraint(clausula_in('status', ESTADOS_DO_PERFIL),
                           name='ck_user_profiles_status'),
        # O dia do mês da renovação. Um 0 ou um 32 não davam erro nenhum: o
        # vencimento é depois calculado a partir daqui.
        db.CheckConstraint('billing_day IS NULL OR (billing_day >= 1 AND billing_day <= 31)',
                           name='ck_user_profiles_billing_day'),
        # 0 quer dizer ILIMITADO; negativo não quer dizer nada.
        db.CheckConstraint('screen_limit >= 0', name='ck_user_profiles_screen_limit'),
        db.CheckConstraint('xp >= 0', name='ck_user_profiles_xp'),
        db.CheckConstraint('lifetime_xp >= 0', name='ck_user_profiles_lifetime_xp'),
        # Um crédito de indicações negativo era uma dívida que o painel não sabe
        # cobrar — e descontava-se sozinho do preço seguinte ao contrário.
        db.CheckConstraint('referral_credit >= 0', name='ck_user_profiles_referral_credit'),
        # ⚠️ Mesmo problema do `coupons.code`: o índice de `username` existe,
        # mas as procuras por nome são todas `func.lower(username) == ...`
        # (o nome do servidor de média não distingue maiúsculas) e uma
        # expressão não usa o índice da coluna. Entrar no painel e sincronizar
        # perfis varriam a tabela inteira, uma vez por utilizador.
        db.Index('ix_user_profiles_username_lower', db.func.lower(username)),
        # As varreduras diárias: `get_all_user_expirations` e
        # `get_all_trial_users` percorriam todos os perfis para encontrar os
        # poucos que têm data. Crescem com o número de utilizadores, e correm
        # todas as madrugadas.
        #
        # ⚠️ São índices PARCIAIS, com a mesma condição das consultas. Não é
        # otimização prematura: um índice completo sobre uma coluna em que a
        # maioria das linhas é NULL indexa sobretudo nada, e o SQLite,
        # estimando que teria de ler quase a tabela toda, preferia varrê-la —
        # ficava um índice a ocupar espaço, a ser mantido a cada escrita, e a
        # nunca ser usado. Com a condição igual à da consulta, ele entra.
        db.Index('ix_user_profiles_expiration_date', expiration_date,
                 sqlite_where=db.text("expiration_date IS NOT NULL AND expiration_date != ''")),
        db.Index('ix_user_profiles_trial_end_date', trial_end_date,
                 sqlite_where=db.text("trial_end_date IS NOT NULL AND trial_end_date != ''")),
    )

class PixPayment(db.Model):
    __tablename__ = 'pix_payments'
    txid = db.Column(db.String, primary_key=True)
    # 📌 HISTÓRICO, não uma referência viva — a mesma decisão que `coupon_code`
    # mais abaixo. Um pagamento recebido ACONTECEU: apagá-lo porque a conta foi
    # removida do painel falsifica o relatório financeiro do mês em que entrou.
    # Por isso não há aqui chave estrangeira, e é por isso que `username` fica
    # gravado nesta linha em vez de se ir buscar ao perfil.
    media_user_id = db.Column(UserId(), nullable=False, index=True)
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
    # 🛡️ Quando esta linha foi tirada da vista, se foi. NULL quer dizer que
    # está viva, que é o caso de quase todas — daí o índice ser PARCIAL.
    # A remoção suave está nestas três tabelas e não em todas de propósito:
    # são as que guardam o que mais custa perder (o histórico financeiro, o
    # registo de quem usou cada cupão, a auditoria de cortes) e as que tinham
    # um botão a apagá-las para sempre, sem confirmação e sem volta.
    deleted_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint(clausula_in('status', ESTADOS_DE_PAGAMENTO),
                           name='ck_pix_payments_status'),
        # Um pagamento de valor negativo entrava no somatório do relatório
        # financeiro e SUBTRAÍA da receita do mês.
        db.CheckConstraint('value >= 0', name='ck_pix_payments_value'),
        db.CheckConstraint('referral_credit_used >= 0',
                           name='ck_pix_payments_referral_credit_used'),
        db.CheckConstraint('screens IS NULL OR screens >= 0', name='ck_pix_payments_screens'),
        # O resumo financeiro do mês e a limpeza de cobranças abandonadas
        # filtram sempre pelos dois ao mesmo tempo ('as CONCLUIDAS deste
        # intervalo', 'as não-concluídas mais velhas do que N dias'). Sem
        # índice, as duas varriam a tabela de pagamentos inteira — que é a
        # única que só cresce.
        db.Index('ix_pix_payments_status_created_at', status, created_at),
        db.Index('ix_pix_payments_deleted_at', deleted_at,
                 sqlite_where=db.text('deleted_at IS NOT NULL')),
    )

class PushSubscription(db.Model):
    """Um APARELHO que aceitou receber notificações do painel.

    Não é uma pessoa: cada navegador, cada celular e cada instalação do painel
    na tela de início tem a sua própria subscrição, com chaves próprias. A mesma
    pessoa tem tantas linhas quantos os aparelhos onde ligou as notificações.

    ⚠️ **`media_user_id` a NULL quer dizer ADMINISTRADOR**, exatamente como em
    `notifications` — é a mesma convenção, de propósito: quem recebe o aviso no
    sino do painel é quem o deve receber no celular. E resolve o caso do dono,
    que pode ainda não ter perfil local (só passa a tê-lo no primeiro login
    depois da versão que o cria), e a quem uma chave estrangeira obrigatória
    impediria de subscrever.
    """

    __tablename__ = 'push_subscriptions'
    id = db.Column(db.Integer, primary_key=True)
    media_user_id = db.Column(
        UserId(),
        db.ForeignKey('user_profiles.media_user_id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=True, index=True,
    )
    # O endereço que o serviço de push (Google, Mozilla, Apple) deu ao aparelho.
    # É único: um mesmo navegador que volte a subscrever devolve o MESMO
    # endereço, e sem isto ficavam linhas duplicadas a entregar a mesma
    # notificação duas e três vezes ao mesmo aparelho.
    endpoint = db.Column(db.String(512), nullable=False, unique=True)
    # As chaves da subscrição, em base64 de URL, tal como o navegador as deu.
    p256dh = db.Column(db.String(255), nullable=False)
    auth = db.Column(db.String(64), nullable=False)
    # Para a pessoa reconhecer o aparelho na lista ("Chrome no Android").
    device_label = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=agora_utc, nullable=False)
    # Quando foi a última entrega aceite. Serve para a lista de aparelhos dizer
    # o que ainda está vivo — uma subscrição morta só se descobre ao tentar.
    last_success_at = db.Column(db.DateTime, nullable=True)


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    media_user_id = db.Column(
        UserId(),
        db.ForeignKey('user_profiles.media_user_id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=True, index=True,
    )
    message = db.Column(db.String, nullable=False)
    category = db.Column(db.String(20), nullable=False, default='info')
    timestamp = db.Column(db.DateTime, default=agora_utc)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    link = db.Column(db.String, nullable=True)

    __table_args__ = (
        # A categoria escolhe a cor e o ícone na interface. Uma categoria
        # desconhecida não dava erro: dava uma notificação sem estilo nenhum.
        db.CheckConstraint(clausula_in('category', CATEGORIAS_DE_NOTIFICACAO),
                           name='ck_notifications_category'),
    )

class ShortLink(db.Model):
    __tablename__ = 'short_links'
    id = db.Column(db.Integer, primary_key=True)
    short_code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    original_url = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, default=agora_utc)

class PasswordReset(db.Model):
    """Um pedido de reposição de palavra-passe, à espera de ser usado.

    Só existe onde as contas são LOCAIS (o painel cria-as e é responsável pelas
    credenciais). Num painel Plex a palavra-passe vive no plex.tv e o painel não
    tem nada que a repor.

    🛡️ **Guarda-se o RESUMO do token, não o token.** Quem lesse a base de dados
    — ou um ZIP de backup, que é só um ficheiro — ficava com uma porta aberta
    para cada pedido ainda válido. Com o resumo, o que está guardado não serve
    para nada: só quem recebeu o link na notificação o consegue usar.

    A linha fica depois de usada (com `used_at` preenchido) de propósito: um
    segundo clique no mesmo link tem de dizer "já foi usado", que é diferente de
    "não existe".
    """

    __tablename__ = 'password_resets'

    token_hash = db.Column(db.String(64), primary_key=True)
    media_user_id = db.Column(
        UserId(),
        db.ForeignKey('user_profiles.media_user_id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=agora_utc)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)


class ApiKey(db.Model):
    """Uma chave de integração, com nome e com escopo.

    ⚠️ **Havia UMA chave para tudo** (`INTERNAL_TRIGGER_KEY`, no config.json),
    partilhada pelo endpoint de convites para bots e pelo webhook do Seerr.
    Isso tem duas consequências que só se notam no pior dia: regenerá-la porque
    um bot foi comprometido derrubava também o Seerr, e a chave do bot de
    Telegram do vizinho podia aceitar webhooks em nome do painel.

    🛡️ **Guarda-se o RESUMO, não a chave** — a mesma decisão de
    `PasswordReset`. O que fica na base de dados (e dentro do ZIP de backup,
    que é só um ficheiro) não serve para nada: só quem copiou a chave no
    momento em que ela foi criada a tem. Por isso ela é mostrada UMA vez e o
    painel não sabe recuperá-la.

    O `prefixo` é o que a interface mostra para se distinguirem umas das
    outras, e é também por ele que a verificação encontra a linha sem ter de
    percorrer a tabela a comparar resumos.

    Uma chave revogada FICA, com `revoked_at` preenchido: a auditoria fala dela
    pelo id, e "esta chave foi revogada em março" é diferente de "esta chave
    nunca existiu".
    """

    __tablename__ = 'api_keys'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), nullable=False)
    prefixo = db.Column(db.String(16), nullable=False, unique=True, index=True)
    resumo = db.Column(db.String(64), nullable=False)
    escopos = db.Column(db.Text, nullable=False, default='[]')
    created_at = db.Column(db.DateTime, nullable=False, default=agora_utc)
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        # PARCIAL, como os outros `deleted_at`: quase todas as chaves estão
        # vivas, e um índice completo sobre uma coluna maioritariamente NULL
        # indexa sobretudo nada.
        db.Index('ix_api_keys_revoked_at', revoked_at,
                 sqlite_where=db.text('revoked_at IS NOT NULL')),
    )


class UnlockedAchievement(db.Model):
    __tablename__ = 'unlocked_achievements'
    id = db.Column(db.Integer, primary_key=True)
    media_user_id = db.Column(
        UserId(),
        db.ForeignKey('user_profiles.media_user_id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True,
    )
    username = db.Column(db.String, nullable=False)
    achievement_id = db.Column(db.String, nullable=False)
    unlocked_at = db.Column(db.DateTime, default=agora_utc)
    __table_args__ = (db.UniqueConstraint('media_user_id', 'achievement_id', name='_user_achievement_uc'),)

class AuditLog(db.Model):
    """Quem mudou o quê, quando, e a partir de onde.

    🛡️ **Não havia nada disto.** O único rasto de uma ação administrativa era
    uma linha de texto no `app.log` — e esse ficheiro roda (perde-se sozinho ao
    fim de alguns megabytes) e tem um botão nas Configurações que o trunca. Uma
    trilha de auditoria que a própria pessoa auditada pode apagar com um clique
    não é uma trilha de auditoria.

    Pior: as mudanças que mais interessam nem lá chegavam. Gravar as
    Configurações reescreve preços, URLs e credenciais dos gateways e não
    escrevia UMA linha sobre o que tinha mudado; os pagamentos manuais ficavam
    registados como "pelo Admin", sem dizer qual nem a partir de onde.

    O que fica guardado, e porquê:

    - `acao` é um verbo curto e estável (`definicoes.gravar`,
      `pagamento.apagar`), pensado para se poder filtrar meses depois;
    - `alvo_tipo`/`alvo_id` dizem SOBRE QUEM ou sobre o quê foi;
    - `detalhes` é JSON com o antes e o depois — é o que transforma "alguém
      mexeu nos preços" em "o plano de 2 telas passou de 25 para 35";
    - `endereco_ip` responde à pergunta que o nome de utilizador não responde
      num painel de administrador único: foi de onde?

    🛡️ **Nenhum valor sensível entra aqui.** O que passa por
    `app/services/audit.py` é filtrado: de uma credencial regista-se que MUDOU,
    nunca o antes nem o depois. Uma auditoria que guarda segredos é mais uma
    cópia dos segredos.
    """

    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=agora_utc, nullable=False)
    # Quem. Fica o nome E o identificador: o nome é o que se lê, o
    # identificador é o que continua a servir se a pessoa mudar de nome no
    # servidor de média. Ambos anuláveis — há ações que nascem de um webhook ou
    # de uma tarefa de fundo, e inventar um autor seria pior do que não ter.
    ator = db.Column(db.String(255), nullable=True)
    ator_id = db.Column(UserId(), nullable=True, index=True)
    acao = db.Column(db.String(64), nullable=False, index=True)
    alvo_tipo = db.Column(db.String(32), nullable=True)
    alvo_id = db.Column(db.String(255), nullable=True)
    detalhes = db.Column(db.Text, nullable=True)
    endereco_ip = db.Column(db.String(45), nullable=True)

    __table_args__ = (
        # A leitura normal é "as últimas N", e a filtrada é "as últimas N desta
        # ação". As duas ordenam por data decrescente.
        db.Index('ix_audit_logs_timestamp', timestamp.desc()),
        db.Index('ix_audit_logs_acao_timestamp', acao, timestamp.desc()),
    )


class StreamTerminationLog(db.Model):
    __tablename__ = 'stream_termination_logs'
    id = db.Column(db.Integer, primary_key=True)
    # 📌 HISTÓRICO, como `pix_payments`: a auditoria de cortes tem de continuar
    # a responder "quem foi cortado, quando e porquê" mesmo depois de o perfil
    # deixar de existir. Uma auditoria que se apaga sozinha não é uma auditoria.
    media_user_id = db.Column(UserId(), nullable=False, index=True)
    username = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime, default=agora_utc)
    media_title = db.Column(db.String, nullable=False)
    platform = db.Column(db.String, nullable=True)
    reason = db.Column(db.String, nullable=False)
    # 🛡️ Quando esta linha foi tirada da vista, se foi. NULL quer dizer que
    # está viva, que é o caso de quase todas — daí o índice ser PARCIAL.
    # A remoção suave está nestas três tabelas e não em todas de propósito:
    # são as que guardam o que mais custa perder (o histórico financeiro, o
    # registo de quem usou cada cupão, a auditoria de cortes) e as que tinham
    # um botão a apagá-las para sempre, sem confirmação e sem volta.
    deleted_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        # ⚠️ O mais caro dos que faltavam. `get_last_termination_timestamp` é a
        # marca de água da importação de cortes do servidor: filtra por `reason`
        # e ordena por `timestamp` decrescente para ler UMA linha — e corre de
        # cinco em cinco minutos, para sempre, sobre uma tabela que nunca
        # encolhe. Sem índice era uma varredura mais uma ordenação, 288 vezes
        # por dia.
        db.Index('ix_stream_termination_logs_reason_timestamp', reason, timestamp.desc()),
        db.Index('ix_stream_termination_logs_deleted_at', deleted_at,
                 sqlite_where=db.text('deleted_at IS NOT NULL')),
    )
