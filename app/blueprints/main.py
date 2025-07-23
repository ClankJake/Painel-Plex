# app/blueprints/main.py

from flask import Blueprint, render_template, redirect, url_for, flash, current_app, request
from flask_login import login_required, current_user
from flask_babel import get_locale, _
from datetime import date, datetime
import logging

from ..models import User, UserProfile
from ..decorators import admin_required
from ..config import is_configured, load_or_create_config
# Importa as instâncias dos gestores a partir das extensões
from ..extensions import plex_manager, tautulli_manager, data_manager

main_bp = Blueprint('main', __name__)
logger = logging.getLogger(__name__)

@main_bp.route('/')
@login_required
def index():
    """
    Página inicial (Dashboard).
    Redireciona para o dashboard de admin ou para a página de estatísticas do utilizador.
    """
    if not current_user.is_admin():
        return redirect(url_for('main.statistics_page'))
        
    return render_template('index.html')

@main_bp.route('/users')
@login_required
@admin_required
def users_page():
    """
    Página de gestão de utilizadores.
    """
    return render_template('users.html')


@main_bp.route('/invite/<code>')
def claim_invite_page(code):
    """
    Página para um novo utilizador usar um código de convite.
    Esta rota é o alvo do link de convite gerado pela API e resolve o BuildError.
    """
    return render_template('invite.html', invite_code=code)


@main_bp.route('/statistics')
@login_required
def statistics_page():
    """
    Página de estatísticas.
    """
    return render_template('statistics.html')

@main_bp.route('/financial')
@login_required
@admin_required
def financial_page():
    """Página de dashboard financeiro."""
    return render_template('financial.html')


@main_bp.route('/setup')
def setup():
    """
    Página de configuração inicial da aplicação.
    """
    if is_configured() and not request.args.get('force'):
        return redirect(url_for('auth.login'))
    
    return render_template('setup.html', config=current_app.config, get_locale=get_locale)

@main_bp.route('/settings')
@login_required
@admin_required
def settings_page():
    """
    Página de configurações da aplicação.
    """
    return render_template('settings.html')


@main_bp.route('/account')
@login_required
def account_page():
    """
    Página de gestão da conta do utilizador logado.
    """
    return render_template('account.html')

@main_bp.route('/pay/<string:token>')
def payment_page(token):
    """
    Página de pagamento pública para um utilizador específico, utilizando um token seguro.
    """
    logger.info(f"Acesso à página de pagamento com o token: {token}")
    config = load_or_create_config()
    
    profile = UserProfile.query.filter_by(payment_token=token).first()

    if not profile:
        logger.warning(f"Token de pagamento não encontrado na base de dados: {token}")
        return render_template('payment_unavailable.html', 
                               reason_title=_("Link de Pagamento Inválido"),
                               reason_message=_("O link que você acessou não é válido ou expirou. Por favor, solicite um novo link ao administrador.")), 404

    username = profile.username
    logger.info(f"Token '{token}' corresponde ao utilizador '{username}'. A verificar a janela de renovação.")
    
    if not profile.expiration_date:
        return render_template('payment_public.html', token=token, username=username)

    try:
        exp_date = datetime.fromisoformat(profile.expiration_date).date()
        today = date.today()
        days_left = (exp_date - today).days
        renewal_window = int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7))
        grace_period = int(config.get("PAYMENT_LINK_GRACE_PERIOD_DAYS", 7))

        if days_left < renewal_window:
            if days_left < -grace_period:
                flash(_("Sua assinatura expirou. Renove seu acesso aqui na sua página de conta."), "warning")
                if current_user.is_authenticated:
                    return redirect(url_for('main.account_page'))
                else:
                    return redirect(url_for('auth.login', next=url_for('main.account_page')))
            return render_template('payment_public.html', token=token, username=username)
        else:
            message = _("Sua assinatura vence em %(days)d dias. A renovação estará disponível quando faltarem %(window)d dias ou menos para o vencimento.", days=days_left, window=renewal_window)
            return render_template('payment_unavailable.html',
                                   reason_title=_("Renovação Indisponível no Momento"),
                                   reason_message=message)
    except (ValueError, TypeError):
        return render_template('payment_public.html', token=token, username=username)
