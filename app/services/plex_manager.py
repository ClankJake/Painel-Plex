# app/services/plex_manager.py

import logging
import base64
import time
from urllib.parse import urlparse, parse_qsl, urlencode
from flask import current_app, url_for
from flask_babel import gettext as _
from requests.exceptions import ConnectTimeout, ReadTimeout, ConnectionError, RequestException
from datetime import date, datetime, timezone, timedelta

from .plex.connection import PlexConnectionManager
from .plex.user_manager import PlexUserManager
from .plex.invite_manager import PlexInviteManager
from .plex.subscription_manager import PlexSubscriptionManager

logger = logging.getLogger(__name__)

class PlexManager:
    """
    Atua como uma fachada (Facade), a coordenar vários serviços relacionados com o Plex.
    """
    def __init__(self, data_manager, tautulli_manager, notifier_manager, overseerr_manager):
        self.conn = PlexConnectionManager()
        self.users = PlexUserManager(self.conn, data_manager, tautulli_manager, overseerr_manager)
        self.invites = PlexInviteManager(self.conn, self.users, data_manager, self, overseerr_manager, notifier_manager)
        self.subscriptions = PlexSubscriptionManager(data_manager, self.users)
        self.subscriptions.plex_manager = self
        self.stream_manager = None
        self.data_manager = data_manager
        self.tautulli_manager = tautulli_manager
        self.notifier_manager = notifier_manager
        self.overseerr_manager = overseerr_manager
        self.app = None
        self.plex = None
        self.account = None

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

    # --- DELEGAÇÕES SIMPLES ---
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

    # --- SESSÕES E STREAMING ---
    def get_active_sessions(self):
        """Obtém todas as sessões ativas do Plex e formata os dados."""
        if not self.conn.plex:
            return {"success": False, "sessions": [], "stream_count": 0}
        
        try:
            all_users = self.users.get_all_plex_users() or []
            user_thumb_map = {user['id']: user['thumb'] for user in all_users}
            
            sessions = self.conn.plex.sessions()
            session_details = [self._parse_session_data(s, user_thumb_map) for s in sessions]
            
            return {"success": True, "sessions": session_details, "stream_count": len(sessions)}
            
        except (ConnectionError, ReadTimeout, RequestException) as e:
            logger.warning(f"Erro de rede ao obter sessões do Plex: {e}")
            return {"success": False, "sessions": [], "stream_count": 0}
        except Exception as e:
            logger.error(f"Erro inesperado ao parsear sessões do Plex: {e}", exc_info=True)
            return {"success": False, "sessions": [], "stream_count": 0}

    def _parse_session_data(self, s, user_thumb_map):
        """Método auxiliar para tratar os dados de uma única sessão com precisão militar."""
        progress = 0
        view_offset = getattr(s, 'viewOffset', 0)
        duration = getattr(s, 'duration', 0)
        
        if view_offset and duration and duration > 0:
            progress = (view_offset / duration) * 100
            
        state = "stopped"
        if s.players:
            player_state = getattr(s.players[0], "state", "stopped")
            state = {"paused": "paused", "playing": "playing", "buffering": "buffering"}.get(player_state, "stopped")
            
        # 🛡️ MELHORIA 1: Duplo check de Transcoding importado da referência
        is_transcoding = False
        video_decision, audio_decision = "Direct Play", "Direct Play"
        transcode_progress = None
        transcode_speed = None
        
        transcode_session = getattr(s, "transcodeSession", None)
        transcode_sessions_list = getattr(s, "transcodeSessions", [])
        
        # Agarra a sessão de transcoding, seja ela um objeto isolado ou o primeiro item da nova lista
        active_ts = transcode_session if transcode_session else (transcode_sessions_list[0] if transcode_sessions_list else None)
        
        if active_ts:
            v_dec = getattr(active_ts, 'videoDecision', 'copy')
            a_dec = getattr(active_ts, 'audioDecision', 'copy')
            
            if v_dec == "transcode": 
                is_transcoding, video_decision = True, "Transcode"
            if a_dec == "transcode": 
                is_transcoding, audio_decision = True, "Transcode"
                
            if is_transcoding:
                # 🛡️ MELHORIA 2: Extração da Velocidade para deteção de Gargalos
                try: 
                    transcode_progress = int(getattr(active_ts, "progress", 0))
                    transcode_speed = float(getattr(active_ts, "speed", 0.0))
                except (ValueError, TypeError): 
                    pass

        media = s.media[0] if getattr(s, 'media', None) else None
        video_codec = (getattr(media, "videoCodec", None) or "N/A").upper()
        audio_codec = (getattr(media, "audioCodec", None) or "N/A").upper()
        container = (getattr(media, "container", None) or "N/A").upper()
        
        video_resolution = getattr(media, "videoResolution", "N/A")
        try: 
            video_resolution = f"{int(video_resolution)}p"
        except (ValueError, TypeError): 
            pass
        
        # 🛡️ MELHORIA 3: Formatação blindada de Títulos (previne erros se não houver número da temporada)
        title = getattr(s, 'title', 'Desconhecido')
        subtitle = str(getattr(s, 'year', ''))
        media_type = getattr(s, "type", "unknown").lower()
        
        if media_type == 'episode':
            grandparent = getattr(s, 'grandparentTitle', '')
            title = grandparent if grandparent else title
            
            season_num = getattr(s, 'parentIndex', None)
            episode_num = getattr(s, 'index', None)
            
            if season_num is not None and episode_num is not None:
                subtitle = f"S{int(season_num):02d} · E{int(episode_num):02d} - {getattr(s, 'title', '')}"
            else:
                subtitle = getattr(s, 'title', '')
        
        # 🛡️ MELHORIA 4: Cascata de Imagens da Referência (Garante que há sempre um Poster bonito)
        thumb_key = None
        for attr in ['grandparentThumb', 'parentThumb', 'art', 'thumb']:
            val = getattr(s, attr, None)
            if val:
                thumb_key = val
                break
                
        thumb_url = None
        if thumb_key:
            # Mantemos o proxy de segurança do seu painel para não expor o Plex Token!
            b64_payload = base64.urlsafe_b64encode(f"plex:{thumb_key}".encode('utf-8')).decode('utf-8')
            try:
                thumb_url = url_for('image.proxy_image', source=b64_payload)
            except RuntimeError:
                thumb_url = f"/image/?source={b64_payload}"
        
        # Tratamento de Imagem do Utilizador (Mantido o seu filtro Anti-Double-Proxy perfeito)
        final_user_thumb = user_thumb_map.get(getattr(s.user, 'id', None))
        if not final_user_thumb and getattr(s.user, 'thumb', None):
            try:
                original_thumb = s.user.thumb
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
                        final_user_thumb = url_for('image.proxy_image', source=b64_payload)
                    except RuntimeError:
                        final_user_thumb = f"/image/?source={b64_payload}"
                else:
                    final_user_thumb = original_thumb
            except Exception:
                pass

        return {
            "session_key": getattr(s, 'sessionKey', '0'), 
            "user": getattr(s.user, 'title', 'Desconhecido'), 
            "user_thumb": final_user_thumb,
            "player": getattr(s.player, 'title', 'Desconhecido') if getattr(s, 'player', None) else 'Desconhecido', 
            "platform": getattr(s.player, 'platform', 'Desconhecido') if getattr(s, 'player', None) else 'Desconhecido', 
            "type": media_type,
            "title": title, 
            "subtitle": subtitle, 
            "progress": round(progress, 2),
            "view_offset": view_offset, 
            "duration": duration, 
            "thumb_url": thumb_url, 
            "state": state,
            "stream_details": { 
                "video_decision": video_decision, 
                "audio_decision": audio_decision, 
                "video_codec": video_codec, 
                "audio_codec": audio_codec, 
                "video_resolution": video_resolution, 
                "stream": "Transcode" if is_transcoding else "Direct Play", 
                "container": container, 
                "is_transcoding": is_transcoding, 
                "transcode_progress": transcode_progress,
                "transcode_speed": transcode_speed  # Adicionada Velocidade!
            }
        }

    # --- BIBLIOTECAS E ACESSOS ---
    def get_libraries(self): return self.conn.get_libraries()
    
    def get_all_plex_users(self, force_refresh=False): 
        from app.extensions import cache
        
        last_sync = cache.get('last_plex_user_sync')
        current_time = time.time()
        
        # 1. AUTO-RENOVAÇÃO BLINDADA (A cada 6 horas)
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
        
        # 2. CLONE DE SEGURANÇA E PROXY LIMPO
        for u in cached_users:
            user = dict(u)  # Clona para não envenenar a cache
            original_thumb = user.get('thumb')
            
            if original_thumb:
                try:
                    # Filtro crítico: Se a URL já tiver /image/, ignora para não criar double proxy
                    if '/image/' not in original_thumb:
                        parsed_thumb = urlparse(original_thumb)
                        
                        # Remove o token antigo para não cachear links mortos
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
                        # Se já for um proxy gerado por nós, usa-o sem tocar
                        user['thumb'] = original_thumb
                        
                except Exception as e:
                    logger.debug(f"Erro ao converter imagem do utilizador {user.get('username')}: {e}")
            
            processed_users.append(user)

        return processed_users

    def get_user_libraries(self, plex_user_id): return self.users.get_user_libraries(plex_user_id)
    def update_user_libraries(self, plex_user_id, library_titles, allow_sync=None): return self.users.update_user_libraries(plex_user_id, library_titles, allow_sync=allow_sync)
    def update_all_users_libraries(self, library_titles): return self.users.update_all_users_libraries(library_titles)
    def toggle_overseerr_access(self, plex_user_id, access: bool): return self.users.toggle_overseerr_access(plex_user_id, access)
    
    # --- CONVITES ---
    def create_invitation(self, **kwargs): return self.invites.create_invitation(**kwargs)
    def get_invitation_by_code(self, code): return self.invites.get_invitation_by_code(code)
    def claim_invitation(self, code, plex_user_account): return self.invites.claim_invitation(code, plex_user_account)
    def list_invitations(self): return self.invites.list_invitations()
    def delete_invitation(self, code): return self.invites.delete_invitation(code)
    def reactivate_invitation(self, code): return self.invites.reactivate_invitation(code)

    # --- ASSINATURAS E RENOVAÇÕES ---
    def renew_subscription(self, plex_user_id, months_to_add, screens=None, base_mode='today', base_date_str=None, expiration_time_str=None, is_reactivation=False):
        return self.subscriptions.renew_subscription(
            plex_user_id, months_to_add, screens=screens, base_mode=base_mode, 
            base_date_str=base_date_str, expiration_time_str=expiration_time_str, 
            is_reactivation=is_reactivation
        )

    # --- NOTIFICAÇÕES E EXPIRAÇÕES ---
    def get_users_within_notification_window(self):
        from app.config import load_or_create_config
        config = load_or_create_config()
        days_to_notify = config.get("DAYS_TO_NOTIFY_EXPIRATION", 0)
        
        if not days_to_notify > 0: 
            return []
        
        user_expirations = self.data_manager.get_all_user_expirations()
        today = datetime.now(timezone.utc).date()
        users_to_check = []
        
        for plex_id, data in user_expirations.items():
            try:
                if data.get('expiration_date'):
                    exp_date = datetime.fromisoformat(data['expiration_date']).date()
                    if 0 <= (exp_date - today).days <= days_to_notify:
                        users_to_check.append(plex_id)
            except (ValueError, TypeError): 
                continue
                
        return users_to_check

    def send_expiration_notification_if_needed(self, user_info):
        plex_user_id = user_info['id']
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile:
            return
        
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
                days_left = (datetime.fromisoformat(expiration_date_str).date() - datetime.now(timezone.utc).date()).days
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

        today = datetime.now(timezone.utc).date()
        users_to_remove = []
        
        for plex_id, block_data in blocked_users_data.items():
            try:
                blocked_at_str = block_data.get('blocked_at')
                if not blocked_at_str:
                    continue

                blocked_date = datetime.fromisoformat(blocked_at_str).date()
                
                if (today - blocked_date).days >= days_to_remove:
                    users_to_remove.append(plex_id)

            except (ValueError, TypeError, AttributeError): 
                continue
                
        return users_to_remove
