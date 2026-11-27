import os
import logging
from logging.handlers import RotatingFileHandler

from .utils.log_formatting import CompactFormatter, RepeatSuppressFilter

logger = logging.getLogger(__name__)

LOG_LEVELS = {
    'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING,
    'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL,
}


def apply_log_level(app, log_level):
    level = LOG_LEVELS.get(log_level, logging.INFO)
    logging.getLogger().setLevel(level)
    app.logger.setLevel(level)
    handler = app.extensions.get('log_file_handler')
    if handler is None:
        return

    handler.setLevel(level)
    handler.setFormatter(CompactFormatter(max_frames=12 if level == logging.DEBUG else 4))
    for existing in handler.filters[:]:
        if isinstance(existing, RepeatSuppressFilter):
            handler.removeFilter(existing)
    window = app.config.get('LOG_DEDUP_SECONDS', 60)
    if window and level != logging.DEBUG:
        handler.addFilter(RepeatSuppressFilter(window_seconds=window))


def setup_logging(app, log_level='INFO'):
    log_file = app.config.get('LOG_FILE')

    if not log_file:
        # Fallback crítico se a config falhar antes deste ponto
        print("CRÍTICO: LOG_FILE não definido. Logs serão apenas no console.")
        logging.basicConfig(level=LOG_LEVELS.get(log_level, logging.INFO))
        apply_log_level(app, log_level)
        return

    log_dir = os.path.dirname(log_file)
    if log_dir: 
        os.makedirs(log_dir, exist_ok=True)
    
    max_bytes = app.config.get('LOG_MAX_BYTES', 1024 * 1024) # 1MB padrão
    backup_count = app.config.get('LOG_BACKUP_COUNT', 5)

    # Handler de Arquivo Rotativo
    file_handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    app.extensions['log_file_handler'] = file_handler

    # Configura o logger raiz para capturar logs de todas as bibliotecas
    root_logger = logging.getLogger()
    
    # Remove handlers antigos para evitar duplicação em reloads
    for handler in root_logger.handlers[:]:
        handler.close()
        root_logger.removeHandler(handler)
        
    root_logger.addHandler(file_handler)
    
    # Redireciona o logger da app Flask para usar os handlers raiz
    app.logger.handlers = root_logger.handlers
    apply_log_level(app, log_level)
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
