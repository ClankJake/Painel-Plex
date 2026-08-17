# app/services/tautulli/stats_handler.py

import logging
import base64
from datetime import datetime, timedelta, timezone
from collections import Counter
from typing import Dict, List, Any, Optional

from flask_babel import gettext as _
from requests.exceptions import RequestException
from flask import url_for

from app.config import load_or_create_config

logger = logging.getLogger(__name__)


def _safe_int_float(value: Any, default: int = 0) -> int:
    """Converte um valor para int de forma segura, lidando com strings vazias, nulas ou objetos inválidos."""
    if value is None or value == '':
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError, OverflowError):
        return default


class StatsHandler:
    """Gere toda a lógica de análise de dados e gamificação baseada na API do Tautulli."""
    
    def __init__(self, api_client, data_manager=None):
        self.api = api_client
        self.data_manager = data_manager

    def _get_achievement_definitions(self, days: int) -> Dict[str, Any]:
        """Retorna as definições dinâmicas de conquistas baseadas nas configurações atuais."""
        config = load_or_create_config()
        return {
            "movie_marathon": {
                "title": _("Maratonista de Cinema"), 
                "icon": "🎬", 
                "levels": { 
                    "bronze": {"goal": config.get("ACHIEVEMENT_MOVIE_MARATHON_BRONZE", 5), "description": _("Bronze: Assista a %(goal)d filmes nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_MOVIE_MARATHON_BRONZE", 5), days=days)}, 
                    "silver": {"goal": config.get("ACHIEVEMENT_MOVIE_MARATHON_SILVER", 10), "description": _("Prata: Assista a %(goal)d filmes nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_MOVIE_MARATHON_SILVER", 10), days=days)}, 
                    "gold": {"goal": config.get("ACHIEVEMENT_MOVIE_MARATHON_GOLD", 20), "description": _("Ouro: Assista a %(goal)d filmes nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_MOVIE_MARATHON_GOLD", 20), days=days)} 
                }, 
                "check": lambda s: s.get("movie_count", 0) 
            },
            "series_binger": {
                "title": _("Rei das Séries"), 
                "icon": "📺", 
                "levels": { 
                    "bronze": {"goal": config.get("ACHIEVEMENT_SERIES_BINGER_BRONZE", 20), "description": _("Bronze: Assista a %(goal)d episódios nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_SERIES_BINGER_BRONZE", 20), days=days)}, 
                    "silver": {"goal": config.get("ACHIEVEMENT_SERIES_BINGER_SILVER", 50), "description": _("Prata: Assista a %(goal)d episódios nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_SERIES_BINGER_SILVER", 50), days=days)}, 
                    "gold": {"goal": config.get("ACHIEVEMENT_SERIES_BINGER_GOLD", 100), "description": _("Ouro: Assista a %(goal)d episódios nos últimos %(days)d dias.", goal=config.get("ACHIEVEMENT_SERIES_BINGER_GOLD", 100), days=days)} 
                }, 
                "check": lambda s: s.get("episode_count", 0) 
            },
            "weekend_warrior": {
                "title": _("Guerreiro de Fim de Semana"), 
                "icon": "🎉", 
                "levels": { 
                    "bronze": { "goal": 0.55, "description": _("Bronze: Mais de 55%% do seu tempo de visualização é no fim de semana.") } 
                }, 
                "check": lambda s: (s["weekly_activity_python"][5] + s["weekly_activity_python"][6]) / sum(s["weekly_activity_python"]) if sum(s["weekly_activity_python"]) > 0 else 0 
            },
            "time_traveler": {
                "title": _("Viajante do Tempo"), 
                "icon": "⏳", 
                "levels": { 
                    "bronze": {"goal": config.get("ACHIEVEMENT_TIME_TRAVELER_BRONZE", 3), "description": _("Bronze: Assista a filmes de %(goal)d décadas diferentes.", goal=config.get("ACHIEVEMENT_TIME_TRAVELER_BRONZE", 3))}, 
                    "silver": {"goal": config.get("ACHIEVEMENT_TIME_TRAVELER_SILVER", 5), "description": _("Prata: Assista a filmes de %(goal)d décadas diferentes.", goal=config.get("ACHIEVEMENT_TIME_TRAVELER_SILVER", 5))}, 
                    "gold": {"goal": config.get("ACHIEVEMENT_TIME_TRAVELER_GOLD", 7), "description": _("Ouro: Assista a filmes de %(goal)d décadas diferentes.", goal=config.get("ACHIEVEMENT_TIME_TRAVELER_GOLD", 7))} 
                }, 
                "check": lambda s: len(s.get("unique_decades", [])) 
            },
            "director_fan": {
                "title": _("Fã do Realizador"), 
                "icon": "🎥", 
                "levels": { 
                    "bronze": {"goal": config.get("ACHIEVEMENT_DIRECTOR_FAN_BRONZE", 3), "description": _("Bronze: Assista a %(goal)d filmes do seu realizador favorito.", goal=config.get("ACHIEVEMENT_DIRECTOR_FAN_BRONZE", 3))}, 
                    "silver": {"goal": config.get("ACHIEVEMENT_DIRECTOR_FAN_SILVER", 5), "description": _("Prata: Assista a %(goal)d filmes do seu realizador favorito.", goal=config.get("ACHIEVEMENT_DIRECTOR_FAN_SILVER", 5))}, 
                    "gold": {"goal": config.get("ACHIEVEMENT_DIRECTOR_FAN_GOLD", 7), "description": _("Ouro: Assista a %(goal)d filmes do seu realizador favorito.", goal=config.get("ACHIEVEMENT_DIRECTOR_FAN_GOLD", 7))} 
                }, 
                "check": lambda s: s.get("favorite_director_count", 0) 
            },
            "night_owl": {
                "title": _("Coruja"), 
                "icon": "🦉", 
                "levels": { 
                    "bronze": {"goal": config.get("ACHIEVEMENT_NIGHT_OWL_BRONZE", 3), "description": _("Bronze: Assista a %(goal)d sessões entre as 2h e as 5h da manhã.", goal=config.get("ACHIEVEMENT_NIGHT_OWL_BRONZE", 3))}, 
                    "silver": {"goal": config.get("ACHIEVEMENT_NIGHT_OWL_SILVER", 10), "description": _("Prata: Assista a %(goal)d sessões entre as 2h e as 5h da manhã.", goal=config.get("ACHIEVEMENT_NIGHT_OWL_SILVER", 10))}, 
                    "gold": {"goal": config.get("ACHIEVEMENT_NIGHT_OWL_GOLD", 25), "description": _("Ouro: Assista a %(goal)d sessões entre as 2h e as 5h da manhã.", goal=config.get("ACHIEVEMENT_NIGHT_OWL_GOLD", 25))} 
                }, 
                "check": lambda s: s.get("night_owl_session_count", 0) 
            },
            "pioneer": {
                "title": _("Pioneiro"), 
                "icon": "🚀", 
                "levels": { 
                    "bronze": {"goal": config.get("ACHIEVEMENT_PIONEER_BRONZE", 1), "description": _("Bronze: Seja um dos primeiros a assistir a %(goal)d lançamento(s) recente(s) do servidor (nas primeiras %(hours)dh após adicionado).", goal=config.get("ACHIEVEMENT_PIONEER_BRONZE", 1), hours=config.get("ACHIEVEMENT_PIONEER_WINDOW_HOURS", 48))}, 
                    "silver": {"goal": config.get("ACHIEVEMENT_PIONEER_SILVER", 5), "description": _("Prata: Seja um dos primeiros a assistir a %(goal)d lançamentos recentes do servidor.", goal=config.get("ACHIEVEMENT_PIONEER_SILVER", 5))}, 
                    "gold": {"goal": config.get("ACHIEVEMENT_PIONEER_GOLD", 15), "description": _("Ouro: Seja um dos primeiros a assistir a %(goal)d lançamentos recentes do servidor.", goal=config.get("ACHIEVEMENT_PIONEER_GOLD", 15))} 
                }, 
                "check": lambda s: s.get("pioneer_count", 0) 
            }
        }

    def _calculate_achievements(self, stats: Dict[str, Any], days: int, plex_user_id: str, username: str, current_user: Any) -> List[Dict[str, Any]]:
        if not self.data_manager: 
            return []
            
        achievement_definitions = self._get_achievement_definitions(days)
        unlocked_in_db = self.data_manager.get_unlocked_achievements(plex_user_id)
        newly_unlocked = []

        for key, definition in achievement_definitions.items():
            current_value = definition["check"](stats)
            unlocked_level = None
            
            # Testa as regras começando do Ouro e descendo para o Bronze
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
        
        if newly_unlocked:
            self.data_manager.add_unlocked_achievements(plex_user_id, username, newly_unlocked)
            # Notifica apenas se for o próprio a visualizar as suas estatísticas
            if current_user and str(current_user.id) == str(plex_user_id):
                for ach in newly_unlocked:
                    self.data_manager.create_notification(
                        message=_("Nova conquista desbloqueada: %(title)s (%(level)s)!", title=ach['title'], level=ach['level']), 
                        category='success', link="/statistics", user_plex_id=plex_user_id
                    )

        final_achievements = []
        all_unlocked_ever = self.data_manager.get_unlocked_achievements(plex_user_id)
        
        for ach_id in all_unlocked_ever:
            try:
                base_key, level = ach_id.rsplit('_', 1)
                if base_key in achievement_definitions and level in achievement_definitions[base_key]["levels"]:
                    definition = achievement_definitions[base_key]
                    final_achievements.append({
                        "id": ach_id, 
                        "title": definition["title"], 
                        "description": definition["levels"][level]["description"], 
                        "icon": definition["icon"], 
                        "level": level
                    })
            except ValueError:
                continue
                
        return final_achievements

    def get_watch_stats(self, days: int = 7, plex_users_info: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Devolve o top visualizadores de todos os utilizadores (Estatística Global)."""
        try:
            after_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d')
            history_response = self.api.get_history(after=after_date)
            history_data = history_response.get('data', [])
            
            user_stats = {}
            for item in history_data:
                user_id = item.get("user_id")
                if user_id:
                    if user_id not in user_stats:
                        user_stats[user_id] = {"plays": 0, "total_duration": 0, "username": item.get("user")}
                    user_stats[user_id]["plays"] += 1
                    user_stats[user_id]["total_duration"] += item.get("duration", 0)
            
            formatted_stats = [
                {
                    'user_id': uid, 
                    'username': details['username'], 
                    'plays': details['plays'], 
                    'total_duration': details['total_duration'],
                    'avg_duration': details['total_duration'] / details['plays'] if details['plays'] > 0 else 0,
                    'thumb': plex_users_info.get(uid) if plex_users_info else None
                }
                for uid, details in user_stats.items()
            ]
            
            return {"success": True, "stats": sorted(formatted_stats, key=lambda x: x['total_duration'], reverse=True)}
        except RequestException as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"Erro em get_watch_stats: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    def get_user_watch_details(self, plex_user_id: str, username: str, days: int = 7, current_user: Any = None) -> Dict[str, Any]:
        """Devolve as métricas profundas de um utilizador específico."""
        try:
            days = int(days)
            after_date_str = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d')
            history_response = self.api.get_history(after=after_date_str, user_id=plex_user_id)
            history = history_response.get('data', [])
            
            if not history:
                return {"success": True, "details": {}}
            
            stats = self._initialize_stats_dict()
            series_genre_cache = {}
            recently_added_lookup = self._get_recently_added_lookup()

            for item in history:
                self._process_history_item(item, stats, series_genre_cache, recently_added_lookup)

            self._finalize_stats(stats)
            
            stats["achievements"] = self._calculate_achievements(stats, days, plex_user_id, username, current_user)
            
            return {"success": True, "details": stats}
        except RequestException as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"Erro inesperado ao processar detalhes do utilizador: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    def _get_recently_added_lookup(self) -> Dict[str, int]:
        """
        Constrói um dicionário {rating_key: added_at_timestamp} com os itens
        recentemente adicionados ao servidor, usado para calcular a conquista
        "Pioneiro". Busca uma janela ampla (90 dias) para cobrir o histórico
        de visualização mais longo possível sem precisar de uma chamada extra
        por item assistido (só 1 chamada no total, reaproveitando get_recently_added).

        Falha de forma segura: se a API do Tautulli não devolver o formato
        esperado, devolve um dicionário vazio (a conquista "Pioneiro" simplesmente
        não é concedida nesta execução, sem quebrar o resto das estatísticas).
        """
        try:
            config = load_or_create_config()
            window_hours = config.get("ACHIEVEMENT_PIONEER_WINDOW_HOURS", 48)
            # Busca uma janela generosa (90 dias) de itens adicionados, para cobrir
            # o período de histórico consultado pelo utilizador.
            response = self.api.get_recently_added(count=500)
            items = response.get('recently_added', []) if isinstance(response, dict) else []

            lookup = {}
            for item in items:
                rating_key = item.get('rating_key')
                added_at = _safe_int_float(item.get('added_at'), default=0)
                if rating_key and added_at > 0:
                    lookup[str(rating_key)] = added_at
            lookup['_window_hours'] = window_hours
            return lookup
        except Exception as e:
            logger.debug(f"Não foi possível construir a lista de 'recém-adicionados' para a conquista Pioneiro: {e}")
            return {}

    # --- HELPERS DA LÓGICA DE DETALHES ---

    def _initialize_stats_dict(self) -> Dict[str, Any]:
        """Inicializa as variáveis com estruturas de dados explícitas e limpas."""
        return {
            "plays": 0,
            "total_duration": 0,
            "movie_count": 0,
            "total_movie_duration": 0,
            "episode_count": 0,
            "total_episode_duration": 0,
            "late_night_plays": 0,
            "night_owl_session_count": 0,
            "pioneer_count": 0,
            "genre_counts": Counter(),
            "recent": [],
            "weekly_activity_python": [0] * 7,
            "weekly_activity_js": [0] * 7,
            "top_movies": Counter(),
            "top_shows": Counter(),
            "unique_genres": set(),
            "unique_days": set(),
            "unique_platforms": set(),
            "director_counts": Counter(),
            "unique_decades": set()
        }

    def _process_history_item(self, item: Dict[str, Any], stats: Dict[str, Any], series_genre_cache: Dict[str, Any], recently_added_lookup: Optional[Dict[str, int]] = None) -> None:
        """Trata cada entrada do histórico alimentando as métricas parciais."""
        dt = datetime.fromtimestamp(item.get('date', 0), tz=timezone.utc)
        duration = item.get('duration', 0)
        media_type = item.get("media_type")

        stats["plays"] += 1
        stats["total_duration"] += duration
        stats["weekly_activity_python"][dt.weekday()] += duration
        stats["weekly_activity_js"][(dt.weekday() + 1) % 7] += duration
        stats["unique_days"].add(dt.date())
        
        if item.get("platform"): 
            stats["unique_platforms"].add(item.get("platform"))
            
        if 0 <= dt.hour < 4: 
            stats["late_night_plays"] += 1

        # 🦉 Conquista "Coruja": sessões assistidas entre as 2h e as 5h da manhã.
        if 2 <= dt.hour < 5:
            stats["night_owl_session_count"] += 1

        # 🚀 Conquista "Pioneiro": assistiu a algo pouco tempo depois de ser
        # adicionado ao servidor (não confundir com data de lançamento/estreia).
        if recently_added_lookup:
            rating_key = str(item.get('rating_key') or '')
            added_at_ts = recently_added_lookup.get(rating_key)
            if added_at_ts:
                window_hours = recently_added_lookup.get('_window_hours', 48)
                watched_ts = item.get('date', 0)
                elapsed_hours = (watched_ts - added_at_ts) / 3600
                if 0 <= elapsed_hours <= window_hours:
                    stats["pioneer_count"] += 1
            
        item_genres = item.get("genres")
        if not item_genres and media_type == 'episode':
            grandparent_rating_key = item.get('grandparent_rating_key')
            if grandparent_rating_key:
                if grandparent_rating_key not in series_genre_cache:
                    try:
                        metadata = self.api.get_metadata(grandparent_rating_key)
                        series_genre_cache[grandparent_rating_key] = metadata.get('genres', []) if metadata else []
                    except RequestException:
                        series_genre_cache[grandparent_rating_key] = []
                item_genres = series_genre_cache[grandparent_rating_key]
                
        if item_genres and isinstance(item_genres, list):
            stats["genre_counts"].update(item_genres)
            stats["unique_genres"].update(item_genres)

        if media_type == 'movie':
            stats["movie_count"] += 1
            stats["total_movie_duration"] += duration
            stats["top_movies"][item.get("title")] += 1
            
            if item.get("directors"): 
                stats["director_counts"].update(item.get("directors"))
                
            year = _safe_int_float(item.get("year"))
            if year > 0:
                stats["unique_decades"].add(f"{year // 10}0s")
                
        elif media_type == 'episode':
            stats["episode_count"] += 1
            stats["total_episode_duration"] += duration
            stats["top_shows"][item.get("grandparent_title")] += 1
            
        if len(stats["recent"]) < 9:
            poster_url = None
            if item.get('thumb'):
                tautulli_path = f"/pms_image_proxy?img={item['thumb']}&width=200&height=300"
                payload_str = f"tautulli:{tautulli_path}"
                b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                
                # 🛡️ Fallback Robusto: Previne crash quando não há contexto HTTP (ex: Background Thread)
                try:
                    poster_url = url_for('image.proxy_image', source=b64_payload)
                except RuntimeError:
                    poster_url = f"/image/?source={b64_payload}"
            
            stats["recent"].append({
                "type": media_type, 
                "title": item.get("title"), 
                "series": item.get("grandparent_title"), 
                "poster_url": poster_url, 
                "play_date": dt.strftime('%d/%m/%Y %H:%M')
            })

    def _finalize_stats(self, stats: Dict[str, Any]) -> None:
        """Converte Sets para Listas de forma ao JSON serializar para a API final."""
        stats["favorite_genre"] = stats["genre_counts"].most_common(1)[0][0] if stats["genre_counts"] else _('N/D')
        
        favorite_director_info = stats["director_counts"].most_common(1)
        stats["favorite_director_count"] = favorite_director_info[0][1] if favorite_director_info else 0
        
        stats['unique_genres'] = list(stats['unique_genres'])
        stats['unique_platforms'] = list(stats['unique_platforms'])
        stats['unique_decades'] = list(stats['unique_decades'])
        stats['unique_days'] = [d.isoformat() for d in stats['unique_days']]

    # --- RESTANTES MÉTODOS PÚBLICOS ---

    def get_user_watch_history(self, user_id: str, page: int = 1, length: int = 25, search: str = "") -> Dict[str, Any]:
        try:
            history_response = self.api.get_history(
                user_id=user_id, start=(page - 1) * length, length=length, search=search
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
                    
                    # 🛡️ Fallback Robusto: URL Estática para processos Assíncronos
                    try:
                        poster_url = url_for('image.proxy_image', source=b64_payload)
                    except RuntimeError:
                        poster_url = f"/image/?source={b64_payload}"
                
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
                    "date": datetime.fromtimestamp(item.get('date', 0), tz=timezone.utc).strftime('%d/%m/%Y %H:%M'),
                    "player": item.get('player'),
                    "percent_complete": item.get('percent_complete'),
                    "poster_url": poster_url,
                })

            total_pages = (total_count + length - 1) // length if length > 0 else 1

            return {
                "success": True,
                "history": processed_history,
                "pagination": {
                    "current_page": page,
                    "total_pages": total_pages,
                    "total_records": total_count,
                }
            }
        except RequestException as e:
            logger.error(f"Erro de API ao buscar histórico para user_id {user_id}: {e}")
            return {"success": False, "message": f"Erro de comunicação com o Tautulli: {e}"}
        except Exception as e:
            logger.error(f"Erro inesperado ao buscar histórico para user_id {user_id}: {e}", exc_info=True)
            return {"success": False, "message": f"Erro inesperado: {e}"}

    def get_recently_added(self, days: int = 7) -> Dict[str, Any]:
        try:
            response = self.api.get_recently_added(count=250)
            all_media = response.get('recently_added', [])
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            filtered_media = []
            
            for item in all_media:
                if not item.get('added_at'):
                    continue
                    
                added_at = datetime.fromtimestamp(int(item.get('added_at')), tz=timezone.utc)
                if added_at >= cutoff_date:
                    thumb_key = item.get('thumb')
                    if item.get('media_type') == 'episode' and item.get('grandparent_thumb'): 
                        thumb_key = item.get('grandparent_thumb')
                        
                    poster_url = None
                    if thumb_key:
                        tautulli_path = f"/pms_image_proxy?img={thumb_key}&width=300&height=450"
                        payload_str = f"tautulli:{tautulli_path}"
                        b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                        
                        # 🛡️ Fallback Robusto
                        try:
                            poster_url = url_for('image.proxy_image', source=b64_payload)
                        except RuntimeError:
                            poster_url = f"/image/?source={b64_payload}"
                            
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
            logger.error(f"Erro em get_recently_added: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    def get_user_devices(self, plex_user_id: str) -> Dict[str, Any]:
        try:
            history_response = self.api.get_history(user_id=plex_user_id, length=500)
            history = history_response.get('data', [])
            if not history: return {"success": True, "devices": []}
            
            devices = {}
            for item in history:
                # Usa nome limpo se existir para não causar chaves gigantes
                player = item.get('player', 'Desconhecido')
                platform = item.get('platform', 'Desconhecida')
                device_key = f"{player}|{platform}"
                
                if device_key not in devices or item.get('date', 0) > devices[device_key]['last_seen']:
                    devices[device_key] = {
                        'player': player, 
                        'platform': platform, 
                        'last_seen': item.get('date', 0)
                    }
            return {"success": True, "devices": sorted(list(devices.values()), key=lambda x: x['last_seen'], reverse=True)}
        except RequestException as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"Erro em get_user_devices: {e}", exc_info=True)
            return {"success": False, "message": str(e)}
