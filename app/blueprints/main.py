# app/blueprints/main.py

import logging
from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, current_app, request, session
from flask_login import login_required, current_user
from flask_babel import get_locale, gettext as _

from ..models import UserProfile
from .auth import admin_required  # Otimizado: Importação direta do módulo irmão auth.py
from ..config import is_configured, load_or_create_config
from .. import extensions
from ..utils.log_sanitizer import mask_token

main_bp = Blueprint('main', __name__)
logger = logging.getLogger(__name__)

@main_bp.route('/')
@login_required
def index():
    """
    Página inicial (Dashboard).
    Redireciona para a página da conta se for um utilizador comum, 
    ou mostra o painel de administração se for um Admin.
    """
    if not current_user.is_admin():
        return redirect(url_for('main.account_page'))
        
    return render_template('index.html')

@main_bp.route('/users')
@login_required
@admin_required
def users_page():
    """Página de gestão de utilizadores (Exclusivo Admin)."""
    return render_template('users.html')

def get_pending_referrer_name():
    """
    Devolve o nome de quem indicou, se houver um código de indicação pendente na
    sessão. Serve para dar continuidade visual ao fluxo "Indique e Ganhe": depois
    da landing, o utilizador continua a ver de onde veio no convite e no login.

    IMPORTANTE: apenas LÊ a sessão (não faz 'pop'). O código só deve ser consumido
    no momento em que a indicação é efetivamente registada, durante o resgate do
    convite. Se o consumíssemos aqui, uma simples passagem pela página de login
    faria a indicação perder-se.

    Falha sempre em silêncio: um erro aqui é puramente cosmético e nunca pode
    impedir alguém de entrar ou de resgatar um convite.
    """
    try:
        code = session.get('pending_referral_code')
        if not code:
            return None
        if not load_or_create_config().get("REFERRAL_ENABLED", False):
            return None
        referrer = extensions.data_manager.get_user_profile_by_referral_code(code)
        return referrer.get('username') if referrer else None
    except Exception as e:
        logger.debug(f"Não foi possível obter o nome de quem indicou: {e}")
        return None


@main_bp.route('/invite/<string:code>')
def claim_invite_page(code):
    """
    Página pública para um novo utilizador resgatar um código de convite.
    """
    return render_template('invite.html', invite_code=code, referrer_name=get_pending_referrer_name())

@main_bp.route('/r/<string:ref_code>')
def referral_landing(ref_code):
    """
    Página de entrada de um link de indicação ("Indique e Ganhe").

    🐛 CORREÇÃO DE FLUXO: antes esta rota encaminhava diretamente para o login — o
    que não funcionava para o público-alvo. Quem chega por um link de indicação é,
    normalmente, alguém que AINDA NÃO tem acesso ao servidor Plex, e o login só
    aceita quem já é amigo do servidor ("Acesso negado..."). O caminho correto para
    entrar de novo é resgatar um convite.

    Agora mostramos um ecrã intermédio com os dois caminhos possíveis:
      • Sou novo  -> resgatar o convite padrão configurado pelo administrador;
      • Já tenho acesso -> login normal (a indicação não se aplica, mas o link
        deixa de parecer avariado para quem já é utilizador).
    """
    config = load_or_create_config()
    if not config.get("REFERRAL_ENABLED", False):
        return redirect(url_for('main.index'))

    ref_code = str(ref_code).strip().upper()

    # Valida o código antes de mostrar o que quer que seja: um código inválido não
    # deve levar ninguém a criar expectativas de recompensa.
    referrer = extensions.data_manager.get_user_profile_by_referral_code(ref_code)
    if not referrer:
        logger.info(f"Link de indicação com código inválido: {ref_code}")
        return render_template(
            'referral_landing.html',
            error=_("Este link de indicação não é válido ou expirou."),
            referrer_name=None, invite_code=None, ref_code=ref_code
        ), 404

    # Guarda na sessão: o fluxo de login/convite do Plex passa por redirecionamentos
    # externos, que fariam perder qualquer parâmetro na URL.
    session['pending_referral_code'] = ref_code
    logger.info(f"Visitante chegou pelo link de indicação de '{referrer.get('username')}'.")

    # Convite padrão definido pelo administrador. Sem ele, não há como dar acesso a
    # alguém novo — mostramos uma mensagem clara em vez de um erro confuso.
    invite_code = str(config.get("REFERRAL_DEFAULT_INVITE_CODE", "") or "").strip()
    invite_available = False
    if invite_code:
        invitation, invite_msg = extensions.plex_manager.invites.get_invitation_by_code(invite_code)
        invite_available = invitation is not None
        if not invite_available:
            logger.warning(
                f"O convite padrão de indicações ('{invite_code}') não é válido: {invite_msg}. "
                f"Verifique as Configurações -> Gamificação -> Indique e Ganhe."
            )

    return render_template(
        'referral_landing.html',
        error=None,
        referrer_name=referrer.get('username'),
        invite_code=invite_code if invite_available else None,
        ref_code=ref_code
    )

