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
    "TELEGRAM_REACTIVATION_MESSAGE_TEMPLATE": "✅ *Conta Reativada*\n\nOlá {name}!\nA sua subscrição foi renovada e a sua conta reativada com sucesso.\nNovo vencimento: *{new_date}*\n\nPara acessar o servidor, clique no link abaixo e aceite o convite:\n{invite_link}",
    "TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE": "⌛ *Fim do Período de Teste*\n\n{name}, o seu período de teste terminou.\nPara manter o seu acesso, realize a renovação no botão abaixo:",
    "DISCORD_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso de Vencimento", "description": "Olá **{username}**! 👋\\n\\nO seu acesso ao Plex está prestes a expirar em **{days} dia(s)**, no dia **{date}**.\\n\\nPara evitar a interrupção do serviço, por favor, [clique aqui para renovar]({payment_link}).", "color": 16776960}]}',
    "DISCORD_RENEWAL_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Renovação Confirmada!", "description": "Olá **{username}**! ✅\\n\\nA sua assinatura foi renovada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\nObrigado e aproveite!", "color": 65280}]}',
    "DISCORD_REACTIVATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Conta Reativada!", "description": "Olá **{username}**! ✅\\n\\nA sua assinatura foi reativada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\n[Clique aqui para aceitar o convite do Plex]({invite_link})", "color": 65280}]}',
    "DISCORD_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Período de Teste Terminou", "description": "Olá **{username}**! ⌛\\n\\nO seu período de teste gratuito terminou. Para continuar a ter acesso, por favor, [clique aqui para renovar]({payment_link}).", "color": 16711680}]}',
    "WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}"}',
    "WEBHOOK_RENEWAL_MESSAGE_TEMPLATE": '{"content": "✅ A subscrição de {username} foi renovada. Novo vencimento: {new_date}."}',
    "WEBHOOK_REACTIVATION_MESSAGE_TEMPLATE": '{"content": "✅ A subscrição de {username} foi reativada. Novo vencimento: {new_date}. Link de acesso: {invite_link}"}',
    "WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "O período de teste para {username} terminou. Para renovar, acesse: {payment_link}"}',
    "TELEGRAM_BULK_MESSAGE_TEMPLATE": "📢 *Aviso do Servidor*\n\nOlá {name},\n\n{message}",
    "DISCORD_BULK_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso do Servidor", "description": "{message}", "color": 3447003}]}',
    "WEBHOOK_BULK_MESSAGE_TEMPLATE": '{"phone": "{phone_number}@s.whatsapp.net", "message": "{message}"}'
}

def get_greeting():
    current_hour = datetime.now(get_localzone()).hour
    if 5 <= current_hour < 12: return _("Bom dia")
    elif 12 <= current_hour < 18: return _("Boa tarde")
    else: return _("Boa noite")

