# app/services/plex_manager.py

import logging
import base64
import time
import os
import pytz
from urllib.parse import urlparse, parse_qsl, urlencode
from flask import current_app, url_for
from flask_babel import gettext as _
from datetime import datetime, timezone, timedelta
from tzlocal import get_localzone_name

from .plex.connection import PlexConnectionManager
from .plex.user_manager import PlexUserManager
from .plex.invite_manager import PlexInviteManager
from .plex.subscription_manager import PlexSubscriptionManager

# Importação da instância global do scheduler
from ..extensions import scheduler as global_scheduler

logger = logging.getLogger(__name__)

def _get_local_tz():
    """Obtém o fuso horário real do sistema respeitando o Docker (ex: America/Sao_Paulo)."""
    tz_env = os.environ.get('TZ')
    if tz_env:
        try:
            return pytz.timezone(tz_env)
        except pytz.UnknownTimeZoneError:
            pass
    try: 
        return pytz.timezone(get_localzone_name())
    except Exception: 
        return pytz.UTC

class PlexManager:
    """
    Atua como uma fachada (Facade), a coordenar vários serviços relacionados com o Plex.
    """
    def __init__(self, data_manager, tautulli_manager=None, notifier_manager=None, overseerr_manager=None):
        self.conn = PlexConnectionManager()
        self.users = PlexUserManager(self.conn, data_manager, tautulli_manager, overseerr_manager)
        self.invites = PlexInviteManager(self.conn, self.users, data_manager, self, overseerr_manager, notifier_manager)
        self.subscriptions = PlexSubscriptionManager(data_manager, self.users, scheduler=global_scheduler)
        self.subscriptions.plex_manager = self
        
        self.stream_manager = None
        self.data_manager = data_manager
        self.notifier_manager = notifier_manager
        self.overseerr_manager = overseerr_manager
        self.app = None
        self.plex = None
        self.account = None
        self._device_cache = {}

    def init_app(self, app):
        from app.config import is_configured
        self.app = app
        if is_configured():
            self.reload_connections()

    def reload_connections(self, from_job=False):
        """Recarrega as conexões e atualiza as referências dos objetos principais."""
        success, message = self.conn.reload(from_job=from_job)
        if success:
            self.plex = self.conn.plex
            self.account = self.conn.account
            self.users.invalidate_user_cache()
            
            if self.app:
                with self.app.app_context():
                    from app.extensions import cache
                    cache.set('last_plex_user_sync', time.time(), timeout=86400)
                    from app.config import load_or_create_config
                    self.app.config.update(load_or_create_config())
                
        return success, message

    def check_status(self):
        """Verifica o estado da conexão com o Plex."""
        if self.conn and self.conn.plex and self.conn.account:
            try:
                self.conn.plex.library.sections()
                return {"status": "ONLINE", "message": _("Conectado com sucesso.")}
            except Exception as e:
                logger.warning(f"Falha na verificação de estado do Plex: {e}")
                return {"status": "OFFLINE", "message": _("Falha na comunicação com o servidor Plex.")}
        return {"status": "OFFLINE", "message": _("Não configurado ou falha na conexão inicial.")}

    def get_user_by_id(self, plex_user_id):
        return self.users.get_user_by_id(plex_user_id)
        
    def update_screen_limit(self, plex_user_id, screens):
        profile = self.data_manager.get_user_profile(plex_user_id)
        if profile:
            profile['screen_limit'] = screens
            self.data_manager.set_user_profile(plex_user_id, profile)
            logger.info(f"Limite de telas para o utilizador ID '{plex_user_id}' atualizado para {screens}.")

    def block_user(self, plex_user_id, reason='manual'):
        if self.stream_manager and not self.users.stream_manager:
            self.users.stream_manager = self.stream_manager
        return self.users.block_user(plex_user_id, reason)

    def unblock_user(self, plex_user_id):
        return self.users.unblock_user(plex_user_id)

    def remove_user(self, plex_user_id):
        if self.stream_manager and not getattr(self.users, 'stream_manager', None):
            self.users.stream_manager = self.stream_manager
        return self.users.remove_user(plex_user_id)

    def get_active_sessions(self):
        if not self.conn.plex:
            return {"success": False, "sessions": [], "stream_count": 0}
        
        # Tenta usar o stream_manager existente, se configurado
        if self.stream_manager:
            try:
                return self.stream_manager.get_now_playing()
            except Exception as e:
                logger.error(f"Erro inesperado ao delegar sessões ao Motor de Streams: {e}", exc_info=True)
                
        # Fallback direto na API do Plex
        try:
            sessions = self.conn.plex.sessions()
            active_streams = []
            
            for session in sessions:
                stream_data = {
                    "user": session.usernames[0] if session.usernames else "Local",
                    "title": session.title,
                    "type": session.type,
                    "state": session.players[0].state if session.players else "unknown",
                    "progress": getattr(session, 'viewOffset', 0) or 0,
                    "duration": getattr(session, 'duration', 0) or 0,
                    "player": session.players[0].title if session.players else "N/A",
                }
                active_streams.append(stream_data)
                
            return {
                "success": True,
                "stream_count": len(active_streams),
                "sessions": active_streams
            }
        except Exception as e:
            logger.error(f"Erro ao buscar sessões direto do Plex: {e}")
            return {"success": False, "sessions": [], "stream_count": 0}

    def get_libraries(self): return self.conn.get_libraries()
    
    def get_all_plex_users(self, force_refresh=False): 
        from app.extensions import cache
        
        last_sync = cache.get('last_plex_user_sync')
        current_time = time.time()
        
        if not force_refresh and (not last_sync or (current_time - last_sync > 21600)):
            force_refresh = True
            logger.debug("🔄 Auto-sincronização global de utilizadores ativada.")
            
        if force_refresh:
            self.users.invalidate_user_cache()
            cache.set('last_plex_user_sync', current_time, timeout=86400)
            
        cached_users = self.users.get_all_plex_users()
        
        if not cached_users:
            return []

        processed_users = []
        
        for u in cached_users:
            user = dict(u) 
            original_thumb = user.get('thumb')
            
            if original_thumb:
                try:
                    if '/image/' not in original_thumb:
                        parsed_thumb = urlparse(original_thumb)
                        
                        query_params = parse_qsl(parsed_thumb.query)
                        clean_query = urlencode([(k, v) for k, v in query_params if k.lower() != 'x-plex-token'])
                        clean_url = parsed_thumb._replace(query=clean_query).geturl()
                        
                        if 'plex.tv' in parsed_thumb.netloc or not parsed_thumb.netloc:
                            payload_str = f"plex_account:{clean_url}"
                        else:
                            payload_str = f"url:{clean_url}"
                            
                        b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                        
                        try:
                            user['thumb'] = url_for('image.proxy_image', source=b64_payload)
                        except RuntimeError:
                            user['thumb'] = f"/image/?source={b64_payload}"
                    else:
                        user['thumb'] = original_thumb
                        
                except Exception as e:
                    logger.debug(f"Erro ao converter imagem do utilizador {user.get('username')}: {e}")
            
            processed_users.append(user)

        return processed_users

    def get_user_libraries(self, plex_user_id): return self.users.get_user_libraries(plex_user_id)
    def update_user_libraries(self, plex_user_id, library_titles, allow_sync=None): return self.users.update_user_libraries(plex_user_id, library_titles, allow_sync=allow_sync)
    def update_all_users_libraries(self, library_titles): return self.users.update_all_users_libraries(library_titles)
    def toggle_overseerr_access(self, plex_user_id, access: bool): return self.users.toggle_overseerr_access(plex_user_id, access)
    
    def create_invitation(self, **kwargs): return self.invites.create_invitation(**kwargs)
    def get_invitation_by_code(self, code): return self.invites.get_invitation_by_code(code)
    def claim_invitation(self, code, plex_user_account): return self.invites.claim_invitation(code, plex_user_account)
    def list_invitations(self): return self.invites.list_invitations()
    def delete_invitation(self, code): return self.invites.delete_invitation(code)
    def reactivate_invitation(self, code): return self.invites.reactivate_invitation(code)

    def renew_subscription(self, plex_user_id, months_to_add, screens=None, base_mode='today', base_date_str=None, expiration_time_str=None, is_reactivation=False):
        return self.subscriptions.renew_subscription(
            plex_user_id, months_to_add, screens=screens, base_mode=base_mode, 
            base_date_str=base_date_str, expiration_time_str=expiration_time_str, 
            is_reactivation=is_reactivation
        )

    def get_users_within_notification_window(self):
        from app.config import load_or_create_config
        config = load_or_create_config()
        days_to_notify = config.get("DAYS_TO_NOTIFY_EXPIRATION", 0)
        
        if not days_to_notify > 0: 
            return []
        
        user_expirations = self.data_manager.get_all_user_expirations()
        
        local_tz = _get_local_tz()
        today_local = datetime.now(local_tz).date()
        users_to_check = []
        
        for plex_id, data in user_expirations.items():
            try:
                if data.get('expiration_date'):
                    exp_date_utc = datetime.fromisoformat(data['expiration_date'])
                    if exp_date_utc.tzinfo is None:
                        exp_date_utc = exp_date_utc.replace(tzinfo=timezone.utc)
                    
                    exp_date_local = exp_date_utc.astimezone(local_tz).date()
                    days_left = (exp_date_local - today_local).days

                    if 0 <= days_left < days_to_notify:
                        users_to_check.append(plex_id)
            except (ValueError, TypeError): 
                continue
                
        return users_to_check

    def send_expiration_notification_if_needed(self, user_info):
        plex_user_id = user_info['id']
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile:
            return
        
        from app.config import load_or_create_config
        config = load_or_create_config()
        days_to_notify = config.get("DAYS_TO_NOTIFY_EXPIRATION", 0)

        last_sent_str = profile.get('last_notification_sent')
        if last_sent_str:
            try:
                last_sent_dt = datetime.fromisoformat(last_sent_str)
                if last_sent_dt.tzinfo is None:
                    last_sent_dt = last_sent_dt.replace(tzinfo=timezone.utc)

                if (datetime.now(timezone.utc) - last_sent_dt) < timedelta(hours=23):
                    logger.info(f"Notificação para {user_info['username']} já foi enviada nas últimas 23 horas. A saltar.")
                    return
            except (ValueError, TypeError):
                pass

        expiration_date_str = profile.get('expiration_date')
        if expiration_date_str:
            try:
                local_tz = _get_local_tz()
                today_local = datetime.now(local_tz).date()

                exp_date_utc = datetime.fromisoformat(expiration_date_str)
                if exp_date_utc.tzinfo is None:
                    exp_date_utc = exp_date_utc.replace(tzinfo=timezone.utc)

                days_left = (exp_date_utc.astimezone(local_tz).date() - today_local).days

                if days_left >= days_to_notify:
                    return

                if days_left < 0:
                    logger.debug(f"Utilizador {user_info.get('username')} expirou há {abs(days_left)} dia(s). Notificação preventiva ignorada.")
                    return

                self.notifier_manager.send_expiration_notification(user_info, days_left, profile)
                self.data_manager.update_user_notification_timestamp(plex_user_id)
            except (ValueError, TypeError) as e:
                logger.error(f"Erro ao processar data de expiração para '{user_info['username']}': {e}")
        
    def get_users_to_remove(self):
        from app.config import load_or_create_config
        config = load_or_create_config()
        days_to_remove = config.get("DAYS_TO_REMOVE_BLOCKED_USER", 0)
        
        if not days_to_remove > 0: 
            return []
            
        blocked_users_data = self.data_manager.get_blocked_users_dict()
        if not blocked_users_data: 
            return []

        local_tz = _get_local_tz()
        today_local = datetime.now(local_tz).date()
        users_to_remove = []
        
        for plex_id, block_data in blocked_users_data.items():
            try:
                blocked_at_str = block_data.get('blocked_at')
                if not blocked_at_str:
                    continue

                blocked_utc = datetime.fromisoformat(blocked_at_str)
                if blocked_utc.tzinfo is None:
                    blocked_utc = blocked_utc.replace(tzinfo=timezone.utc)

                blocked_local = blocked_utc.astimezone(local_tz).date()
                
                if (today_local - blocked_local).days >= days_to_remove:
                    users_to_remove.append(plex_id)

            except (ValueError, TypeError, AttributeError): 
                continue
                
        return users_to_remove

    def _get_local_account_id(self, plex_tv_id):
        """
        [CORREÇÃO CRÍTICA]: O Plex guarda o histórico associado ao "Account ID" LOCAL (1, 2, 3...) 
        e não ao ID global do Plex TV. Este tradutor previne históricos vazios!
        """
        plex_tv_id = str(plex_tv_id)
        
        # 1. Se for o dono do servidor, no banco local o ID é sempre 1.
        if self.conn.account and str(self.conn.account.id) == plex_tv_id:
            return 1
            
        # 2. Se for amigo, buscar o nome de utilizador e mapear para o ID local
        username = None
        try:
            profile = self.data_manager.get_user_profile(int(plex_tv_id))
            if profile:
                username = profile.get('username')
        except Exception:
            pass
            
        if not username:
            try:
                return int(plex_tv_id)
            except:
                return plex_tv_id
                
        if self.conn.plex:
            try:
                # systemAccounts() lista todas as contas vinculadas a ESTE servidor local!
                accounts = self.conn.plex.systemAccounts()
                for acc in accounts:
                    if acc.name.lower() == username.lower():
                        return int(acc.id) # Retorna o ID Local convertido para número inteiro!
            except Exception as e:
                logger.debug(f"Falha ao mapear systemAccounts: {e}")
                
            # 3. FALLBACK EXTRA: Buscar no historico global para descobrir o ID Local
            try:
                recent = self.conn.plex.history(maxresults=500)
                for item in recent:
                    acc = getattr(item, 'account', None)
                    if acc and getattr(acc, 'name', '').lower() == username.lower():
                        return int(getattr(item, 'accountID', plex_tv_id))
            except Exception:
                pass
                
        try:
            return int(plex_tv_id)
        except:
            return plex_tv_id # Fallback de segurança

    def _extract_device_info(self, item):
        """
        [CORREÇÃO DE APARELHO DESCONHECIDO]:
        O Plex nativo por vezes não coloca o nome do aparelho no histórico, apenas um ID numérico.
        Esta função cruza o ID numérico com a base de dados interna do Plex e utiliza um cache
        para evitar o problema "N+1 Queries" (que causava o travamento do servidor).
        """
        player = "Plex Client"
        platform = "Plex"
        client_id = None
        
        # 1. Tentar ler do XML (Sessões ativas geralmente têm a tag Player)
        player_node = item._data.find('Player') if hasattr(item, '_data') else None
        if player_node is not None:
            platform = player_node.get('platform', 'Plex')
            player = player_node.get('title', platform)
            client_id = player_node.get('machineIdentifier')
            return player, platform, client_id

        # 2. No histórico, ler o "deviceID" e perguntar ao servidor Plex qual é o nome!
        device_id = getattr(item, 'deviceID', None)
        
        # --- VERIFICAÇÃO DE CACHE (PREVINE CONGELAMENTO DO FLASK) ---
        if device_id:
            if not hasattr(self, '_device_cache'):
                self._device_cache = {}
            if device_id in self._device_cache:
                return self._device_cache[device_id]

        if device_id and self.conn.plex:
            try:
                # Consulta à base de dados local do Plex (Endpoint /devices/{id})
                device_xml = self.conn.plex.query(f'/devices/{device_id}')
                if device_xml is not None and len(device_xml) > 0:
                    device_node = device_xml[0]
                    platform = device_node.attrib.get('platform', 'Plex')
                    player = device_node.attrib.get('name', platform)
                    client_id = device_node.attrib.get('clientIdentifier')
                    
                    result = (player, platform, client_id)
                    self._device_cache[device_id] = result
                    return result
            except Exception:
                pass
        
        # 3. Fallbacks gerais (Evitando item.device() que faria novas queries bloqueantes)
        try:
            device_node = item._data.find('Device') if hasattr(item, '_data') else None
            if device_node is not None:
                platform = device_node.get('platform', 'Plex')
                player = device_node.get('name', platform)
                client_id = device_node.get('clientIdentifier', None)
        except Exception:
            pass
            
        if not player or str(player).lower() == 'none':
            player = platform
            
        if not client_id:
            client_id = f"{platform}-{player}"
            
        result = (player, platform, client_id)
        if device_id:
             self._device_cache[device_id] = result
             
        return result

    def get_watch_stats(self, days=7, plex_users_info=None):
        if not self.conn.plex:
            return {"success": False, "stats": []}
        
        try:
            # Construir dicionário reverso: Nome -> ID Plex TV
            all_users = self.get_all_plex_users() or []
            user_map_by_name = {u.get('username', '').lower(): str(u['id']) for u in all_users if 'username' in u}
            
            # Construir Mapa de Conversão (ID Local -> ID Plex TV)
            local_to_plex_tv = {"1": str(self.conn.account.id)} if self.conn.account else {}
            try:
                for acc in self.conn.plex.systemAccounts():
                    if acc.name.lower() in user_map_by_name:
                        local_to_plex_tv[str(acc.id)] = user_map_by_name[acc.name.lower()]
            except Exception:
                pass

            mindate = datetime.now() - timedelta(days=days)
            # Reduzido para evitar sobrecarga excessiva de RAM e bloqueios ao processar XML
            history = self.conn.plex.history(mindate=mindate, maxresults=2500)
            
            user_stats = {}
            for item in history:
                account_id = getattr(item, 'accountID', None)
                if not account_id: 
                    continue

                local_uid = str(account_id)
                # Tradução de volta para o ID do Plex TV real
                uid = local_to_plex_tv.get(local_uid, local_uid)
                
                if uid not in user_stats:
                    username = "Desconhecido"
                    account_obj = getattr(item, 'account', None)
                    if account_obj:
                        username = getattr(account_obj, 'name', "Desconhecido")
                        
                    user_stats[uid] = {
                        "user_id": uid,
                        "username": username,
                        "total_plays": 0,
                        "total_duration": 0,
                        "thumb": plex_users_info.get(uid, "") if plex_users_info else ""
                    }
                
                user_stats[uid]["total_plays"] += 1
                
                try:
                    dur_ms = int(getattr(item, 'duration', 0) or 0)
                except (ValueError, TypeError):
                    dur_ms = 0
                    
                if not dur_ms:
                    item_type = getattr(item, 'type', 'unknown')
                    if item_type == 'movie': dur_ms = 5400000 
                    elif item_type == 'episode': dur_ms = 2400000
                
                user_stats[uid]["total_duration"] += int(dur_ms / 1000)
                    
            stats_list = list(user_stats.values())
            stats_list.sort(key=lambda x: x["total_duration"], reverse=True)
            
            return {"success": True, "stats": stats_list}
        except Exception as e:
            logger.error(f"Erro ao buscar estatísticas nativas do Plex: {e}")
            return {"success": False, "stats": []}

    def get_user_watch_details(self, plex_user_id, days=7):
        if not self.conn.plex:
            return {"success": False}
            
        try:
            local_acc_id = self._get_local_account_id(plex_user_id)
            mindate = datetime.now() - timedelta(days=days)
            history = self.conn.plex.history(mindate=mindate, accountID=local_acc_id, maxresults=500)
            
            plays_by_type = {}
            recent_history = []
            
            total_duration_sec = 0
            movie_count = 0
            episode_count = 0
            
            days_map = {0: "Seg", 1: "Ter", 2: "Qua", 3: "Qui", 4: "Sex", 5: "Sáb", 6: "Dom"}
            plays_by_day = {d: 0 for d in days_map.values()}
            media_freq = {}
            
            for item in history:
                item_type = getattr(item, 'type', 'unknown')
                plays_by_type[item_type] = plays_by_type.get(item_type, 0) + 1
                
                if item_type == 'movie': movie_count += 1
                elif item_type == 'episode': episode_count += 1
                
                if item_type == 'episode':
                    media_key = getattr(item, 'grandparentRatingKey', getattr(item, 'ratingKey', None))
                else:
                    media_key = getattr(item, 'ratingKey', None)
                    
                if media_key:
                    media_freq[media_key] = media_freq.get(media_key, 0) + 1
                
                try:
                    dur_ms = int(getattr(item, 'duration', 0) or 0)
                except (ValueError, TypeError):
                    dur_ms = 0
                    
                if not dur_ms or dur_ms == 0:
                    if item_type == 'movie': dur_ms = 5400000 
                    elif item_type == 'episode': dur_ms = 2400000
                    else: dur_ms = 0
                total_duration_sec += int(dur_ms / 1000)
                
                viewed_at = getattr(item, 'viewedAt', None)
                if viewed_at:
                    weekday_name = days_map.get(viewed_at.weekday(), "Desconhecido")
                    if weekday_name in plays_by_day:
                        plays_by_day[weekday_name] += 1
                        
                if len(recent_history) < 15:
                    player, platform, _ = self._extract_device_info(item)
                        
                    title = getattr(item, 'title', None) or ""
                    grandparent = getattr(item, 'grandparentTitle', None) or ""
                    parent = getattr(item, 'parentTitle', None) or ""
                    
                    if grandparent and title:
                        full_title = f"{grandparent} - {title}"
                    elif title:
                        full_title = title
                    else:
                        full_title = "Conteúdo Desconhecido"
                        
                    thumb_path = getattr(item, 'grandparentThumb', None) or getattr(item, 'parentThumb', None) or getattr(item, 'thumb', None)
                    thumb_url = ""
                    if thumb_path:
                        payload_str = f"plex:{thumb_path}"
                        b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                        try:
                            from flask import url_for
                            thumb_url = url_for('image.proxy_image', source=b64_payload)
                        except Exception:
                            thumb_url = f"/image/?source={b64_payload}"
                            
                    timestamp = int(viewed_at.timestamp()) if viewed_at else 0
                            
                    recent_history.append({
                        "title": title,
                        "full_title": full_title,
                        "grandparent_title": grandparent,
                        "show_title": grandparent,
                        "series": grandparent,
                        "parent_title": parent,
                        "original_title": title,
                        "type": item_type,
                        "media_type": item_type,
                        "date": timestamp,
                        "last_played": timestamp,
                        "viewed_at": viewed_at.isoformat() if viewed_at else None,
                        "play_date": viewed_at.strftime('%d/%m/%Y - %H:%M') if viewed_at else "Desconhecido",
                        "player": player,
                        "platform": platform,
                        "thumb": thumb_url,
                        "poster_url": thumb_url,
                        "duration": int(dur_ms / 1000)
                    })
                    
            genre_counter = {}
            top_media_keys = sorted(media_freq.keys(), key=lambda k: media_freq[k], reverse=True)[:12]
            
            for key in top_media_keys:
                try:
                    media_item = self.conn.plex.fetchItem(int(key))
                    if hasattr(media_item, 'genres') and media_item.genres:
                        for g in media_item.genres:
                            g_tag = getattr(g, 'tag', None)
                            if g_tag:
                                genre_counter[g_tag] = genre_counter.get(g_tag, 0) + media_freq[key]
                except Exception:
                    continue
                    
            if genre_counter:
                sorted_genres = sorted(genre_counter.items(), key=lambda x: x[1], reverse=True)
                genres_list = [{"genre": k, "count": v} for k, v in sorted_genres]
            else:
                genres_list = [{"genre": "Misto", "count": sum(plays_by_type.values()) if plays_by_type else 1}]
            
            stats = [{"type": k, "count": v} for k, v in plays_by_type.items()]
            activity_by_day = [{"day": k, "count": v} for k, v in plays_by_day.items()]
            
            return {
                "success": True,
                "stats": stats,
                "recent_history": recent_history,
                "total_time": total_duration_sec,
                "total_plays": sum(plays_by_type.values()),
                "movie_count": movie_count,
                "episode_count": episode_count,
                "activity_by_day": activity_by_day,
                "genres": genres_list,
                "watch_time_stats": [{"total_time": total_duration_sec, "total_plays": sum(plays_by_type.values())}]
            }
        except Exception as e:
            logger.error(f"Erro ao buscar detalhes do utilizador {plex_user_id} via Plex: {e}", exc_info=True)
            return {"success": False}

    def get_user_devices(self, plex_user_id):
        if not self.conn.plex:
            return []
        try:
            local_acc_id = self._get_local_account_id(plex_user_id)
            history = self.conn.plex.history(accountID=local_acc_id, maxresults=300)
            
            devices_map = {}
            for item in history:
                player, platform, client_id = self._extract_device_info(item)
                viewed_at = getattr(item, 'viewedAt', None)
                timestamp = int(viewed_at.timestamp()) if viewed_at else 0
                
                if client_id not in devices_map or devices_map[client_id]['last_seen'] < timestamp:
                    devices_map[client_id] = {
                        "player": player,
                        "platform": platform,
                        "last_seen": timestamp
                    }
            
            device_list = list(devices_map.values())
            device_list.sort(key=lambda x: x["last_seen"], reverse=True)
            return device_list
        except Exception as e:
            logger.error(f"Erro ao buscar dispositivos para o utilizador {plex_user_id}: {e}")
            return []

    def get_user_watch_history(self, user_id, page=1, length=15, search=''):
        if not self.conn.plex:
            return {"success": False, "data": [], "total_records": 0}
            
        try:
            local_acc_id = self._get_local_account_id(user_id)
            # Limitamos a maxresults=500 para evitar sobrecarga excessiva de RAM ao converter o histórico
            history = self.conn.plex.history(accountID=local_acc_id, maxresults=500)
            
            if search:
                search_lower = search.lower()
                filtered = []
                for h in history:
                    t1 = getattr(h, 'title', '')
                    t1 = t1.lower() if t1 else ""
                    t2 = getattr(h, 'grandparentTitle', '')
                    t2 = t2.lower() if t2 else ""
                    
                    if search_lower in t1 or search_lower in t2:
                        filtered.append(h)
                history = filtered
                
            total_records = len(history)
            start = (page - 1) * length
            end = start + length
            paginated_history = history[start:end]
            
            data = []
            for item in paginated_history:
                player, platform, _ = self._extract_device_info(item)
                viewed_at = getattr(item, 'viewedAt', None)
                
                # [CORREÇÃO]: A API PlayHistory omite alguns campos. Extraímos direto do XML bruto
                raw_data = getattr(item, '_data', None)
                
                season = getattr(item, 'parentIndex', None)
                episode = getattr(item, 'index', None)
                gp_title = getattr(item, 'grandparentTitle', '')
                year = getattr(item, 'year', None)
                raw_offset = getattr(item, 'viewOffset', None)
                raw_duration = getattr(item, 'duration', 0)
                
                if raw_data is not None:
                    if season is None: season = raw_data.attrib.get('parentIndex')
                    if episode is None: episode = raw_data.attrib.get('index')
                    if not gp_title: gp_title = raw_data.attrib.get('grandparentTitle', '')
                    if year is None: year = raw_data.attrib.get('year')
                    if raw_offset is None: raw_offset = raw_data.attrib.get('viewOffset')
                    if not raw_duration: raw_duration = raw_data.attrib.get('duration', 0)

                # [CORREÇÃO PROGRESSO]: Quando se acaba um filme, o Plex guarda viewOffset = "0"
                try:
                    if raw_offset is None or int(raw_offset) == 0:
                        percent_complete = 100
                    else:
                        view_offset_ms = int(raw_offset)
                        duration_ms = int(raw_duration) if raw_duration else 0
    
                        if not duration_ms or duration_ms <= 0:
                            item_type = getattr(item, 'type', 'unknown')
                            if item_type == 'movie': duration_ms = 5400000 
                            elif item_type == 'episode': duration_ms = 2400000
                            else: duration_ms = 1
    
                        percent_complete = int((view_offset_ms / duration_ms) * 100)
                        if percent_complete > 100: percent_complete = 100
                        if percent_complete < 0: percent_complete = 0
                except Exception:
                    percent_complete = 100

                # [NOVO]: Capturar a capa (poster da série e filme)
                thumb_path = getattr(item, 'grandparentThumb', None) or getattr(item, 'parentThumb', None) or getattr(item, 'thumb', None)
                if not thumb_path and raw_data is not None:
                    thumb_path = raw_data.attrib.get('grandparentThumb') or raw_data.attrib.get('parentThumb') or raw_data.attrib.get('thumb')

                thumb_url = ""
                if thumb_path:
                    payload_str = f"plex:{thumb_path}"
                    b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                    thumb_url = f"/image/?source={b64_payload}"
                    
                data.append({
                    "title": getattr(item, 'title', 'Desconhecido'),
                    "grandparent_title": gp_title,
                    "type": getattr(item, 'type', 'unknown'),
                    "season": season,
                    "episode": episode,
                    "year": year,
                    "thumb": thumb_url,
                    "viewed_at": viewed_at.isoformat() if viewed_at else None,
                    "player": player,
                    "percent_complete": percent_complete
                })
                
            return {
                "success": True, 
                "data": data,
                "total_records": total_records,
                "recordsFiltered": total_records
            }
        except Exception as e:
            logger.error(f"Erro ao buscar histórico paginado para o ID {user_id}: {e}")
            return {"success": False, "data": [], "total_records": 0}

    def get_recently_added(self, days=7):
        if not self.conn.plex:
            return []
            
        try:
            recently_added = []
            for section in self.conn.plex.library.sections():
                items = section.recentlyAdded(maxresults=10)
                for item in items:
                    recently_added.append({
                        "title": item.title,
                        "type": item.type,
                        "added_at": item.addedAt.isoformat() if item.addedAt else None,
                        "library_name": section.title
                    })
            
            recently_added.sort(key=lambda x: x["added_at"] or "", reverse=True)
            return recently_added[:20]
        except Exception as e:
            logger.error(f"Erro ao buscar itens adicionados recentemente: {e}")
            return []
