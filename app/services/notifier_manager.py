# app/services/notifier_manager.py

import json
import logging
import uuid
import time
import threading
from datetime import datetime
import requests
from flask_babel import gettext as _, ngettext
from flask import url_for

from ..config import load_or_create_config
from ..models import UserProfile

logger = logging.getLogger(__name__)

# --- CONSTANTES DE TEMPLATES PADRÃO ---
# Fallback para garantir que as notificações nunca sejam enviadas vazias se o utilizador apagar o template nas configurações.
DEFAULT_TEMPLATES = {
    "TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}",
    "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "✅ Olá {username}! A sua subscrição foi renovada com sucesso. O seu novo vencimento é em {new_date}.",
    "TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE": "Seu período de teste para {username} terminou. Para continuar com o acesso, renove sua assinatura em: {payment_link}",
    "DISCORD_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso de Vencimento", "description": "Olá **{username}**! 👋\\n\\nO seu acesso ao Plex está prestes a expirar em **{days} dia(s)**, no dia **{date}**.\\n\\nPara evitar a interrupção do serviço, por favor, [clique aqui para renovar]({payment_link}).", "color": 16776960}]}',
    "DISCORD_RENEWAL_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Renovação Confirmada!", "description": "Olá **{username}**! ✅\\n\\nA sua assinatura foi renovada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\nObrigado e aproveite!", "color": 65280}]}',
    "DISCORD_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Período de Teste Terminou", "description": "Olá **{username}**! ⌛\\n\\nO seu período de teste gratuito terminou. Para continuar a ter acesso, por favor, [clique aqui para renovar]({payment_link}).", "color": 16711680}]}',
    "WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}"}',
    "WEBHOOK_RENEWAL_MESSAGE_TEMPLATE": '{"content": "✅ A subscrição de {username} foi renovada. Novo vencimento: {new_date}."}',
    "WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "O período de teste para {username} terminou. Para renovar, acesse: {payment_link}"}',
    "TELEGRAM_BULK_MESSAGE_TEMPLATE": "Olá {name}, um aviso do servidor: {message}",
    "DISCORD_BULK_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso do Servidor", "description": "{message}", "color": 3447003}]}',
    "WEBHOOK_BULK_MESSAGE_TEMPLATE": '{"phone": "{phone_number}@s.whatsapp.net", "message": "{message}"}'
}

def get_greeting():
    """Retorna uma saudação com base na hora atual."""
    current_hour = datetime.now().hour
    if 5 <= current_hour < 12:
        return _("Bom dia")
    elif 12 <= current_hour < 18:
        return _("Boa tarde")
    else:
        return _("Boa noite")

