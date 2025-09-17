# app/services/notifier_manager.py

import json
import logging
import uuid
import time
from datetime import datetime
import requests
from flask_babel import gettext as _, ngettext
from flask import url_for

from ..config import load_or_create_config
from ..models import UserProfile

logger = logging.getLogger(__name__)

# --- CONSTANTES DE TEMPLATES PADRÃO ---
DEFAULT_TEMPLATES = {
    "TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}",
    "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "? Olá {username}! A sua subscrição foi renovada com sucesso. O seu novo vencimento é em {new_date}.",
    "TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE": "Seu período de teste para {username} terminou. Para continuar com o acesso, renove sua assinatura em: {payment_link}",
    "DISCORD_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso de Vencimento", "description": "Olá **{username}**! ?\\n\\nO seu acesso ao Plex está prestes a expirar em **{days} dia(s)**, no dia **{date}**.\\n\\nPara evitar a interrupção do serviço, por favor, [clique aqui para renovar]({payment_link}).", "color": 16776960}]}',
    "DISCORD_RENEWAL_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Renovação Confirmada!", "description": "Olá **{username}**! ?\\n\\nA sua assinatura foi renovada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\nObrigado e aproveite!", "color": 65280}]}',
    "DISCORD_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Período de Teste Terminou", "description": "Olá **{username}**! ?\\n\\nO seu período de teste gratuito terminou. Para continuar a ter acesso, por favor, [clique aqui para renovar]({payment_link}).", "color": 16711680}]}',
    "WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}"}',
    "WEBHOOK_RENEWAL_MESSAGE_TEMPLATE": '{"content": "? A subscrição de {username} foi renovada. Novo vencimento: {new_date}."}',
    "WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "O período de teste para {username} terminou. Para renovar, acesse: {payment_link}"}',
    "TELEGRAM_BULK_MESSAGE_TEMPLATE": "Olá {name}, um aviso do servidor: {message}",
    "DISCORD_BULK_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso do Servidor", "description": "{message}", "color": 3447003}]}',
    "WEBHOOK_BULK_MESSAGE_TEMPLATE": '{"phone": "{phone_number}@s.whatsapp.net", "message": "{message}"}'
}

def get_greeting():
    current_hour = datetime.now().hour
    if 5 <= current_hour < 12: return _("Bom dia")
    elif 12 <= current_hour < 18: return _("Boa tarde")
    else: return _("Boa noite")

class NotifierManager:
    def __init__(self, link_shortener_service=None, socketio_instance=None):
        self.link_shortener = link_shortener_service
        self.socketio = socketio_instance

    def _send_telegram_notification(self, message, chat_id, request_id):
        config = load_or_create_config()
        bot_token = config.get("TELEGRAM_BOT_TOKEN")
        api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'}
        try:
            requests.post(api_url, json=payload, timeout=10).raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha de conexão ao enviar para Telegram: {e}")

    def _send_webhook_notification(self, payload, request_id):
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
                    logger.error(f"[ID: {request_id}] Formato do cabeçalho de autorização do Webhook é inválido.")
            else:
                headers['Authorization'] = auth_header.strip()
        try:
            requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=30).raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha ao enviar para Webhook: {e}")

    def _send_discord_notification(self, payload, request_id):
        config = load_or_create_config()
        webhook_url = config.get("DISCORD_WEBHOOK_URL")
        headers = {'Content-Type': 'application/json'}
        try:
            requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=30).raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha ao enviar para Discord: {e}")

    def _prepare_and_send(self, event_type, user, user_profile, context):
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

        plan_name = ngettext('%(num)d Tela', '%(num)d Telas', user_screen_limit) % {'num': user_screen_limit} if user_screen_limit > 0 else _("Plano Padrão")
        
        payment_link = "#"
        if event_type != 'renewal' and user_profile.get('payment_token'):
            long_url = url_for('main.payment_page', token=user_profile['payment_token'], _external=True)
            if config.get("ENABLE_LINK_SHORTENER") and self.link_shortener:
                try: payment_link = self.link_shortener.create_short_link(long_url)
                except Exception: payment_link = long_url
            else: payment_link = long_url

        placeholders = {**self._build_placeholders('notification', user, user_profile, context), 'payment_link': payment_link, 'price': formatted_price, 'plan_name': plan_name}
        
        if config.get("TELEGRAM_ENABLED") and user_profile.get('telegram_user'):
            template = config.get(f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE")
            if template:
                self._send_telegram_notification(template.format(**placeholders), user_profile['telegram_user'], request_id)
        
        if config.get("WEBHOOK_ENABLED") and user_profile.get('phone_number'):
            template_str = config.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE")
            if template_str:
                try:
                    message_with_placeholders = template_str
                    for key, value in placeholders.items():
                        json_escaped_value = json.dumps(str(value))[1:-1]
                        message_with_placeholders = message_with_placeholders.replace(f"{{{key}}}", json_escaped_value)
                    self._send_webhook_notification(json.loads(message_with_placeholders), request_id)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"[ID: {request_id}] Erro no template do Webhook: {e}")

        if config.get("DISCORD_ENABLED") and user_profile.get('discord_user_id'):
            template_str = config.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE")
            if template_str:
                try:
                    placeholders['discord_user_id'] = user_profile['discord_user_id']
                    message_with_placeholders = template_str
                    for key, value in placeholders.items():
                        json_escaped_value = json.dumps(str(value))[1:-1]
                        message_with_placeholders = message_with_placeholders.replace(f"{{{key}}}", json_escaped_value)
                    self._send_discord_notification(json.loads(message_with_placeholders), request_id)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"[ID: {request_id}] Erro no template do Discord: {e}")

    def send_expiration_notification(self, user, days_left, user_profile):
        expiration_date_str = user_profile.get('expiration_date')
        formatted_date = datetime.fromisoformat(expiration_date_str).strftime('%d/%m/%Y') if expiration_date_str else ""
        self._prepare_and_send('expiration', user, user_profile, {'days': days_left, 'date': formatted_date})

    def send_renewal_notification(self, user, new_expiration_date, user_profile):
        formatted_date = new_expiration_date.strftime('%d/%m/%Y')
        self._prepare_and_send('renewal', user, user_profile, {'new_date': formatted_date, 'date': formatted_date})
        
    def send_trial_end_notification(self, user, user_profile):
        self._prepare_and_send('trial_end', user, user_profile, {})

    def _build_placeholders(self, event_type, user, user_profile, context):
        # Esta função é um wrapper para evitar duplicação; a lógica principal está em _prepare_and_send
        return {
            'username': user.get('username'), 'name': user_profile.get('name') or user.get('username'),
            'email': user.get('email'), 'greeting': get_greeting(),
            'telegram_user': user_profile.get('telegram_user', ''), 'discord_user_id': user_profile.get('discord_user_id', ''),
            'phone_number': user_profile.get('phone_number', ''), **context
        }
