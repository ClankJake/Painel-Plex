# app/blueprints/api/stats.py

import logging
import bleach
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from flask_babel import gettext as _

from ...extensions import plex_manager, data_manager

logger = logging.getLogger(__name__)
stats_api_bp = Blueprint('stats_api', __name__)

@stats_api_bp.route('/')
@login_required
def get_statistics_data():
    days = request.args.get('days', 7, type=int)
    
    plex_users = plex_manager.get_all_plex_users() or []
    # Converte o ID explicitamente para STRING para combinar com o histórico
    plex_users_info = {str(u['id']): u['thumb'] for u in plex_users}
    
    # Busca os dados diretamente do Plex via plex_manager
    stats_data = plex_manager.get_watch_stats(days=days, plex_users_info=plex_users_info)

    if not stats_data.get("success"):
        return jsonify(stats_data)

    api_user_ids = {str(user_stat['user_id']) for user_stat in stats_data.get("stats", [])}
    plex_user_ids = {str(u['id']) for u in plex_users}
    all_user_ids = list(api_user_ids.union(plex_user_ids))

    # Pega todos os perfis do banco de dados 
    all_profiles = data_manager.get_user_profiles_by_id(all_user_ids)
    
    processed_stats = []
    for user_stat in stats_data.get("stats", []):
        user_id_str = str(user_stat["user_id"])
        user_id_int = int(user_id_str) if user_id_str.isdigit() else None
        
        # Busca o perfil de forma robusta lidando com chaves Int ou String
        profile = all_profiles.get(user_id_str) or all_profiles.get(user_id_int) or {}
        
        # Se o Plex não fornecer o username, pegamos da DB local
        if user_stat.get("username") in ["Unknown", "Desconhecido", "", None]:
            user_stat["username"] = profile.get('username') or next((u['username'] for u in plex_users if str(u['id']) == user_id_str), "Desconhecido")

        # Verifica proteção de privacidade com segurança (lida com string, boolean ou int)
        is_private = profile.get('hide_from_leaderboard') in [True, 'true', 'True', 1, '1']
        user_stat["is_private"] = is_private
        user_stat["original_username"] = user_stat["username"]

        # SE FOR PRIVADO E QUEM ESTÁ A VER NÃO É O ADMIN NEM O PRÓPRIO UTILIZADOR
        if not current_user.is_admin() and is_private and str(current_user.id) != user_id_str:
            # Ofusca exatamente como pedido pelo utilizador
            user_stat["username"] = "Usuário Anônimo"
            # Esconde a foto de perfil
            user_stat["thumb"] = f"https://placehold.co/80x80/1F2937/E5E7EB?text=?"

        # Injeta os apelidos/aliases que o JavaScript espera ler
        user_stat["total_plays"] = int(user_stat.get("total_plays", 0))
        user_stat["total_duration"] = int(user_stat.get("total_duration", 0))
        user_stat["total_time"] = user_stat["total_duration"]
        user_stat["plays"] = user_stat["total_plays"]

        processed_stats.append(user_stat)
    
    stats_data["stats"] = processed_stats
    return jsonify(stats_data)

