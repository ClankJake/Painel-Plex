# app/services/plex/user_manager.py
import logging
import json
from datetime import datetime, timedelta
from plexapi.exceptions import NotFound
from requests.exceptions import RequestException
from flask_babel import gettext as _
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from flask import current_app

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
        self.stream_manager = None # Será injetado pelo PlexManager

    def invalidate_user_cache(self):
        """Invalida a cache de utilizadores."""
        self._user_cache = None
        self._user_cache_time = None
        logger.info(_("Cache de utilizadores do Plex invalidado."))

    def get_all_plex_users(self, force_refresh=False):
        """
        Obtém todos os utilizadores com acesso ao servidor Plex configurado, utilizando uma cache.
        """
        if not self.conn.account or not self.conn.plex:
            return None

        now = datetime.now()
        if not force_refresh and self._user_cache and self._user_cache_time and (now - self._user_cache_time < self._cache_ttl):
            logger.debug(_("A devolver a lista de utilizadores do servidor a partir da cache."))
            return self._user_cache

        logger.debug(_("A obter e filtrar a lista de utilizadores da API do Plex (cache expirada ou forçada)."))
        try:
            if force_refresh:
                self.conn.account.reload()

            server_identifier = self.conn.plex.machineIdentifier
            all_friends = self.conn.account.users()
            users_with_access = [
                {'username': user.username, 'email': user.email, 'id': user.id, 'thumb': user.thumb, 'servers': user.servers}
                for user in all_friends if any(s.machineIdentifier == server_identifier for s in user.servers)
            ]
            
            logger.debug(_("Encontrados %(friends)d amigos na conta, %(access)d com acesso a este servidor.", friends=len(all_friends), access=len(users_with_access)))
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

    def get_user_libraries(self, email):
        """
        Obtém as bibliotecas a que um utilizador específico tem acesso.
        """
        if not self.conn.account:
            return {"success": False, "message": _("A conta Plex não está configurada.")}
        try:
            user = self.conn.account.user(email)
            username = user.username
            profile = self.data_manager.get_user_profile(username)
            if profile and profile.get('libraries'):
                try:
                    return {"success": True, "libraries": json.loads(profile['libraries'])}
                except (json.JSONDecodeError, TypeError):
                    logger.warning(_("Não foi possível descodificar as bibliotecas em cache para '%(username)s'. A obter do Plex.", username=username))

            server_resource = next((s for s in user.servers if s.machineIdentifier == self.conn.plex.machineIdentifier), None)
            library_titles = [sec.title for sec in server_resource.sections()] if server_resource else []
            
            profile['libraries'] = json.dumps(library_titles)
            self.data_manager.set_user_profile(username, profile)
            return {"success": True, "libraries": library_titles}
        except NotFound:
            return {"success": False, "message": _("Utilizador com o email %(email)s não encontrado na sua conta Plex.", email=email)}
        except Exception as e:
            logger.error(_("Erro ao obter bibliotecas para %(email)s: %(error)s", email=email, error=e), exc_info=True)
            return {"success": False, "message": _("Ocorreu um erro inesperado: %(error)s", error=e)}

    def update_user_libraries(self, email, library_titles):
        """
        Atualiza as bibliotecas para um utilizador específico.
        """
        if not self.conn.account:
            return {"success": False, "message": _("A conta Plex não está configurada.")}
        try:
            user_to_update = self.conn.account.user(email)
            username = user_to_update.username
            libraries_to_share = [s for s in self.conn.plex.library.sections() if s.title in library_titles]
            
            self.conn.account.updateFriend(user=user_to_update, server=self.conn.plex, sections=libraries_to_share)
            
            profile = self.data_manager.get_user_profile(username)
            profile['libraries'] = json.dumps(library_titles)
            self.data_manager.set_user_profile(username, profile)
            return {"success": True, "message": _("Bibliotecas de %(username)s atualizadas com sucesso.", username=username)}
        except Exception as e:
            logger.error(_("Erro ao atualizar bibliotecas para %(email)s: %(error)s", email=email, error=e), exc_info=True)
            return {"success": False, "message": str(e)}

    def update_all_users_libraries(self, library_titles):
        """
        Atualiza as bibliotecas para TODOS os utilizadores com acesso ao servidor, usando threads para otimização.
        """
        if not self.conn.account:
            return {"success": False, "message": _("A conta Plex não está configurada.")}
        
        all_users = self.get_all_plex_users()
        if not all_users:
            return {"success": False, "message": _("Não foi possível obter a lista de utilizadores do Plex.")}

        libraries_to_share = [s for s in self.conn.plex.library.sections() if s.title in library_titles]
        
        success_count = 0
        error_count = 0
        lock = Lock()
        app = current_app._get_current_object()

        def _update_single_user(user_data, app_context):
            nonlocal success_count, error_count
            with app_context.app_context():
                try:
                    user_to_update = self.conn.account.user(user_data['email'])
                    self.conn.account.updateFriend(user=user_to_update, server=self.conn.plex, sections=libraries_to_share)
                    
                    profile = self.data_manager.get_user_profile(user_data['username'])
                    profile['libraries'] = json.dumps(library_titles)
                    self.data_manager.set_user_profile(user_data['username'], profile)
                    
                    with lock:
                        success_count += 1
                    logger.info(f"Bibliotecas de {user_data['username']} atualizadas com sucesso (em massa).")
                except Exception as e:
                    with lock:
                        error_count += 1
                    logger.error(f"Erro ao atualizar bibliotecas para {user_data['username']} (em massa): {e}")

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_update_single_user, user, app) for user in all_users]
            for future in as_completed(futures):
                future.result()

        message = _("Bibliotecas atualizadas para %(success)d utilizadores. Falha em %(errors)d.", success=success_count, errors=error_count)
        return {"success": error_count == 0, "message": message}

    def block_user(self, email, reason='manual'):
        """
        Bloqueia um utilizador, terminando as suas sessões e atualizando o estado na base de dados.
        """
        if not self.conn.account:
            return {"success": False, "message": _("A conta Plex não está configurada.")}

        try:
            user_to_block = self.conn.account.user(email)
            username = user_to_block.username
            
            self.data_manager.add_blocked_user(username, reason=reason)

            if self.stream_manager:
                reason_message = "O seu acesso ao servidor foi bloqueado pelo administrador."
                if reason == 'expired':
                    reason_message = "A sua subscrição expirou. Por favor, renove para continuar."
                elif reason == 'trial_expired':
                    reason_message = "O seu período de teste terminou. Renove para continuar."
                
                self.stream_manager.block_user_sessions(username, reason=reason_message)
            
            return {"success": True, "message": _("Utilizador %(username)s bloqueado com sucesso.", username=username)}
        except NotFound:
            return {"success": False, "message": _("Utilizador com o email %(email)s não encontrado.", email=email)}
        except Exception as e:
            logger.error(_("Erro ao bloquear o utilizador %(email)s: %(error)s", email=email, error=e), exc_info=True)
            return {"success": False, "message": str(e)}

    def unblock_user(self, email):
        """
        Desbloqueia um utilizador, removendo-o da base de dados de bloqueio.
        """
        if not self.conn.account:
            return {"success": False, "message": _("A conta Plex não está configurada.")}
        try:
            user_to_unblock = self.conn.account.user(email)
            username = user_to_unblock.username
            
            self.data_manager.remove_blocked_user(username)
            
            return {"success": True, "message": _("Utilizador %(username)s desbloqueado com sucesso.", username=username)}
        except NotFound:
            return {"success": False, "message": _("Utilizador com o email %(email)s não encontrado.", email=email)}
        except Exception as e:
            logger.error(_("Erro ao desbloquear o utilizador %(email)s: %(error)s", email=email, error=e), exc_info=True)
            return {"success": False, "message": str(e)}

    def remove_user(self, email):
        """
        Remove completamente um utilizador do Plex, Tautulli e da base de dados local.
        """
        if not self.conn.account:
            return {"success": False, "message": _("O Plex não está configurado.")}
        try:
            from app.extensions import scheduler
            
            user_to_remove = self.conn.account.user(email)
            username = user_to_remove.username
            profile = self.data_manager.get_user_profile(username)

            # 1. Termina as sessões ativas
            if self.stream_manager:
                self.stream_manager.block_user_sessions(username, "A sua conta está a ser removida do servidor.")
            
            # 2. Remove tarefas agendadas associadas
            if profile.get('trial_job_id'):
                try: scheduler.remove_job(profile['trial_job_id'])
                except Exception as e: logger.warning(_("Não foi possível remover a tarefa de teste agendada '%(job_id)s': %(error)s", job_id=profile['trial_job_id'], error=e))
            if profile.get('expiration_job_id'):
                 try: scheduler.remove_job(profile['expiration_job_id'])
                 except Exception as e: logger.warning(_("Não foi possível remover a tarefa de expiração agendada '%(job_id)s': %(error)s", job_id=profile['expiration_job_id'], error=e))

            # 3. Remove o acesso ao Overseerr
            if profile.get('overseerr_access'):
                self.overseerr_manager.remove_user(email)

            # 4. Remove a partilha do Plex
            self.conn.account.removeFriend(user_to_remove)
            
            # 5. Limpa os dados locais
            self.invalidate_user_cache()
            self.data_manager.remove_blocked_user(username)
            self.data_manager.delete_user_profile(username)

            return {"success": True, "message": _("Utilizador %(username)s removido com sucesso de todos os serviços.", username=username)}
        except NotFound:
            return {"success": False, "message": _("Utilizador com o email %(email)s não encontrado.", email=email)}
        except Exception as e:
            logger.error(_("Erro ao remover o utilizador %(email)s: %(error)s", email=email, error=e), exc_info=True)
            return {"success": False, "message": str(e)}

    def toggle_overseerr_access(self, email, username, access: bool):
        """
        Concede ou revoga o acesso de um utilizador ao Overseerr.
        """
        profile = self.data_manager.get_user_profile(username)
        
        if access:
            user_info = next((u for u in self.get_all_plex_users() if u['username'] == username), None)
            if not user_info:
                return {"success": False, "message": _("Utilizador não encontrado no Plex.")}
            result = self.overseerr_manager.import_from_plex(user_info)
        else:
            result = self.overseerr_manager.remove_user(email)
        
        if result.get("success"):
            profile['overseerr_access'] = access
            self.data_manager.set_user_profile(username, profile)
            message = _("Acesso ao Overseerr concedido.") if access else _("Acesso ao Overseerr removido.")
            return {"success": True, "message": message}
        else:
            return {"success": False, "message": result.get("message", _("Erro desconhecido."))}

