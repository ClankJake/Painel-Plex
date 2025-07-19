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

    def get_watch_stats(self, days=7, plex_users_info=None):
        """Obtém as estatísticas de visualização para todos os utilizadores."""
        try:
            after_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            history_response = self.api.get_history(after=after_date)
            
            # CORREÇÃO: A lista de histórico está dentro da chave 'data'.
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

            # CORREÇÃO: A lista de histórico está dentro da chave 'data'.
            history = history_response.get('data', [])
            
            details = {"movie_count": 0, "episode_count": 0, "total_movie_duration": 0, "total_episode_duration": 0, "genre_list": [], "recent": [], "weekly_activity": [0]*7, "top_movies": Counter(), "top_shows": Counter()}
            
            for item in history:
                dt = datetime.fromtimestamp(item.get('date'))
                details["weekly_activity"][(dt.weekday() + 1) % 7] += item.get('duration', 0)
                if item.get("media_type") == 'movie':
                    details["movie_count"] += 1
                    details["total_movie_duration"] += item.get('duration', 0)
                    details["top_movies"][item.get("title")] += 1
                elif item.get("media_type") == 'episode':
                    details["episode_count"] += 1
                    details["total_episode_duration"] += item.get('duration', 0)
                    details["top_shows"][item.get("grandparent_title")] += 1
                if item.get("genres"): details["genre_list"].extend(item.get("genres"))
                if len(details["recent"]) < 5:
                    poster_url = f"{self.api.base_url}/pms_image_proxy?img={item['thumb']}&width=200&height=300&apikey={self.api.api_key}" if item.get('thumb') else ''
                    details["recent"].append({"type": item.get("media_type"), "title": item.get("title"), "series": item.get("grandparent_title"), "poster_url": poster_url, "play_date": dt.strftime('%d/%m/%Y %H:%M')})
            
            details["favorite_genre"] = Counter(details.pop("genre_list")).most_common(1)[0][0] if details["genre_list"] else _('N/D')
            details["top_movies"] = [{'title': title, 'plays': plays} for title, plays in details["top_movies"].most_common(3)]
            details["top_shows"] = [{'title': title, 'plays': plays} for title, plays in details["top_shows"].most_common(3)]

            return {"success": True, "details": details}
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

