# app/services/plex/user_manager.py

import logging
import json
import base64
from datetime import datetime, timedelta
from plexapi.exceptions import NotFound
from requests.exceptions import RequestException
from flask_babel import gettext as _
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from flask import current_app, url_for
from urllib.parse import urlparse
from apscheduler.jobstores.base import JobLookupError

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
        self._user_cache = None
        self._user_cache_time = None
        self._cache_ttl = timedelta(seconds=300)
        self.stream_manager = None

    def invalidate_user_cache(self):
        """Invalida a cache de utilizadores."""
        self._user_cache = None
        self._user_cache_time = None
        logger.info(_("Cache de utilizadores do Plex invalidado."))

    def get_user_by_id(self, plex_user_id):
        """Busca um único utilizador pelo seu ID do Plex, utilizando a cache."""
        all_users = self.get_all_plex_users()
        if all_users is None:
            return None
        return next((u for u in all_users if u['id'] == plex_user_id), None)

    def get_all_plex_users(self, force_refresh=False):
        """
        Obtém todos os utilizadores com acesso ao servidor Plex configurado, incluindo a conta do administrador.
        """
        if not self.conn.account or not self.conn.plex:
            return None

        now = datetime.now()
        if not force_refresh and self._user_cache and self._user_cache_time and (now - self._user_cache_time < self._cache_ttl):
            return self._user_cache

        try:
            if force_refresh:
                self.conn.account.reload()

            server_identifier = self.conn.plex.machineIdentifier
            all_friends = self.conn.account.users()

            with current_app.app_context():
                users_with_access = []
                for user in all_friends:
                    if any(s.machineIdentifier == server_identifier for s in user.servers):
                        user_thumb_url = None
                        if user.thumb:
                            try:
                                parsed_thumb = urlparse(user.thumb)
                                path_with_query = parsed_thumb.path
                                if parsed_thumb.query: path_with_query += "?" + parsed_thumb.query
                                
                                payload_str = f"plex_account:{path_with_query}"
                                b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                                user_thumb_url = url_for('image.proxy_image', source=b64_payload)
                            except Exception as e:
                                logger.error(f"Falha ao processar a URL do avatar para o utilizador {user.username}: {e}")

                        users_with_access.append({
                            'username': user.username, 
                            'email': user.email, 
                            'id': user.id, 
                            'thumb': user_thumb_url, 
                            'servers': [s.name for s in user.servers]
                        })

                if self.conn.account:
                    admin_id = self.conn.account.id
                    is_admin_in_list = any(u['id'] == admin_id for u in users_with_access)
                    
                    if not is_admin_in_list:
                        admin_thumb_url = None
                        if self.conn.account.thumb:
                            try:
                                parsed_thumb = urlparse(self.conn.account.thumb)
                                path_with_query = parsed_thumb.path
                                if parsed_thumb.query: path_with_query += "?" + parsed_thumb.query
                                
                                payload_str = f"plex_account:{path_with_query}"
                                b64_payload = base64.urlsafe_b64encode(payload_str.encode('utf-8')).decode('utf-8')
                                admin_thumb_url = url_for('image.proxy_image', source=b64_payload)
                            except Exception as e:
                                logger.error(f"Falha ao processar a URL do avatar para o administrador: {e}")

                        users_with_access.append({
                            'username': self.conn.account.username, 
                            'email': self.conn.account.email, 
                            'id': admin_id, 
                            'thumb': admin_thumb_url, 
                            'servers': [self.conn.plex.friendlyName]
                        })
            
            self._user_cache = users_with_access
            self._user_cache_time = now
            return users_with_access
        except RequestException as e:
            logger.error(_("Erro de rede ao obter utilizadores do Plex: %(error)s", error=e))
            self.invalidate_user_cache()
            return None
        except Exception as e:
            logger.error(_("Erro inesperado ao obter utilizadores do Plex: %(error)s", error=e), exc_info=True)
            self.invalidate_user_cache()
            return None

    def get_user_libraries(self, plex_user_id):
        user = self.get_user_by_id(plex_user_id)
        if not user:
            return {"success": False, "message": _("Utilizador não encontrado.")}
        
        email = user['email']
        username = user['username']

        if not self.conn.account:
            return {"success": False, "message": _("A conta Plex não está configurada.")}
            
        # CORREÇÃO: Adiciona uma verificação para a conta do administrador
        if self.conn.account.id == plex_user_id:
            all_libraries = [sec.title for sec in self.conn.plex.library.sections()]
            return {"success": True, "libraries": all_libraries}
            
        try:
            plex_user_obj = self.conn.account.user(email)
            profile = self.data_manager.get_user_profile(plex_user_id)
            if profile and profile.get('libraries'):
                try:
                    return {"success": True, "libraries": json.loads(profile['libraries'])}
                except (json.JSONDecodeError, TypeError):
                    pass

            server_resource = next((s for s in plex_user_obj.servers if s.machineIdentifier == self.conn.plex.machineIdentifier), None)
            library_titles = [sec.title for sec in server_resource.sections()] if server_resource else []
            
            profile['libraries'] = json.dumps(library_titles)
            self.data_manager.set_user_profile(plex_user_id, profile)
            return {"success": True, "libraries": library_titles}
        except NotFound:
            return {"success": False, "message": _("Utilizador com o email %(email)s não encontrado na sua conta Plex.", email=email)}
        except Exception as e:
            logger.error(_("Erro ao obter bibliotecas para %(email)s: %(error)s", email=email, error=e), exc_info=True)
            return {"success": False, "message": _("Ocorreu um erro inesperado: %(error)s", error=e)}

    def update_user_libraries(self, plex_user_id, library_titles):
        user = self.get_user_by_id(plex_user_id)
        if not user:
            return {"success": False, "message": _("Utilizador não encontrado.")}
        
        if not self.conn.account:
            return {"success": False, "message": _("A conta Plex não está configurada.")}
            
        # CORREÇÃO: Adiciona uma verificação para a conta do administrador
        if self.conn.account.id == plex_user_id:
            return {"success": True, "message": _("O administrador já tem acesso a todas as bibliotecas. Nenhuma alteração é necessária.")}
            
        try:
            user_to_update = self.conn.account.user(user['email'])
            libraries_to_share = [s for s in self.conn.plex.library.sections() if s.title in library_titles]
            
            self.conn.account.updateFriend(user=user_to_update, server=self.conn.plex, sections=libraries_to_share)
            
            profile = self.data_manager.get_user_profile(plex_user_id)
            profile['libraries'] = json.dumps(library_titles)
            self.data_manager.set_user_profile(plex_user_id, profile)
            return {"success": True, "message": _("Bibliotecas de %(username)s atualizadas com sucesso.", username=user['username'])}
        except Exception as e:
            logger.error(_("Erro ao atualizar bibliotecas para %(email)s: %(error)s", email=user['email'], error=e), exc_info=True)
            return {"success": False, "message": str(e)}

    def update_all_users_libraries(self, library_titles):
        """
        Atualiza as bibliotecas para TODOS os utilizadores com acesso ao servidor.
        """
        all_users = self.get_all_plex_users()
        if not all_users:
            return {"success": False, "message": _("Não foi possível obter a lista de usuário do Plex.")}

        for user_data in all_users:
            # A lógica dentro de update_user_libraries já ignora o admin
            self.update_user_libraries(user_data['id'], library_titles)

        return {"success": True, "message": _("Bibliotecas atualizadas para todos os usuário.")}

    def block_user(self, plex_user_id, reason='manual'):
        user_to_block = self.get_user_by_id(plex_user_id)
        if not user_to_block:
            return {"success": False, "message": _("Utilizador não encontrado.")}

        if not self.conn.account:
            return {"success": False, "message": _("A conta Plex não está configurada.")}

        try:
            username = user_to_block['username']
            self.data_manager.add_blocked_user(plex_user_id, username, reason=reason)

            if self.stream_manager:
                # Lógica para obter a mensagem de bloqueio
                if reason == 'expired':
                    reason_message = "A sua subscrição expirou. Por favor, renove para continuar."
                elif reason == 'trial_expired':
                    reason_message = "O seu período de teste terminou. Renove para continuar."
                else:
                    reason_message = "O seu acesso ao servidor foi bloqueado pelo administrador."
                self.stream_manager.block_user_sessions(plex_user_id, reason=reason_message)
            
            return {"success": True, "message": _("Utilizador %(username)s bloqueado com sucesso.", username=username)}
        except Exception as e:
            logger.error(_("Erro ao bloquear o utilizador %(username)s: %(error)s", username=user_to_block['username'], error=e), exc_info=True)
            return {"success": False, "message": str(e)}

    def unblock_user(self, plex_user_id):
        user_to_unblock = self.get_user_by_id(plex_user_id)
        if not user_to_unblock:
            return {"success": False, "message": _("Utilizador não encontrado.")}

        try:
            self.data_manager.remove_blocked_user(plex_user_id)
            return {"success": True, "message": _("Utilizador %(username)s desbloqueado com sucesso.", username=user_to_unblock['username'])}
        except Exception as e:
            logger.error(_("Erro ao desbloquear o utilizador %(username)s: %(error)s", username=user_to_unblock['username'], error=e), exc_info=True)
            return {"success": False, "message": str(e)}

    def remove_user(self, plex_user_id):
        # Primeiro, obtemos o perfil local para ter o email/username, mesmo que o utilizador já não exista no Plex
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile:
            # Se não houver nem perfil local, não há nada a fazer
            return {"success": False, "message": _("Utilizador não encontrado na base de dados local.")}

        email = profile.get('email')
        username = profile.get('username')

        if not self.conn.account:
            return {"success": False, "message": _("O Plex não está configurado.")}
        try:
            from app.extensions import scheduler

            # Interrompe os streams do utilizador, se houver
            if self.stream_manager:
                self.stream_manager.block_user_sessions(plex_user_id, "A sua conta está a ser removida do servidor.")
            
            # Remove do Overseerr, se aplicável
            if profile.get('overseerr_access') and email:
                self.overseerr_manager.remove_user(email)

            # Tenta remover do Plex, mas não falha se já não existir
            try:
                # Tenta encontrar o utilizador pelo email ou username para remover
                identifier = email or username
                if identifier:
                    plex_user_obj = self.conn.account.user(identifier)
                    self.conn.account.removeFriend(plex_user_obj)
                    logger.info(f"Acesso ao Plex para '{username}' removido com sucesso.")
                else:
                    logger.warning(f"Não foi possível tentar remover '{username}' do Plex por falta de email/username.")
            except NotFound:
                logger.warning(f"Utilizador '{username}' já não era amigo na conta Plex. A continuar com a desativação local.")
            
            # Procede com a limpeza local independentemente do resultado do Plex
            profile['status'] = 'inactive'
            profile['expiration_date'] = None

            if profile.get('trial_job_id'):
                try: scheduler.remove_job(profile['trial_job_id'])
                except JobLookupError: pass
                profile['trial_job_id'] = None
            if profile.get('expiration_job_id'):
                 try: scheduler.remove_job(profile['expiration_job_id'])
                 except JobLookupError: pass
                 profile['expiration_job_id'] = None

            self.data_manager.set_user_profile(plex_user_id, profile)
            self.data_manager.remove_blocked_user(plex_user_id)
            self.invalidate_user_cache()

            return {"success": True, "message": _("Utilizador %(username)s desativado e acesso removido com sucesso.", username=username), "username": username}
        except Exception as e:
            logger.error(_("Erro ao remover o utilizador %(username)s: %(error)s", username=username, error=e), exc_info=True)
            return {"success": False, "message": str(e)}

    def toggle_overseerr_access(self, plex_user_id, access: bool):
        user_info = self.get_user_by_id(plex_user_id)
        if not user_info:
            return {"success": False, "message": _("Utilizador não encontrado no Plex.")}

        profile = self.data_manager.get_user_profile(plex_user_id)
        
        if access:
            result = self.overseerr_manager.import_from_plex(user_info)
        else:
            result = self.overseerr_manager.remove_user(user_info['email'])
        
        if result.get("success"):
            profile['overseerr_access'] = access
            self.data_manager.set_user_profile(plex_user_id, profile)
            message = _("Acesso ao Overseerr concedido.") if access else _("Acesso ao Overseerr removido.")
            return {"success": True, "message": message}
        else:
            return {"success": False, "message": result.get("message", _("Erro desconhecido."))}
