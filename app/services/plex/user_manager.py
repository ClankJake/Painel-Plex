# app/services/plex/user_manager.py

import logging
import json
import time
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import current_app
from flask_babel import gettext as _
from plexapi.exceptions import NotFound
from requests.exceptions import RequestException
from apscheduler.jobstores.base import JobLookupError

from ...extensions import cache

logger = logging.getLogger(__name__)

class PlexUserManager:
    """
    Gere todas as operações relacionadas com os utilizadores do Plex.
    """
    def __init__(self, connection, data_manager, tautulli_manager, overseerr_manager):
        self.conn = connection
        self.data_manager = data_manager
        self.tautulli_manager = tautulli_manager
        self.overseerr_manager = overseerr_manager
        self.stream_manager = None

    def invalidate_user_cache(self):
        """Invalida a cache de utilizadores."""
        cache.delete_memoized(self.get_all_plex_users)
        logger.info(_("Cache de utilizadores do Plex invalidado."))

    def get_user_by_id(self, plex_user_id):
        """Busca um único utilizador pelo seu ID do Plex, utilizando a cache."""
        all_users = self.get_all_plex_users()
        if not all_users:
            return None
        return next((u for u in all_users if str(u['id']) == str(plex_user_id)), None)

    def _process_single_user(self, user, server_identifier):
        if any(s.machineIdentifier == server_identifier for s in user.servers):
            return {
                'username': user.username, 
                'email': user.email, 
                'id': user.id, 
                'thumb': user.thumb, 
                'servers': [s.name for s in user.servers]
            }
        return None

    @cache.memoize(timeout=300)
    def get_all_plex_users(self, force_refresh_signal=None):
        if not self.conn.account or not self.conn.plex:
            return None

        try:
            self.conn.account._users = None 
            
            server_identifier = self.conn.plex.machineIdentifier
            all_friends = self.conn.account.users()
            users_with_access = []

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(self._process_single_user, user, server_identifier) for user in all_friends]
                for future in as_completed(futures):
                    try:
                        if result := future.result():
                            users_with_access.append(result)
                    except Exception as exc:
                        logger.error(f"Erro ao processar utilizador em thread: {exc}")

            if self.conn.account:
                admin_id = self.conn.account.id
                if not any(str(u['id']) == str(admin_id) for u in users_with_access):
                    users_with_access.append({
                        'username': self.conn.account.username, 
                        'email': self.conn.account.email, 
                        'id': admin_id, 
                        'thumb': self.conn.account.thumb, 
                        'servers': [self.conn.plex.friendlyName]
                    })

            return users_with_access
            
        # Tratamento otimizado: falhas de rede registam log limpo, bugs mantêm traceback
        except RequestException as e:
            logger.error(_("A API do Plex.tv está temporariamente inacessível (Timeout/Rede). Detalhes: %(error)s", error=e))
            self.invalidate_user_cache()
            return None
        except Exception as e:
            logger.error(_("Erro inesperado ao obter utilizadores do Plex: %(error)s", error=e), exc_info=True)
            self.invalidate_user_cache()
            return None

    def get_user_libraries(self, plex_user_id):
        """Busca as bibliotecas e permissão de Downloads diretamente da Plex.tv."""
        if not self.conn.account or not self.conn.plex:
            return {"success": False, "message": _("Plex não configurado.")}

        if str(self.conn.account.id) == str(plex_user_id):
            return {"success": True, "libraries": [sec.title for sec in self.conn.plex.library.sections()], "allow_sync": True}

        profile = self.data_manager.get_user_profile(plex_user_id)

        try:
            self.conn.account._users = None 
            all_friends = self.conn.account.users()
            plex_user_obj = next((u for u in all_friends if str(u.id) == str(plex_user_id)), None)
            
            if not plex_user_obj:
                raise ValueError(f"Utilizador não encontrado na Plex.tv")

            server_resource = next((s for s in plex_user_obj.servers if s.machineIdentifier == self.conn.plex.machineIdentifier), None)
            
            allow_sync = False
            library_titles = []

            if server_resource:
                allow_sync = getattr(server_resource, 'allowSync', getattr(plex_user_obj, 'allowSync', False))
                
                # Resolve automaticamente o problema de "Tudo marcado"
                if getattr(server_resource, 'allLibraries', False):
                    library_titles = [sec.title for sec in self.conn.plex.library.sections()]
                else:
                    sections_attr = getattr(server_resource, 'sections', [])
                    sections_list = sections_attr() if callable(sections_attr) else sections_attr
                    
                    for section in (sections_list or []):
                        title = getattr(section, "title", None)
                        if title:
                            is_shared = getattr(section, "shared", True)
                            if is_shared is not False:
                                library_titles.append(title)
                        elif isinstance(section, dict) and 'title' in section:
                            is_shared = section.get("shared", True)
                            if is_shared is not False:
                                library_titles.append(section['title'])

            if profile:
                profile['libraries'] = json.dumps(library_titles)
                self.data_manager.set_user_profile(plex_user_id, profile)
                
            return {"success": True, "libraries": library_titles, "allow_sync": allow_sync}

        except Exception as e:
            logger.warning(f"Sincronização falhou para ID {plex_user_id}. Usando Cache Local. Motivo: {e}")
            if profile and profile.get('libraries'):
                try: return {"success": True, "libraries": json.loads(profile['libraries']), "allow_sync": False}
                except: pass
            return {"success": False, "message": _("Falha ao sincronizar com o Plex.")}

    # =========================================================================
    # LÓGICA AVANÇADA DA API V2 DIRETA (Bypass aos Bugs da Biblioteca Python)
    # Baseado na arquitetura documentada para a API V2 Oficial da Plex
    # =========================================================================

    def _get_all_library_global_ids(self) -> dict:
        """Obtém o mapeamento de todas as bibliotecas (Título -> ID Global)."""
        try:
            url = f"https://plex.tv/api/v2/servers/{self.conn.plex.machineIdentifier}"
            params = {
                "X-Plex-Product": "PlexPanel",
                "X-Plex-Version": "1.0",
                "X-Plex-Client-Identifier": getattr(self.conn.account, 'uuid', 'PlexPanel'),
                "X-Plex-Token": self.conn.account.authToken,
                "X-Plex-Platform": "Web",
                "X-Plex-Features": "external-media,indirect-media,hub-style-list",
                "X-Plex-Language": "pt",
            }
            headers = {"Accept": "application/json"}
            session = getattr(self.conn.account, '_session', requests.Session())
            
            # PROTEÇÃO: Adicionado timeout=15 para evitar que a thread congele se a API Plex.tv cair
            resp = session.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            server_data = resp.json()
            
            libraries = server_data.get("librarySections", [])
            library_map = {}
            for lib in libraries:
                title = lib.get("title")
                lib_id = lib.get("id")
                if title and lib_id:
                    library_map[title] = lib_id
            return library_map
        except Exception as e:
            logger.error(f"Falha ao obter IDs Globais das bibliotecas: {e}")
            return {}

    def _get_share_data(self, plex_user_id) -> dict:
        """Obtém o objeto completo de partilha da API Oficial da Plex."""
        try:
            url = "https://clients.plex.tv/api/v2/shared_servers/owned/accepted"
            params = {
                "X-Plex-Product": "PlexPanel",
                "X-Plex-Version": "1.0",
                "X-Plex-Client-Identifier": getattr(self.conn.account, 'uuid', 'PlexPanel'),
                "X-Plex-Token": self.conn.account.authToken,
                "X-Plex-Platform": "Web",
                "X-Plex-Features": "external-media,indirect-media,hub-style-list",
                "X-Plex-Language": "pt",
            }
            headers = {"Accept": "application/json"}
            session = getattr(self.conn.account, '_session', requests.Session())
            
            # PROTEÇÃO: Adicionado timeout=15
            resp = session.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            shared_servers = resp.json()
            
            for share in shared_servers:
                if share.get("machineIdentifier") == self.conn.plex.machineIdentifier and str(share.get("invitedId")) == str(plex_user_id):
                    return share
            return {}
        except Exception as e:
            logger.error(f"Falha ao obter dados de partilha V2: {e}")
            return {}

    def update_user_libraries(self, plex_user_id, library_titles, allow_sync=None, bulk_mode=False):
        """Atualiza as bibliotecas e Downloads usando a API V2 Nativa (Método POST rigoroso)."""
        if not self.conn.account or not self.conn.plex:
            return {"success": False, "message": _("Plex não configurado.")}

        if str(self.conn.account.id) == str(plex_user_id):
            return {"success": True, "message": _("O administrador tem acesso total por defeito.")}
            
        try:
            # 1. Obtém o ID da partilha e configurações atuais usando o endpoint V2
            share_data = self._get_share_data(plex_user_id)
            if not share_data:
                raise ValueError("Partilha não encontrada na API da Plex. O utilizador já aceitou o convite?")
                
            shared_server_id = share_data.get("id")
            
            # 2. Configura as permissões, mantendo o que não queremos alterar
            if allow_sync is None:
                allow_sync = share_data.get("allowSync", False)
            else:
                allow_sync = bool(allow_sync)
                
            settings = {
                "allowSync": allow_sync,
                "allowChannels": share_data.get("allowChannels", False),
                "allowCameraUpload": share_data.get("allowCameraUpload", False),
                "filterMovies": "",
                "filterMusic": "",
                "filterPhotos": None,
                "filterTelevision": "",
                "filterAll": None,
                "allowSubtitleAdmin": False,
                "allowTuners": 0,
            }
            
            # 3. Converte os Títulos das Bibliotecas para os IDs Globais
            global_libs_map = self._get_all_library_global_ids()
            section_ids = []
            for title in library_titles:
                if title in global_libs_map:
                    section_ids.append(int(global_libs_map[title]))
                else:
                    logger.warning(f"Biblioteca '{title}' ignorada (Não encontrada no mapa global da Plex).")
                    
            # 4. Envia o pedido usando POST (Como exigido pela API v2) com Payload JSON estrito
            url = f"https://clients.plex.tv/api/v2/shared_servers/{shared_server_id}"
            
            params = {
                "X-Plex-Product": "PlexPanel",
                "X-Plex-Version": "1.0",
                "X-Plex-Client-Identifier": getattr(self.conn.account, 'uuid', 'PlexPanel'),
                "X-Plex-Token": self.conn.account.authToken,
                "X-Plex-Platform": "Web",
                "X-Plex-Platform-Version": "1.0",
                "X-Plex-Features": "external-media,indirect-media,hub-style-list",
                "X-Plex-Language": "pt",
            }
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            
            payload = {
                "settings": settings,
                "librarySectionIds": section_ids,
            }
            
            session = getattr(self.conn.account, '_session', requests.Session())

            # PROTEÇÃO: Adicionado timeout=15
            resp = session.post(url, params=params, headers=headers, json=payload, timeout=15)
            resp.raise_for_status()
            
            # 6. Registo na Base de Dados Local (Movido para antes da limpeza de cache)
            profile = self.data_manager.get_user_profile(plex_user_id)
            if profile:
                profile['libraries'] = json.dumps(library_titles)
                self.data_manager.set_user_profile(plex_user_id, profile)
            
            # 5. Otimização Bulk: Evita sobrecarga de API (sleep) e invalidações de cache constantes
            if not bulk_mode:
                time.sleep(1.0)
                self.conn.account._users = None
                self.invalidate_user_cache()
                
            return {"success": True, "message": _("Bibliotecas e permissões de download atualizadas com sucesso!")}
            
        except RequestException as req_e:
            err_msg = ""
            if req_e.response is not None:
                err_msg = f" (Código: {req_e.response.status_code}) {req_e.response.text}"
            # Aqui também removemos o exc_info=True para não cuspir traceback de erros de rede
            logger.error(f"Erro HTTP ao atualizar Plex API: {req_e}{err_msg}")
            return {"success": False, "message": f"Erro de comunicação com a Plex: Falha de Rede ou Servidor Ocupado."}
        except Exception as e:
            logger.error(f"Erro inesperado ao atualizar bibliotecas e downloads: {e}", exc_info=True)
            return {"success": False, "message": str(e)}

    def update_all_users_libraries(self, library_titles):
        """Atualiza bibliotecas em massa de forma paralela, evitando timeouts do servidor web."""
        all_users = self.get_all_plex_users()
        if not all_users: 
            return {"success": False, "message": _("Falha ao ler utilizadores.")}

        admin_id = str(self.conn.account.id)
        success_count = 0
        
        # PERFORMANCE: Executa as atualizações na API em simultâneo
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for user_data in all_users:
                if str(user_data['id']) != admin_id:
                    # Passa bulk_mode=True para não fazer cache flush por cada utilizador
                    futures.append(executor.submit(self.update_user_libraries, user_data['id'], library_titles, None, True))
            
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result.get("success"):
                        success_count += 1
                except Exception as e:
                    logger.error(f"Erro ao processar atualização de biblioteca em massa: {e}")

        # Limpa o cache apenas uma vez no final da operação em massa
        self.conn.account._users = None
        self.invalidate_user_cache()

        return {"success": True, "message": _("Bibliotecas atualizadas para %(count)d utilizadores.", count=success_count)}

    def block_user(self, plex_user_id, reason='manual'):
        user_to_block = self.get_user_by_id(plex_user_id)
        profile = self.data_manager.get_user_profile(plex_user_id)
        if profile and profile.get('is_admin'):
            return {"success": False, "message": "Contas de Administrador não podem ser bloqueadas."}

        username = user_to_block['username'] if user_to_block else str(plex_user_id)
        try:
            self.data_manager.add_blocked_user(plex_user_id, username, reason=reason)
            if self.stream_manager:
                self.stream_manager.block_user_sessions(plex_user_id, reason="O seu acesso ao servidor foi bloqueado pelo administrador.")
            return {"success": True, "message": _("Utilizador bloqueado.")}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def unblock_user(self, plex_user_id):
        user_to_unblock = self.get_user_by_id(plex_user_id)
        username = user_to_unblock['username'] if user_to_unblock else str(plex_user_id)
        try:
            self.data_manager.remove_blocked_user(plex_user_id)
            return {"success": True, "message": _("Utilizador desbloqueado.")}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def remove_user(self, plex_user_id):
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile: return {"success": False, "message": _("Utilizador não encontrado.")}
        if not self.conn.account: return {"success": False, "message": _("O Plex não está configurado.")}

        username, email = profile.get('username'), profile.get('email')
        try:
            if self.stream_manager: self.stream_manager.block_user_sessions(plex_user_id, "A sua conta está a ser removida.")
            if profile.get('overseerr_access') and email: self.overseerr_manager.remove_user(email)
            self._remove_plex_friend(plex_user_id, email, username)
            self._deactivate_user_profile(plex_user_id, profile)
            return {"success": True, "message": _("Utilizador desativado."), "username": username}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _remove_plex_friend(self, plex_user_id, email, username):
        user_removed = False
        try:
            self.conn.account._users = None 
            friend_to_remove = next((u for u in self.conn.account.users() if str(u.id) == str(plex_user_id)), None)
            if friend_to_remove:
                self.conn.account.removeFriend(friend_to_remove)
                user_removed = True
        except Exception as e: 
            logger.debug(f"Não foi possível remover amigo Plex pelo objeto de amigo: {e}")

        if not user_removed and (email or username):
            try:
                self.conn.account._users = None 
                self.conn.account.removeFriend(email or username)
            except Exception as e: 
                logger.debug(f"Não foi possível remover amigo Plex por email/username: {e}")

    def _deactivate_user_profile(self, plex_user_id, profile):
        from app.extensions import scheduler
        profile['status'], profile['expiration_date'] = 'inactive', None
        for job_key in ['trial_job_id', 'expiration_job_id']:
            if profile.get(job_key):
                try: scheduler.remove_job(profile[job_key])
                except JobLookupError: pass
                profile[job_key] = None
        self.data_manager.set_user_profile(plex_user_id, profile)
        self.data_manager.remove_blocked_user(plex_user_id)
        self.invalidate_user_cache()

    def toggle_overseerr_access(self, plex_user_id, access: bool):
        user_info = self.get_user_by_id(plex_user_id)
        if not user_info: return {"success": False, "message": _("Utilizador não encontrado.")}
        profile = self.data_manager.get_user_profile(plex_user_id)
        
        if access: result = self.overseerr_manager.import_from_plex(user_info)
        else: result = self.overseerr_manager.remove_user(user_info['email'])
        
        if result.get("success"):
            profile['overseerr_access'] = access
            self.data_manager.set_user_profile(plex_user_id, profile)
            return {"success": True, "message": "Sucesso."}
        return {"success": False, "message": "Erro."}
