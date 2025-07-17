import os
import json
import secrets
import logging

logger = logging.getLogger(__name__)

# --- Constantes ---
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config.json')

def load_or_create_config():
    """
    Carrega a configuração do config.json ou cria um ficheiro padrão se não existir.
    A SECRET_KEY é tratada com prioridade para segurança.
    """
    # Prioridade 1: Tenta carregar a chave a partir de uma variável de ambiente.
    # Isto é ideal para ambientes de produção (ex: Docker, Heroku).
    secret_key_from_env = os.environ.get('SECRET_KEY')

    if not os.path.exists(CONFIG_FILE):
        logger.info(f"O ficheiro de configuração '{CONFIG_FILE}' não foi encontrado. A criar um novo.")
        default_config = {
            "IS_CONFIGURED": False,
            # Se a variável de ambiente existir, usa-a; senão, gera uma nova chave segura.
            "SECRET_KEY": secret_key_from_env or secrets.token_hex(16),
            "INTERNAL_TRIGGER_KEY": secrets.token_hex(32),
            "APP_TITLE": "Painel Plex",
            "APP_HOST": "0.0.0.0",
            "APP_PORT": 5000,
            "APP_BASE_URL": "",
            "LOG_LEVEL": "INFO",
            "ADMIN_USER": "",
            "PLEX_URL": "",
            "PLEX_TOKEN": "",
            "TAUTULLI_URL": "",
            "TAUTULLI_API_KEY": "",
            "BLOCKING_NOTIFIER_ID": 0,
            "SCREEN_LIMIT_NOTIFIER_ID": 0,
            "TRIAL_BLOCK_NOTIFIER_ID": 0,
            "DAYS_TO_REMOVE_BLOCKED_USER": 0,
            "EXPIRATION_NOTIFICATION_TIME": "09:00",
            "BLOCK_REMOVAL_TIME": "02:00",
            "WEBHOOK_URL": "",
            "WEBHOOK_AUTHORIZATION_HEADER": "",
            "WEBHOOK_ENABLED": False,
            "WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE": "{\"content\": \"Atenção: O acesso de {username} expira em {days} dias.\"}",
            "WEBHOOK_RENEWAL_MESSAGE_TEMPLATE": "{\"content\": \"✅ A subscrição de {username} foi renovada. Novo vencimento: {new_date}.\"}",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "", 
            "TELEGRAM_ENABLED": False,
            "TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE": "Atenção: O acesso de {username} expira em {days} dias.",
            "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "✅ Olá {username}! A sua subscrição foi renovada com sucesso. O seu novo vencimento é em {new_date}.",
            "TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE": "Seu período de teste para {username} terminou. Para continuar com o acesso, renove sua assinatura.",
            "WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE": "{\"content\": \"O período de teste para {username} terminou.\"}",
            "DAYS_TO_NOTIFY_EXPIRATION": 2,
            "LOG_FILE": "app.log",
            "LOG_MAX_BYTES": 1024 * 1024, # 1 MB
            "LOG_BACKUP_COUNT": 5,
            "LAST_NOTIFICATION_CHECK": "1970-01-01T00:00:00",
            "EFI_ENABLED": False,
            "EFI_CLIENT_ID": "",
            "EFI_CLIENT_SECRET": "",
            "EFI_CERTIFICATE": "./certs/efisandbox.pem", # Caminho para o seu certificado .pem
            "EFI_SANDBOX": True,
            "EFI_PIX_KEY": "", # A sua chave PIX
            "MERCADOPAGO_ENABLED": False,
            "MERCADOPAGO_ACCESS_TOKEN": "",
            "RENEWAL_PRICE": "10.00",
            "SCREEN_PRICES": {
                "1": "10.00",
                "2": "18.00",
                "3": "25.00",
                "4": "30.00"
            },
            "OVERSEERR_ENABLED": False,
            "OVERSEERR_URL": "",
            "OVERSEERR_API_KEY": ""
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
                config.setdefault("INTERNAL_TRIGGER_KEY", secrets.token_hex(32))
                config.setdefault("APP_HOST", "0.0.0.0")
                config.setdefault("APP_PORT", 5000)
                config.setdefault("APP_BASE_URL", "")
                config.setdefault("LOG_LEVEL", "INFO")
                config.setdefault("LOG_FILE", "app.log")
                config.setdefault("LOG_MAX_BYTES", 1024 * 1024)
                config.setdefault("LOG_BACKUP_COUNT", 5)
                config.setdefault("TRIAL_BLOCK_NOTIFIER_ID", 0)
                config.setdefault("EXPIRATION_NOTIFICATION_TIME", "09:00")
                config.setdefault("BLOCK_REMOVAL_TIME", "02:00")
                config.setdefault("WEBHOOK_AUTHORIZATION_HEADER", "")
                config.setdefault("WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE", "{\"content\": \"Atenção: O acesso de {username} expira em {days} dias.\"}")
                config.setdefault("WEBHOOK_RENEWAL_MESSAGE_TEMPLATE", "{\"content\": \"✅ A subscrição de {username} foi renovada. Novo vencimento: {new_date}.\"}")
                config.setdefault("TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE", "Atenção: O acesso de {username} expira em {days} dias.")
                config.setdefault("TELEGRAM_RENEWAL_MESSAGE_TEMPLATE", "✅ Olá {username}! A sua subscrição foi renovada com sucesso. O seu novo vencimento é em {new_date}.")
                config.setdefault("TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE", "Seu período de teste para {username} terminou. Para continuar com o acesso, renove sua assinatura.")
                config.setdefault("WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE", "{\"content\": \"O período de teste para {username} terminou.\"}")
                config.setdefault("LAST_NOTIFICATION_CHECK", "1970-01-01T00:00:00")
                config.setdefault("EFI_ENABLED", False)
                config.setdefault("EFI_CLIENT_ID", "")
                config.setdefault("EFI_CLIENT_SECRET", "")
                config.setdefault("EFI_CERTIFICATE", "./certs/efisandbox.pem")
                config.setdefault("EFI_SANDBOX", True)
                config.setdefault("EFI_PIX_KEY", "")
                config.setdefault("RENEWAL_PRICE", "10.00")
                config.setdefault("SCREEN_PRICES", {"1": "10.00", "2": "18.00", "3": "25.00", "4": "30.00"})
                config.setdefault("MERCADOPAGO_ENABLED", False)
                config.setdefault("MERCADOPAGO_ACCESS_TOKEN", "")
                config.setdefault("OVERSEERR_ENABLED", False)
                config.setdefault("OVERSEERR_URL", "")
                config.setdefault("OVERSEERR_API_KEY", "")
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