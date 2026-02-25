import logging
import os
import re
from functools import wraps
from typing import Callable, Any

from filelock import Timeout, FileLock
from .config import CONFIG_DIR

logger = logging.getLogger(__name__)

# Garante que o diretório de locks existe no arranque da aplicação
LOCK_DIR = os.path.join(CONFIG_DIR, 'locks')
os.makedirs(LOCK_DIR, exist_ok=True)

def _sanitize_filename(name: str) -> str:
    """
    Remove caracteres inválidos para nomes de ficheiros.
    Evita vulnerabilidades de Path Traversal ou erros de I/O.
    """
    return re.sub(r'[^A-Za-z0-9_\-]', '_', name)

def single_instance_job(lock_name: str, timeout: int = 0):
    """
    Decorator para garantir que apenas uma instância de uma tarefa agendada
    seja executada de cada vez em múltiplos workers/processos.

    :param lock_name: Nome identificador da tarefa.
    :param timeout: Segundos a aguardar pelo lock. '0' falha imediatamente se ocupado (Ideal para Cron Jobs).
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            safe_name = _sanitize_filename(lock_name)
            lock_path = os.path.join(LOCK_DIR, f"{safe_name}.lock")
            
            lock = FileLock(lock_path, timeout=timeout)

            try:
                # Tenta adquirir o lock de forma exclusiva entre todos os processos
                with lock:
                    logger.debug(f"Lock '{safe_name}' adquirido com sucesso. A iniciar execução.")
                    
                    try:
                        result = func(*args, **kwargs)
                        logger.debug(f"Tarefa '{safe_name}' concluída. O lock será libertado.")
                        return result
                    except Exception as e:
                        # Log detalhado caso a tarefa rebente ENQUANTO detém o lock
                        logger.error(f"Erro crítico durante a execução da tarefa '{safe_name}': {e}", exc_info=True)
                        raise  # Re-lança o erro para o Scheduler lidar com ele
                        
            except Timeout:
                # Ocorre instantaneamente (timeout=0) se outro worker já estiver a correr a tarefa
                logger.debug(f"Lock '{safe_name}' ocupado. A tarefa já está a correr noutro processo. A ignorar.")
                return None
                
        return wrapper
    return decorator
