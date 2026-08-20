# app/blueprints/auth.py

import uuid
import secrets
import logging
import requests
import time
from functools import wraps
from datetime import datetime, timezone, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, session, jsonify, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from plexapi.myplex import MyPlexAccount
from flask_babel import gettext as _

from ..models import User
from ..config import is_configured, load_or_create_config, save_app_config
from ..extensions import plex_manager, data_manager, limiter

# --- Configurações e Constantes ---
logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__)

PLEX_API_BASE_URL = "https://plex.tv/api/v2"
REQUEST_TIMEOUT = 10  # Segundos

# -------------------------------------------------------------------------
# CACHE PARA DEBOUNCING DE LOGS (Evita spam em polling/redirecionamentos)
# -------------------------------------------------------------------------
access_log_cache = {}

def should_log_access(identifier, cooldown=5):
    """
    Verifica se deve logar o acesso ou se é um 'spam' de requisições rápidas.
    Útil para endpoints de polling ou redirects.
    """
    try:
        now = time.time()
        last_time = access_log_cache.get(identifier, 0)
        
        if len(access_log_cache) > 2000:
            access_log_cache.clear()
        
        if now - last_time > cooldown:
            access_log_cache[identifier] = now
            return True
        return False
    except Exception:
        return True

# --- Helpers ---
def safe_log_request_info(force=False, identifier=None):
    """
    Faz o log de informações da requisição de forma segura e controlada.
    Se force=False, aplica debounce baseado no IP ou identificador.
    """
    try:
        if not force:
            key = identifier or request.remote_addr
            if not should_log_access(key, cooldown=60):
                return

        user_agent = request.headers.get('User-Agent', 'unknown')
        logger.debug(f"Requisição recebida - endpoint: {request.endpoint} - UA: {user_agent}")
    except Exception:
        pass

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

def _login_user_session(account, role, redirect_endpoint, from_settings=False, plex_token=None):
    """Helper para DRY na lógica de login e inicialização da sessão Flask."""
    user_details = {
        'id': str(account.id), 'username': account.username, 'email': account.email,
        'thumb': account.thumb, 'role': role
    }
    user_obj = User(**user_details)
    login_user(user_obj, remember=True)
    session.permanent = True
    session['user_details'] = user_details

    # 🎁 INDIQUE E GANHE: quem chega por um link de indicação tem o código guardado
    # na sessão. A indicação NÃO é registada aqui, e sim no resgate do convite
    # (ver PlexInviteManager._resolve_pending_referral) — porque o programa premeia
    # trazer utilizadores NOVOS. Se registássemos no login, um utilizador que já
    # tem acesso podia abrir o link de um amigo e gerar-lhe uma recompensa
    # indevida na sua próxima renovação.
    #
    # Limpamos aqui o código pendente para que não fique "preso" na sessão e acabe
    # aplicado por engano a um resgate de convite posterior.
    discarded_ref = session.pop('pending_referral_code', None)
    if discarded_ref:
        logger.info(
            f"Código de indicação '{discarded_ref}' descartado: '{account.username}' entrou por login "
            f"(o programa de indicações aplica-se apenas a novos utilizadores via convite)."
        )

    if from_settings:
        session['plex_token'] = plex_token
        session.pop('from_settings', None)
        redirect_url = url_for('main.settings_page', _external=False)
    else:
        redirect_url = url_for(redirect_endpoint, _external=False)
    
    return jsonify({"success": True, "action": "login", "redirect_url": redirect_url})

