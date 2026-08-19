# app/blueprints/api/stats.py

import logging
import bleach
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from flask_babel import gettext as _

from ...extensions import plex_manager, tautulli_manager, data_manager

logger = logging.getLogger(__name__)
stats_api_bp = Blueprint('stats_api', __name__)

def _obfuscate_username(username):
    if len(username) <= 2: return username
    return f"{username[0]}{'*' * (len(username) - 2)}{username[-1]}"

@stats_api_bp.route('/')
@login_required
def get_statistics_data():
    days = request.args.get('days', 7, type=int)
    
    plex_users = plex_manager.get_all_plex_users() or []
    plex_users_info = {u['id']: u['thumb'] for u in plex_users}
    tautulli_data = tautulli_manager.get_watch_stats(days=days, plex_users_info=plex_users_info)

    if not tautulli_data.get("success"):
        return jsonify(tautulli_data)

    tautulli_user_ids = {user_stat['user_id'] for user_stat in tautulli_data.get("stats", [])}
    plex_user_ids = {u['id'] for u in plex_users}
    all_user_ids = list(tautulli_user_ids.union(plex_user_ids))

    all_profiles = data_manager.get_user_profiles_by_id(all_user_ids)
    
    processed_stats = []
    for user_stat in tautulli_data.get("stats", []):
        user_id = user_stat["user_id"]
        profile = all_profiles.get(user_id, {})
        
        is_private = profile.get('hide_from_leaderboard', False)
        user_stat["is_private"] = is_private
        user_stat["original_username"] = user_stat["username"]

        if not current_user.is_admin() and is_private and current_user.id != str(user_id):
            user_stat["username"] = _obfuscate_username(user_stat["username"])
            user_stat["thumb"] = f"https://placehold.co/80x80/1F2937/E5E7EB?text=?"

        processed_stats.append(user_stat)
    
    tautulli_data["stats"] = processed_stats
    return jsonify(tautulli_data)

@stats_api_bp.route('/user/<int:plex_user_id>')
@login_required
def get_user_statistics(plex_user_id):
    """
    Obtém as estatísticas detalhadas de um utilizador, respeitando as configurações de privacidade.
    """
    profile = data_manager.get_user_profile(plex_user_id)
    is_private = profile.get('hide_from_leaderboard', False)

    if not is_private or current_user.is_admin() or current_user.id == str(plex_user_id):
        days = request.args.get('days', 7, type=int)
        return jsonify(tautulli_manager.get_user_watch_details(plex_user_id=plex_user_id, days=days))
    else:
        logger.warning(f"Acesso negado para '{current_user.username}' ao tentar ver as estatísticas privadas do utilizador ID '{plex_user_id}'.")
        return jsonify({"success": False, "message": _("Este usuário prefere manter suas estatísticas privadas.")}), 403

@stats_api_bp.route('/user/history')
@login_required
def get_user_watch_history_route():
    """Endpoint para obter o histórico de visualização paginado do utilizador logado."""
    try:
        page = request.args.get('page', 1, type=int)
        length = request.args.get('length', 15, type=int)
        
        raw_search = request.args.get('search', '', type=str)
        search = bleach.clean(raw_search, strip=True)

        history_data = tautulli_manager.get_user_watch_history(
            user_id=int(current_user.id),
            page=page,
            length=length,
            search=search
        )
        return jsonify(history_data)
    except Exception as e:
        logger.error(f"Erro ao obter o histórico de visualização para {current_user.username}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao obter histórico de visualização."}), 500

@stats_api_bp.route('/recently-added')
@login_required
def get_recently_added_route():
    days = request.args.get('days', 7, type=int)
    return jsonify(tautulli_manager.get_recently_added(days=days))

@stats_api_bp.route('/wrapped/<int:plex_user_id>')
@login_required
def get_wrapped_data_route(plex_user_id):
    """
    Retrospectiva anual estilo 'Plex Wrapped' de um utilizador. Respeita a
    mesma configuração de privacidade (hide_from_leaderboard) do restante
    das estatísticas.
    """
    profile = data_manager.get_user_profile(plex_user_id)
    if not profile:
        return jsonify({"success": False, "message": _("Utilizador não encontrado.")}), 404

    is_private = profile.get('hide_from_leaderboard', False)
    if is_private and not current_user.is_admin() and current_user.id != str(plex_user_id):
        logger.warning(f"Acesso negado para '{current_user.username}' ao tentar ver o Plex Wrapped privado do utilizador ID '{plex_user_id}'.")
        return jsonify({"success": False, "message": _("Este usuário prefere manter suas estatísticas privadas.")}), 403

    year = request.args.get('year', type=int)
    return jsonify(tautulli_manager.get_wrapped_data(plex_user_id=plex_user_id, year=year))

