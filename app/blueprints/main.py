# app/blueprints/main.py

import logging
from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, current_app, request, session
from flask_login import login_required, current_user
from flask_babel import get_locale, gettext as _

from ..models import UserProfile
from .auth import admin_required  # Otimizado: Importação direta do módulo irmão auth.py
from ..config import is_configured, load_or_create_config

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

@main_bp.route('/invite/<string:code>')
def claim_invite_page(code):
    """
    Página pública para um novo utilizador resgatar um código de convite.
    """
    return render_template('invite.html', invite_code=code)

@main_bp.route('/r/<string:ref_code>')
def referral_landing(ref_code):
    """
    Página de entrada de um link de indicação ("Indique e Ganhe").

    Guarda o código na sessão e encaminha o visitante para o login. Depois de ele
    se autenticar com o Plex, o código é consumido e a indicação fica registada
    (ver 'auth._login_user_session'). Usamos a sessão em vez de um parâmetro na
    URL porque o fluxo de login com o Plex passa por redirecionamentos externos,
    que fariam perder qualquer query string.
    """
    config = load_or_create_config()
    if not config.get("REFERRAL_ENABLED", False):
        return redirect(url_for('main.index'))

    session['pending_referral_code'] = str(ref_code).strip().upper()
    logger.info(f"Visitante chegou por um link de indicação (código: {ref_code}).")
    return redirect(url_for('auth.login'))

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
        logger.warning(f"Tentativa de acesso com token de pagamento inválido ou expirado: {token}")
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