# --- MIDDLEWARE: Verificação Global de Status ---
@auth_bp.before_app_request
def check_user_active_status():
    """
    Verifica a cada requisição se o utilizador logado ainda está ativo/válido.
    Se o utilizador for removido do banco de dados ou marcado como inativo
    enquanto navega, esta função força o logout imediato.
    """
    if not current_user.is_authenticated:
        return

    if current_user.is_admin():
        return

    # Ignorar rotas de autenticação, estáticos e pagamentos para evitar loops de redirecionamento
    if request.endpoint and (
        'static' in request.endpoint or 
        request.endpoint.startswith('auth.') or
        request.endpoint in ['main.payment_page'] or
        request.endpoint.startswith('payments_api.')
    ):
        return

    try:
        plex_user_id = int(current_user.id)
        user_profile = data_manager.get_user_profile(plex_user_id)
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.path.startswith('/api/')

        # CASO 1: Utilizador foi apagado do banco de dados
        if not user_profile:
            logger.warning(f"Sessão encerrada: Perfil do utilizador '{current_user.username}' (ID: {plex_user_id}) não encontrado.")
            logout_user()
            session.clear()
            
            if is_ajax:
                return jsonify({"success": False, "message": "unauthorized", "redirect_url": url_for('auth.login')}), 401
                
            flash(_("A sua conta não foi encontrada ou foi removida."), "error")
            return redirect(url_for('auth.login'))

        # CASO 2: Utilizador ficou inativo (expirou enquanto navegava)
        if user_profile.get('status') == 'inactive':
            logger.info(f"Sessão encerrada: Utilizador '{current_user.username}' está marcado como inativo.")
            logout_user()
            session.clear()
            
            if is_ajax:
                return jsonify({"success": False, "message": "unauthorized", "redirect_url": url_for('auth.login')}), 401
                
            flash(_("A sua subscrição expirou. Por favor, faça login novamente para regularizar o acesso."), "warning")
            return redirect(url_for('auth.login'))

    except ValueError:
        logout_user()
        session.clear()
        return redirect(url_for('auth.login'))
    except Exception as e:
        logger.debug(f"Erro ao verificar status global do utilizador: {e}")

# --- Decorators de Autorização ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        safe_log_request_info(force=True) 
        if not current_user.is_authenticated or not current_user.is_admin():
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                return jsonify({"success": False, "message": _("Acesso negado. Requer permissão de administrador.")}), 403
            flash(_("Acesso restrito a administradores."), "error")
            return redirect(url_for('main.statistics_page'))
        return f(*args, **kwargs)
    return decorated_function

# --- Rotas de Autenticação ---
@auth_bp.route('/login')
@limiter.limit("10 per minute")
def login():
    safe_log_request_info(force=True)
    if current_user.is_authenticated:
        return redirect(url_for('main.index' if current_user.is_admin() else 'main.statistics_page'))

    # 🎁 Mantém o contexto da indicação visível para quem veio de um link de amigo
    # e escolheu "Já tenho acesso" na landing.
    from .main import get_pending_referrer_name
    return render_template('login.html', referrer_name=get_pending_referrer_name())

@auth_bp.route('/logout')
@login_required
def logout():
    safe_log_request_info(force=True)
    logout_user()
    session.clear()
    flash(_("Você foi desconectado com sucesso."), "success")
    return redirect(url_for('auth.login'))

