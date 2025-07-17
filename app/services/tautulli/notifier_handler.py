# app/services/tautulli/notifier_handler.py
import json
import logging
import threading
from flask_babel import gettext as _
from requests.exceptions import RequestException

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

class NotifierHandler:
    """Gere a lógica de atualização dos notificadores do Tautulli."""

    def __init__(self, api_client, data_manager):
        self.api = api_client
        self.data_manager = data_manager
        self.update_lock = threading.Lock()

    def _update_notifier_safely(self, notifier_id, update_logic_func):
        """Executa uma atualização atômica na configuração de um notificador."""
        with self.update_lock:
            logger.info(_("LOCK ADQUIRIDO: A iniciar atualização segura para o notificador ID: %(id)s", id=notifier_id))
            try:
                current_config = self.api.get_notifier_config(notifier_id)
                modified_config = update_logic_func(current_config)

                # Prepara os dados para a escrita
                data_for_body = {k: v for k, v in modified_config.items() if k not in ['config', 'custom_conditions']}
                data_for_body.update(modified_config.get('config', {}))
                if "script" in data_for_body: data_for_body["scripts_script"] = data_for_body.pop("script")
                if "script_folder" in data_for_body: data_for_body["scripts_script_folder"] = data_for_body.pop("script_folder")
                if "timeout" in data_for_body: data_for_body["scripts_timeout"] = data_for_body.pop("timeout")
                data_for_body['custom_conditions'] = json.dumps(modified_config.get('custom_conditions', []))

                self.api.set_notifier_config(notifier_id, data_for_body)
                logger.info(_("LOCK LIBERADO: Atualização para o notificador ID %(id)s concluída com sucesso.", id=notifier_id))
                return {"success": True}
            except (RequestException, ValueError) as e:
                logger.error(_("LOCK LIBERADO: Erro durante a atualização segura do notificador %(id)s: %(error)s", id=notifier_id, error=e))
                return {"success": False, "message": str(e)}
            except Exception as e:
                logger.error(_("LOCK LIBERADO: Erro inesperado durante a atualização segura do notificador %(id)s: %(error)s", id=notifier_id, error=e), exc_info=True)
                return {"success": False, "message": _("Erro inesperado: %(error)s", error=e)}

    def update_screen_limit(self, user_email, username, screens):
        """Atualiza o limite de telas de um utilizador de forma segura."""
        config = load_or_create_config()
        notifier_id = config.get("SCREEN_LIMIT_NOTIFIER_ID")
        if not notifier_id:
            return {"success": False, "message": _("ID do notificador de limite de telas não configurado.")}

        def update_logic(current_config):
            custom_conditions = current_config.get("custom_conditions", [])
            for condition in custom_conditions:
                if condition.get("parameter") == "user_email":
                    user_list = set(c for c in condition.get("value", []) if c != '~')
                    user_list.discard(user_email)
                    condition["value"] = sorted(list(user_list)) if user_list else ['~']
            if screens > 0:
                condition_found = False
                for i, c in enumerate(custom_conditions):
                    if (c.get("parameter") == "user_streams" and c.get("value") == [str(screens)] and
                        (i + 1) < len(custom_conditions) and custom_conditions[i+1].get("parameter") == "user_email"):
                        email_list = set(custom_conditions[i+1].get("value", []))
                        email_list.discard('~')
                        email_list.add(user_email)
                        custom_conditions[i+1]["value"] = sorted(list(email_list))
                        condition_found = True
                        break
                if not condition_found:
                    raise ValueError(_("Condição para %(screens)s tela(s) não encontrada na configuração do Tautulli.", screens=screens))
            current_config["custom_conditions"] = custom_conditions
            return current_config

        result = self._update_notifier_safely(notifier_id, update_logic)
        if result.get("success"):
            profile = self.data_manager.get_user_profile(username)
            profile['screen_limit'] = screens
            self.data_manager.set_user_profile(username, profile)
            result["message"] = _("Limite de %(screens)s tela(s) aplicado.", screens=screens) if screens > 0 else _("Limite removido.")
        return result
            
    def update_all_users_screen_limit(self, users, screens: int):
        """Atualiza o limite de telas para todos os utilizadores de forma segura."""
        user_emails = [u['email'] for u in users]
        config = load_or_create_config()
        notifier_id = config.get("SCREEN_LIMIT_NOTIFIER_ID")
        if not notifier_id:
            return {"success": False, "message": _("ID do notificador de limite de telas não configurado.")}

        def update_logic(current_config):
            custom_conditions = current_config.get("custom_conditions", [])
            for c in custom_conditions:
                if c.get("parameter") == "user_email": c["value"] = ['~']
            if screens > 0:
                condition_found = False
                for i, c in enumerate(custom_conditions):
                    if (c.get("parameter") == "user_streams" and c.get("value") == [str(screens)] and
                        (i + 1) < len(custom_conditions) and custom_conditions[i+1].get("parameter") == "user_email"):
                        custom_conditions[i+1]["value"] = sorted(user_emails)
                        condition_found = True
                        break
                if not condition_found:
                    raise ValueError(_("Condição para %(screens)s tela(s) não encontrada.", screens=screens))
            current_config["custom_conditions"] = custom_conditions
            return current_config
        
        result = self._update_notifier_safely(notifier_id, update_logic)
        if result.get("success"):
            for user in users:
                profile = self.data_manager.get_user_profile(user['username'])
                profile['screen_limit'] = screens
                self.data_manager.set_user_profile(user['username'], profile)
            result["message"] = _("Limite de %(screens)s tela(s) aplicado para todos.", screens=screens) if screens > 0 else _("Limites removidos de todos.")
        return result

    def manage_block_unblock(self, user_email, username, action: str, notifier_id: int = None, reason: str = 'manual'):
        """Adiciona ou remove um utilizador de uma lista de bloqueio de forma segura."""
        config = load_or_create_config()
        if notifier_id is None:
            notifier_id = config.get("BLOCKING_NOTIFIER_ID")
        if not notifier_id or notifier_id == 0:
            logger.warning(_("Nenhum notificador definido para a ação de '%(action)s' para o utilizador '%(user)s'.", action=action, user=username))
            return {"success": True, "message": _("Nenhum notificador configurado para esta ação.")}

        log_reason = _(" e motivo: %(reason)s", reason=reason) if action == 'add' else ""
        logger.info(_("A preparar atualização segura '%(action)s' para o utilizador '%(user)s' com o notificador ID: %(id)s%(reason)s", action=action, user=username, id=notifier_id, reason=log_reason))

        def update_logic(current_config):
            custom_conditions = current_config.get("custom_conditions", [])
            condition_found = False
            for c in custom_conditions:
                if c.get("parameter") == "user_email":
                    user_list = set(v for v in c.get("value", []) if v != '~')
                    if action == 'add': user_list.add(user_email)
                    else: user_list.discard(user_email)
                    c["value"] = sorted(list(user_list)) if user_list else ['~']
                    condition_found = True
                    break
            if not condition_found:
                raise ValueError(_("Condição 'user_email' não encontrada na configuração do notificador."))
            current_config["custom_conditions"] = custom_conditions
            return current_config

        result = self._update_notifier_safely(notifier_id, update_logic)
        if result.get("success"):
            if action == 'add':
                self.data_manager.add_blocked_user(username, reason=reason)
                result["message"] = _("Utilizador %(username)s bloqueado.", username=username)
            else:
                self.data_manager.remove_blocked_user(username)
                result["message"] = _("Utilizador %(username)s desbloqueado.", username=username)
        return result
