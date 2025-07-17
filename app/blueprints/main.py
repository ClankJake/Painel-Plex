# app/blueprints/main.py

from flask import Blueprint, render_template, redirect, url_for, flash, current_app, request
from flask_login import login_required, current_user
from flask_babel import get_locale

from ..models import User
from ..decorators import admin_required
from ..config import is_configured, load_or_create_config
# Importa as instâncias dos gestores a partir das extensões
from ..extensions import plex_manager, tautulli_manager

main_bp = Blueprint('main', __name__)

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
