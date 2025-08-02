# app/services/tautulli_manager.py

import logging
from flask_babel import gettext as _

from .tautulli.api_client import TautulliApiClient
from .tautulli.notifier_handler import NotifierHandler
from .tautulli.stats_handler import StatsHandler

logger = logging.getLogger(__name__)

class TautulliManager:
    """
    Atua como uma fachada, a coordenar os vários serviços do Tautulli.
    Isto mantém a API pública consistente para o resto da aplicação.
    """
    def __init__(self, data_manager):
        self.api_client = TautulliApiClient()
        self.notifiers = NotifierHandler(self.api_client, data_manager)
        self.stats = StatsHandler(self.api_client, data_manager)

    def reload_credentials(self):
        """Recarrega as credenciais e configurações para o Tautulli."""
        logger.info("A recarregar as credenciais do Tautulli Manager...")
        self.api_client.reload_config()

    def check_status(self):
        """Verifica o estado da conexão com o Tautulli."""
        if not self.api_client.is_configured:
            return {"status": "OFFLINE", "message": _("Não configurado.")}
        
        test_result = self.api_client.test_connection(self.api_client.base_url, self.api_client.api_key)
        if test_result['success']:
            return {"status": "ONLINE", "message": _("Conectado com sucesso.")}
        else:
            return {"status": "OFFLINE", "message": test_result['message']}

    def test_connection(self, url, api_key):
        return self.api_client.test_connection(url, api_key)

    def update_screen_limit(self, user_email, username, screens):
        return self.notifiers.update_screen_limit(user_email, username, screens)

    def update_all_users_screen_limit(self, users, screens: int):
        return self.notifiers.update_all_users_screen_limit(users, screens)

    def manage_block_unblock(self, user_email, username, action: str, notifier_id: int = None, reason: str = 'manual'):
        return self.notifiers.manage_block_unblock(user_email, username, action, notifier_id, reason)

    def get_watch_stats(self, days=7, plex_users_info=None):
        return self.stats.get_watch_stats(days, plex_users_info)

    def get_user_watch_details(self, username, days=7, current_user=None):
        return self.stats.get_user_watch_details(username, days, current_user)

    def get_recently_added(self, days=7):
        return self.stats.get_recently_added(days)

    def get_user_devices(self, username):
        return self.stats.get_user_devices(username)

    def set_notifier_conditions(self, url, api_key, notifier_id, notifier_type):
        """
        Este método é um caso especial, pois é usado durante a configuração,
        antes que o cliente da API principal esteja totalmente configurado.
        Portanto, ele interage diretamente com a lógica de payload, mas poderia ser
        movido para o NotifierHandler se a lógica de configuração fosse alterada.
        """
        base_payload = self._get_base_payload_for_notifier(notifier_type)
        if not base_payload:
            return {"success": False, "message": _("Tipo de notificador inválido.")}
        
        try:
            import requests, json
            api_url = f"{url.rstrip('/')}/api/v2"
            params_get = {"apikey": api_key, "cmd": "get_notifier_config", "notifier_id": notifier_id}
            response_get = requests.get(api_url, params=params_get, timeout=10)
            response_get.raise_for_status()
            current_config = response_get.json().get("response", {}).get("data", {})
            
            params_for_set = {"apikey": api_key, "cmd": "set_notifier_config", "notifier_id": notifier_id}
            config_section = current_config.get('config', {})
            params_for_set.update({
                'agent_id': current_config.get('agent_id'),
                'scripts_script_folder': config_section.get('script_folder'),
                'scripts_script': config_section.get('script'),
                'scripts_timeout': config_section.get('timeout'),
                'friendly_name': base_payload['friendly_name'],
                'custom_conditions': json.dumps(base_payload.get('custom_conditions', [])),
                'custom_conditions_logic': base_payload.get('custom_conditions_logic', '')
            })
            all_triggers = ["on_play", "on_pause", "on_resume", "on_stop", "on_watched", "on_create", "on_new_device", "on_browser_stream", "on_concurrent"]
            for trigger in all_triggers: params_for_set[trigger] = 0
            for trigger, value in base_payload.get("actions", {}).items(): params_for_set[trigger] = value
            for key, value in base_payload.get("notify_text", {}).items(): params_for_set[key] = value
            
            response_set = requests.get(api_url, params=params_for_set, timeout=10)
            response_set.raise_for_status()
            set_response_data = response_set.json().get("response", {})

            if set_response_data.get("result") == "success":
                return {"success": True, "message": _("Notificador '%(name)s' configurado com sucesso!", name=base_payload['friendly_name'])}
            else:
                error_msg = set_response_data.get("message", _("Falha ao atualizar o notificador no Tautulli."))
                logger.error(_("Erro da API do Tautulli ao configurar notificador: %(error)s. Parâmetros enviados: %(params)s", error=error_msg, params=params_for_set))
                return {"success": False, "message": error_msg}
        except Exception as e:
            logger.error(_("Erro inesperado ao auto-configurar notificador %(id)s: %(error)s", id=notifier_id, error=e), exc_info=True)
            return {'success': False, 'message': str(e)}

    def _get_base_payload_for_notifier(self, notifier_type):
        """Retorna o payload base (nome, condições, ações, etc.) para um tipo de notificador."""
        if notifier_type == 'screen_limit':
            return {
                "friendly_name": _("Limite de Tela (Painel)"),
                "custom_conditions": [
                    {"parameter": "user_streams", "operator": "is greater than", "value": ["1"]}, {"parameter": "user_email", "operator": "is", "value": ["~"]},
                    {"parameter": "user_streams", "operator": "is greater than", "value": ["2"]}, {"parameter": "user_email", "operator": "is", "value": ["~"]},
                    {"parameter": "user_streams", "operator": "is greater than", "value": ["3"]}, {"parameter": "user_email", "operator": "is", "value": ["~"]},
                    {"parameter": "user_streams", "operator": "is greater than", "value": ["4"]}, {"parameter": "user_email", "operator": "is", "value": ["~"]}
                ],
                "custom_conditions_logic": "({1} and {2}) or ({3} and {4}) or ({5} and {6}) or ({7} and {8})",
                "actions": {"on_play": 1},
                "notify_text": {"on_play_subject": "--jbop stream --username {user} --sessionId {session_id} --limit 'true' --killMessage 'Você atingiu o limite de telas simultâneas.'", "on_play_body": ""}
            }
        elif notifier_type == 'blocking':
            return {
                "friendly_name": _("Bloqueio de Usuario (Painel)"),
                "actions": {"on_play": 1},
                "notify_text": {"on_play_subject": "--jbop allStreams --username {user_email} --sessionId {session_id} --killMessage 'Seu acesso expirou. Caso deseje continuar só regularizar'", "on_play_body": ""},
                "custom_conditions": [{"parameter": "user_email", "operator": "is", "value": ["~"]}]
            }
        elif notifier_type == 'trial':
            return {
                "friendly_name": _("Periodo de Teste (Painel)"),
                "actions": {"on_play": 1},
                "notify_text": {"on_play_subject": "--jbop allStreams --username {user_email} --sessionId {session_id} --killMessage 'Seu período de teste foi finalizado'", "on_play_body": ""},
                "custom_conditions": [{"parameter": "user_email", "operator": "is", "value": ["~"]}]
            }
        return None