@auth_bp.route('/plex/auth-context', methods=['GET'])
@limiter.limit("15 per minute")
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
@limiter.limit("60 per minute")
def check_plex_pin(client_id, pin_id):
    safe_log_request_info(identifier=f"check_pin_{pin_id}")

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
        
        # Verificação robusta de administrador (Username ou Email ignorando cases)
        admin_username = str(config.get('ADMIN_USER', '')).strip().lower()
        plex_username = str(account.username).strip().lower()
        plex_email = str(account.email or '').strip().lower()
        
        is_admin_login = admin_username != "" and admin_username in (plex_username, plex_email)

        # 1. Login do Administrador
        if is_admin_login:
            # 🛡️ Grava o ID Plex do administrador na configuração. Isto permite que
            # tarefas automáticas (ex: 'removal_job') o identifiquem por ID — um
            # critério imune a mudanças de username/email e que funciona mesmo
            # quando o Plex não devolve dados do utilizador (o dono do servidor não
            # aparece na lista de amigos). Só grava quando o valor muda, para não
            # escrever no disco a cada login.
            try:
                if str(config.get('ADMIN_USER_ID', '') or '') != str(account.id):
                    config['ADMIN_USER_ID'] = str(account.id)
                    save_app_config(config)
                    logger.info(f"ID do administrador ({account.id}) registado na configuração.")
            except Exception as e:
                logger.warning(f"Não foi possível registar o ADMIN_USER_ID: {e}")

            return _login_user_session(
                account, 'admin', 'main.index', 
                from_settings=session.get('from_settings'), plex_token=plex_token
            )
        
        # --- LÓGICA DE LOGIN PARA UTILIZADORES NORMAIS ---
        plex_users = plex_manager.get_all_plex_users()
        user_profile = data_manager.get_user_profile(int(account.id))
        
        # Sincronização Local
        if user_profile:
            updates = {}
            if user_profile.get('username') != account.username:
                logger.info(f"Sincronização: Username alterado de '{user_profile.get('username')}' para '{account.username}'.")
                updates['username'] = account.username
            
            if account.email and user_profile.get('email') != account.email:
                logger.info(f"Sincronização: Email alterado de '{user_profile.get('email')}' para '{account.email}'.")
                updates['email'] = account.email
            
            if updates:
                data_manager.set_user_profile(int(account.id), updates)
                user_profile.update(updates)
        else:
            user_profile = data_manager.get_user_profile_by_username(account.username)

        has_plex_access = False
        if plex_users:
            for u in plex_users:
                if str(u.get('id')) == str(account.id) or u.get('username') == account.username:
                    has_plex_access = True
                    break

        if has_plex_access:
            if not user_profile:
                logger.info(f"Novo utilizador detetado '{account.username}' (ID: {account.id}). Criando perfil local.")
                new_profile = {
                    'plex_user_id': int(account.id),
                    'username': account.username,
                    'email': account.email,
                    'status': 'active',
                    'payment_token': secrets.token_urlsafe(16),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'libraries': '[]'
                }
                data_manager.set_user_profile(int(account.id), new_profile)
                user_profile = new_profile

            if user_profile and user_profile.get('status') == 'inactive':
                logger.info(f"Utilizador '{account.username}' está ativo no Plex mas inativo localmente. A atualizar para 'ativo'.")
                user_profile['status'] = 'active'
                data_manager.set_user_profile(user_profile['plex_user_id'], user_profile)

            return _login_user_session(account, 'user', 'main.statistics_page')
        else:
            if not user_profile:
                error_msg = _("Acesso negado. O usuário %(username)s não tem acesso a este servidor.", username=account.username)
                return jsonify({"success": False, "message": "auth_denied", "error": error_msg})

            if user_profile.get('status') == 'inactive':
                logger.info(f"Tentativa de login do utilizador inativo '{account.username}'. A redirecionar para pagamento.")
                
                # Garante que o token de pagamento existe
                if not user_profile.get('payment_token'):
                    user_profile['payment_token'] = secrets.token_urlsafe(16)
                    data_manager.set_user_profile(user_profile['plex_user_id'], user_profile)
                
                flash(_("A sua conta está inativa. Por favor, efetue o pagamento para reativar o seu acesso."), "info")
                reactivation_url = url_for('main.payment_page', token=user_profile.get('payment_token'), _external=False)
                return jsonify({"success": True, "action": "reactivate", "redirect_url": reactivation_url})
            
            if user_profile.get('status') == 'active':
                latest_payment = data_manager.get_latest_completed_payment(user_profile['plex_user_id'])
                is_recently_paid = False
                if latest_payment and latest_payment.get('created_at'):
                    try:
                        payment_time_utc = datetime.fromisoformat(latest_payment['created_at'])
                        if payment_time_utc.tzinfo is None:
                            payment_time_utc = payment_time_utc.replace(tzinfo=timezone.utc)
                            
                        if (datetime.now(timezone.utc) - payment_time_utc) < timedelta(minutes=15):
                            is_recently_paid = True
                    except (ValueError, TypeError):
                        pass
                
                if is_recently_paid:
                    logger.info(f"Utilizador '{account.username}' tem pagamento recente, mas acesso ao Plex pendente. Permitindo login.")
                    return _login_user_session(account, 'user', 'main.statistics_page')
                else:
                    logger.warning(f"Utilizador '{account.username}' em estado inconsistente. A forçar reativação.")
                    
                    if not user_profile.get('payment_token'):
                        user_profile['payment_token'] = secrets.token_urlsafe(16)
                        data_manager.set_user_profile(user_profile['plex_user_id'], user_profile)
                        
                    flash(_("A sua conta está num estado inconsistente. Por favor, efetue o pagamento para garantir o seu acesso."), "warning")
                    reactivation_url = url_for('main.payment_page', token=user_profile.get('payment_token'), _external=False)
                    return jsonify({"success": True, "action": "reactivate", "redirect_url": reactivation_url})

        error_msg = _("Acesso negado. O usuário %(username)s não tem acesso a este servidor.", username=account.username)
        return jsonify({"success": False, "message": "auth_denied", "error": error_msg})

    except Exception as e:
        logger.error(f"Erro ao verificar o PIN {pin_id} do Plex: {e}", exc_info=True)
        return jsonify({"success": False, "message": "error", "error": "Falha ao verificar autenticação."}), 500

@auth_bp.route('/plex/check-pin-for-token/<string:client_id>/<int:pin_id>', methods=['GET'])
@limiter.limit("60 per minute")
def check_plex_pin_for_token(client_id, pin_id):
    safe_log_request_info(identifier=f"check_pin_token_{pin_id}")
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
