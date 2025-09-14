import logging
import os
from functools import wraps
from filelock import Timeout, FileLock
from .config import CONFIG_DIR

logger = logging.getLogger(__name__)
LOCK_DIR = os.path.join(CONFIG_DIR, 'locks')
os.makedirs(LOCK_DIR, exist_ok=True)

def single_instance_job(lock_name):
    """
    Decorator para garantir que apenas uma instância de uma tarefa agendada
    seja executada de cada vez em múltiplos workers/processos.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            lock_path = os.path.join(LOCK_DIR, f"{lock_name}.lock")
            # Timeout de 1 segundo: se não conseguir o lock instantaneamente, desiste.
            lock = FileLock(lock_path, timeout=1)

            try:
                # Tenta adquirir o lock. Se outro processo já o tiver,
                # a excepção Timeout será lançada e a tarefa será ignorada.
                with lock:
                    logger.debug(f"Lock adquirido para a tarefa '{lock_name}'. A executar a tarefa.")
                    return func(*args, **kwargs)
            except Timeout:
                logger.debug(f"Não foi possível adquirir o lock para a tarefa '{lock_name}'. Outra instância provavelmente está a ser executada. A ignorar.")
        return wrapper
    return decorator
