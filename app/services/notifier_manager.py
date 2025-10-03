# app/services/notifier_manager.py

import json
import logging
import uuid
import time
from datetime import datetime, timezone
import requests
from flask_babel import gettext as _, ngettext
from flask import url_for

from ..config import load_or_create_config
from ..models import UserProfile

logger = logging.getLogger(__name__)

# --- CONSTANTES DE TEMPLATES PADRÃO ---
DEFAULT_TEMPLATES = {
    "TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}",
    "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "✅ Olá {username}! A sua subscrição foi renovada com sucesso. O seu novo vencimento é em {new_date}.",
    "TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE": "Seu período de teste para {username} terminou. Para continuar com o acesso, renove sua assinatura em: {payment_link}",
    "DISCORD_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso de Vencimento", "description": "Olá **{username}**! 👋\\n\\nO seu acesso ao Plex está prestes a expirar em **{days} dia(s)**, no dia **{date}**.\\n\\nPara evitar a interrupção do serviço, por favor, [clique aqui para renovar]({payment_link}).", "color": 16776960}]}',
    "DISCORD_RENEWAL_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Renovação Confirmada!", "description": "Olá **{username}**! ✅\\n\\nA sua assinatura foi renovada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\nObrigado e aproveite!\", "color": 65280}]}',
    "DISCORD_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Período de Teste Terminou", "description": "Olá **{username}**! ⌛\\n\\nO seu período de teste gratuito terminou. Para continuar a ter acesso, por favor, [clique aqui para renovar]({payment_link}).", "color": 16711680}]}',
    "WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}"}',
    "WEBHOOK_RENEWAL_MESSAGE_TEMPLATE": '{"content": "✅ A subscrição de {username} foi renovada. Novo vencimento: {new_date}."}',
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
            requests.post(webhook_url, json=payload, headers=headers, timeout=30).raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha ao enviar para Webhook: {e}")

    def _send_discord_notification(self, payload, request_id):
        config = load_or_create_config()
        webhook_url = config.get("DISCORD_WEBHOOK_URL")
        headers = {'Content-Type': 'application/json'}
        try:
            requests.post(webhook_url, json=payload, headers=headers, timeout=30).raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha ao enviar para Discord: {e}")

    def _format_template(self, template_str, placeholders, is_json=False):
        """Função unificada para substituir placeholders de forma segura."""
        if not template_str:
            return None
        
        safe_placeholders = {k: str(v) if v is not None else '' for k, v in placeholders.items()}
        
        if not is_json:
            try:
                return template_str.format(**safe_placeholders)
            except KeyError as e:
                logger.error(f"Placeholder ausente no template de texto: {e}. Template: '{template_str}'")
                return template_str
        else:
            output = template_str
            for key, value in safe_placeholders.items():
                json_escaped_value = json.dumps(value)[1:-1]
                output = output.replace(f"{{{key}}}", json_escaped_value)
            try:
                return json.loads(output)
            except json.JSONDecodeError as e:
                logger.error(f"Erro de sintaxe JSON no template após a substituição de placeholders: {e}. Template final: '{output}'")
                return None


    def _prepare_and_send(self, event_type, user, user_profile, context):
        config = load_or_create_config()
        request_id = uuid.uuid4()
        
        can_notify_telegram = config.get("TELEGRAM_ENABLED") and user_profile.get('telegram_user')
        can_notify_webhook = config.get("WEBHOOK_ENABLED") and user_profile.get('phone_number')
        can_notify_discord = config.get("DISCORD_ENABLED") and user_profile.get('discord_user_id')

        if not (can_notify_telegram or can_notify_webhook or can_notify_discord):
            logger.info(f"[ID: {request_id}] Nenhuma notificação enviada para '{user.get('username')}' (evento: {event_type}) porque nenhum método de contacto está registado ou habilitado.")
            return

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
        
        if can_notify_telegram:
            template = config.get(f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE")
            message = self._format_template(template, placeholders)
            if message:
                self._send_telegram_notification(message, user_profile['telegram_user'], request_id)
        
        if can_notify_webhook:
            template_str = config.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, placeholders, is_json=True)
            if payload:
                self._send_webhook_notification(payload, request_id)

        if can_notify_discord:
            template_str = config.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, placeholders, is_json=True)
            if payload:
                self._send_discord_notification(payload, request_id)

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
        return {
            'username': user.get('username'), 'name': user_profile.get('name') or user.get('username'),
            'email': user.get('email'), 'greeting': get_greeting(),
            'telegram_user': user_profile.get('telegram_user', ''), 'discord_user_id': user_profile.get('discord_user_id', ''),
            'phone_number': user_profile.get('phone_number', ''), **context
        }
        
    def process_bulk_notification_task(self, task):
        from .. import extensions
        try:
            payload = json.loads(task.payload or '{}')
            message = payload.get('message')
            target_audience = payload.get('target_audience', 'active')

            if not message:
                raise ValueError("A mensagem está vazia no payload da tarefa.")

            all_plex_users = extensions.plex_manager.get_all_plex_users()
            if not all_plex_users:
                raise ValueError("Não foi possível obter a lista de utilizadores do Plex.")

            if target_audience == 'all':
                users_to_notify = all_plex_users
            elif target_audience == 'blocked':
                target_user_ids = {u['user_plex_id'] for u in extensions.data_manager.get_blocked_users_list()}
                users_to_notify = [u for u in all_plex_users if u['id'] in target_user_ids]
            else: # 'active' is the default
                blocked_ids = {u['user_plex_id'] for u in extensions.data_manager.get_blocked_users_list()}
                users_to_notify = [u for u in all_plex_users if u['id'] not in blocked_ids]
            
            all_profiles = extensions.data_manager.get_all_user_profiles()
            profiles_map = {p['plex_user_id']: p for p in all_profiles}

            total_users = len(users_to_notify)
            extensions.data_manager.update_task(task.id, {'status': 'running', 'progress_total': total_users})
            if self.socketio:
                self.socketio.emit('bulk_notification_start', {'total': total_users}, namespace='/dashboard')

            processed_count = 0
            for user in users_to_notify:
                profile = profiles_map.get(user['id'], {})
                
                has_contact = profile.get('telegram_user') or profile.get('discord_user_id') or profile.get('phone_number')
                
                if not has_contact:
                    continue

                context = {'message': message}
                self._prepare_and_send_bulk(user, profile, context)

                processed_count += 1
                if processed_count % 5 == 0 or processed_count == total_users:
                    extensions.data_manager.update_task(task.id, {'progress_current': processed_count})
                    if self.socketio:
                        self.socketio.emit('bulk_notification_progress', {'current': processed_count, 'total': total_users}, namespace='/dashboard')
                
                time.sleep(1)

            extensions.data_manager.update_task(task.id, {'status': 'completed', 'completed_at': datetime.now(timezone.utc), 'result': f'{processed_count} notificações enviadas.'})
            if self.socketio:
                self.socketio.emit('bulk_notification_end', {'total': processed_count}, namespace='/dashboard')

        except Exception as e:
            logger.error(f"Erro ao processar a tarefa de notificação em massa (ID: {task.id}): {e}", exc_info=True)
            extensions.data_manager.update_task(task.id, {'status': 'failed', 'completed_at': datetime.now(timezone.utc), 'result': str(e)})
            if self.socketio:
                self.socketio.emit('bulk_notification_error', {'message': str(e)}, namespace='/dashboard')

    def _prepare_and_send_bulk(self, user, user_profile, context):
        config = load_or_create_config()
        request_id = uuid.uuid4()
        
        base_placeholders = self._build_placeholders('bulk', user, user_profile, {})
        raw_message_from_ui = context.get('message', '')

        try:
            formatted_message_from_ui = raw_message_from_ui.format(**base_placeholders)
        except KeyError as e:
            logger.warning(f"Placeholder {e} inválido na mensagem em massa para '{user.get('username')}'. A usar mensagem sem formatação.")
            formatted_message_from_ui = raw_message_from_ui

        final_placeholders = {**base_placeholders, 'message': formatted_message_from_ui}
        
        if config.get("TELEGRAM_ENABLED") and user_profile.get('telegram_user'):
            template = config.get("TELEGRAM_BULK_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get("TELEGRAM_BULK_MESSAGE_TEMPLATE")
            final_telegram_message = self._format_template(template, final_placeholders)
            if final_telegram_message:
                self._send_telegram_notification(final_telegram_message, user_profile['telegram_user'], request_id)
        
        if config.get("WEBHOOK_ENABLED") and user_profile.get('phone_number'):
            template_str = config.get("WEBHOOK_BULK_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get("WEBHOOK_BULK_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, final_placeholders, is_json=True)
            if payload:
                self._send_webhook_notification(payload, request_id)

        if config.get("DISCORD_ENABLED") and user_profile.get('discord_user_id'):
            template_str = config.get("DISCORD_BULK_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get("DISCORD_BULK_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, final_placeholders, is_json=True)
            if payload:
                self._send_discord_notification(payload, request_id)

