# app/blueprints/api/stats.py

import logging
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
    plex_users = plex_manager.get_all_plex_users()
    all_profiles = {p['username']: p for p in data_manager.get_all_user_profiles()}
    plex_users_info = {u['username']: u['thumb'] for u in plex_users}
    tautulli_data = tautulli_manager.get_watch_stats(days=days, plex_users_info=plex_users_info)
    if tautulli_data.get("success"):
        processed_stats = []
        for user_stat in tautulli_data["stats"]:
            username = user_stat["username"]
            profile = all_profiles.get(username, {})
            
            is_private = profile.get('hide_from_leaderboard', False)
            user_stat["is_private"] = is_private
            
            if not current_user.is_admin() and is_private:
                user_stat["username"] = _obfuscate_username(username)
                user_stat["thumb"] = f"https://placehold.co/80x80/1F2937/E5E7EB?text=?"

            processed_stats.append(user_stat)
        tautulli_data["stats"] = processed_stats
    return jsonify(tautulli_data)

@stats_api_bp.route('/user/<username>')
@login_required
def get_user_statistics(username):
    """
    Obtém as estatísticas detalhadas de um usuário, respeitando as configurações de privacidade.
    """
    profile = data_manager.get_user_profile(username)
    is_private = profile.get('hide_from_leaderboard', False)

    if not is_private or current_user.is_admin() or current_user.username == username:
        days = request.args.get('days', 7, type=int)
        return jsonify(tautulli_manager.get_user_watch_details(username, days=days, current_user=current_user))
    else:
        logger.warning(f"Acesso negado para '{current_user.username}' ao tentar ver as estatísticas privadas de '{username}'.")
        return jsonify({"success": False, "message": _("Este usuário prefere manter suas estatísticas privadas.")}), 403

@stats_api_bp.route('/recently-added')
@login_required
def get_recently_added_route():
    days = request.args.get('days', 7, type=int)
    return jsonify(tautulli_manager.get_recently_added(days=days))