@main_bp.route('/statistics')
@login_required
def statistics_page():
    """Página de estatísticas de consumo do utilizador e globais."""
    return render_template('statistics.html')

@main_bp.route('/wrapped')
@login_required
def wrapped_page():
    """Página de retrospectiva anual estilo 'Plex Wrapped'."""
    return render_template('wrapped.html', now_year=datetime.now(timezone.utc).year)

@main_bp.route('/financial')
@login_required
@admin_required
def financial_page():
    """Página de dashboard financeiro e pagamentos (Exclusivo Admin)."""
    return render_template('financial.html')

@main_bp.route('/setup')
def setup():
    """
    Página de configuração inicial da aplicação.
    Protege contra reconfiguração acidental bloqueando o acesso após configurado.
    """
    if is_configured():
        # PROTEÇÃO CRÍTICA: O parâmetro 'force' só funciona se for um Admin autenticado.
        # Evita que qualquer utilizador externo tente reconfigurar a aplicação acedendo a /setup?force=true
        if request.args.get('force') == 'true' and current_user.is_authenticated and current_user.is_admin():
            pass
        else:
            return redirect(url_for('auth.login'))
    
    return render_template('setup.html', config=current_app.config, get_locale=get_locale)

@main_bp.route('/settings')
@login_required
@admin_required
def settings_page():
    """Página de configurações do sistema (Exclusivo Admin)."""
    return render_template('settings.html')

@main_bp.route('/account')
@login_required
def account_page():
    """Página de gestão da conta, onde o utilizador logado vê o seu status."""
    return render_template('account.html')

@main_bp.route('/pay/<string:token>')
def payment_page(token):
    """
    Página de pagamento PÚBLICA (não exige login).
    Acede através do token único e seguro enviado por notificação (Telegram/Discord/Email).
    """
    config = load_or_create_config()
    profile = UserProfile.query.filter_by(payment_token=token).first()

    if not profile:
        logger.warning(f"Tentativa de acesso com token de pagamento inválido ou expirado: {mask_token(token)}")
        return render_template('payment_unavailable.html', 
                               reason_title=_("Link de Pagamento Inválido"),
                               reason_message=_("O link que tentou aceder não é válido ou já expirou. Por favor, solicite um novo link ao administrador.")), 404

    username = profile.username
    is_reactivation = (profile.status == 'inactive')
    
    logger.info(f"Página de pagamento pública acedida para o perfil '{username}' (Status: {profile.status}).")

    if not is_reactivation and profile.expiration_date:
        try:
            # Lida de forma segura com datas Naive (Sem TZ) vs Aware (Com TZ) guardadas na base de dados
            exp_dt = datetime.fromisoformat(profile.expiration_date)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                
            exp_date = exp_dt.astimezone(timezone.utc).date()
            today = datetime.now(timezone.utc).date()
            
            days_left = (exp_date - today).days
            renewal_window = int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7))
            grace_period = int(config.get("PAYMENT_LINK_GRACE_PERIOD_DAYS", 7))

            # 1. Proteção: Bloqueia a renovação se ainda faltar muito tempo para expirar
            if days_left > renewal_window:
                message = _("A sua assinatura vence em %(days)d dias. A renovação só estará disponível quando faltarem %(window)d dias (ou menos) para o vencimento.", days=days_left, window=renewal_window)
                return render_template('payment_unavailable.html',
                                       reason_title=_("Renovação Indisponível no Momento"),
                                       reason_message=message)

            # 2. Proteção: Bloqueia o link se já passou demasiado tempo desde a expiração (Período de Carência)
            days_expired = -days_left
            if days_expired > grace_period:
                flash(_("A sua assinatura expirou há muito tempo e este link foi desativado. Por favor, faça login para ver as opções atuais na sua conta."), "warning")
                
                # Se for o próprio utilizador logado a aceder, encaminha para a conta dele
                if current_user.is_authenticated and current_user.username == username:
                    return redirect(url_for('main.account_page'))
                # Se for um acesso exterior anónimo, manda para o login primeiro
                else:
                    return redirect(url_for('auth.login', next=url_for('main.account_page')))
                    
        except (ValueError, TypeError) as e:
             logger.error(f"Erro ao processar cálculos de datas de expiração no portal de pagamentos para '{username}': {e}")

    return render_template('payment_public.html', token=token, username=username, is_reactivation=is_reactivation, current_year=datetime.utcnow().year)
