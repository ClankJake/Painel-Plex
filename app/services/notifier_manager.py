# app/services/notifier_manager.py

import json
import logging
import uuid
import time
import html
import re
from datetime import datetime, timezone
from tzlocal import get_localzone

import requests
import telebot
from telebot import types
from flask_babel import gettext as _

from ..config import load_or_create_config

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
    """Retorna a saudação baseada na hora local real do servidor."""
    current_hour = datetime.now(get_localzone()).hour
    if 5 <= current_hour < 12: return _("Bom dia")
    elif 12 <= current_hour < 18: return _("Boa tarde")
    else: return _("Boa noite")

class NotifierManager:
    """
    Gere o envio de notificações assíncronas para os utilizadores (Telegram, Discord, Webhooks).
    """
    def __init__(self, link_shortener_service=None, socketio_instance=None):
        self.link_shortener = link_shortener_service
        self.socketio = socketio_instance
        self._bot = None

    def _get_bot(self):
        """Inicializa o bot apenas para envio de mensagens (Push)."""
        config = load_or_create_config()
        token = config.get("TELEGRAM_BOT_TOKEN")
        if not token: 
            return None
            
        if self._bot is None or self._bot.token != token:
            self._bot = telebot.TeleBot(token, threaded=False)
        return self._bot

    # --- PROCESSAMENTO DE TEMPLATES ---

    def _convert_md_to_html(self, text):
        """Converte Markdown básico para HTML do Telegram para evitar erros de parsing."""
        if not text: return ""
        text = re.sub(r'\*(.*?)\*', r'<b>\1</b>', text)
        text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)
        return text

    def _format_template(self, template_str, placeholders, is_json=False, use_html_escape=False):
        """Preenche o template com as variáveis, aplicando escapes necessários."""
        if not template_str: return None
        
        safe_placeholders = {}
        for k, v in placeholders.items():
            val = str(v) if v is not None else ''
            if use_html_escape and not any(sub in k.lower() for sub in ['link', 'url']):
                val = html.escape(val)
            safe_placeholders[k] = val
            
        if not is_json:
            try: 
                return template_str.format(**safe_placeholders)
            except KeyError as e:
                logger.error(f"Placeholder {e} ausente na string de template.")
                return template_str
        else:
            output = template_str
            for key, value in safe_placeholders.items():
                json_escaped_value = json.dumps(value)[1:-1]
                output = output.replace(f"{{{key}}}", json_escaped_value)
            try: 
                return json.loads(output)
            except Exception as e: 
                logger.error(f"Falha ao processar template JSON: {e}")
                return None

    # --- DESPACHO DE MENSAGENS (DISPATCHERS) ---

    def _send_telegram_notification(self, message, chat_id, request_id, reply_markup=None, plex_user_id=None, photo_url=None):
        bot = self._get_bot()
        if not bot: return
        
        html_message = self._convert_md_to_html(message)
        
        try:
            if photo_url:
                bot.send_photo(
                    chat_id=chat_id, photo=photo_url, caption=html_message,
                    parse_mode='HTML', reply_markup=reply_markup
                )
            else:
                bot.send_message(
                    chat_id=chat_id, text=html_message, parse_mode='HTML',
                    reply_markup=reply_markup, disable_web_page_preview=True
                )
        except telebot.apihelper.ApiTelegramException as e:
            # Tratamento de Rate Limit (429)
            if e.error_code == 429:
                retry_after = e.result_json.get('parameters', {}).get('retry_after', 5)
                logger.warning(f"Telegram Rate Limit atingido. A aguardar {retry_after}s...")
                time.sleep(retry_after + 1)
                return self._send_telegram_notification(message, chat_id, request_id, reply_markup, plex_user_id, photo_url)
                
            # Tratamento de Bloqueio do Bot (403)
            if e.error_code == 403 and plex_user_id:
                logger.warning(f"[ID: {request_id}] Bot bloqueado pelo utilizador {plex_user_id}. A remover contacto.")
                from .. import extensions
                extensions.data_manager.update_user_profile(plex_user_id, {'telegram_id': None, 'telegram_user': None})
            else:
                logger.error(f"[ID: {request_id}] Erro Telegram: {e.description}")
        except Exception as e:
            logger.error(f"[ID: {request_id}] Erro inesperado ao enviar Telegram: {e}")

    def _send_webhook_notification(self, payload, request_id, config):
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
            error_response = e.response.text if e.response else "Sem resposta do servidor"
            logger.error(f"[ID: {request_id}] Falha no Webhook: {e} | Resposta: {error_response}")

    def _send_discord_notification(self, payload, request_id, config):
        webhook_url = config.get("DISCORD_WEBHOOK_URL")
        if not webhook_url: return
        
        try:
            requests.post(webhook_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=30).raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha no Discord: {e}")

    # --- LÓGICA DE PREPARAÇÃO DA MENSAGEM (SRP) ---

    def _get_price_and_plan(self, config, user_screen_limit):
        """Determina o preço correto e o nome do plano do utilizador."""
        from flask_babel import ngettext, gettext as _
        
        screen_prices = config.get("SCREEN_PRICES", {})
        renewal_price_str = screen_prices.get(str(user_screen_limit), config.get("RENEWAL_PRICE", "0.00"))
        
        try:
            price_value = float(renewal_price_str.replace(',', '.'))
            formatted_price = f"R$ {price_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except ValueError: 
            formatted_price = "N/A"
            
        plan_name = ngettext('%(num)d Tela', '%(num)d Telas', user_screen_limit) % {'num': user_screen_limit} if user_screen_limit > 0 else _("Plano Padrão")
        return formatted_price, plan_name

    def _get_payment_link(self, config, event_type, user_profile):
        """Gera o link de pagamento, forçando o uso do APP_BASE_URL nas rotinas de Background."""
        if event_type == 'renewal' or not user_profile.get('payment_token'):
            return None
            
        try:
            token = user_profile['payment_token']
            app_base_url = config.get("APP_BASE_URL", "").strip().rstrip('/')
            
            if app_base_url:
                long_url = f"{app_base_url}/pay/{token}"
            else:
                from flask import url_for
                long_url = url_for('main.payment_page', token=token, _external=True)

            if config.get("ENABLE_LINK_SHORTENER") and self.link_shortener:
                return self.link_shortener.create_short_link(long_url)
            return long_url
        except Exception as e:
            logger.warning(f"Erro ao gerar link de pagamento: {e}")
            return long_url if 'long_url' in locals() else None

    def _prepare_and_send(self, event_type, user, user_profile, context):
        """Orquestra a montagem e o envio da notificação."""
        config = load_or_create_config()
        request_id = str(uuid.uuid4())
        
        telegram_chat_id = user_profile.get('telegram_id') or user_profile.get('telegram_user')
        can_notify_telegram = config.get("TELEGRAM_ENABLED") and telegram_chat_id
        can_notify_webhook = config.get("WEBHOOK_ENABLED") and user_profile.get('phone_number')
        can_notify_discord = config.get("DISCORD_ENABLED") and user_profile.get('discord_user_id')
        
        if not (can_notify_telegram or can_notify_webhook or can_notify_discord): 
            return

        formatted_price, plan_name = self._get_price_and_plan(config, user_profile.get('screen_limit', 0))
        payment_link = self._get_payment_link(config, event_type, user_profile)

        now = datetime.now(get_localzone())
        placeholders = {
            **self._build_placeholders(user, user_profile, context),
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
            photo_url = config.get(f"TELEGRAM_{event_type.upper()}_BANNER_URL")
            
            markup = None
            if payment_link and event_type in ['expiration', 'trial_end']:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(text="💳 Pagar Agora / Renovar", url=payment_link))
                
            if message:
                self._send_telegram_notification(
                    message, telegram_chat_id, request_id, 
                    reply_markup=markup, plex_user_id=user_profile.get('plex_user_id'), photo_url=photo_url
                )

        if can_notify_webhook:
            template_str = config.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, placeholders, is_json=True)
            if payload: 
                self._send_webhook_notification(payload, request_id, config)

        if can_notify_discord:
            template_str = config.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, placeholders, is_json=True)
            if payload: 
                self._send_discord_notification(payload, request_id, config)

    # --- API PÚBLICA DE EVENTOS ---

    def send_expiration_notification(self, user, days_left, user_profile):
        expiration_date_str = user_profile.get('expiration_date')
        formatted_date = ""
        
        # --- CORREÇÃO DO FUSO HORÁRIO (TIMEZONE) NA DATA DE VENCIMENTO ---
        if expiration_date_str:
            try:
                exp_dt = datetime.fromisoformat(expiration_date_str)
                # Converte o UTC da Base de Dados de volta para o Fuso Horário Local (ex: Brasil)
                if exp_dt.tzinfo:
                    exp_dt = exp_dt.astimezone(get_localzone())
                formatted_date = exp_dt.strftime('%d/%m/%Y')
            except (ValueError, TypeError):
                # Fallback de segurança se a data vier num formato inesperado
                formatted_date = expiration_date_str[:10]
        # -----------------------------------------------------------------
                
        self._prepare_and_send('expiration', user, user_profile, {'days': days_left, 'date': formatted_date})

    def send_renewal_notification(self, user, new_expiration_date, user_profile):
        # --- CORREÇÃO DO FUSO HORÁRIO (TIMEZONE) NA DATA DE RENOVAÇÃO ---
        if isinstance(new_expiration_date, datetime):
            if new_expiration_date.tzinfo:
                new_expiration_date = new_expiration_date.astimezone(get_localzone())
            formatted_date = new_expiration_date.strftime('%d/%m/%Y')
        else:
            try:
                exp_dt = datetime.fromisoformat(str(new_expiration_date))
                if exp_dt.tzinfo:
                    exp_dt = exp_dt.astimezone(get_localzone())
                formatted_date = exp_dt.strftime('%d/%m/%Y')
            except (ValueError, TypeError):
                formatted_date = str(new_expiration_date)[:10]
        # -----------------------------------------------------------------
        
        self._prepare_and_send('renewal', user, user_profile, {'new_date': formatted_date, 'date': formatted_date})

    def send_trial_end_notification(self, user, user_profile):
        self._prepare_and_send('trial_end', user, user_profile, {})

    def _build_placeholders(self, user, user_profile, context):
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

    # --- PROCESSAMENTO DE MENSAGENS EM MASSA (BULK) ---

    def process_bulk_notification_task(self, task):
        """Processa a tarefa de envio em massa (background job)."""
        from .. import extensions
        try:
            payload = json.loads(task.payload or '{}')
            message = payload.get('message')
            if not message: 
                raise ValueError("Mensagem vazia.")

            users_to_notify = self._get_bulk_target_users(payload, extensions)
            
            total_users = len(users_to_notify)
            extensions.data_manager.update_task(task.id, {'status': 'running', 'progress_total': total_users})
            
            if extensions.socketio:
                extensions.socketio.emit('bulk_notification_start', {'total': total_users}, namespace='/dashboard')
            
            all_profiles = {p['plex_user_id']: p for p in extensions.data_manager.get_all_user_profiles()}
            processed_count = 0
            
            for user in users_to_notify:
                profile = all_profiles.get(user['id'], {})
                
                has_contact = profile.get('telegram_id') or profile.get('telegram_user') or profile.get('phone_number') or profile.get('discord_user_id')
                if not has_contact: 
                    continue
                
                self._prepare_and_send('bulk', user, profile, {'message': message})
                processed_count += 1
                
                if processed_count % 5 == 0: 
                    extensions.data_manager.update_task(task.id, {'progress_current': processed_count})
                    if extensions.socketio:
                        extensions.socketio.emit('bulk_notification_progress', {'current': processed_count, 'total': total_users}, namespace='/dashboard')

                time.sleep(0.4)
                
            extensions.data_manager.update_task(task.id, {
                'status': 'completed', 
                'completed_at': datetime.now(timezone.utc), 
                'result': f'{processed_count} notificações enviadas.'
            })
            
            if extensions.socketio:
                extensions.socketio.emit('bulk_notification_end', {'message': f'{processed_count} enviadas.'}, namespace='/dashboard')
            
        except Exception as e:
            logger.error(f"Erro no processamento Bulk: {e}", exc_info=True)
            extensions.data_manager.update_task(task.id, {'status': 'failed', 'result': str(e)})
            
            if extensions.socketio:
                extensions.socketio.emit('bulk_notification_error', {'message': str(e)}, namespace='/dashboard')

    def _get_bulk_target_users(self, payload, extensions):
        """Filtra quais os utilizadores devem receber a mensagem em massa."""
        target_audience = payload.get('target_audience', 'active')
        target_user_ids = payload.get('user_ids', [])
        
        all_plex_users = extensions.plex_manager.get_all_plex_users()
        if not all_plex_users: 
            raise ValueError("Não foi possível obter a lista de utilizadores do Plex.")

        if target_audience == 'specific': 
            return [u for u in all_plex_users if str(u['id']) in map(str, target_user_ids)]
        elif target_audience == 'all': 
            return all_plex_users
        else:
            blocked_ids = {str(u['user_plex_id']) for u in extensions.data_manager.get_blocked_users_list()}
            if target_audience == 'blocked':
                return [u for u in all_plex_users if str(u['id']) in blocked_ids]
            else: # 'active'
                return [u for u in all_plex_users if str(u['id']) not in blocked_ids]