class NotifierManager:
    def __init__(self, link_shortener_service=None, socketio_instance=None):
        self.link_shortener = link_shortener_service
        self.socketio = socketio_instance
        self._bot = None

    def _get_bot(self):
        config = load_or_create_config()
        token = config.get("TELEGRAM_BOT_TOKEN")
        if not token: 
            return None
        if self._bot is None or self._bot.token != token:
            self._bot = telebot.TeleBot(token, threaded=False)
        return self._bot

    def _convert_md_to_html(self, text):
        if not text: return ""
        # Uso do modificador flag in-line (?s) = re.DOTALL para que o .* cubra quebras de linha de forma contida
        text = re.sub(r'\*(.*?)\*', r'<b>\1</b>', text, flags=re.DOTALL)
        text = re.sub(r'_(.*?)_', r'<i>\1</i>', text, flags=re.DOTALL)
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text, flags=re.DOTALL)
        return text

    def _format_template(self, template_str, placeholders, is_json=False, use_html_escape=False):
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

    def _send_telegram_notification(self, message, chat_id, request_id, reply_markup=None, plex_user_id=None, photo_url=None):
        bot = self._get_bot()
        if not bot: return
        
        html_message = self._convert_md_to_html(message)
        max_retries = 3
        
        # 🚀 OTIMIZAÇÃO: Substituição de Recursão por Loop Iterativo Seguro
        for attempt in range(max_retries):
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
                return  # Sucesso! Sai do loop.
                
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    retry_after = e.result_json.get('parameters', {}).get('retry_after', 5)
                    logger.warning(f"Telegram Rate Limit atingido. A aguardar {retry_after}s... (Tentativa {attempt + 1}/{max_retries})")
                    time.sleep(retry_after + 1)
                    continue  # Tenta de novo na próxima iteração do loop
                    
                if e.error_code == 403 and plex_user_id:
                    logger.warning(f"[ID: {request_id}] Bot bloqueado pelo utilizador {plex_user_id}. A remover contacto.")
                    from .. import extensions
                    extensions.data_manager.update_user_profile(plex_user_id, {'telegram_id': None, 'telegram_user': None})
                    return
                else:
                    logger.error(f"[ID: {request_id}] Erro Telegram: {e.description}")
                    raise e
            except Exception as e:
                logger.error(f"[ID: {request_id}] Erro inesperado ao enviar Telegram: {e}")
                raise e

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
            # 🐛 CORREÇÃO: `bool(response)` do requests retorna False para qualquer status de erro
            # (4xx/5xx), então checar "e.response" diretamente escondia o corpo real do erro
            # justamente quando ele é mais necessário. Usamos "is not None" para pegar sempre
            # a resposta de verdade, quando ela existir.
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code
                body = e.response.text.strip() if e.response.text else "(corpo de resposta vazio)"
                error_response = f"HTTP {status_code} - {body}"
            else:
                error_response = "Sem resposta do servidor (falha de conexão/timeout)"
            logger.error(f"[ID: {request_id}] Falha no Webhook: {e} | Resposta: {error_response}")
            raise Exception(f"Falha de Webhook: {error_response}")

    def _send_discord_notification(self, payload, request_id, config):
        webhook_url = config.get("DISCORD_WEBHOOK_URL")
        if not webhook_url: return
        
        try:
            requests.post(webhook_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=30).raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"[ID: {request_id}] Falha no Discord: {e}")
            raise e

    def _get_price_and_plan(self, config, user_screen_limit):
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
        if event_type in ['renewal', 'reactivation'] or not user_profile.get('payment_token'):
            return None
            
        try:
            token = user_profile['payment_token']
            app_base_url = config.get("APP_BASE_URL", "").strip().rstrip('/')
            
            # 🚀 OTIMIZAÇÃO: Prioriza sempre a APP_BASE_URL.
            # O url_for via Scheduler em background costuma falhar pois não tem o SERVER_NAME injetado na thread.
            if app_base_url:
                long_url = f"{app_base_url}/pay/{token}"
            else:
                from flask import url_for
                # Fallback perigoso se executado fora de uma web request (ex: Tarefas CRON)
                long_url = url_for('main.payment_page', token=token, _external=True)

            if config.get("ENABLE_LINK_SHORTENER") and self.link_shortener:
                return self.link_shortener.create_short_link(long_url)
            return long_url
        except Exception as e:
            logger.warning(f"Erro ao gerar link de pagamento: {e}")
            return long_url if 'long_url' in locals() else None

    def _prepare_and_send(self, event_type, user, user_profile, context):
        config = load_or_create_config()
        request_id = str(uuid.uuid4())
        
        telegram_chat_id = user_profile.get('telegram_id') or user_profile.get('telegram_user')
        can_notify_telegram = config.get("TELEGRAM_ENABLED") and telegram_chat_id
        can_notify_webhook = config.get("WEBHOOK_ENABLED") and user_profile.get('phone_number')
        can_notify_discord = config.get("DISCORD_ENABLED") and user_profile.get('discord_user_id')
        
        if not (can_notify_telegram or can_notify_webhook or can_notify_discord): 
            return

        t_tpl = config.get(f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"TELEGRAM_{event_type.upper()}_MESSAGE_TEMPLATE", "")
        w_tpl = config.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE", "")
        d_tpl = config.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE", "")
        bulk_msg = context.get('message', '')
        
        all_text = f"{t_tpl} {w_tpl} {d_tpl} {bulk_msg}"
        
        # Inteligência: Só gera link se a mensagem realmente pedir
        needs_payment_link = (
            "{payment_link}" in all_text or 
            "{paymentlink}" in all_text or 
            event_type in ['expiration', 'trial_end']
        )
        
        if needs_payment_link:
            payment_link = self._get_payment_link(config, event_type, user_profile)
        else:
            payment_link = None

        formatted_price, plan_name = self._get_price_and_plan(config, user_profile.get('screen_limit', 0))

        now = datetime.now(get_localzone())
        placeholders = {
            **self._build_placeholders(user, user_profile, context),
            'payment_link': payment_link or "#",
            'paymentlink': payment_link or "#",
            'price': formatted_price,
            'plan_name': plan_name,
            'planname': plan_name,
            'date_time': now.strftime('%d/%m/%Y %H:%M'),
            'days_left': context.get('days', 0),
            'invite_link': context.get('invite_link', '')
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
                try:
                    self._send_telegram_notification(
                        message, telegram_chat_id, request_id, 
                        reply_markup=markup, plex_user_id=user_profile.get('plex_user_id'), photo_url=photo_url
                    )
                except Exception as e:
                    # Isola a falha: não deixa o erro do Telegram impedir o envio pelo
                    # Webhook/Discord logo abaixo (ou interromper outros usuários no lote).
                    logger.error(f"[ID: {request_id}] Notificação via Telegram falhou para '{user.get('username')}': {e}")

        if can_notify_webhook:
            template_str = config.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"WEBHOOK_{event_type.upper()}_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, placeholders, is_json=True)
            if payload:
                try:
                    self._send_webhook_notification(payload, request_id, config)
                except Exception as e:
                    # Isola a falha: não deixa o erro do Webhook impedir o envio pelo Discord
                    # logo abaixo (ou interromper a notificação de outros usuários no lote).
                    logger.error(f"[ID: {request_id}] Notificação via Webhook falhou para '{user.get('username')}': {e}")

        if can_notify_discord:
            template_str = config.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE") or DEFAULT_TEMPLATES.get(f"DISCORD_{event_type.upper()}_MESSAGE_TEMPLATE")
            payload = self._format_template(template_str, placeholders, is_json=True)
            if payload:
                try:
                    self._send_discord_notification(payload, request_id, config)
                except Exception as e:
                    logger.error(f"[ID: {request_id}] Notificação via Discord falhou para '{user.get('username')}': {e}")

    def send_expiration_notification(self, user, days_left, user_profile):
        expiration_date_str = user_profile.get('expiration_date')
        formatted_date = ""
        if expiration_date_str:
            try:
                exp_dt = datetime.fromisoformat(expiration_date_str)
                if exp_dt.tzinfo:
                    exp_dt = exp_dt.astimezone(get_localzone())
                formatted_date = exp_dt.strftime('%d/%m/%Y')
            except (ValueError, TypeError):
                formatted_date = expiration_date_str[:10]
        self._prepare_and_send('expiration', user, user_profile, {'days': days_left, 'date': formatted_date})

    def send_renewal_notification(self, user, new_expiration_date, user_profile):
        # 🛡️ ANTI-DUPLICAÇÃO: Evita enviar a mensagem de Renovação logo após uma Reativação (Janela de 60 Segundos)
        # 🐛 NOTA: usar 'or 0' em vez de confiar no default do .get(). A coluna
        # 'last_reactivation_time' é nullable, por isso a chave EXISTE no perfil
        # com valor None para quem nunca foi reativado — e o default do .get()
        # só se aplica a chaves AUSENTES, não a chaves com valor None. Sem isto,
        # a subtração rebentava com "unsupported operand type(s) for -: 'float' and 'NoneType'".
        #
        # O float() envolvente protege ainda contra o valor vir como texto (o que
        # pode acontecer se o campo for escrito a partir de JSON ou de um formulário).
        try:
            last_reactivation = float(user_profile.get('last_reactivation_time') or 0)
        except (TypeError, ValueError):
            last_reactivation = 0.0

        if time.time() - last_reactivation < 60:
            logger.info(f"Notificação de Renovação ignorada para '{user.get('username')}' porque a notificação de Reativação já foi enviada.")
            return

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
        self._prepare_and_send('renewal', user, user_profile, {'new_date': formatted_date, 'date': formatted_date})

    def send_reactivation_notification(self, user, new_expiration_date, user_profile, invite_link):
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
        self._prepare_and_send('reactivation', user, user_profile, {'new_date': formatted_date, 'date': formatted_date, 'invite_link': invite_link})

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

    # --- PROCESSAMENTO DE MENSAGENS EM MASSA (BULK) EM TEMPO REAL ---

    def process_bulk_notification_task(self, task):
        from .. import extensions
        from flask import current_app
        
        # Garante que extraímos o ID e o Payload quer seja um objeto SQLAlchemy ou um Dicionário nativo
        if isinstance(task, dict):
            task_id = task.get('id')
            raw_payload = task.get('payload', '{}')
        else:
            task_id = getattr(task, 'id', None)
            raw_payload = getattr(task, 'payload', '{}')

        if not task_id:
            logger.error("Tarefa sem ID não pode ser processada.")
            return

        app_obj = current_app._get_current_object() if current_app else None

        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            
            message = payload.get('message')
            if not message: 
                raise ValueError("Mensagem vazia.")

            users_to_notify = self._get_bulk_target_users(payload, extensions)
            total_users = len(users_to_notify)
            
            extensions.data_manager.update_task(task_id, {'status': 'running', 'progress_total': total_users})
            
            # O worker empacotado para o SocketIO
            def bulk_worker():
                if app_obj:
                    ctx = app_obj.test_request_context('/')
                    ctx.push()

                try:
                    def emit_ws(event, data):
                        try:
                            if extensions.socketio:
                                extensions.socketio.emit(event, data, namespace='/dashboard')
                                extensions.socketio.emit(event, data, namespace='/users')
                        except Exception as e:
                            logger.debug(f"Aviso ao tentar emitir evento WS: {e}")

                    if extensions.socketio: extensions.socketio.sleep(1)

                    emit_ws('bulk_notification_start', {'total': total_users})
                    emit_ws('bulk_console_log', {'msg': "========================================================="})
                    emit_ws('bulk_console_log', {'msg': f"🚀 INÍCIO DO ENVIO EM MASSA ({total_users} utilizadores elegíveis)"})
                    emit_ws('bulk_console_log', {'msg': "========================================================="})
                    
                    all_profiles = {p['plex_user_id']: p for p in extensions.data_manager.get_all_user_profiles()}
                    processed_count = 0
                    
                    for index, user in enumerate(users_to_notify, 1):
                        username = user.get('username', f'ID {user.get("id")}')
                        profile = all_profiles.get(user['id'], {})
                        
                        emit_ws('bulk_notification_progress', {'current': index, 'total': total_users})
                        
                        # Atualiza a base de dados a cada 10 envios para poupar conexões
                        if index % 10 == 0 or index == total_users:
                            extensions.data_manager.update_task(task_id, {'progress_current': index})

                        has_contact = profile.get('telegram_id') or profile.get('telegram_user') or profile.get('phone_number') or profile.get('discord_user_id')
                        if not has_contact: 
                            emit_ws('bulk_console_log', {'msg': f"[{index}/{total_users}] ⏭️ Ignorado: {username} (Sem dados de contacto)"})
                            if extensions.socketio: extensions.socketio.sleep(0.1)
                            continue
                        
                        try:
                            emit_ws('bulk_console_log', {'msg': f"[{index}/{total_users}] ⏳ A processar envio para {username}..."})
                            self._prepare_and_send('bulk', user, profile, {'message': message})
                            processed_count += 1
                            emit_ws('bulk_console_log', {'msg': f"[{index}/{total_users}] ✅ Sucesso: Entregue a {username}."})
                            
                        except Exception as user_err:
                            logger.error(f"Erro no envio em massa para {username}: {user_err}")
                            emit_ws('bulk_console_log', {'msg': f"[{index}/{total_users}] ❌ Erro ({username}): {str(user_err)}"})
                        
                        # ⏱️ OTIMIZAÇÃO: Reduzido de 0.5s para 0.2s. A proteção real contra
                        # rate-limit do Telegram já é feita via tratamento do erro 429
                        # (com retry_after) em _send_telegram_notification — esta pausa aqui
                        # é apenas uma cautela extra, e 0.5s por usuário tornava envios em
                        # massa desnecessariamente lentos em bases grandes.
                        if extensions.socketio:
                            extensions.socketio.sleep(0.2)
                        else:
                            time.sleep(0.2)
                        
                    extensions.data_manager.update_task(task_id, {
                        'status': 'completed', 
                        'completed_at': datetime.now(timezone.utc), 
                        'result': f'{processed_count} notificações enviadas.'
                    })
                    
                    emit_ws('bulk_console_log', {'msg': f"🎉 Concluído! Mensagens entregues com sucesso: {processed_count}"})
                    emit_ws('bulk_notification_end', {'message': f'{processed_count} mensagens enviadas com sucesso.'})
                    
                finally:
                    if app_obj:
                        ctx.pop()

            if extensions.socketio:
                extensions.socketio.start_background_task(bulk_worker)
            else:
                bulk_worker()
            
        except Exception as e:
            logger.error(f"Erro crítico no processamento Bulk: {e}", exc_info=True)
            
            if task_id:
                extensions.data_manager.update_task(task_id, {'status': 'failed', 'result': str(e)})
            
            try:
                if extensions.socketio:
                    extensions.socketio.emit('bulk_console_log', {'msg': f"💥 ERRO CRÍTICO NA TAREFA: {str(e)}"})
                    extensions.socketio.emit('bulk_notification_error', {'message': str(e)}, namespace='/dashboard')
                    extensions.socketio.emit('bulk_notification_error', {'message': str(e)}, namespace='/users')
            except Exception:
                pass

    def _get_bulk_target_users(self, payload, extensions):
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
            else: 
                return [u for u in all_plex_users if str(u['id']) not in blocked_ids]