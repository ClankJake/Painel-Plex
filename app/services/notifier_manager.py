# app/services/notifier_manager.py

import json
import logging
import uuid
from datetime import datetime
import requests
from flask_babel import gettext as _
from flask import url_for

from ..config import load_or_create_config
from ..models import UserProfile

logger = logging.getLogger(__name__)

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
    def __init__(self, link_shortener_service=None):
        """Inicializa o gestor de notificações."""
        self.link_shortener = link_shortener_service

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
        
        app_base_url = config.get("APP_BASE_URL")
        if not app_base_url or "127.0.0.1" in app_base_url or "localhost" in app_base_url:
            logger.warning(f"A APP_BASE_URL ('{app_base_url}') não está configurada ou está definida para um endereço local. Os links de pagamento gerados podem não ser acessíveis externamente.")

        payment_link = "#"
        # CORREÇÃO: Apenas gera um link de pagamento se o evento não for uma confirmação de renovação.
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
            'phone_number': user_profile.get('phone_number', ''),
            'payment_link': payment_link
        }
        placeholders.update(context)
        
        logger.debug(f"[ID: {request_id}] Placeholders para notificação: {placeholders}")

        # --- Envio para Telegram ---
        if config.get("TELEGRAM_ENABLED"):
            telegram_template = config.get(f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE")
            telegram_user_id = user_profile.get('telegram_user')
            
            if telegram_template and telegram_user_id:
                telegram_message = telegram_template.format(**placeholders)
                self._send_telegram_notification(telegram_message, telegram_user_id, request_id)
            elif telegram_template and not telegram_user_id:
                 logger.warning(f"[ID: {request_id}] A notificação por Telegram para '{placeholders['username']}' foi ignorada porque o ID do Telegram não está definido no seu perfil.")

        # --- Envio para Webhook ---
        if config.get("WEBHOOK_ENABLED"):
            webhook_template_str = config.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE")
            phone_number = user_profile.get('phone_number')

            if webhook_template_str and phone_number:
                try:
                    message_with_placeholders = webhook_template_str
                    for key, value in placeholders.items():
                        message_with_placeholders = message_with_placeholders.replace(f"{{{key}}}", str(value))

                    webhook_payload = json.loads(message_with_placeholders)
                    self._send_webhook_notification(webhook_payload, request_id)
                except json.JSONDecodeError:
                    logger.error(f"[ID: {request_id}] O modelo de mensagem do Webhook para '{event_type.upper()}' não é um JSON válido.")
                except KeyError as e:
                    logger.error(f"[ID: {request_id}] Placeholder inválido no modelo do Webhook: {e}")
            elif webhook_template_str and not phone_number:
                logger.warning(f"[ID: {request_id}] A notificação via Webhook para '{placeholders['username']}' foi ignorada porque o número de telefone não está definido no seu perfil.")

        # --- Envio para Discord ---
        if config.get("DISCORD_ENABLED"):
            discord_template_str = config.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE")
            discord_user_id = user_profile.get('discord_user_id') # Usa um novo campo do perfil

            if discord_template_str and discord_user_id:
                try:
                    # Adiciona o ID do utilizador ao placeholder para poder mencioná-lo
                    placeholders['discord_user_id'] = discord_user_id
                    
                    message_with_placeholders = discord_template_str
                    for key, value in placeholders.items():
                        # Substitui as variáveis no JSON
                        message_with_placeholders = message_with_placeholders.replace(f"{{{key}}}", str(value))
                    
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
            context={} # Sem contexto extra necessário para este evento
        )
