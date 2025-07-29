# app/services/tautulli/stats_handler.py
import logging
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from flask_babel import gettext as _
from requests.exceptions import RequestException

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

class StatsHandler:
    """Gere a obtenção e processamento de estatísticas do Tautulli."""

    def __init__(self, api_client, data_manager=None):
        self.api = api_client
        self.data_manager = data_manager

    def _calculate_achievements(self, stats, days, username, current_user):
        """
        Calcula as conquistas de um utilizador. Esta função atribui e guarda as conquistas
        assim que os critérios são cumpridos, mas só notifica o utilizador se ele
        estiver a visualizar o seu próprio perfil.
        """
        if not self.data_manager:
            return []

        config = load_or_create_config()
        
        achievement_definitions = {
            "movie_marathon": {
                "title": _("Maratonista de Cinema"), "icon": "🎬",
                "levels": {
                    "bronze": {"goal": config.get("ACHIEVEMENT_MOVIE_MARATHON_BRONZE", 5), "description": _("Bronze: Assista a %(goal)d filmes nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_MOVIE_MARATHON_BRONZE", 5), days=days)},
                    "silver": {"goal": config.get("ACHIEVEMENT_MOVIE_MARATHON_SILVER", 10), "description": _("Prata: Assista a %(goal)d filmes nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_MOVIE_MARATHON_SILVER", 10), days=days)},
                    "gold": {"goal": config.get("ACHIEVEMENT_MOVIE_MARATHON_GOLD", 20), "description": _("Ouro: Assista a %(goal)d filmes nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_MOVIE_MARATHON_GOLD", 20), days=days)}
                },
                "check": lambda s: s.get("movie_count", 0)
            },
            "series_binger": {
                "title": _("Rei das Séries"), "icon": "📺",
                "levels": {
                    "bronze": {"goal": config.get("ACHIEVEMENT_SERIES_BINGER_BRONZE", 20), "description": _("Bronze: Assista a %(goal)d episódios nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_SERIES_BINGER_BRONZE", 20), days=days)},
                    "silver": {"goal": config.get("ACHIEVEMENT_SERIES_BINGER_SILVER", 50), "description": _("Prata: Assista a %(goal)d episódios nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_SERIES_BINGER_SILVER", 50), days=days)},
                    "gold": {"goal": config.get("ACHIEVEMENT_SERIES_BINGER_GOLD", 100), "description": _("Ouro: Assista a %(goal)d episódios nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_SERIES_BINGER_GOLD", 100), days=days)}
                },
                "check": lambda s: s.get("episode_count", 0)
            },
            "weekend_warrior": {
                "title": _("Guerreiro de Fim de Semana"), "icon": "🎉",
                "levels": { "bronze": { "goal": 0.55, "description": _("Bronze: Mais de 55% do seu tempo de visualização é no fim de semana.") } },
                "check": lambda s: (s.get("weekly_activity_python", [0]*7)[5] + s.get("weekly_activity_python", [0]*7)[6]) / sum(s.get("weekly_activity_python", [])) if sum(s.get("weekly_activity_python", [])) > 0 else 0
            },
            "time_traveler": {
                "title": _("Viajante do Tempo"), "icon": "⏳",
                "levels": {
                    "bronze": {"goal": config.get("ACHIEVEMENT_TIME_TRAVELER_BRONZE", 3), "description": _("Bronze: Assista a filmes de %(goal)d décadas diferentes.", goal=config.get("ACHIEVEMENT_TIME_TRAVELER_BRONZE", 3))},
                    "silver": {"goal": config.get("ACHIEVEMENT_TIME_TRAVELER_SILVER", 5), "description": _("Prata: Assista a filmes de %(goal)d décadas diferentes.", goal=config.get("ACHIEVEMENT_TIME_TRAVELER_SILVER", 5))},
                    "gold": {"goal": config.get("ACHIEVEMENT_TIME_TRAVELER_GOLD", 7), "description": _("Ouro: Assista a filmes de %(goal)d décadas diferentes.", goal=config.get("ACHIEVEMENT_TIME_TRAVELER_GOLD", 7))}
                },
                "check": lambda s: len(s.get("unique_decades", set()))
            },
            "director_fan": {
                "title": _("Fã do Realizador"), "icon": "📽️",
                "levels": {
                    "bronze": {"goal": config.get("ACHIEVEMENT_DIRECTOR_FAN_BRONZE", 3), "description": _("Bronze: Assista a %(goal)d filmes do seu realizador favorito.", goal=config.get("ACHIEVEMENT_DIRECTOR_FAN_BRONZE", 3))},
                    "silver": {"goal": config.get("ACHIEVEMENT_DIRECTOR_FAN_SILVER", 5), "description": _("Prata: Assista a %(goal)d filmes do seu realizador favorito.", goal=config.get("ACHIEVEMENT_DIRECTOR_FAN_SILVER", 5))},
                    "gold": {"goal": config.get("ACHIEVEMENT_DIRECTOR_FAN_GOLD", 7), "description": _("Ouro: Assista a %(goal)d filmes do seu realizador favorito.", goal=config.get("ACHIEVEMENT_DIRECTOR_FAN_GOLD", 7))}
                },
                "check": lambda s: s.get("favorite_director_count", 0)
            }
        }
        
        # 1. Verificar quais conquistas são cumpridas com as estatísticas atuais
        unlocked_in_db = self.data_manager.get_unlocked_achievements(username)
        newly_unlocked = []

        for key, definition in achievement_definitions.items():
            current_value = definition["check"](stats)
            unlocked_level = None
            
            for level in ["gold", "silver", "bronze"]:
                if level in definition["levels"] and current_value >= definition["levels"][level]["goal"]:
                    unlocked_level = level
                    break 
            
            if unlocked_level:
                achievement_id = f"{key}_{unlocked_level}"
                if achievement_id not in unlocked_in_db:
                    newly_unlocked.append({
                        "id": achievement_id,
                        "title": definition["title"],
                        "level": unlocked_level,
                        "icon": definition["icon"]
                    })
        
        # 2. Guardar novas conquistas na base de dados, independentemente de quem está a ver
        if newly_unlocked:
            self.data_manager.add_unlocked_achievements(username, newly_unlocked)
            
            # 3. Notificar o utilizador APENAS se ele estiver a ver o seu próprio perfil
            if current_user and current_user.username == username:
                for ach in newly_unlocked:
                    self.data_manager.create_notification(
                        message=_("Nova conquista desbloqueada: %(title)s (%(level)s)!", username=username, title=ach['title'], level=ach['level']),
                        category='success',
                        link=f"/statistics",
                        username=username
                    )

        # 4. Retornar a lista completa de conquistas da base de dados (agora atualizada)
        final_achievements = []
        all_unlocked_ever = self.data_manager.get_unlocked_achievements(username)
        for ach_id in all_unlocked_ever:
            try:
                base_key, level = ach_id.rsplit('_', 1)
                if base_key in achievement_definitions:
                    definition = achievement_definitions[base_key]
                    final_achievements.append({
                        "id": ach_id,
                        "title": definition["title"],
                        "description": definition["levels"][level]["description"],
                        "icon": definition["icon"],
                        "level": level
                    })
            except ValueError:
                logger.warning(f"ID de conquista mal formado encontrado na base de dados: '{ach_id}' para o utilizador '{username}'.")

        return final_achievements

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

    def get_user_watch_details(self, username, days=7, current_user=None):
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
                "max_movie_duration": 0, "unique_platforms": set(),
                "director_counts": Counter(), "unique_decades": set()
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
                    if item.get("directors"):
                        stats["director_counts"].update(item.get("directors"))
                    if item.get("year"):
                        stats["unique_decades"].add(f"{item.get('year') // 10}0s")

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

            favorite_director_info = stats["director_counts"].most_common(1)
            stats["favorite_director_count"] = favorite_director_info[0][1] if favorite_director_info else 0

            achievements = self._calculate_achievements(stats, days, username, current_user)

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
    
    def get_recently_added(self, days=7):
        """Obtém os itens adicionados recentemente do Tautulli e filtra por data."""
        try:
            response = self.api.get_recently_added(count=250)
            
            all_media = response.get('recently_added', [])
            
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            filtered_media = []
            for item in all_media:
                added_at_ts = item.get('added_at')
                if added_at_ts:
                    added_at = datetime.fromtimestamp(int(added_at_ts), tz=timezone.utc)
                    if added_at >= cutoff_date:
                        thumb_key = item.get('thumb')
                        if item.get('media_type') == 'episode' and item.get('grandparent_thumb'):
                            thumb_key = item.get('grandparent_thumb')
                        
                        poster_url = f"{self.api.base_url}/pms_image_proxy?img={thumb_key}&width=300&height=450&apikey={self.api.api_key}" if thumb_key else ''
                        
                        def safe_int(value, default=0):
                            try:
                                return int(float(value)) if value else default
                            except (ValueError, TypeError):
                                return default

                        filtered_media.append({
                            'title': item.get('title'),
                            'year': item.get('year'),
                            'poster_url': poster_url,
                            'added_at': added_at.isoformat(),
                            'media_type': item.get('media_type'),
                            'grandparent_title': item.get('grandparent_title'),
                            'parent_title': item.get('parent_title'),
                            'media_index': safe_int(item.get('media_index')),
                            'parent_media_index': safe_int(item.get('parent_media_index'))
                        })

            return {"success": True, "media": filtered_media}

        except RequestException as e:
            return {"success": False, "message": _("Erro de conexão com o Tautulli: %(error)s", error=str(e))}
        except Exception as e:
            logger.error(_("Erro inesperado ao buscar itens adicionados recentemente: %(error)s", error=str(e)), exc_info=True)
            return {"success": False, "message": str(e)}

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
