# app/services/tautulli/stats_handler.py
import logging
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from flask_babel import gettext as _
from requests.exceptions import RequestException

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

class StatsHandler:
    """Gere a obtenção e processamento de estatísticas do Tautulli."""

    def __init__(self, api_client):
        self.api = api_client

    def _calculate_achievements(self, stats, days):
        """Calcula as conquistas de um usuário com base em estatísticas pré-calculadas."""
        achievements = []
        
        # Definições criativas e expandidas para as conquistas
        achievement_definitions = {
            "movie_marathon": {
                "title": _("Maratonista de Cinema"),
                "description": _("Assistiu a 10 ou mais filmes nos últimos %(days)d dias.", days=days),
                "icon": "🎬",
                "check": lambda s: s.get("movie_count", 0) >= 10
            },
            "series_binger": {
                "title": _("Rei das Séries"),
                "description": _("Assistiu a 20 ou mais episódios nos últimos %(days)d dias.", days=days),
                "icon": "📺",
                "check": lambda s: s.get("episode_count", 0) >= 20
            },
            "night_owl": {
                "title": _("Coruja da Madrugada"),
                "description": _("Assistiu a 5 ou mais títulos entre 00:00 e 04:00."),
                "icon": "🦉",
                "check": lambda s: s.get("late_night_plays", 0) >= 5
            },
            "weekend_warrior": {
                "title": _("Guerreiro de Fim de Semana"),
                "description": _("A maior parte do seu tempo de visualização foi no fim de semana."),
                "icon": "🎉",
                "check": lambda s: (s.get("weekly_activity_python", [0]*7)[5] + s.get("weekly_activity_python", [0]*7)[6]) > sum(s.get("weekly_activity_python", [0]*7)[0:5]) if sum(s.get("weekly_activity_python", [])) > 0 else False
            },
            "genre_expert": {
                "title": _("Especialista em Gênero"),
                "description": _("Assistiu a 10 ou mais títulos do seu gênero favorito."),
                "icon": "🎭",
                "check": lambda s: s.get("favorite_genre_count", 0) >= 10
            },
            "explorer": {
                "title": _("O Explorador"),
                "description": _("Assistiu a títulos de 5 ou mais gêneros diferentes."),
                "icon": "🗺️",
                "check": lambda s: len(s.get("unique_genres", set())) >= 5
            },
            "loyal_companion": {
                "title": _("Fiel Escudeiro"),
                "description": _("Assistiu a algo em 5 dias diferentes na última semana (se o período for >= 7 dias)."),
                "icon": "�",
                "check": lambda s: len(s.get("unique_days", set())) >= 5 and days >= 7
            },
            "the_epic": {
                "title": _("O Épico"),
                "description": _("Assistiu a um filme com mais de 3 horas de duração."),
                "icon": "⏳",
                "check": lambda s: s.get("max_movie_duration", 0) >= 10800 # 3 horas em segundos
            },
            "multitasker": {
                "title": _("Multitarefa"),
                "description": _("Assistiu em 3 ou mais tipos de dispositivos diferentes (ex: Celular, TV, Computador)."),
                "icon": "📱💻📺",
                "check": lambda s: len(s.get("unique_platforms", set())) >= 3
            }
        }

        for key, definition in achievement_definitions.items():
            if definition["check"](stats):
                achievements.append({
                    "id": key,
                    "title": definition["title"],
                    "description": definition["description"],
                    "icon": definition["icon"]
                })
        
        return achievements

    def get_watch_stats(self, days=7, plex_users_info=None):
        """Obtém as estatísticas de visualização para todos os utilizadores."""
        try:
            after_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            history_response = self.api.get_history(after=after_date)
            
            history_data = history_response.get('data', [])
            
            user_stats = {}
            for item in history_data:
                username = item.get("user")
                if not username: continue
                user_stats.setdefault(username, {"plays": 0, "total_duration": 0})
                user_stats[username]["plays"] += 1
                user_stats[username]["total_duration"] += item.get("duration", 0)
            
            formatted_stats = [{
                'username': user, 'plays': details['plays'], 'total_duration': details['total_duration'],
                'avg_duration': details['total_duration'] / details['plays'] if details['plays'] > 0 else 0,
                'thumb': plex_users_info.get(user, None) if plex_users_info else None
            } for user, details in user_stats.items()]
            
            return {"success": True, "stats": sorted(formatted_stats, key=lambda x: x['total_duration'], reverse=True)}
        except RequestException as e:
            return {"success": False, "message": _("Erro de conexão com o Tautulli: %(error)s", error=e)}
        except Exception as e:
            logger.error(_("Erro inesperado ao processar estatísticas: %(error)s", error=e), exc_info=True)
            return {"success": False, "message": _("Erro inesperado ao processar estatísticas: %(error)s", error=e)}

    def get_user_watch_details(self, username, days=7):
        """Obtém detalhes de visualização para um único utilizador."""
        try:
            after_date_str = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            history_response = self.api.get_history(after=after_date_str, user=username)

            history = history_response.get('data', [])
            
            stats = {
                "movie_count": 0, "episode_count": 0,
                "total_movie_duration": 0, "total_episode_duration": 0,
                "genre_counts": Counter(), "recent": [],
                "weekly_activity_python": [0]*7,
                "weekly_activity_js": [0]*7,
                "top_movies": Counter(), "top_shows": Counter(),
                "late_night_plays": 0,
                "unique_genres": set(), "unique_days": set(),
                "max_movie_duration": 0, "unique_platforms": set()
            }
            
            for item in history:
                dt = datetime.fromtimestamp(item.get('date'))
                stats["weekly_activity_python"][dt.weekday()] += item.get('duration', 0)
                stats["weekly_activity_js"][(dt.weekday() + 1) % 7] += item.get('duration', 0)
                stats["unique_days"].add(dt.date())
                
                if item.get("platform"):
                    stats["unique_platforms"].add(item.get("platform"))

                if 0 <= dt.hour < 4:
                    stats["late_night_plays"] += 1
                
                if item.get("media_type") == 'movie':
                    stats["movie_count"] += 1
                    duration = item.get('duration', 0)
                    stats["total_movie_duration"] += duration
                    stats["top_movies"][item.get("title")] += 1
                    if duration > stats["max_movie_duration"]:
                        stats["max_movie_duration"] = duration
                elif item.get("media_type") == 'episode':
                    stats["episode_count"] += 1
                    stats["total_episode_duration"] += item.get('duration', 0)
                    stats["top_shows"][item.get("grandparent_title")] += 1
                
                if item.get("genres"):
                    stats["genre_counts"].update(item.get("genres"))
                    stats["unique_genres"].update(item.get("genres"))
                
                if len(stats["recent"]) < 5:
                    poster_url = f"{self.api.base_url}/pms_image_proxy?img={item['thumb']}&width=200&height=300&apikey={self.api.api_key}" if item.get('thumb') else ''
                    stats["recent"].append({"type": item.get("media_type"), "title": item.get("title"), "series": item.get("grandparent_title"), "poster_url": poster_url, "play_date": dt.strftime('%d/%m/%Y %H:%M')})
            
            favorite_genre_info = stats["genre_counts"].most_common(1)
            stats["favorite_genre"] = favorite_genre_info[0][0] if favorite_genre_info else _('N/D')
            stats["favorite_genre_count"] = favorite_genre_info[0][1] if favorite_genre_info else 0

            achievements = self._calculate_achievements(stats, days)

            details_to_return = {
                "movie_count": stats["movie_count"],
                "episode_count": stats["episode_count"],
                "total_movie_duration": stats["total_movie_duration"],
                "total_episode_duration": stats["total_episode_duration"],
                "recent": stats["recent"],
                "weekly_activity": stats["weekly_activity_js"],
                "favorite_genre": stats["favorite_genre"],
                "top_movies": [{'title': title, 'plays': plays} for title, plays in stats["top_movies"].most_common(3)],
                "top_shows": [{'title': title, 'plays': plays} for title, plays in stats["top_shows"].most_common(3)],
                "achievements": achievements
            }

            return {"success": True, "details": details_to_return}
        except RequestException as e:
            return {"success": False, "message": _("Erro de conexão com o Tautulli: %(error)s", error=e)}
        except Exception as e:
            logger.error(_("Erro inesperado ao processar detalhes do utilizador: %(error)s", error=e), exc_info=True)
            return {"success": False, "message": _("Erro inesperado: %(error)s", error=e)}

    def get_user_devices(self, username):
        """Obtém os dispositivos utilizados por um utilizador a partir do seu histórico."""
        try:
            history_response = self.api.get_history(user=username, length=500)
            history = history_response.get('data', [])

            if not history:
                return {"success": True, "devices": []}

            devices = defaultdict(lambda: {'platform': '', 'last_seen': 0})
            for item in history:
                device_key = f"{item.get('player', 'Desconhecido')}|{item.get('platform', 'Desconhecida')}"
                
                if item.get('date') > devices[device_key]['last_seen']:
                    devices[device_key]['player'] = item.get('player', 'Desconhecido')
                    devices[device_key]['platform'] = item.get('platform', 'Desconhecida')
                    devices[device_key]['last_seen'] = item.get('date')

            formatted_devices = sorted(
                list(devices.values()),
                key=lambda x: x['last_seen'],
                reverse=True
            )

            return {"success": True, "devices": formatted_devices}
        except RequestException as e:
            return {"success": False, "message": _("Erro de conexão com o Tautulli: %(error)s", error=e)}
        except Exception as e:
            logger.error(_("Erro inesperado ao processar dispositivos do utilizador: %(error)s", error=e), exc_info=True)
            return {"success": False, "message": _("Erro inesperado: %(error)s", error=e)}