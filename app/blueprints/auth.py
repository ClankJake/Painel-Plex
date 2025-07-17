# app/blueprints/auth.py

import uuid
import urllib.parse
import logging
import requests
from functools import wraps
from datetime import datetime, timezone

from flask import Blueprint, render_template, redirect, url_for, flash, session, jsonify, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _

from ..models import User
from ..config import is_configured, load_or_create_config
from ..extensions import plex_manager

# --- Configurações e Constantes ---
logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)

PLEX_API_BASE_URL = "https://plex.tv/api/v2"
REQUEST_TIMEOUT = 10  # Segundos

# --- Helpers ---
def safe_log_request_info():
    """Faz o log de informações da requisição de forma segura."""
    user_agent = request.headers.get('User-Agent', 'unknown')
    logger.debug(f"Requisição recebida - User-Agent: {user_agent}")

def _get_pin_status(pin_id, client_id):
    """
    Função auxiliar para verificar o status de um PIN do Plex.
    Centraliza a chamada à API para evitar duplicação de código.
    """
    url = f"{PLEX_API_BASE_URL}/pins/{pin_id}"
    headers = {
        'X-Plex-Client-Identifier': client_id,
        'Accept': 'application/json'
    }
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning(f"PIN {pin_id} não encontrado (provavelmente expirado).")
            return {"error": "expired", "message": _("O pedido de autenticação expirou ou é inválido.")}
        logger.error(f"Erro HTTP ao verificar o PIN {pin_id}: {e}")
        raise e
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de rede ao verificar o PIN {pin_id}: {e}")
        raise e

# --- Decorators de Autorização ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        safe_log_request_info()
        if not current_user.is_authenticated or not current_user.is_admin():
            if request.path.startswith('/api/'):
                return jsonify({"success": False, "message": _("Acesso negado. Requer permissão de administrador.")}), 403
            flash(_("Acesso restrito a administradores."), "error")
            return redirect(url_for('main.statistics_page'))
        return f(*args, **kwargs)
    return decorated_function

# --- Rotas de Autenticação ---
@auth_bp.route('/login')
def login():
    safe_log_request_info()
    if current_user.is_authenticated:
        return redirect(url_for('main.index' if current_user.is_admin() else 'main.statistics_page'))
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    safe_log_request_info()
    logout_user()
    session.clear()
    flash(_("Você foi desconectado com sucesso."), "success")
    return redirect(url_for('auth.login'))

@auth_bp.route('/plex/auth-context', methods=['GET'])
def get_plex_auth_context():
    """Fornece o contexto necessário para o cliente iniciar a autenticação Plex."""
    safe_log_request_info()
    
    if request.args.get('from_settings') == 'true':
        session['from_settings'] = True
        logger.info("Sinalizador 'from_settings' definido na sessão para re-autenticação.")
    else:
        session.pop('from_settings', None)

    try:
        config = load_or_create_config()
        product_name = config.get("APP_TITLE", _(current_app.config.get("DEFAULT_APP_TITLE", "Painel de Gestão Plex")))
        client_id = str(uuid.uuid4())
        
        return jsonify({
            "success": True,
            "product_name": product_name,
            "client_id": client_id
        })
    except Exception as e:
        logger.error(f"Falha ao obter o contexto de autenticação do Plex: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Erro interno ao obter contexto de autenticação."}), 500


@auth_bp.route('/plex/check-pin/<string:client_id>/<int:pin_id>', methods=['GET'])
def check_plex_pin(client_id, pin_id):
    safe_log_request_info()
    try:
        data = _get_pin_status(pin_id, client_id)

        if data.get("error"):
            return jsonify({"success": False, "message": "auth_denied", "error": data["message"]})

        if not data.get('authToken'):
            return jsonify({"success": False, "message": "pending"})

        plex_token = data['authToken']
        account = MyPlexAccount(token=plex_token)
        config = load_or_create_config()

        if not is_configured():
            session['plex_token'] = plex_token
            session['plex_username'] = account.username
            redirect_url = url_for('main.setup', _external=False)
            return jsonify({"success": True, "redirect_url": redirect_url})

        admin_username = config.get('ADMIN_USER')
        user_role = 'admin' if account.username == admin_username else 'user'

        if user_role == 'user':
            plex_users = plex_manager.get_all_plex_users()
            user_found = any(u['username'] == account.username for u in plex_users if u.get('servers'))
            if not user_found:
                error_msg = _("Acesso negado. O usuário %(username)s não tem acesso a este servidor.", username=account.username)
                return jsonify({"success": False, "message": "auth_denied", "error": error_msg})

        user_details = {
            'id': str(account.id), 'username': account.username, 'email': account.email,
            'thumb': account.thumb, 'role': user_role
        }
        user_obj = User(**user_details)
        login_user(user_obj, remember=True)
        session.permanent = True
        session['user_details'] = user_details

        if user_role == 'admin' and session.get('from_settings'):
            session['plex_token'] = plex_token
            session.pop('from_settings', None)
            redirect_url = url_for('main.settings_page', _external=False)
        else:
            redirect_url = url_for('main.index' if user_role == 'admin' else 'main.statistics_page', _external=False)

        return jsonify({"success": True, "redirect_url": redirect_url})

    except Exception as e:
        logger.error(f"Erro ao verificar o PIN {pin_id} do Plex: {e}", exc_info=True)
        return jsonify({"success": False, "message": "error", "error": "Falha ao verificar autenticação."}), 500

@auth_bp.route('/plex/check-pin-for-token/<string:client_id>/<int:pin_id>', methods=['GET'])
def check_plex_pin_for_token(client_id, pin_id):
    safe_log_request_info()
    try:
        data = _get_pin_status(pin_id, client_id)

        if data.get("error"):
            return jsonify({"success": False, "message": "auth_denied", "error": data["message"]})

        if data.get('authToken'):
            return jsonify({"success": True, "token": data['authToken']})

        return jsonify({"success": False, "message": "pending"})

    except Exception as e:
        logger.error(f"Erro ao verificar o PIN {pin_id} para token: {e}", exc_info=True)
        return jsonify({"success": False, "message": "error", "error": "Erro inesperado ao verificar o PIN."}), 500

@auth_bp.route('/plex/redirect-to-auth')
def redirect_to_auth():
    """
    Renderiza uma página intermediária que irá gerar o PIN e redirecionar para a autenticação do Plex.
    """
    context_url = url_for('auth.get_plex_auth_context')
    return render_template('plex_redirect.html', get_plex_auth_context_url=context_url)

