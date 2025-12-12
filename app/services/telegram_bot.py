import logging
import threading
import telebot
from telebot import types
from datetime import datetime
import time
import os
from filelock import FileLock, Timeout
from app.config import load_or_create_config
from app.models import UserProfile, Invitation
# Importações necessárias para o contexto da app
from flask import current_app

logger = logging.getLogger(__name__)

class TelegramBotService:
    def __init__(self, app):
        self.app = app
        self.bot = None
        self.bot_thread = None
        self.monitor_thread = None
        self.is_running = False
        
        # Define o arquivo de lock para evitar duplicidade de processos
        log_file = app.config.get('LOG_FILE', 'app.log')
        self.lock_file = os.path.join(os.path.dirname(log_file), 'telegram_bot.lock')
        self.lock = FileLock(self.lock_file, timeout=0)

    def start(self):
        """Inicia a thread de monitoramento (não inicia o bot diretamente)."""
        if self.monitor_thread and self.monitor_thread.is_alive():
            return

        logger.info("Iniciando monitor de estado do Telegram Bot...")
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _monitor_loop(self):
        """
        Verifica periodicamente se a configuração mudou.
        Se ativado no config e parado -> Inicia.
        Se desativado no config e rodando -> Para.
        """
        while True:
            try:
                self._check_and_toggle()
            except Exception as e:
                logger.error(f"Erro no monitor do Telegram: {e}")
            
            # Verifica a cada 10 segundos
            time.sleep(10)

    def _check_and_toggle(self):
        # Recarrega a configuração diretamente do arquivo/banco
        config = load_or_create_config()
        
        # Tratamento seguro para booleans que podem vir como string/int
        raw_enabled = config.get("TELEGRAM_ENABLED", False)
        if isinstance(raw_enabled, str):
            enabled = raw_enabled.lower() == 'true'
        else:
            enabled = bool(raw_enabled)
            
        token = config.get("TELEGRAM_BOT_TOKEN")

        should_run = enabled and token

        if should_run and not self.is_running:
            self._start_bot_process(token)
        elif not should_run and self.is_running:
            self._stop_bot_process()

    def _start_bot_process(self, token):
        """Tenta adquirir o lock e iniciar o bot."""
        try:
            self.lock.acquire()
        except Timeout:
            # Outro worker (Gunicorn) já está rodando o bot.
            # Não faz nada, apenas marca como 'não rodando neste processo'.
            return

        try:
            logger.info("Configuração detectada: Iniciando Bot do Telegram...")
            self.bot = telebot.TeleBot(token)
            self._register_handlers()
            
            self.bot_thread = threading.Thread(target=self._run_polling, daemon=True)
            self.bot_thread.start()
            self.is_running = True
        except Exception as e:
            logger.error(f"Falha ao iniciar o Bot do Telegram: {e}")
            if self.lock.is_locked:
                self.lock.release()

    def _stop_bot_process(self):
        """Para o bot e libera recursos."""
        logger.info("Configuração desativada: Parando Bot do Telegram...")
        
        if self.bot:
            try:
                self.bot.stop_polling()
            except Exception as e:
                logger.warning(f"Erro ao parar polling do Telegram: {e}")
            self.bot = None
        
        self.is_running = False
        
        if self.lock.is_locked:
            self.lock.release()
            
        logger.info("Bot do Telegram parado com sucesso.")

    def _run_polling(self):
        """Loop de polling do bot."""
        if not self.bot: return
        try:
            self.bot.infinity_polling(timeout=10, long_polling_timeout=5)
        except Exception as e:
            # Se o polling cair (ex: erro de rede ou stop forçado), atualiza estado
            logger.info(f"Polling do Telegram encerrado: {e}")
        finally:
            self.is_running = False
            if self.lock.is_locked:
                self.lock.release()

    # --- Lógicas de Mensagem e Menus (Mantidas e Atualizadas) ---

    def _get_inline_menu(self):
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_status = types.InlineKeyboardButton("📊 Ver Status", callback_data="check_status")
        btn_invite = types.InlineKeyboardButton("🎟️ Ver Convites", callback_data="check_invite")
        btn_info = types.InlineKeyboardButton("ℹ️ Como Funciona", callback_data="how_it_works")
        btn_id = types.InlineKeyboardButton("🆔 Meu ID", callback_data="my_id")
        markup.add(btn_status, btn_invite, btn_info, btn_id)
        return markup

    def _get_welcome_message(self, username):
        """Gera o texto da mensagem de boas-vindas."""
        config = load_or_create_config() # Garante config atualizada
        app_title = config.get('APP_TITLE', 'Painel Plex')
        return (
            f"🎬 Bem-vindo ao <b>{app_title}</b>, {username}!\n\n"
            f"<b>🔥 Acesso completo ao servidor Plex!</b>\n"
            f"• Filmes, séries, animes, novelas, documentários.\n"
            f"• Acesso 24/7 sem limites\n"
            f"• Múltiplos dispositivos\n"
            f"• Conteúdo atualizado diariamente\n"
            f"• Sistema de pedidos\n\n"
            f"👇 <b>Selecione uma opção abaixo para começar:</b>"
        )

    def _response_how_it_works(self):
        msg = (
            "<b>🎬 O que é o Plex?</b>\n"
            "O Plex é uma plataforma de streaming que organiza e transmite mídia. Nosso servidor contém milhares de filmes, séries, animes e documentários.\n\n"
            "<b>📱 Como acessar:</b>\n"
            "1. Faça seu teste grátis ou assine\n"
            "2. Receba um convite por email\n"
            "3. Acesse app.plex.tv ou baixe o app\n"
            "4. Aceite o convite do servidor\n"
            "5. Comece a assistir!\n\n"
            "<b>💻 Dispositivos suportados:</b>\n"
            "• Smartphones (Android/iOS)\n"
            "• Smart TVs (Samsung, LG, etc.)\n"
            "• Computadores (Windows/Mac)\n"
            "• Chromecast, Fire TV Stick, Apple TV\n"
            "• PlayStation, Xbox\n\n"
            "<b>🔐 Segurança:</b>\n"
            "• Servidor privado e seguro\n"
            "• Sem vírus ou malware\n"
            "• Acesso 24/7 garantido"
        )
        markup = types.InlineKeyboardMarkup()
        btn_plex = types.InlineKeyboardButton("Baixar Plex App", url="https://www.plex.tv/media-server-downloads/#plex-app")
        markup.add(btn_plex)
        return msg, markup

    def _response_status(self, chat_id):
        with self.app.app_context():
            user = UserProfile.query.filter_by(telegram_user=str(chat_id)).first()
            
            if not user:
                return ("❌ <b>Conta não vinculada.</b>\n\nEnvie este ID para o administrador: <code>" + str(chat_id) + "</code>", None)

            status_emoji = "✅" if user.status == 'active' else "🔴"
            expiration = "N/A"
            
            if user.expiration_date:
                try:
                    dt = datetime.fromisoformat(user.expiration_date)
                    expiration = dt.strftime('%d/%m/%Y')
                except ValueError:
                    pass
            
            response = (
                f"👤 <b>Conta:</b> {user.username}\n"
                f"📊 <b>Status:</b> {status_emoji} {user.status.upper()}\n"
                f"📅 <b>Vencimento:</b> {expiration}\n"
                f"📺 <b>Telas:</b> {user.screen_limit if user.screen_limit > 0 else 'Ilimitado'}"
            )
            
            markup = None
            if user.payment_token:
                config = load_or_create_config()
                base_url = config.get('APP_BASE_URL', '').rstrip('/')
                if base_url:
                    payment_link = f"{base_url}/pay/{user.payment_token}"
                    markup = types.InlineKeyboardMarkup()
                    btn_renew = types.InlineKeyboardButton("🔗 Renovar Agora", url=payment_link)
                    markup.add(btn_renew)
            
            return response, markup

    def _response_invite(self, chat_id):
        with self.app.app_context():
            now_iso = datetime.utcnow().isoformat()
            invites = Invitation.query.filter_by(telegram_id=str(chat_id)).all()
            
            valid_invite = None
            for invite in invites:
                is_expired = invite.expires_at and invite.expires_at < now_iso
                is_full = invite.use_count >= invite.max_uses
                
                if not is_expired and not is_full:
                    valid_invite = invite
                    break
            
            if valid_invite:
                config = load_or_create_config()
                base_url = config.get('APP_BASE_URL', '').rstrip('/')
                invite_url = f"{base_url}/invite/{valid_invite.code}" if base_url else f"Código: {valid_invite.code}"
                
                msg = (
                    f"🎉 <b>Convite Disponível!</b>\n\n"
                    f"Você tem um convite aguardando resgate.\n"
                    f"Clique no botão abaixo para ativar."
                )
                
                markup = types.InlineKeyboardMarkup()
                if base_url:
                    btn_claim = types.InlineKeyboardButton("🚀 Resgatar Convite", url=invite_url)
                    markup.add(btn_claim)
                return msg, markup
            else:
                return ("❌ Nenhum convite ativo encontrado para o seu Telegram ID.", None)

    def _register_handlers(self):
        """Registra os handlers do bot."""

        @self.bot.message_handler(commands=['start', 'ajuda', 'help'])
        def send_welcome(message):
            chat_id = message.chat.id
            username = message.from_user.first_name
            msg = self._get_welcome_message(username)
            self.bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=self._get_inline_menu())

        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_query(call):
            chat_id = call.message.chat.id
            message_id = call.message.message_id
            text, markup = None, None

            if call.data == "back_to_main":
                text = self._get_welcome_message(call.from_user.first_name)
                markup = self._get_inline_menu()
            elif call.data == "how_it_works":
                text, markup = self._response_how_it_works()
            elif call.data == "my_id":
                text = f"Seu ID: <code>{chat_id}</code>"
                markup = None
            elif call.data == "check_status":
                text, markup = self._response_status(chat_id)
            elif call.data == "check_invite":
                text, markup = self._response_invite(chat_id)

            if text:
                if call.data != "back_to_main":
                    if markup is None: markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("🔙 Voltar", callback_data="back_to_main"))

                try:
                    self.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
                except Exception:
                    pass
                self.bot.answer_callback_query(call.id)

        @self.bot.message_handler(commands=['comofunciona'])
        def send_how_it_works(message):
            text, markup = self._response_how_it_works()
            self.bot.reply_to(message, text, parse_mode="HTML", reply_markup=markup)

        @self.bot.message_handler(commands=['id'])
        def send_id(message):
            self.bot.reply_to(message, f"Seu ID: <code>{message.chat.id}</code>", parse_mode="HTML")

        @self.bot.message_handler(commands=['status', 'minhaconta'])
        def check_status(message):
            text, markup = self._response_status(message.chat.id)
            self.bot.reply_to(message, text, parse_mode="HTML", reply_markup=markup)

        @self.bot.message_handler(commands=['convite'])
        def check_invite(message):
            text, markup = self._response_invite(message.chat.id)
            self.bot.reply_to(message, text, parse_mode="HTML", reply_markup=markup)