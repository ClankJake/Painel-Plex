import os
import json
import secrets
import logging

logger = logging.getLogger(__name__)

# --- Constantes ---
# O diretório de configuração pode ser redirecionado através da variável de
# ambiente PAINEL_PLEX_CONFIG_DIR. Isto é usado pelos testes automatizados (para
# não tocarem no config.json real) e permite, em instalações personalizadas,
# manter os dados fora da pasta do código.
CONFIG_DIR = os.environ.get('PAINEL_PLEX_CONFIG_DIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'config'
)
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')

def load_or_create_config():
    """
    Carrega a configuração do config.json ou cria um ficheiro padrão se não existir.
    A SECRET_KEY é tratada com prioridade para segurança.
    """
    # Garante que o diretório de configuração existe
    os.makedirs(CONFIG_DIR, exist_ok=True)

    # Prioridade 1: Tenta carregar a chave a partir de uma variável de ambiente.
    # Isto é ideal para ambientes de produção (ex: Docker, Heroku).
    secret_key_from_env = os.environ.get('SECRET_KEY')

    if not os.path.exists(CONFIG_FILE):
        logger.info(f"O ficheiro de configuração '{CONFIG_FILE}' não foi encontrado. Criando um novo.")
        default_config = {
            "IS_CONFIGURED": False,
            # Se a variável de ambiente existir, usa-a; senão, gera uma nova chave segura.
            "SECRET_KEY": secret_key_from_env or secrets.token_hex(16),
            "INTERNAL_TRIGGER_KEY": secrets.token_hex(32),
            "APP_TITLE": "Painel Plex",
            "APP_BASE_URL": "",
            "LOG_LEVEL": "INFO",
            "LOG_FILE": os.path.join(CONFIG_DIR, "app.log"),
            "LOG_MAX_BYTES": 1024 * 1024, # 1 MB
            "LOG_BACKUP_COUNT": 5,
            # Janela (segundos) durante a qual mensagens de log idênticas são
            # agrupadas em vez de repetidas linha a linha. 0 desativa.
            "LOG_DEDUP_SECONDS": 60,
            "LAST_NOTIFICATION_CHECK": "1970-01-01T00:00:00",
            "ADMIN_USER": "",
            "ADMIN_USER_ID": "",
            "PLEX_URL": "",
            "PLEX_TOKEN": "",
            "TAUTULLI_URL": "",
            "TAUTULLI_API_KEY": "",
            "STREAM_CHECK_INTERVAL_SECONDS": 15,
            "SCREEN_LIMIT_TERMINATION_STRATEGY": "oldest",
            "DAYS_TO_REMOVE_BLOCKED_USER": 0,
            "EXPIRATION_NOTIFICATION_TIME": "09:00",
            "BLOCK_REMOVAL_TIME": "02:00",
            "UNIVERSAL_EXPIRATION_ENABLED": False,
            "UNIVERSAL_EXPIRATION_TIME": "23:59",
            "WEBHOOK_URL": "",
            "WEBHOOK_AUTHORIZATION_HEADER": "",
            "WEBHOOK_ENABLED": False,
            "WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}"}',
            "WEBHOOK_RENEWAL_MESSAGE_TEMPLATE": '{"content": "✅ A subscrição de {username} foi renovada. Novo vencimento: {new_date}."}',
            "WEBHOOK_REACTIVATION_MESSAGE_TEMPLATE": '{"content": "✅ A subscrição de {username} foi reativada. Novo vencimento: {new_date}. Acesse o servidor: {invite_link}"}',
            "WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "O período de teste para {username} terminou. Para renovar, acesse: {payment_link}"}',
            "WEBHOOK_BULK_MESSAGE_TEMPLATE": '{"phone": "{phone_number}@s.whatsapp.net", "message": "{message}"}',
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "", 
            "TELEGRAM_ENABLED": False,
            "TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE": "Olá {name}, {greeting}!\n\nEste é um lembrete de que sua fatura está com o vencimento próximo.\nVencimento: *{date}*\nValor: *{price}*\nPlano: *{plan_name}*\nAcesso: `{email}`\n\nNa data do vencimento o sistema poderá bloquear o acesso. Para evitar a interrupção, realize o pagamento clicando no botão abaixo:",
            "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "✅ *Renovação Confirmada*\n\nOlá {name}!\nA sua subscrição foi renovada com sucesso.\nNovo vencimento: *{new_date}*.",
            "TELEGRAM_REACTIVATION_MESSAGE_TEMPLATE": "✅ *Conta Reativada*\n\nOlá {name}!\nA sua subscrição foi renovada e a sua conta reativada com sucesso.\nNovo vencimento: *{new_date}*\n\nPara acessar o servidor, aceite o convite no link abaixo:\n{invite_link}",
            "TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE": "⌛ *Fim do Período de Teste*\n\n{name}, o seu período de teste terminou.\nPara manter o seu acesso, realize a renovação no botão abaixo:",
            "TELEGRAM_BULK_MESSAGE_TEMPLATE": "📢 *Aviso do Servidor*\n\nOlá {name},\n\n{message}",
            "DISCORD_ENABLED": False,
            "DISCORD_WEBHOOK_URL": "",
            "DISCORD_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso de Vencimento", "description": "Olá **{username}**! 👋\\n\\nO seu acesso ao Plex está prestes a expirar em **{days} dia(s)**, no dia **{date}**.\\n\\nPara evitar a interrupção do serviço, por favor, [clique aqui para renovar]({payment_link}).", "color": 16776960}]}',
            "DISCORD_RENEWAL_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Renovação Confirmada!", "description": "Olá **{username}**! ✅\\n\\nA sua assinatura foi renovada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\nObrigado e aproveite!", "color": 65280}]}',
            "DISCORD_REACTIVATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Conta Reativada!", "description": "Olá **{username}**! ✅\\n\\nA sua assinatura foi reativada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\n[Clique aqui para aceitar o convite do Plex]({invite_link})", "color": 65280}]}',
            "DISCORD_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Período de Teste Terminou", "description": "Olá **{username}**! ⌛\\n\\nO seu período de teste gratuito terminou. Para continuar a ter acesso, por favor, [clique aqui para renovar]({payment_link}).", "color": 16711680}]}',
            "DISCORD_BULK_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso do Servidor", "description": "{message}", "color": 3447003}]}',
            "DAYS_TO_NOTIFY_EXPIRATION": 2,
            "EFI_ENABLED": False,
            "EFI_CLIENT_ID": "",
            "EFI_CLIENT_SECRET": "",
            "EFI_CERTIFICATE": "/app/certs/efisandbox.pem",
            "EFI_SANDBOX": True,
            "EFI_PIX_KEY": "", # A sua chave PIX
            "EFI_USE_MTLS": True,
            "EFI_WEBHOOK_HMAC_SECRET": secrets.token_hex(16),
            "MERCADOPAGO_ENABLED": False,
            "MERCADOPAGO_ACCESS_TOKEN": "",
            "MERCADOPAGO_WEBHOOK_SECRET": "",
            "MERCADOPAGO_MIN_AMOUNT": 1.0,
            "GATES2B_ENABLED": False,
            "GATES2B_AUTH_TOKEN": "",
            "GATES2B_MIN_AMOUNT": 3.0,
            "RENEWAL_PRICE": "10.00",
            "SCREEN_PRICES": {
                "1": "10.00",
                "2": "18.00",
                "3": "25.00",
                "4": "30.00",
                "5": "35.00",
                "6": "40.00"
            },
            "OVERSEERR_ENABLED": False,
            "OVERSEERR_URL": "",
            "OVERSEERR_API_KEY": "",
            "CLEANUP_PENDING_PAYMENTS_ENABLED": True,
            "CLEANUP_PENDING_PAYMENTS_DAYS": 3,
            "CLEANUP_TIME": "03:00",
            "ENABLE_LINK_SHORTENER": True,
            "PAYMENT_LINK_GRACE_PERIOD_DAYS": 7,
            "ACHIEVEMENT_MOVIE_MARATHON_BRONZE": 5,
            "ACHIEVEMENT_MOVIE_MARATHON_SILVER": 10,
            "ACHIEVEMENT_MOVIE_MARATHON_GOLD": 20,
            "ACHIEVEMENT_SERIES_BINGER_BRONZE": 20,
            "ACHIEVEMENT_SERIES_BINGER_SILVER": 50,
            "ACHIEVEMENT_SERIES_BINGER_GOLD": 100,
            "ACHIEVEMENT_TIME_TRAVELER_BRONZE": 3,
            "ACHIEVEMENT_TIME_TRAVELER_SILVER": 5,
            "ACHIEVEMENT_TIME_TRAVELER_GOLD": 7,
            "ACHIEVEMENT_DIRECTOR_FAN_BRONZE": 3,
            "ACHIEVEMENT_DIRECTOR_FAN_SILVER": 5,
            "ACHIEVEMENT_DIRECTOR_FAN_GOLD": 7,
            "ACHIEVEMENT_NIGHT_OWL_BRONZE": 3,
            "ACHIEVEMENT_NIGHT_OWL_SILVER": 10,
            "ACHIEVEMENT_NIGHT_OWL_GOLD": 25,
            "ACHIEVEMENT_PIONEER_BRONZE": 1,
            "ACHIEVEMENT_PIONEER_SILVER": 5,
            "ACHIEVEMENT_PIONEER_GOLD": 15,
            "ACHIEVEMENT_PIONEER_WINDOW_HOURS": 48,
            "XP_PER_MINUTE_WATCHED": 1.0,
            "XP_BONUS_PER_COMPLETED_ITEM": 20,
            "XP_COMPLETION_THRESHOLD_PERCENT": 90,
            # 🎯 Recomendações ("Porque assistiu X, pode gostar de Y").
            "RECOMMENDATIONS_ENABLED": True,
            "RECOMMENDATIONS_HISTORY_DAYS": 180,
            "RECOMMENDATIONS_MIN_PERCENT_WATCHED": 25,
            "RECOMMENDATIONS_MIN_CO_OCCURRENCE": 2,
            "RECOMMENDATIONS_MAX_SECTIONS": 4,
            "RECOMMENDATIONS_ITEMS_PER_SECTION": 8,
            "RECOMMENDATIONS_RESPECT_PRIVACY": True,
            "RECOMMENDATIONS_GENRE_LOOKUP_LIMIT": 40,
            "WHATSAPP_ENABLED": False,
            "WHATSAPP_PROVIDER": "evolution",
            "WHATSAPP_API_URL": "",
            "WHATSAPP_API_KEY": "",
            "WHATSAPP_INSTANCE": "",
            "WHATSAPP_DEFAULT_COUNTRY_CODE": "55",
            "WHATSAPP_CUSTOM_PAYLOAD_TEMPLATE": "",
            "PRORATION_ENABLED": False,
            "PRORATION_MIN_CHARGE": 2.0,
            "PRORATION_MIN_DAYS": 3,
            "PRORATION_FREE_BELOW_MINIMUM": True,
            "REFERRAL_ENABLED": False,
            "REFERRAL_REWARD_TYPE": "days",
            "REFERRAL_REWARD_DAYS": 7,
            "REFERRAL_REWARD_CREDIT": 5.0,
            "REFERRAL_DEFAULT_INVITE_CODE": "",
            "REFERRAL_MAX_REWARDS_PER_USER": 0,
            "XP_LEVEL_TABLE": [],
            "XP_RESET_ENABLED": False,
            "XP_RESET_MONTHS": [],
            "XP_LAST_RESET_AT": "",
            "XP_LAST_RESET_PERIOD": "",
            "TERMINATION_MSG_BLOCKED_MANUAL": "O seu acesso ao servidor foi bloqueado pelo administrador.",
            "TERMINATION_MSG_BLOCKED_EXPIRED": "A sua subscrição para o utilizador {username} expirou. Por favor, renove para continuar.",
            "TERMINATION_MSG_BLOCKED_TRIAL_EXPIRED": "O seu período de teste para {username} terminou. Renove para continuar.",
            "TERMINATION_MSG_SCREEN_LIMIT": "{username}, você excedeu o seu limite de {limit} tela(s) simultânea(s).",
            "IMAGE_CACHE_CLEANUP_ENABLED": True,
            "IMAGE_CACHE_MAX_AGE_DAYS": 30,
            "IMAGE_CACHE_CLEANUP_TIME": "04:00",
            "SHORT_LINK_CLEANUP_ENABLED": True,
            "SHORT_LINK_MAX_AGE_DAYS": 30,
            "BACKUP_ENABLED": False,
            "BACKUP_TIME": "05:00",
            "BACKUP_MAX_COUNT": 7
        }
        save_app_config(default_config)
        return default_config
    else:
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Garante que a SECRET_KEY existe e é segura
            if secret_key_from_env:
                # A variável de ambiente tem sempre prioridade
                config['SECRET_KEY'] = secret_key_from_env
            elif 'SECRET_KEY' not in config or not config['SECRET_KEY']:
                # Se não houver chave no ficheiro, gera uma nova e guarda
                logger.warning("SECRET_KEY não encontrada no config.json. A gerar uma nova chave segura.")
                config['SECRET_KEY'] = secrets.token_hex(16)
                save_app_config(config)

            # 🛡️ MIGRAÇÃO DE CONFIGURAÇÃO: Preenche com valores padrão qualquer chave que
            # ainda não exista no config.json. Isto roda SEMPRE que um ficheiro já existente
            # é carregado (independentemente do estado da SECRET_KEY), para que atualizações
            # do sistema que introduzam novos campos não deixem instalações antigas com
            # configurações em falta.
            config_was_modified = False

            def _set_default(key, value):
                nonlocal config_was_modified
                if key not in config:
                    config[key] = value
                    config_was_modified = True

            _set_default("ADMIN_USER_ID", "")
            _set_default("INTERNAL_TRIGGER_KEY", secrets.token_hex(32))
            _set_default("APP_BASE_URL", "")
            _set_default("LOG_LEVEL", "INFO")
            _set_default("LOG_FILE", os.path.join(CONFIG_DIR, "app.log"))
            _set_default("LOG_MAX_BYTES", 1024 * 1024)
            _set_default("LOG_BACKUP_COUNT", 5)
            _set_default("LOG_DEDUP_SECONDS", 60)
            _set_default("STREAM_CHECK_INTERVAL_SECONDS", 15)
            _set_default("SCREEN_LIMIT_TERMINATION_STRATEGY", "oldest")
            _set_default("TERMINATION_MSG_BLOCKED_MANUAL", "O seu acesso ao servidor foi bloqueado pelo administrador.")
            _set_default("TERMINATION_MSG_BLOCKED_EXPIRED", "A sua subscrição para o utilizador {username} expirou. Por favor, renove para continuar.")
            _set_default("TERMINATION_MSG_BLOCKED_TRIAL_EXPIRED", "O seu período de teste para {username} terminou. Renove para continuar.")
            _set_default("TERMINATION_MSG_SCREEN_LIMIT", "{username}, você excedeu o seu limite de {limit} tela(s) simultânea(s).")
            _set_default("EXPIRATION_NOTIFICATION_TIME", "09:00")
            _set_default("BLOCK_REMOVAL_TIME", "02:00")
            _set_default("UNIVERSAL_EXPIRATION_ENABLED", False)
            _set_default("UNIVERSAL_EXPIRATION_TIME", "23:59")
            _set_default("WEBHOOK_URL", "")
            _set_default("WEBHOOK_AUTHORIZATION_HEADER", "")
            _set_default("WEBHOOK_ENABLED", False)
            _set_default("TELEGRAM_BOT_TOKEN", "")
            _set_default("TELEGRAM_CHAT_ID", "")
            _set_default("TELEGRAM_ENABLED", False)
            _set_default("DISCORD_ENABLED", False)
            _set_default("DISCORD_WEBHOOK_URL", "")
            _set_default("LAST_NOTIFICATION_CHECK", "1970-01-01T00:00:00")
            _set_default("EFI_ENABLED", False)
            _set_default("EFI_CLIENT_ID", "")
            _set_default("EFI_CLIENT_SECRET", "")
            _set_default("EFI_CERTIFICATE", "/app/certs/efisandbox.pem")
            _set_default("EFI_SANDBOX", True)
            _set_default("EFI_PIX_KEY", "")
            _set_default("RENEWAL_PRICE", "10.00")
            _set_default("MERCADOPAGO_ENABLED", False)
            _set_default("MERCADOPAGO_ACCESS_TOKEN", "")
            _set_default("MERCADOPAGO_WEBHOOK_SECRET", "")
            _set_default("MERCADOPAGO_MIN_AMOUNT", 1.0)
            _set_default("GATES2B_ENABLED", False)
            _set_default("GATES2B_AUTH_TOKEN", "")
            _set_default("GATES2B_MIN_AMOUNT", 3.0)

            # 🔄 MIGRAÇÃO BPIX -> GATES2B: o gateway mudou de marca e de domínio
            # (api.bpix.app -> api.gates2b.com). Quem já tinha a integração
            # configurada não deve perder as credenciais nem ter de as reintroduzir,
            # por isso copiamos os valores antigos uma única vez. As chaves antigas
            # são removidas a seguir para não ficarem a confundir no config.json.
            if "BPIX_AUTH_TOKEN" in config:
                if not config.get("GATES2B_AUTH_TOKEN"):
                    config["GATES2B_AUTH_TOKEN"] = config.get("BPIX_AUTH_TOKEN", "")
                    config["GATES2B_ENABLED"] = config.get("BPIX_ENABLED", False)
                    logger.info("Configuração da BPIX migrada automaticamente para a Gates2b.")
                config.pop("BPIX_AUTH_TOKEN", None)
                config.pop("BPIX_ENABLED", None)
                config_was_modified = True
            _set_default("OVERSEERR_ENABLED", False)
            _set_default("OVERSEERR_URL", "")
            _set_default("OVERSEERR_API_KEY", "")
            _set_default("CLEANUP_PENDING_PAYMENTS_ENABLED", True)
            _set_default("CLEANUP_PENDING_PAYMENTS_DAYS", 3)
            _set_default("CLEANUP_TIME", "03:00")
            _set_default("ENABLE_LINK_SHORTENER", True)
            _set_default("PAYMENT_LINK_GRACE_PERIOD_DAYS", 7)
            _set_default("ACHIEVEMENT_MOVIE_MARATHON_BRONZE", 5)
            _set_default("ACHIEVEMENT_MOVIE_MARATHON_SILVER", 10)
            _set_default("ACHIEVEMENT_MOVIE_MARATHON_GOLD", 20)
            _set_default("ACHIEVEMENT_SERIES_BINGER_BRONZE", 20)
            _set_default("ACHIEVEMENT_SERIES_BINGER_SILVER", 50)
            _set_default("ACHIEVEMENT_SERIES_BINGER_GOLD", 100)
            _set_default("ACHIEVEMENT_TIME_TRAVELER_BRONZE", 3)
            _set_default("ACHIEVEMENT_TIME_TRAVELER_SILVER", 5)
            _set_default("ACHIEVEMENT_TIME_TRAVELER_GOLD", 7)
            _set_default("ACHIEVEMENT_DIRECTOR_FAN_BRONZE", 3)
            _set_default("ACHIEVEMENT_DIRECTOR_FAN_SILVER", 5)
            _set_default("ACHIEVEMENT_DIRECTOR_FAN_GOLD", 7)
            _set_default("ACHIEVEMENT_NIGHT_OWL_BRONZE", 3)
            _set_default("ACHIEVEMENT_NIGHT_OWL_SILVER", 10)
            _set_default("ACHIEVEMENT_NIGHT_OWL_GOLD", 25)
            _set_default("ACHIEVEMENT_PIONEER_BRONZE", 1)
            _set_default("ACHIEVEMENT_PIONEER_SILVER", 5)
            _set_default("ACHIEVEMENT_PIONEER_GOLD", 15)
            _set_default("ACHIEVEMENT_PIONEER_WINDOW_HOURS", 48)
            _set_default("XP_PER_MINUTE_WATCHED", 1.0)
            _set_default("XP_BONUS_PER_COMPLETED_ITEM", 20)
            _set_default("XP_COMPLETION_THRESHOLD_PERCENT", 90)
            _set_default("RECOMMENDATIONS_ENABLED", True)
            _set_default("RECOMMENDATIONS_HISTORY_DAYS", 180)
            _set_default("RECOMMENDATIONS_MIN_PERCENT_WATCHED", 25)
            _set_default("RECOMMENDATIONS_MIN_CO_OCCURRENCE", 2)
            _set_default("RECOMMENDATIONS_MAX_SECTIONS", 4)
            _set_default("RECOMMENDATIONS_ITEMS_PER_SECTION", 8)
            _set_default("RECOMMENDATIONS_RESPECT_PRIVACY", True)
            _set_default("RECOMMENDATIONS_GENRE_LOOKUP_LIMIT", 40)
            # Tabela de níveis personalizável. Lista vazia = usa a tabela padrão do código.
            _set_default("WHATSAPP_ENABLED", False)
            _set_default("WHATSAPP_PROVIDER", "evolution")
            _set_default("WHATSAPP_API_URL", "")
            _set_default("WHATSAPP_API_KEY", "")
            _set_default("WHATSAPP_INSTANCE", "")
            _set_default("WHATSAPP_DEFAULT_COUNTRY_CODE", "55")
            _set_default("WHATSAPP_CUSTOM_PAYLOAD_TEMPLATE", "")
            _set_default("PRORATION_ENABLED", False)
            _set_default("PRORATION_MIN_CHARGE", 2.0)
            _set_default("PRORATION_MIN_DAYS", 3)
            _set_default("PRORATION_FREE_BELOW_MINIMUM", True)
            _set_default("REFERRAL_ENABLED", False)
            _set_default("REFERRAL_REWARD_TYPE", "days")
            _set_default("REFERRAL_REWARD_DAYS", 7)
            _set_default("REFERRAL_REWARD_CREDIT", 5.0)
            _set_default("REFERRAL_DEFAULT_INVITE_CODE", "")
            _set_default("REFERRAL_MAX_REWARDS_PER_USER", 0)
            _set_default("XP_LEVEL_TABLE", [])
            _set_default("XP_RESET_ENABLED", False)
            _set_default("XP_RESET_MONTHS", [])
            _set_default("XP_LAST_RESET_AT", "")
            _set_default("XP_LAST_RESET_PERIOD", "")
            _set_default("IMAGE_CACHE_CLEANUP_ENABLED", True)
            _set_default("IMAGE_CACHE_MAX_AGE_DAYS", 30)
            _set_default("IMAGE_CACHE_CLEANUP_TIME", "04:00")
            _set_default("SHORT_LINK_CLEANUP_ENABLED", True)
            _set_default("SHORT_LINK_MAX_AGE_DAYS", 30)
            _set_default("BACKUP_ENABLED", False)
            _set_default("BACKUP_TIME", "05:00")
            _set_default("BACKUP_MAX_COUNT", 7)

            # Adicionando as opções 5 e 6 no fallback default também
            default_screen_prices = {"1": "10.00", "2": "18.00", "3": "25.00", "4": "30.00", "5": "35.00", "6": "40.00"}
            if "SCREEN_PRICES" not in config:
                config["SCREEN_PRICES"] = default_screen_prices
                config_was_modified = True
            else:
                if "5" not in config["SCREEN_PRICES"]:
                    config["SCREEN_PRICES"]["5"] = "35.00"
                    config_was_modified = True
                if "6" not in config["SCREEN_PRICES"]:
                    config["SCREEN_PRICES"]["6"] = "40.00"
                    config_was_modified = True

            # 🔧 AUTO-CURA DOS TEMPLATES DE MENSAGEM: setdefault() só age em chaves AUSENTES.
            # Se uma atualização anterior deixou um template gravado como string vazia (""),
            # setdefault não o corrige. Aqui reescrevemos qualquer template de mensagem que
            # esteja em branco, já que um template vazio nunca é um estado intencional válido
            # (diferente de campos como WEBHOOK_URL, que podem ficar vazios legitimamente).
            # Importado aqui dentro (e não no topo) para evitar import circular:
            # 'notifier_manager' importa 'config'. Assim os textos padrão vivem
            # num único sítio, sem duplicação entre os dois ficheiros.
            from .services.notifier_manager import DEFAULT_TEMPLATES

            message_template_defaults = {
                "TELEGRAM_MEDIA_PENDING_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("TELEGRAM_MEDIA_PENDING_MESSAGE_TEMPLATE", ""),
                "WHATSAPP_MEDIA_PENDING_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("WHATSAPP_MEDIA_PENDING_MESSAGE_TEMPLATE", ""),
                "DISCORD_MEDIA_PENDING_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("DISCORD_MEDIA_PENDING_MESSAGE_TEMPLATE", ""),
                "TELEGRAM_MEDIA_APPROVED_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("TELEGRAM_MEDIA_APPROVED_MESSAGE_TEMPLATE", ""),
                "WHATSAPP_MEDIA_APPROVED_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("WHATSAPP_MEDIA_APPROVED_MESSAGE_TEMPLATE", ""),
                "DISCORD_MEDIA_APPROVED_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("DISCORD_MEDIA_APPROVED_MESSAGE_TEMPLATE", ""),
                "TELEGRAM_MEDIA_AVAILABLE_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("TELEGRAM_MEDIA_AVAILABLE_MESSAGE_TEMPLATE", ""),
                "WHATSAPP_MEDIA_AVAILABLE_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("WHATSAPP_MEDIA_AVAILABLE_MESSAGE_TEMPLATE", ""),
                "DISCORD_MEDIA_AVAILABLE_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("DISCORD_MEDIA_AVAILABLE_MESSAGE_TEMPLATE", ""),
                "TELEGRAM_MEDIA_DECLINED_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("TELEGRAM_MEDIA_DECLINED_MESSAGE_TEMPLATE", ""),
                "WHATSAPP_MEDIA_DECLINED_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("WHATSAPP_MEDIA_DECLINED_MESSAGE_TEMPLATE", ""),
                "DISCORD_MEDIA_DECLINED_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("DISCORD_MEDIA_DECLINED_MESSAGE_TEMPLATE", ""),
                "TELEGRAM_MEDIA_FAILED_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("TELEGRAM_MEDIA_FAILED_MESSAGE_TEMPLATE", ""),
                "WHATSAPP_MEDIA_FAILED_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("WHATSAPP_MEDIA_FAILED_MESSAGE_TEMPLATE", ""),
                "DISCORD_MEDIA_FAILED_MESSAGE_TEMPLATE": DEFAULT_TEMPLATES.get("DISCORD_MEDIA_FAILED_MESSAGE_TEMPLATE", ""),
                "TELEGRAM_MEDIA_REQUEST_MESSAGE_TEMPLATE": "🍿 *Novo Conteúdo Solicitado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n🚀 *Acesse o pedido:*\n{media_url}",
                "WHATSAPP_MEDIA_REQUEST_MESSAGE_TEMPLATE": "🍿 *Novo Conteúdo Solicitado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n🚀 *Acesse o pedido:*\n{media_url}",
                "DISCORD_MEDIA_REQUEST_MESSAGE_TEMPLATE": '{"embeds": [{"title": "🍿 Novo Conteúdo Solicitado", "description": "**{title}**\\n\\n📝 {overview}", "color": 10181046, "fields": [{"name": "👤 Usuário", "value": "{username}", "inline": true}, {"name": "📊 Status", "value": "{status}", "inline": true}], "url": "{media_url}"}]}',
                "WHATSAPP_EXPIRATION_MESSAGE_TEMPLATE": "Olá {name}, {greeting}!\n\nO seu acesso vence em {days} dia(s), no dia {date}.\nPlano: {plan_name}\nValor: {price}\n\nRenove aqui para não perder o acesso:\n{payment_link}",
                "WHATSAPP_RENEWAL_MESSAGE_TEMPLATE": "✅ Renovação confirmada!\n\nOlá {name}, a sua subscrição foi renovada com sucesso.\nNovo vencimento: {new_date}\n\nBom entretenimento!",
                "WHATSAPP_REACTIVATION_MESSAGE_TEMPLATE": "✅ Conta reativada!\n\nOlá {name}, a sua conta foi reativada.\nNovo vencimento: {new_date}\n\nAceite o convite para voltar a aceder:\n{invite_link}",
                "WHATSAPP_TRIAL_END_MESSAGE_TEMPLATE": "⌛ O seu período de teste terminou\n\nOlá {name}, esperamos que tenha gostado!\nPara continuar com acesso, faça a sua assinatura aqui:\n{payment_link}",
                "WHATSAPP_BULK_MESSAGE_TEMPLATE": "📢 Aviso do servidor\n\nOlá {name},\n\n{message}",
                "WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}"}',
                "WEBHOOK_RENEWAL_MESSAGE_TEMPLATE": '{"content": "✅ A subscrição de {username} foi renovada. Novo vencimento: {new_date}."}',
                "WEBHOOK_REACTIVATION_MESSAGE_TEMPLATE": '{"content": "✅ A subscrição de {username} foi reativada. Novo vencimento: {new_date}. Acesse o servidor: {invite_link}"}',
                "WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "O período de teste para {username} terminou. Para renovar, acesse: {payment_link}"}',
                "WEBHOOK_BULK_MESSAGE_TEMPLATE": '{"phone": "{phone_number}@s.whatsapp.net", "message": "{message}"}',
                "TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE": "Olá {name}, {greeting}!\n\nEste é um lembrete de que sua fatura está com o vencimento próximo.\nVencimento: *{date}*\nValor: *{price}*\nPlano: *{plan_name}*\nAcesso: `{email}`\n\nNa data do vencimento o sistema poderá bloquear o acesso. Para evitar a interrupção, realize o pagamento clicando no botão abaixo:",
                "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "✅ *Renovação Confirmada*\n\nOlá {name}!\nA sua subscrição foi renovada com sucesso.\nNovo vencimento: *{new_date}*.",
                "TELEGRAM_REACTIVATION_MESSAGE_TEMPLATE": "✅ *Conta Reativada*\n\nOlá {name}!\nA sua subscrição foi renovada e a sua conta reativada com sucesso.\nNovo vencimento: *{new_date}*\n\nPara acessar o servidor, aceite o convite no link abaixo:\n{invite_link}",
                "TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE": "⌛ *Fim do Período de Teste*\n\n{name}, o seu período de teste terminou.\nPara manter o seu acesso, realize a renovação no botão abaixo:",
                "TELEGRAM_BULK_MESSAGE_TEMPLATE": "📢 *Aviso do Servidor*\n\nOlá {name},\n\n{message}",
                "DISCORD_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso de Vencimento", "description": "Olá **{username}**! 👋\\n\\nO seu acesso ao Plex está prestes a expirar em **{days} dia(s)**, no dia **{date}**.\\n\\nPara evitar a interrupção do serviço, por favor, [clique aqui para renovar]({payment_link}).", "color": 16776960}]}',
                "DISCORD_RENEWAL_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Renovação Confirmada!", "description": "Olá **{username}**! ✅\\n\\nA sua assinatura foi renovada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\nObrigado e aproveite!", "color": 65280}]}',
                "DISCORD_REACTIVATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Conta Reativada!", "description": "Olá **{username}**! ✅\\n\\nA sua assinatura foi reativada com sucesso. O seu novo vencimento é em **{new_date}**.\\n\\n[Clique aqui para aceitar o convite do Plex]({invite_link})", "color": 65280}]}',
                "DISCORD_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Período de Teste Terminou", "description": "Olá **{username}**! ⌛\\n\\nO seu período de teste gratuito terminou. Para continuar a ter acesso, por favor, [clique aqui para renovar]({payment_link}).", "color": 16711680}]}',
                "DISCORD_BULK_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso do Servidor", "description": "{message}", "color": 3447003}]}',
            }
            for key, default_value in message_template_defaults.items():
                current_value = config.get(key)
                if not current_value or not str(current_value).strip():
                    if key in config and config[key] != default_value:
                        logger.warning(f"Config '{key}' estava em branco. Restaurando o template padrão.")
                    config[key] = default_value
                    config_was_modified = True

            # Persiste no disco qualquer valor que tenha sido preenchido/corrigido nesta migração,
            # para que a correção não precise ser refeita a cada arranque.
            if config_was_modified:
                save_app_config(config)

            log_file_path = config.get("LOG_FILE")
            if log_file_path and not os.path.isabs(log_file_path):
                config["LOG_FILE"] = os.path.join(CONFIG_DIR, os.path.basename(log_file_path))
                logger.debug(f"Caminho do ficheiro de log relativo detetado. Convertido para: {config['LOG_FILE']}")

            return config
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Erro ao carregar o ficheiro de configuração: {e}")
            # Retorna uma configuração mínima de emergência
            return {"SECRET_KEY": secrets.token_hex(16)}

def save_app_config(new_config):
    """Salva a nova configuração no config.json."""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4)
        return True
    except IOError as e:
        logger.error(f"Não foi possível salvar a configuração em {CONFIG_FILE}: {e}")
        return False

def is_configured():
    """Verifica se a aplicação já foi configurada."""
    config = load_or_create_config()
    return config.get("IS_CONFIGURED", False)