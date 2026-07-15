import os
import logging
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)

def setup_logging(app, log_level='INFO'):
    """
    Configura o sistema de logging da aplicação.
    Movemos esta lógica para fora do __init__.py para manter a fábrica limpa.
    """
    log_level_map = {
        'DEBUG': logging.DEBUG, 'INFO': logging.INFO,
        'WARNING': logging.WARNING, 'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL,
    }
    level = log_level_map.get(log_level, logging.INFO)
    
    log_file = app.config.get('LOG_FILE')
    
    if not log_file:
        # Fallback crítico se a config falhar antes deste ponto
        print("CRÍTICO: LOG_FILE não definido. Logs serão apenas no console.")
        logging.basicConfig(level=level)
        return

    log_dir = os.path.dirname(log_file)
    if log_dir: 
        os.makedirs(log_dir, exist_ok=True)
    
    max_bytes = app.config.get('LOG_MAX_BYTES', 1024 * 1024) # 1MB padrão
    backup_count = app.config.get('LOG_BACKUP_COUNT', 5)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Handler de Arquivo Rotativo
    file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    # Configura o logger raiz para capturar logs de todas as bibliotecas
    root_logger = logging.getLogger()
    
    # Remove handlers antigos para evitar duplicação em reloads
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)
        
    root_logger.addHandler(file_handler)
    root_logger.setLevel(level)
    
    # Redireciona o logger da app Flask para usar os handlers raiz
    app.logger.handlers = root_logger.handlers
    app.logger.setLevel(level)
    app.logger.propagate = False

    # Reduz o ruído de bibliotecas barulhentas
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)

    # --- ADIÇÕES PARA SILENCIAR AVISOS DE REDE/PLEX ---
    # Isso vai calar os avisos de "Retrying" e "Connection pool is full"
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)
    logging.getLogger('urllib3.util.retry').setLevel(logging.ERROR)
    
    # Isso cala as desconexões espontâneas do websocket do plex
    logging.getLogger('plexapi').setLevel(logging.CRITICAL)
    logging.getLogger('websocket').setLevel(logging.CRITICAL)

    logger.info(f"Sistema de logging inicializado. Nível: {log_level}, Arquivo: {log_file}")