@stats_api_bp.route('/user/<int:plex_user_id>')
@login_required
def get_user_statistics(plex_user_id):
    """
    Obtém as estatísticas detalhadas de um utilizador diretamente do Plex.
    """
    profile = data_manager.get_user_profile(plex_user_id)
    is_private = profile.get('hide_from_leaderboard') in [True, 'true', 'True', 1, '1'] if profile else False

    if not is_private or current_user.is_admin() or str(current_user.id) == str(plex_user_id):
        days = request.args.get('days', 7, type=int)
        data = plex_manager.get_user_watch_details(plex_user_id=plex_user_id, days=days)
        
        if data and data.get("success"):
            from ...config import load_or_create_config
            config = load_or_create_config()
            
            total_duration = int(data.get("total_time", 0))
            movie_count = int(data.get("movie_count", 0))
            episode_count = int(data.get("episode_count", 0))
            
            achievements = []
            
            # Filmes
            if movie_count >= int(config.get('ACHIEVEMENT_MOVIE_MARATHON_GOLD', 20)):
                achievements.append({"icon": "🍿", "title": "Cinéfilo Ouro", "description": f"Assistiu {movie_count} filmes", "level": "gold"})
            elif movie_count >= int(config.get('ACHIEVEMENT_MOVIE_MARATHON_SILVER', 10)):
                achievements.append({"icon": "🍿", "title": "Cinéfilo Prata", "description": f"Assistiu {movie_count} filmes", "level": "silver"})
            elif movie_count >= int(config.get('ACHIEVEMENT_MOVIE_MARATHON_BRONZE', 5)):
                achievements.append({"icon": "🍿", "title": "Cinéfilo Bronze", "description": f"Assistiu {movie_count} filmes", "level": "bronze"})
                
            # Séries
            if episode_count >= int(config.get('ACHIEVEMENT_SERIES_BINGER_GOLD', 100)):
                achievements.append({"icon": "📺", "title": "Maratonista Ouro", "description": f"Assistiu {episode_count} episódios", "level": "gold"})
            elif episode_count >= int(config.get('ACHIEVEMENT_SERIES_BINGER_SILVER', 50)):
                achievements.append({"icon": "📺", "title": "Maratonista Prata", "description": f"Assistiu {episode_count} episódios", "level": "silver"})
            elif episode_count >= int(config.get('ACHIEVEMENT_SERIES_BINGER_BRONZE', 20)):
                achievements.append({"icon": "📺", "title": "Maratonista Bronze", "description": f"Assistiu {episode_count} episódios", "level": "bronze"})
                
            # Tempo (Horas)
            hours = total_duration / 3600
            if hours >= int(config.get('ACHIEVEMENT_TIME_TRAVELER_GOLD', 7)):
                achievements.append({"icon": "⏳", "title": "Viajante do Tempo Ouro", "description": f"Assistiu por {int(hours)} horas", "level": "gold"})
            elif hours >= int(config.get('ACHIEVEMENT_TIME_TRAVELER_SILVER', 5)):
                achievements.append({"icon": "⏳", "title": "Viajante do Tempo Prata", "description": f"Assistiu por {int(hours)} horas", "level": "silver"})
            elif hours >= int(config.get('ACHIEVEMENT_TIME_TRAVELER_BRONZE', 3)):
                achievements.append({"icon": "⏳", "title": "Viajante do Tempo Bronze", "description": f"Assistiu por {int(hours)} horas", "level": "bronze"})

            details = {
                "stats": data.get("stats", []),
                "recent": data.get("recent_history", []),
                "achievements": achievements,
                
                # Múltiplos alias para o tempo assistido para garantir que o frontend o encontra!
                "total_time": int(data.get("total_time", 0)),
                "total_duration": int(data.get("total_time", 0)),
                "time": int(data.get("total_time", 0)),
                "watch_time_stats": data.get("watch_time_stats", []),
                
                "total_plays": int(data.get("total_plays", 0)),
                "movie_count": int(data.get("movie_count", 0)),
                "episode_count": int(data.get("episode_count", 0)),
                "activity_by_day": data.get("activity_by_day", []),
                "genres": data.get("genres", [])
            }
            
            # Ajusta o nome do contador para compatibilidade
            for stat in details["stats"]:
                stat["count"] = int(stat.get("count", 0))
                
            return jsonify({"success": True, "details": details})
            
        return jsonify({"success": False, "message": _("Falha ao obter dados detalhados deste utilizador.")})
    else:
        logger.warning(f"Acesso negado para '{current_user.username}' ao tentar ver as estatísticas privadas do utilizador ID '{plex_user_id}'.")
        return jsonify({"success": False, "message": _("Este usuário prefere manter suas estatísticas privadas.")}), 403

@stats_api_bp.route('/user/history')
@login_required
def get_user_watch_history_route():
    """Endpoint para obter o histórico de visualização paginado do utilizador logado via Plex."""
    try:
        page = request.args.get('page', 1, type=int)
        length = request.args.get('length', 15, type=int)
        
        raw_search = request.args.get('search', '', type=str)
        search = bleach.clean(raw_search, strip=True)

        history_data = plex_manager.get_user_watch_history(
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
    return jsonify(plex_manager.get_recently_added(days=days))
