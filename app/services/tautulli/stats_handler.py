# app/services/tautulli/stats_handler.py
import logging
import base64
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from flask_babel import gettext as _
from requests.exceptions import RequestException
from flask import url_for

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

def _safe_int_float(value, default=0):
    """Converte um valor para int de forma segura, lidando com strings vazias ou None."""
    if value is None or value == '':
        return default
    try:
        # Tenta converter para float primeiro para lidar com casos como "1.0"
        return int(float(value))
    except (ValueError, TypeError):
        return default

class StatsHandler:
    def __init__(self, api_client, data_manager=None):
        self.api = api_client
        self.data_manager = data_manager

    def _calculate_achievements(self, stats, days, plex_user_id, username, current_user):
        if not self.data_manager: return []
        config = load_or_create_config()
        
        achievement_definitions = {
            "movie_marathon": {"title": _("Maratonista de Cinema"), "icon": "🎬", "levels": { "bronze": {"goal": config.get("ACHIEVEMENT_MOVIE_MARATHON_BRONZE", 5), "description": _("Bronze: Assista a %(goal)d filmes nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_MOVIE_MARATHON_BRONZE", 5), days=days)}, "silver": {"goal": config.get("ACHIEVEMENT_MOVIE_MARATHON_SILVER", 10), "description": _("Prata: Assista a %(goal)d filmes nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_MOVIE_MARATHON_SILVER", 10), days=days)}, "gold": {"goal": config.get("ACHIEVEMENT_MOVIE_MARATHON_GOLD", 20), "description": _("Ouro: Assista a %(goal)d filmes nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_MOVIE_MARATHON_GOLD", 20), days=days)} }, "check": lambda s: s.get("movie_count", 0) },
            "series_binger": {"title": _("Rei das Séries"), "icon": "📺", "levels": { "bronze": {"goal": config.get("ACHIEVEMENT_SERIES_BINGER_BRONZE", 20), "description": _("Bronze: Assista a %(goal)d episódios nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_SERIES_BINGER_BRONZE", 20), days=days)}, "silver": {"goal": config.get("ACHIEVEMENT_SERIES_BINGER_SILVER", 50), "description": _("Prata: Assista a %(goal)d episódios nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_SERIES_BINGER_SILVER", 50), days=days)}, "gold": {"goal": config.get("ACHIEVEMENT_SERIES_BINGER_GOLD", 100), "description": _("Ouro: Assista a %(goal)d episódios nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_SERIES_BINGER_GOLD", 100), days=days)} }, "check": lambda s: s.get("episode_count", 0) },
            "weekend_warrior": {"title": _("Guerreiro de Fim de Semana"), "icon": "🎉", "levels": { "bronze": { "goal": 0.55, "description": _("Bronze: Mais de 55%% do seu tempo de visualização é no fim de semana.") } }, "check": lambda s: (s.get("weekly_activity_python", [0]*7)[5] + s.get("weekly_activity_python", [0]*7)[6]) / sum(s.get("weekly_activity_python", [])) if sum(s.get("weekly_activity_python", [])) > 0 else 0 },
            "time_traveler": {"title": _("Viajante do Tempo"), "icon": "⏳", "levels": { "bronze": {"goal": config.get("ACHIEVEMENT_TIME_TRAVELER_BRONZE", 3), "description": _("Bronze: Assista a filmes de %(goal)d décadas diferentes.", goal=config.get("ACHIEVEMENT_TIME_TRAVELER_BRONZE", 3))}, "silver": {"goal": config.get("ACHIEVEMENT_TIME_TRAVELER_SILVER", 5), "description": _("Prata: Assista a filmes de %(goal)d décadas diferentes.", goal=config.get("ACHIEVEMENT_TIME_TRAVELER_SILVER", 5))}, "gold": {"goal": config.get("ACHIEVEMENT_TIME_TRAVELER_GOLD", 7), "description": _("Ouro: Assista a filmes de %(goal)d décadas diferentes.", goal=config.get("ACHIEVEMENT_TIME_TRAVELER_GOLD", 7))} }, "check": lambda s: len(s.get("unique_decades", set())) },
            "director_fan": {"title": _("Fã do Realizador"), "icon": "🎥", "levels": { "bronze": {"goal": config.get("ACHIEVEMENT_DIRECTOR_FAN_BRONZE", 3), "description": _("Bronze: Assista a %(goal)d filmes do seu realizador favorito.", goal=config.get("ACHIEVEMENT_DIRECTOR_FAN_BRONZE", 3))}, "silver": {"goal": config.get("ACHIEVEMENT_DIRECTOR_FAN_SILVER", 5), "description": _("Prata: Assista a %(goal)d filmes do seu realizador favorito.", goal=config.get("ACHIEVEMENT_DIRECTOR_FAN_SILVER", 5))}, "gold": {"goal": config.get("ACHIEVEMENT_DIRECTOR_FAN_GOLD", 7), "description": _("Ouro: Assista a %(goal)d filmes do seu realizador favorito.", goal=config.get("ACHIEVEMENT_DIRECTOR_FAN_GOLD", 7))} }, "check": lambda s: s.get("favorite_director_count", 0) }
        }
        
        unlocked_in_db = self.data_manager.get_unlocked_achievements(plex_user_id)
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
                    newly_unlocked.append({"id": achievement_id, "title": definition["title"], "level": unlocked_level, "icon": definition["icon"]})
        
        if newly_unlocked:
            self.data_manager.add_unlocked_achievements(plex_user_id, username, newly_unlocked)
            if current_user and current_user.id == str(plex_user_id):
                for ach in newly_unlocked:
                    # CORREÇÃO: Alterado 'plex_user_id=' para 'user_plex_id=' para corresponder à definição da função.
                    self.data_manager.create_notification(message=_("Nova conquista desbloqueada: %(title)s (%(level)s)!", title=ach['title'], level=ach['level']), category='success', link="/statistics", user_plex_id=plex_user_id)

        final_achievements = []
        all_unlocked_ever = self.data_manager.get_unlocked_achievements(plex_user_id)
        for ach_id in all_unlocked_ever:
            try:
                base_key, level = ach_id.rsplit('_', 1)
                if base_key in achievement_definitions:
                    definition = achievement_definitions[base_key]
                    final_achievements.append({"id": ach_id, "title": definition["title"], "description": definition["levels"][level]["description"], "icon": definition["icon"], "level": level})
            except ValueError:
                pass
        return final_achievements

    def get_watch_stats(self, days=7, plex_users_info=None):
        try:
            after_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            history_response = self.api.get_history(after=after_date)
            history_data = history_response.get('data', [])
            
            user_stats = defaultdict(lambda: {"plays": 0, "total_duration": 0})
            for item in history_data:
                user_id = item.get("user_id")
                if user_id:
                    user_stats[user_id]["plays"] += 1
                    user_stats[user_id]["total_duration"] += item.get("duration", 0)
                    user_stats[user_id]["username"] = item.get("user")
            
            formatted_stats = []
            for user_id, details in user_stats.items():
                username = details['username']
                formatted_stats.append({
                    'user_id': user_id, 'username': username, 'plays': details['plays'], 'total_duration': details['total_duration'],
                    'avg_duration': details['total_duration'] / details['plays'] if details['plays'] > 0 else 0,
                    'thumb': plex_users_info.get(user_id) if plex_users_info else None
                })
            
            return {"success": True, "stats": sorted(formatted_stats, key=lambda x: x['total_duration'], reverse=True)}
        except RequestException as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def get_user_watch_details(self, plex_user_id, username, days=7, current_user=None):
        try:
            if not isinstance(days, int):
                days = int(days)

            after_date_str = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            history_response = self.api.get_history(after=after_date_str, user_id=plex_user_id)
            history = history_response.get('data', [])
            
            if not history:
                return {"success": True, "details": {}}
            
            stats = defaultdict(lambda: 0, {"genre_counts": Counter(), "recent": [], "weekly_activity_python": [0]*7, "weekly_activity_js": [0]*7, "top_movies": Counter(), "top_shows": Counter(), "unique_genres": set(), "unique_days": set(), "unique_platforms": set(), "director_counts": Counter(), "unique_decades": set()})
            series_genre_cache = {}

            for item in history:
                dt = datetime.fromtimestamp(item.get('date'))
                stats["weekly_activity_python"][dt.weekday()] += item.get('duration', 0)
                stats["weekly_activity_js"][(dt.weekday() + 1) % 7] += item.get('duration', 0)
                stats["unique_days"].add(dt.date())
                if item.get("platform"): stats["unique_platforms"].add(item.get("platform"))
                if 0 <= dt.hour < 4: stats["late_night_plays"] += 1
                
                item_genres = item.get("genres")
                if not item_genres and item.get("media_type") == 'episode':
                    grandparent_rating_key = item.get('grandparent_rating_key')
                    if grandparent_rating_key:
                        if grandparent_rating_key in series_genre_cache:
                            item_genres = series_genre_cache[grandparent_rating_key]
                        else:
                            try:
                                metadata = self.api.get_metadata(grandparent_rating_key)
                                if metadata and metadata.get('genres'):
                                    item_genres = metadata['genres']
                                series_genre_cache[grandparent_rating_key] = item_genres or []
                            except RequestException:
                                series_genre_cache[grandparent_rating_key] = []
                
                if item_genres and isinstance(item_genres, list):
                    stats["genre_counts"].update(item_genres)
                    stats["unique_genres"].update(item_genres)

                if item.get("media_type") == 'movie':
                    stats["movie_count"] += 1
                    stats["total_movie_duration"] += item.get('duration', 0)
                    stats["top_movies"][item.get("title")] += 1
                    if item.get("directors"): stats["director_counts"].update(item.get("directors"))
                    year = _safe_int_float(item.get("year"))
                    if year > 0:
                        stats["unique_decades"].add(f"{year // 10}0s")
                elif item.get("media_type") == 'episode':
                    stats["episode_count"] += 1
                    stats["total_episode_duration"] += item.get('duration', 0)
                    stats["top_shows"][item.get("grandparent_title")] += 1
                
                if len(stats["recent"]) < 9:
                    poster_url = None
                    if item.get('thumb'):
                        tautulli_path = f"/pms_image_proxy?img={item['thumb']}&width=200&height=300"
                        payload_str = f"tautulli:{tautulli_path}"
                        b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                        poster_url = url_for('image.proxy_image', source=b64_payload)
                    stats["recent"].append({"type": item.get("media_type"), "title": item.get("title"), "series": item.get("grandparent_title"), "poster_url": poster_url, "play_date": dt.strftime('%d/%m/%Y %H:%M')})
            
            stats["favorite_genre"] = stats["genre_counts"].most_common(1)[0][0] if stats["genre_counts"] else _('N/D')
            favorite_director_info = stats["director_counts"].most_common(1)
            stats["favorite_director_count"] = favorite_director_info[0][1] if favorite_director_info else 0
            
            achievements = self._calculate_achievements(stats, days, plex_user_id, username, current_user)
            
            final_stats = {key: value for key, value in stats.items()}
            for key in ['unique_genres', 'unique_platforms', 'unique_decades']:
                if isinstance(final_stats.get(key), set):
                    final_stats[key] = list(final_stats[key])
            if isinstance(final_stats.get('unique_days'), set):
                 final_stats['unique_days'] = [d.isoformat() for d in final_stats['unique_days']]

            final_stats["achievements"] = achievements
            
            return {"success": True, "details": final_stats}
        except RequestException as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"Erro inesperado ao processar detalhes do utilizador: {e}", exc_info=True)
            return {"success": False, "message": str(e)}
    
    def get_user_watch_history(self, user_id, page=1, length=25, search=""):
        try:
            history_response = self.api.get_history(
                user_id=user_id,
                start=(page - 1) * length,
                length=length,
                search=search
            )
            
            history_data = history_response.get('data', [])
            total_count = history_response.get('recordsFiltered', 0)
            
            processed_history = []
            for item in history_data:
                poster_url = None
                thumb_key = item.get('thumb')
                if item.get('media_type') == 'episode' and item.get('grandparent_thumb'):
                    thumb_key = item.get('grandparent_thumb')

                if thumb_key:
                    tautulli_path = f"/pms_image_proxy?img={thumb_key}&width=200&height=300"
                    payload_str = f"tautulli:{tautulli_path}"
                    b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                    poster_url = url_for('image.proxy_image', source=b64_payload)
                
                title = item.get("title")
                subtitle = str(item.get("year")) if item.get("year") else ""
                if item.get("media_type") == 'episode':
                    title = item.get("grandparent_title")
                    parent_index = _safe_int_float(item.get('parent_media_index', 0))
                    media_index = _safe_int_float(item.get('media_index', 0))
                    subtitle = f"S{parent_index:02d} · E{media_index:02d} - {item.get('title')}"

                processed_history.append({
                    "title": title,
                    "subtitle": subtitle,
                    "date": datetime.fromtimestamp(item.get('date')).strftime('%d/%m/%Y %H:%M'),
                    "player": item.get('player'),
                    "percent_complete": item.get('percent_complete'),
                    "poster_url": poster_url,
                })

            return {
                "success": True,
                "history": processed_history,
                "pagination": {
                    "current_page": page,
                    "total_pages": (total_count // length) + (1 if total_count % length > 0 else 0),
                    "total_records": total_count,
                }
            }
        except RequestException as e:
            logger.error(f"Erro de API ao buscar histórico para user_id {user_id}: {e}", exc_info=True)
            return {"success": False, "message": f"Erro de comunicação com o Tautulli: {e}"}
        except Exception as e:
            logger.error(f"Erro inesperado ao buscar histórico para user_id {user_id}: {e}", exc_info=True)
            return {"success": False, "message": f"Erro inesperado: {e}"}
            
    def get_recently_added(self, days=7):
        try:
            response = self.api.get_recently_added(count=250)
            all_media = response.get('recently_added', [])
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            filtered_media = []
            for item in all_media:
                added_at = datetime.fromtimestamp(int(item.get('added_at')), tz=timezone.utc) if item.get('added_at') else None
                if added_at and added_at >= cutoff_date:
                    thumb_key = item.get('thumb')
                    if item.get('media_type') == 'episode' and item.get('grandparent_thumb'): thumb_key = item.get('grandparent_thumb')
                    poster_url = None
                    if thumb_key:
                        tautulli_path = f"/pms_image_proxy?img={thumb_key}&width=300&height=450"
                        payload_str = f"tautulli:{tautulli_path}"
                        b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                        poster_url = url_for('image.proxy_image', source=b64_payload)
                    filtered_media.append({ 
                        'title': item.get('title'), 
                        'year': item.get('year'), 
                        'poster_url': poster_url, 
                        'added_at': added_at.isoformat(), 
                        'media_type': item.get('media_type'), 
                        'grandparent_title': item.get('grandparent_title'), 
                        'parent_title': item.get('parent_title'), 
                        'media_index': _safe_int_float(item.get('media_index')), 
                        'parent_media_index': _safe_int_float(item.get('parent_media_index'))
                    })
            return {"success": True, "media": filtered_media}
        except RequestException as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def get_user_devices(self, plex_user_id):
        try:
            history_response = self.api.get_history(user_id=plex_user_id, length=500)
            history = history_response.get('data', [])
            if not history: return {"success": True, "devices": []}
            devices = defaultdict(lambda: {'platform': '', 'last_seen': 0})
            for item in history:
                device_key = f"{item.get('player', 'Desconhecido')}|{item.get('platform', 'Desconhecida')}"
                if item.get('date') > devices[device_key]['last_seen']:
                    devices[device_key].update({'player': item.get('player', 'Desconhecido'), 'platform': item.get('platform', 'Desconhecida'), 'last_seen': item.get('date')})
            return {"success": True, "devices": sorted(list(devices.values()), key=lambda x: x['last_seen'], reverse=True)}
        except RequestException as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": str(e)}