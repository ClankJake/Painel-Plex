import json
import logging
import uuid
import time
import html
import re
from datetime import datetime, timezone
import requests
import telebot
from telebot import types
from flask_babel import gettext as _, ngettext
from flask import url_for

from ..config import load_or_create_config
from ..models import UserProfile

logger = logging.getLogger(__name__)

# --- CONSTANTES DE TEMPLATES PADRÃO ---
DEFAULT_TEMPLATES = {
    "TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE": "Olá {name}, {greeting}!\n\nEste é um lembrete de que sua fatura está com o vencimento próximo.\nVencimento: *{date}*\nValor: *{price}*\nPlano: *{plan_name}*\nAcesso: `{email}`\n\nNa data do vencimento o sistema poderá bloquear o acesso. Para evitar a interrupção, realize o pagamento clicando no botão abaixo:",
    "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "✅ *Renovação Confirmada*\n\nOlá {name}!\nA sua subscrição foi renovada com sucesso.\nNovo vencimento: *{new_date}*.",
    "TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE": "⌛ *Fim do Período de Teste*\n\n{name}, o seu período de teste terminou.\nPara manter o seu acesso, realize a renovação no botão abaixo:",
    "DISCORD_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso de Vencimento", "description": "Olá **{username}**! 👋\\n\\nO seu acesso ao Plex está prestes a expirar em **{days} dia(s)**, no dia **{date}**.\\n\\nPara evitar a interrupção do serviço, por favor, [clique aqui para renovar]({payment_link}).", "color": 16776960}]}',
    "DISCORD_RENEWAL_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Renovação Confirmada!", "description": "Olá **{username}**! ✅\\n\\nA sua assinatura foi renovada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\nObrigado e aproveite!\", "color": 65280}]}',
    "DISCORD_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Período de Teste Terminou", "description": "Olá **{username}**! ⌛\\n\\nO seu período de teste gratuito terminou. Para continuar a ter acesso, por favor, [clique aqui para renovar]({payment_link}).", "color": 16711680}]}',
    "WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}"}',
    "WEBHOOK_RENEWAL_MESSAGE_TEMPLATE": '{"content": "✅ A subscrição de {username} foi renovada. Novo vencimento: {new_date}."}',
    "WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "O período de teste para {username} terminou. Para renovar, acesse: {payment_link}"}',
    "TELEGRAM_BULK_MESSAGE_TEMPLATE": "📢 *Aviso do Servidor*\n\nOlá {name},\n\n{message}",
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
        self._bot = None

    def _get_bot(self):
        """Inicializa o bot apenas para envio de mensagens (Push)."""
        config = load_or_create_config()
        token = config.get("TELEGRAM_BOT_TOKEN")
        if not token: return None
        if self._bot is None or self._bot.token != token:
            self._bot = telebot.TeleBot(token, threaded=False)
        return self._bot

    def _convert_md_to_html(self, text):
        """Converte Markdown básico para HTML do Telegram para evitar erros de parsing."""
        if not text: return ""
        text = re.sub(r'\*(.*?)\*', r'<b>\1</b>', text)
        text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
        return text

    def _send_telegram_notification(self, message, chat_id, request_id, reply_markup=None, plex_user_id=None, photo_url=None):
        """Envia uma notificação via Telegram (Texto ou Foto com Legenda)."""
        bot = self._get_bot()
        if not bot: return
        
        html_message = self._convert_md_to_html(message)
        
        try:
            if photo_url:
                bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_url,
                    caption=html_message,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            else:
                bot.send_message(
                    chat_id=chat_id,
                    text=html_message,
                    parse_mode='HTML',
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
        except telebot.apihelper.ApiTelegramException as e:
            # Melhoria: Tratamento de Rate Limit (429)
            if e.error_code == 429:
                retry_after = e.result_json.get('parameters', {}).get('retry_after', 5)
                logger.warning(f"Telegram Rate Limit atingido. A aguardar {retry_after}s...")
                time.sleep(retry_after + 1)
                return self._send_telegram_notification(message, chat_id, request_id, reply_markup, plex_user_id, photo_url)
                
            # Se o utilizador bloqueou o bot (403), removemos o contacto do perfil
            if e.error_code == 403 and plex_user_id:
                logger.warning(f"[ID: {request_id}] Bot bloqueado pelo utilizador {plex_user_id}. A remover contacto.")
                from .. import extensions
                extensions.data_manager.update_user_profile(plex_user_id, {'telegram_id': None, 'telegram_user': None})
            else:
                logger.error(f"[ID: {request_id}] Erro Telegram: {e.description}")
        except Exception as e:
            logger.error(f"[ID: {request_id}] Erro inesperado ao enviar Telegram: {e}")

    def _send_webhook_notification(self, payload, request_id):
        config = load_or_create_config()
        webhook_url = config.get("WEBHOOK_URL")
        if not webhook_url: return
        headers = {'Content-Type': 'application/json'}
        auth_header = config.get("WEBHOOK_AUTHORIZATION_HEADER")
        if auth_header:
            if ":" in auth_header:
                try:
                    key, value = auth_header.split(":", 1)
                    headers[key.strip()] = value.strip()
                except ValueError:
                    headers['Authorization'] = auth_header.strip()
            else:
                headers['Authorization'] = auth_header.strip()
        try:
            response = requests.post(webhook_url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            # Melhoria: Log detalhado da resposta do Webhook
            error_response = e.response.text if e.response else "Sem resposta do servidor"
            logger.error(f"[ID: {request_id}] Falha no Webhook: {e} | Resposta: {error_response}")

    def _send_discord_notification(self, payload, request_id):
        config = load_or_create_config()
        webhook_url = config.get("DISCORD_WEBHOOK_URL")
        if not webhook_url: return
        try:
            requests.post(webhook_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=30).raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha no Discord: {e}")

    def _format_template(self, template_str, placeholders, is_json=False, use_html_escape=False):
        if not template_str: return None
        safe_placeholders = {}
        for k, v in placeholders.items():
            val = str(v) if v is not None else ''
            if use_html_escape and not any(sub in k.lower() for sub in ['link', 'url']):
                val = html.escape(val)
            safe_placeholders[k] = val
        if not is_json:
            try: return template_str.format(**safe_placeholders)
            except KeyError as e:
                logger.error(f"Placeholder {e} ausente.")
                return template_str
        else:
            output = template_str
            for key, value in safe_placeholders.items():
                json_escaped_value = json.dumps(value)[1:-1]
                output = output.replace(f"{{{key}}}", json_escaped_value)
            try: return json.loads(output)
            except: return None

    def _prepare_and_send(self, event_type, user, user_profile, context):
        config = load_or_create_config()
        request_id = uuid.uuid4()
        
        # Correção: Verificar tanto telegram_id quanto telegram_user
        telegram_chat_id = user_profile.get('telegram_id') or user_profile.get('telegram_user')
        can_notify_telegram = config.get("TELEGRAM_ENABLED") and telegram_chat_id
        
        can_notify_webhook = config.get("WEBHOOK_ENABLED") and user_profile.get('phone_number')
        can_notify_discord = config.get("DISCORD_ENABLED") and user_profile.get('discord_user_id')
        
        if not (can_notify_telegram or can_notify_webhook or can_notify_discord): return

        user_screen_limit = user_profile.get('screen_limit', 0)
        screen_prices = config.get("SCREEN_PRICES", {})
        renewal_price_str = config.get("RENEWAL_PRICE", "0.00")
        if str(user_screen_limit) in screen_prices: renewal_price_str = screen_prices[str(user_screen_limit)]
        try:
            price_value = float(renewal_price_str.replace(',', '.'))
            formatted_price = f"R$ {price_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except: formatted_price = "N/A"
        plan_name = ngettext('%(num)d Tela', '%(num)d Telas', user_screen_limit) % {'num': user_screen_limit} if user_screen_limit > 0 else _("Plano Padrão")

        payment_link = None
        if event_type != 'renewal' and user_profile.get('payment_token'):
            long_url = url_for('main.payment_page', token=user_profile['payment_token'], _external=True)
            if config.get("ENABLE_LINK_SHORTENER") and self.link_shortener:
                try: payment_link = self.link_shortener.create_short_link(long_url)
                except: payment_link = long_url
            else: payment_link = long_url

        # Novos placeholders úteis
        now = datetime.now()
        placeholders = {
            **self._build_placeholders('notification', user, user_profile, context),
            'payment_link': payment_link or "#",
            'paymentlink': payment_link or "#",
            'price': formatted_price,
            'plan_name': plan_name,
            'planname': plan_name,
            'date_time': now.strftime('%d/%m/%Y %H:%M'),
            'days_left': context.get('days', 0)
        }

        if can_notify_telegram:
            template = config.get(f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE")
            message = self._format_template(template, placeholders, use_html_escape=True)
            
            # Melhoria: Suporte a Banner do Telegram
            photo_url = config.get(f"TELEGRAM_{event_type.upper()}_BANNER_URL")
            
            markup = None
            if payment_link and event_type in ['expiration', 'trial_end']:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(text="💳 Pagar Agora / Renovar", url=payment_link))
            if message:
                self._send_telegram_notification(
                    message, 
                    telegram_chat_id, 
                    request_id, 
                    reply_markup=markup, 
                    plex_user_id=user_profile.get('plex_user_id'),
                    photo_url=photo_url
                )

        if can_notify_webhook:
            template_str = config.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, placeholders, is_json=True)
            if payload: self._send_webhook_notification(payload, request_id)

        if can_notify_discord:
            template_str = config.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, placeholders, is_json=True)
            if payload: self._send_discord_notification(payload, request_id)

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
            'username': user.get('username'), 
            'name': user_profile.get('name') or user.get('username'),
            'email': user.get('email') or user_profile.get('email') or "",
            'greeting': get_greeting(),
            'telegram_user': user_profile.get('telegram_user', ''), 
            'telegram_id': user_profile.get('telegram_id', ''),
            'phone_number': user_profile.get('phone_number', ''),
            **context
        }

    def process_bulk_notification_task(self, task):
        from .. import extensions
        try:
            payload = json.loads(task.payload or '{}')
            message = payload.get('message')
            target_audience = payload.get('target_audience', 'active')
            target_user_ids = payload.get('user_ids')
            if not message: raise ValueError("Mensagem vazia.")
            all_plex_users = extensions.plex_manager.get_all_plex_users()
            if not all_plex_users: raise ValueError("Erro Plex.")
            if target_audience == 'specific': users_to_notify = [u for u in all_plex_users if u['id'] in (target_user_ids or [])]
            elif target_audience == 'all': users_to_notify = all_plex_users
            else:
                blocked_ids = {u['user_plex_id'] for u in extensions.data_manager.get_blocked_users_list()}
                users_to_notify = [u for u in all_plex_users if (u['id'] in blocked_ids if target_audience == 'blocked' else u['id'] not in blocked_ids)]
            all_profiles = extensions.data_manager.get_all_user_profiles()
            profiles_map = {p['plex_user_id']: p for p in all_profiles}
            total_users = len(users_to_notify)
            extensions.data_manager.update_task(task.id, {'status': 'running', 'progress_total': total_users})
            processed_count = 0
            for user in users_to_notify:
                profile = profiles_map.get(user['id'], {})
                
                # Correção: Verificar se existe telegram_id ou telegram_user
                has_telegram = profile.get('telegram_id') or profile.get('telegram_user')
                has_whatsapp = profile.get('phone_number')
                has_discord = profile.get('discord_user_id')
                
                if not (has_telegram or has_whatsapp or has_discord): continue
                
                self._prepare_and_send_bulk(user, profile, {'message': message})
                processed_count += 1
                if processed_count % 5 == 0: extensions.data_manager.update_task(task.id, {'progress_current': processed_count})
                time.sleep(0.4)
            extensions.data_manager.update_task(task.id, {'status': 'completed', 'completed_at': datetime.now(timezone.utc), 'result': f'{processed_count} enviadas.'})
        except Exception as e:
            logger.error(f"Erro Bulk: {e}")
            extensions.data_manager.update_task(task.id, {'status': 'failed', 'result': str(e)})

    def _prepare_and_send_bulk(self, user, user_profile, context):
        self._prepare_and_send('bulk', user, user_profile, context)