class NotifierManager:
    """
    Gestor responsável por enviar notificações para diferentes serviços.
    """
    def __init__(self, link_shortener_service=None, socketio_instance=None):
        """Inicializa o gestor de notificações."""
        self.link_shortener = link_shortener_service
        self.socketio = socketio_instance

    def _send_telegram_notification(self, message, chat_id, request_id):
        """Envia uma notificação para um chat específico do Telegram."""
        config = load_or_create_config()
        bot_token = config.get("TELEGRAM_BOT_TOKEN")
        
        api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'Markdown'
        }
        try:
            logger.info(f"[ID: {request_id}] A enviar para Telegram (Destinatário: {chat_id})")
            response = requests.post(api_url, json=payload, timeout=10)
            response_data = response.json()
            if not response_data.get('ok'):
                error_description = response_data.get('description', 'Erro desconhecido.')
                raise Exception(error_description)
            logger.info(f"[ID: {request_id}] Notificação enviada com sucesso para o Telegram.")
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha de conexão ao enviar notificação para o Telegram: {e}")
        except Exception as e:
             logger.error(f"[ID: {request_id}] Erro da API do Telegram ao enviar para {chat_id}: {e}")


    def _send_webhook_notification(self, payload, request_id):
        """Envia um payload JSON para o Webhook configurado."""
        config = load_or_create_config()
        webhook_url = config.get("WEBHOOK_URL")

        headers = {'Content-Type': 'application/json'}
        auth_header = config.get("WEBHOOK_AUTHORIZATION_HEADER")
        if auth_header:
            if ":" in auth_header:
                try:
                    key, value = auth_header.split(":", 1)
                    headers[key.strip()] = value.strip()
                except ValueError:
                    logger.error(f"[ID: {request_id}] Formato do cabeçalho de autorização do Webhook é inválido. Header: {auth_header}")
            else:
                headers['Authorization'] = auth_header.strip()
        
        try:
            logger.info(f"[ID: {request_id}] A enviar para Webhook (URL: {webhook_url})")
            logger.debug(f"[ID: {request_id}] Corpo do Webhook: {json.dumps(payload)}")
            response = requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=30)
            response.raise_for_status()
            logger.info(f"[ID: {request_id}] Notificação enviada com sucesso via Webhook.")
        except requests.exceptions.HTTPError as e:
             logger.error(f"[ID: {request_id}] Falha ao enviar notificação via Webhook para {webhook_url}: {e}")
             logger.error(f"[ID: {request_id}] Resposta do servidor: {e.response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha de conexão ao enviar notificação via Webhook para {webhook_url}: {e}")

    def _send_discord_notification(self, payload, request_id):
        """Envia uma notificação formatada (embed) para um Webhook do Discord."""
        config = load_or_create_config()
        webhook_url = config.get("DISCORD_WEBHOOK_URL")

        headers = {'Content-Type': 'application/json'}
        
        try:
            logger.info(f"[ID: {request_id}] A enviar para Discord (URL: {webhook_url})")
            logger.debug(f"[ID: {request_id}] Corpo do Discord: {json.dumps(payload)}")
            response = requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=30)
            response.raise_for_status()
            logger.info(f"[ID: {request_id}] Notificação enviada com sucesso via Discord.")
        except requests.exceptions.HTTPError as e:
             logger.error(f"[ID: {request_id}] Falha ao enviar notificação via Discord para {webhook_url}: {e}")
             logger.error(f"[ID: {request_id}] Resposta do servidor: {e.response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha de conexão ao enviar notificação via Discord para {webhook_url}: {e}")


    def _prepare_and_send(self, event_type, user, user_profile, context):
        """Prepara e envia notificações para todos os agentes ativos."""
        config = load_or_create_config()
        request_id = uuid.uuid4()
        
        user_screen_limit = user_profile.get('screen_limit', 0)
        screen_prices = config.get("SCREEN_PRICES", {})
        renewal_price_str = config.get("RENEWAL_PRICE", "0.00") 

        if str(user_screen_limit) in screen_prices:
            renewal_price_str = screen_prices[str(user_screen_limit)]
        
        try:
            price_value = float(renewal_price_str.replace(',', '.'))
            formatted_price = f"R$ {price_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            formatted_price = "N/A"
            logger.warning(f"Não foi possível formatar o preço '{renewal_price_str}' para o utilizador {user.get('username')}.")

        if user_screen_limit > 0:
            plan_name = ngettext('%(num)d Tela', '%(num)d Telas', user_screen_limit) % {'num': user_screen_limit}
        else:
            plan_name = _("Plano Padrão")

        app_base_url = config.get("APP_BASE_URL")
        if not app_base_url or "127.0.0.1" in app_base_url or "localhost" in app_base_url:
            logger.warning(f"A APP_BASE_URL ('{app_base_url}') não está configurada ou está definida para um endereço local. Os links de pagamento gerados podem não ser acessíveis externamente.")

        payment_link = "#"
        if event_type != 'renewal' and user_profile.get('payment_token'):
            long_url = url_for('main.payment_page', token=user_profile['payment_token'], _external=True)
            logger.info(f"URL de pagamento longa gerada para '{user.get('username')}': {long_url}")
            
            if config.get("ENABLE_LINK_SHORTENER"):
                logger.info("Encurtador de links está HABILITADO. A tentar encurtar o link.")
                if self.link_shortener:
                    try:
                        payment_link = self.link_shortener.create_short_link(long_url)
                        logger.info(f"Link encurtado com sucesso para '{user.get('username')}': {payment_link}")
                    except Exception as e:
                        logger.error(f"Falha ao encurtar o link de pagamento para {user.get('username')}: {e}", exc_info=True)
                        payment_link = long_url
                        logger.warning(f"A utilizar a URL longa como fallback para '{user.get('username')}'.")
                else:
                    logger.error("Serviço LinkShortener não foi injetado no NotifierManager. A utilizar a URL longa.")
                    payment_link = long_url
            else:
                logger.info("Encurtador de links está DESABILITADO. A utilizar a URL longa.")
                payment_link = long_url

        placeholders = {
            'username': user.get('username'),
            'name': user_profile.get('name') or user.get('username'),
            'email': user.get('email'),
            'greeting': get_greeting(),
            'telegram_user': user_profile.get('telegram_user', ''),
            'discord_user_id': user_profile.get('discord_user_id', ''),
            'phone_number': user_profile.get('phone_number', ''),
            'payment_link': payment_link,
            'price': formatted_price,
            'plan_name': plan_name
        }
        placeholders.update(context)
        
        logger.debug(f"[ID: {request_id}] Placeholders para notificação: {placeholders}")

        if config.get("TELEGRAM_ENABLED"):
            template_key = f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE"
            telegram_template = config.get(template_key) or DEFAULT_TEMPLATES.get(template_key)
            telegram_user_id = user_profile.get('telegram_user')
            
            if telegram_template and telegram_user_id:
                telegram_message = telegram_template.format(**placeholders)
                self._send_telegram_notification(telegram_message, telegram_user_id, request_id)
            elif telegram_template and not telegram_user_id:
                 logger.warning(f"[ID: {request_id}] A notificação por Telegram para '{placeholders['username']}' foi ignorada porque o ID do Telegram não está definido no seu perfil.")

        if config.get("WEBHOOK_ENABLED"):
            template_key = f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE"
            webhook_template_str = config.get(template_key) or DEFAULT_TEMPLATES.get(template_key)
            phone_number = user_profile.get('phone_number')

            if webhook_template_str and phone_number:
                try:
                    message_with_placeholders = webhook_template_str
                    for key, value in placeholders.items():
                        # CORREÇÃO: Escapa os valores que podem conter caracteres especiais de JSON
                        json_escaped_value = json.dumps(str(value))[1:-1]
                        message_with_placeholders = message_with_placeholders.replace(f"{{{key}}}", json_escaped_value)

                    webhook_payload = json.loads(message_with_placeholders)
                    self._send_webhook_notification(webhook_payload, request_id)
                except json.JSONDecodeError:
                    logger.error(f"[ID: {request_id}] O modelo de mensagem do Webhook para '{event_type.upper()}' não é um JSON válido.")
                except KeyError as e:
                    logger.error(f"[ID: {request_id}] Placeholder inválido no modelo do Webhook: {e}")
            elif webhook_template_str and not phone_number:
                logger.warning(f"[ID: {request_id}] A notificação via Webhook para '{placeholders['username']}' foi ignorada porque o número de telefone não está definido no seu perfil.")

        if config.get("DISCORD_ENABLED"):
            template_key = f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE"
            discord_template_str = config.get(template_key) or DEFAULT_TEMPLATES.get(template_key)
            discord_user_id = user_profile.get('discord_user_id')

            if discord_template_str and discord_user_id:
                try:
                    placeholders['discord_user_id'] = discord_user_id
                    
                    message_with_placeholders = discord_template_str
                    for key, value in placeholders.items():
                        # CORREÇÃO: Escapa os valores que podem conter caracteres especiais de JSON
                        json_escaped_value = json.dumps(str(value))[1:-1]
                        message_with_placeholders = message_with_placeholders.replace(f"{{{key}}}", json_escaped_value)
                    
                    discord_payload = json.loads(message_with_placeholders)
                    self._send_discord_notification(discord_payload, request_id)
                except json.JSONDecodeError:
                    logger.error(f"[ID: {request_id}] O modelo de mensagem do Discord para '{event_type.upper()}' não é um JSON válido.")
                except KeyError as e:
                    logger.error(f"[ID: {request_id}] Placeholder inválido no modelo do Discord: {e}")
            elif discord_template_str and not discord_user_id:
                logger.warning(f"[ID: {request_id}] A notificação via Discord para '{placeholders['username']}' foi ignorada porque o ID de utilizador do Discord não está definido no seu perfil.")


    def send_expiration_notification(self, user, days_left, user_profile):
        """Envia uma notificação de vencimento."""
        expiration_date_str = user_profile.get('expiration_date')
        formatted_date = ""
        if expiration_date_str:
            try:
                formatted_date = datetime.fromisoformat(expiration_date_str).strftime('%d/%m/%Y')
            except (ValueError, TypeError):
                logger.warning(f"Não foi possível formatar a data de vencimento para o placeholder de notificação do utilizador {user.get('username')}")

        self._prepare_and_send(
            event_type='expiration', 
            user=user,
            user_profile=user_profile, 
            context={'days': days_left, 'date': formatted_date}
        )

    def send_renewal_notification(self, user, new_expiration_date, user_profile):
        """Envia uma notificação de renovação."""
        formatted_date = new_expiration_date.strftime('%d/%m/%Y')
        self._prepare_and_send(
            event_type='renewal', 
            user=user,
            user_profile=user_profile, 
            context={'new_date': formatted_date, 'date': formatted_date}
        )
        
    def send_trial_end_notification(self, user, user_profile):
        """Envia uma notificação de fim de período de teste."""
        self._prepare_and_send(
            event_type='trial_end', 
            user=user,
            user_profile=user_profile, 
            context={}
        )

    def process_bulk_notification_task(self, task_obj):
        """
        Executa uma tarefa de notificação em massa. Esta função é chamada pelo agendador.
        """
        from ..extensions import plex_manager, data_manager
        
        task_id = task_obj.id
        try:
            # 1. Marcar a tarefa como 'a correr'
            data_manager.update_task(task_id, {'status': 'running', 'started_at': datetime.utcnow()})
            
            # 2. Obter o payload e executar a lógica
            payload = json.loads(task_obj.payload)
            message = payload['message']
            contacts_only = payload.get('contacts_only', False)

            config = load_or_create_config()
            request_id = uuid.uuid4()
            
            all_users = plex_manager.get_all_plex_users()
            blocked_users = [u['username'] for u in data_manager.get_blocked_users_list()]
            target_users = [u for u in all_users if u['username'] not in blocked_users]
            
            if contacts_only:
                logger.info(f"[Task ID: {task_id}] A filtrar utilizadores para enviar apenas para aqueles com contacto registado.")
                users_with_contacts = []
                telegram_enabled = config.get("TELEGRAM_ENABLED", False)
                discord_enabled = config.get("DISCORD_ENABLED", False)
                webhook_enabled = config.get("WEBHOOK_ENABLED", False)

                for user in target_users:
                    profile = data_manager.get_user_profile(user['username'])
                    if (telegram_enabled and profile.get('telegram_user')) or \
                       (discord_enabled and profile.get('discord_user_id')) or \
                       (webhook_enabled and profile.get('phone_number')):
                        users_with_contacts.append(user)
                
                original_count = len(target_users)
                target_users = users_with_contacts
                logger.info(f"[Task ID: {task_id}] Filtro aplicado. De {original_count} utilizadores, {len(target_users)} serão notificados.")

            total_users = len(target_users)
            data_manager.update_task(task_id, {'progress_total': total_users})
            logger.info(f"[Task ID: {task_id}] A iniciar envio em massa para {total_users} utilizadores.")
            if self.socketio:
                self.socketio.emit('bulk_notification_start', {'total': total_users, 'task_id': task_id}, namespace='/dashboard')

            telegram_template = config.get("TELEGRAM_BULK_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get("TELEGRAM_BULK_MESSAGE_TEMPLATE")
            discord_template = config.get("DISCORD_BULK_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get("DISCORD_BULK_MESSAGE_TEMPLATE")
            webhook_template = config.get("WEBHOOK_BULK_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get("WEBHOOK_BULK_MESSAGE_TEMPLATE")

            for i, user in enumerate(target_users):
                username = user.get('username')
                user_profile = data_manager.get_user_profile(username)
                
                placeholders = { 'message': message, **self._build_placeholders('bulk', user, user_profile, {}) }

                if config.get("TELEGRAM_ENABLED") and telegram_template:
                    telegram_user_id = user_profile.get('telegram_user')
                    if telegram_user_id:
                        full_message = telegram_template.format(**placeholders)
                        self._send_telegram_notification(full_message, telegram_user_id, request_id)
                
                # CORREÇÃO: Lógica de substituição segura para JSON
                if config.get("DISCORD_ENABLED") and discord_template:
                    discord_user_id = user_profile.get('discord_user_id')
                    if discord_user_id:
                        try:
                            escaped_message = json.dumps(message)[1:-1]
                            processed_template = discord_template.replace('{message}', escaped_message)
                            for key, value in placeholders.items():
                                if key != 'message':
                                    processed_template = processed_template.replace(f"{{{key}}}", str(value))
                            
                            payload = json.loads(processed_template)
                            self._send_discord_notification(payload, request_id)
                        except Exception as e:
                            logger.error(f"[ID: {request_id}] Falha ao processar a mensagem do Discord para {username}: {e}")
                
                # CORREÇÃO: Lógica de substituição segura para JSON
                if config.get("WEBHOOK_ENABLED") and webhook_template:
                    phone_number = user_profile.get('phone_number')
                    if phone_number:
                        try:
                            escaped_message = json.dumps(message)[1:-1]
                            processed_template = webhook_template.replace('{message}', escaped_message)
                            for key, value in placeholders.items():
                                if key != 'message':
                                    processed_template = processed_template.replace(f"{{{key}}}", str(value))
                            
                            payload = json.loads(processed_template)
                            self._send_webhook_notification(payload, request_id)
                        except Exception as e:
                            logger.error(f"[ID: {request_id}] Falha ao processar a mensagem do Webhook para {username}: {e}")

                # 3. Atualizar progresso
                data_manager.update_task(task_id, {'progress_current': i + 1})
                if self.socketio:
                    self.socketio.emit('bulk_notification_progress', {'current': i + 1, 'total': total_users, 'task_id': task_id}, namespace='/dashboard')
                
                time.sleep(2) 

            # 4. Marcar a tarefa como concluída
            success_message = f"Envio em massa concluído para {total_users} utilizadores."
            data_manager.update_task(task_id, {'status': 'success', 'result': success_message, 'completed_at': datetime.utcnow()})
            logger.info(f"[Task ID: {task_id}] {success_message}")
            if self.socketio:
                self.socketio.emit('bulk_notification_end', {'total': total_users, 'task_id': task_id}, namespace='/dashboard')

        except Exception as e:
            error_message = f"Falha na tarefa de notificação em massa: {e}"
            logger.error(f"[Task ID: {task_id}] {error_message}", exc_info=True)
            data_manager.update_task(task_id, {'status': 'failed', 'result': error_message, 'completed_at': datetime.utcnow()})
            if self.socketio:
                self.socketio.emit('bulk_notification_error', {'message': error_message, 'task_id': task_id}, namespace='/dashboard')
    
    def _build_placeholders(self, event_type, user, user_profile, context):
        """Constrói um dicionário de placeholders para as mensagens."""
        config = load_or_create_config()
        
        user_screen_limit = user_profile.get('screen_limit', 0)
        screen_prices = config.get("SCREEN_PRICES", {})
        renewal_price_str = config.get("RENEWAL_PRICE", "0.00") 

        if str(user_screen_limit) in screen_prices:
            renewal_price_str = screen_prices[str(user_screen_limit)]
        
        try:
            price_value = float(renewal_price_str.replace(',', '.'))
            formatted_price = f"R$ {price_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            formatted_price = "N/A"

        if user_screen_limit > 0:
            plan_name = ngettext('%(num)d Tela', '%(num)d Telas', user_screen_limit) % {'num': user_screen_limit}
        else:
            plan_name = _("Plano Padrão")

        payment_link = "#"
        if event_type != 'renewal' and user_profile.get('payment_token'):
            long_url = url_for('main.payment_page', token=user_profile['payment_token'], _external=True)
            if config.get("ENABLE_LINK_SHORTENER") and self.link_shortener:
                try:
                    payment_link = self.link_shortener.create_short_link(long_url)
                except Exception:
                    payment_link = long_url
            else:
                payment_link = long_url

        placeholders = {
            'username': user.get('username'),
            'name': user_profile.get('name') or user.get('username'),
            'email': user.get('email'),
            'greeting': get_greeting(),
            'telegram_user': user_profile.get('telegram_user', ''),
            'discord_user_id': user_profile.get('discord_user_id', ''),
            'phone_number': user_profile.get('phone_number', ''),
            'payment_link': payment_link,
            'price': formatted_price,
            'plan_name': plan_name
        }
        placeholders.update(context)
        return placeholders

