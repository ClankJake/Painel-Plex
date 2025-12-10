# app/blueprints/auth.py

import uuid
import urllib.parse
import logging
import requests
from functools import wraps
from datetime import datetime, timezone, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, session, jsonify, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _

from ..models import User
from ..config import is_configured, load_or_create_config
from ..extensions import plex_manager, data_manager, limiter # Importa o limiter

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
@limiter.limit("10 per minute") # Protege a página de login contra força bruta
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
@limiter.limit("15 per minute") # Limita a criação de contextos de autenticação
def get_plex_auth_context():
    """Fornece o contexto necessário para o cliente iniciar la autenticação Plex."""
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
@limiter.limit("60 per minute") # Permite polling a cada 1s (necessário para o frontend), mas bloqueia excessos
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
        is_admin_login = account.username == admin_username

        # Se for o administrador, processa o login imediatamente
        if is_admin_login:
            user_details = {
                'id': str(account.id), 'username': account.username, 'email': account.email,
                'thumb': account.thumb, 'role': 'admin'
            }
            user_obj = User(**user_details)
            login_user(user_obj, remember=True)
            session.permanent = True
            session['user_details'] = user_details

            if session.get('from_settings'):
                session['plex_token'] = plex_token
                session.pop('from_settings', None)
                redirect_url = url_for('main.settings_page', _external=False)
            else:
                redirect_url = url_for('main.index', _external=False)
            
            return jsonify({"success": True, "action": "login", "redirect_url": redirect_url})
        
        # --- LÓGICA DE LOGIN PARA UTILIZADORES NORMAIS ---
        plex_users = plex_manager.get_all_plex_users()
        
        # 1. Sincronização: Tenta encontrar o perfil localmente pelo ID do Plex
        # Isso permite que o utilizador faça login mesmo se mudou o nome de utilizador ou email no Plex
        user_profile = data_manager.get_user_profile(int(account.id))
        
        if user_profile:
            updates = {}
            # Verifica se o username mudou
            if user_profile.get('username') != account.username:
                logger.info(f"Sincronização: Username alterado de '{user_profile.get('username')}' para '{account.username}' (ID: {account.id}). Atualizando localmente.")
                updates['username'] = account.username
            
            # Verifica se o email mudou
            if account.email and user_profile.get('email') != account.email:
                logger.info(f"Sincronização: Email alterado de '{user_profile.get('email')}' para '{account.email}' (ID: {account.id}). Atualizando localmente.")
                updates['email'] = account.email
            
            if updates:
                data_manager.set_user_profile(int(account.id), updates)
                user_profile.update(updates) # Atualiza o objeto em memória para o resto da lógica
        else:
            # Fallback: Se não encontrou por ID, tenta pelo username (para casos legados)
            user_profile = data_manager.get_user_profile_by_username(account.username)

        # 2. Verifica acesso ao servidor (se está na lista de amigos/partilhas)
        has_plex_access = False
        if plex_users:
            for u in plex_users:
                # Verifica por ID (mais robusto)
                if str(u.get('id')) == str(account.id):
                    has_plex_access = True
                    break
                # Verifica por Username (compatibilidade)
                if u.get('username') == account.username:
                    has_plex_access = True
                    break

        if has_plex_access:
            # O utilizador tem acesso ao servidor Plex, procede com o login normal.
            if user_profile and user_profile.get('status') == 'inactive':
                logger.info(f"Utilizador '{account.username}' está ativo no Plex mas inativo localmente. A atualizar para 'ativo'.")
                user_profile['status'] = 'active'
                data_manager.set_user_profile(user_profile['plex_user_id'], user_profile)

            user_details = {
                'id': str(account.id), 'username': account.username, 'email': account.email,
                'thumb': account.thumb, 'role': 'user'
            }
            user_obj = User(**user_details)
            login_user(user_obj, remember=True)
            session.permanent = True
            session['user_details'] = user_details
            redirect_url = url_for('main.statistics_page', _external=False)
            return jsonify({"success": True, "action": "login", "redirect_url": redirect_url})
        else:
            # O utilizador NÃO tem acesso ao servidor Plex.
            if not user_profile:
                # Se não temos perfil local e não tem acesso ao Plex, nega.
                error_msg = _("Acesso negado. O usuário %(username)s não tem acesso a este servidor.", username=account.username)
                return jsonify({"success": False, "message": "auth_denied", "error": error_msg})

            # Se o perfil local existe, verificamos se está inativo (expirado/removido)
            if user_profile.get('status') == 'inactive':
                logger.info(f"Tentativa de login do utilizador inativo '{account.username}' (ID: {account.id}). A redirecionar para pagamento.")
                
                # Garante que o email está atualizado no perfil inativo para receber o convite
                if account.email and user_profile.get('email') != account.email:
                    user_profile['email'] = account.email
                    data_manager.set_user_profile(user_profile['plex_user_id'], user_profile)
                    logger.info(f"E-mail do utilizador inativo '{account.username}' atualizado para '{account.email}'.")
                
                flash(_("A sua conta está inativa. Por favor, efetue o pagamento para reativar o seu acesso."), "info")
                reactivation_url = url_for('main.payment_page', token=user_profile.get('payment_token'), _external=False)
                return jsonify({"success": True, "action": "reactivate", "redirect_url": reactivation_url})
            
            # Se está marcado como ativo mas não tem acesso ao Plex, algo está errado (ou removido manualmente no Plex)
            if user_profile.get('status') == 'active':
                latest_payment = data_manager.get_latest_completed_payment(user_profile['plex_user_id'])
                is_recently_paid = False
                if latest_payment and latest_payment.get('created_at'):
                    try:
                        payment_time_utc = datetime.fromisoformat(latest_payment['created_at']).astimezone(timezone.utc)
                        if (datetime.now(timezone.utc) - payment_time_utc) < timedelta(minutes=15):
                            is_recently_paid = True
                    except (ValueError, TypeError):
                        pass
                
                if is_recently_paid:
                    logger.info(f"Utilizador '{account.username}' tem pagamento recente, mas acesso ao Plex pendente. A permitir login no painel para quebrar o ciclo.")
                    user_details = {
                        'id': str(account.id), 'username': account.username, 'email': account.email,
                        'thumb': account.thumb, 'role': 'user'
                    }
                    user_obj = User(**user_details)
                    login_user(user_obj, remember=True)
                    session.permanent = True
                    session['user_details'] = user_details
                    redirect_url = url_for('main.statistics_page', _external=False)
                    return jsonify({"success": True, "action": "login", "redirect_url": redirect_url})
                else:
                    logger.warning(f"Utilizador '{account.username}' em estado inconsistente (ativo localmente, sem acesso ao Plex, sem pagamento recente). A forçar reativação.")
                    flash(_("A sua conta está num estado inconsistente. Por favor, efetue o pagamento para garantir o seu acesso."), "warning")
                    reactivation_url = url_for('main.payment_page', token=user_profile.get('payment_token'), _external=False)
                    return jsonify({"success": True, "action": "reactivate", "redirect_url": reactivation_url})

        # Fallback para negar o acesso caso nenhuma condição seja satisfeita
        error_msg = _("Acesso negado. O usuário %(username)s não tem acesso a este servidor.", username=account.username)
        return jsonify({"success": False, "message": "auth_denied", "error": error_msg})

    except Exception as e:
        logger.error(f"Erro ao verificar o PIN {pin_id} do Plex: {e}", exc_info=True)
        return jsonify({"success": False, "message": "error", "error": "Falha ao verificar autenticação."}), 500

@auth_bp.route('/plex/check-pin-for-token/<string:client_id>/<int:pin_id>', methods=['GET'])
@limiter.limit("60 per minute") # Limite para polling de token na página de convite
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
@limiter.limit("20 per minute")
def redirect_to_auth():
    """
    Renderiza uma página intermediária que irá gerar o PIN e redirecionar para a autenticação do Plex.
    """
    context_url = url_for('auth.get_plex_auth_context')
    return render_template('plex_redirect.html', get_plex_auth_context_url=context_url)
