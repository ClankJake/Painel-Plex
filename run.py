from gevent import monkey
monkey.patch_all()

import logging
import subprocess
import sys
import os
import platform

# --- CONFIGURAÇÃO DE LIMPEZA DE LOGS ---
# Define o nível para WARNING para evitar que requisições HTTP (INFO) poluam o console
logging.getLogger('geventwebsocket.handler').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
# ---------------------------------------

# NOVO: Cria o diretório de cache de imagens antes de tudo, se necessário
from app.config import CONFIG_DIR, load_or_create_config
from app import create_app, extensions

IMAGE_CACHE_DIR = os.path.join(CONFIG_DIR, 'cache', 'images')
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

# Carrega a configuração antes de criar a aplicação
config = load_or_create_config()
app = create_app()

def run_db_upgrade():
    """Executa o comando 'flask db upgrade' para garantir que a base de dados está atualizada."""
    logging.info("A verificar e aplicar migrações da base de dados...")
    try:
        # Usamos 'sys.executable -m flask' para garantir que estamos a usar o mesmo ambiente python
        # e evitar problemas de PATH.
        
        # Cria uma cópia do ambiente atual e define a codificação de I/O do Python para UTF-8
        # para o subprocesso. Isto resolve erros de 'UnicodeDecodeError' no Windows.
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'

        result = subprocess.run(
            [sys.executable, "-m", "flask", "db", "upgrade"],
            capture_output=True,
            text=True,
            check=True,
            encoding='utf-8', # Espera que a saída seja UTF-8
            env=env # Passa o ambiente modificado para o subprocesso
        )
        logging.info("Migrações da base de dados aplicadas com sucesso.")
        # Se houver alguma saída do comando (ex: "Already up to date."), ela será logada.
        if result.stdout:
            logging.info(f"Saída do Flask DB Upgrade:\n{result.stdout}")
        return True
    except FileNotFoundError:
        logging.error("Erro: O comando 'flask' não foi encontrado. Certifique-se de que o Flask está instalado e no seu PATH.")
        return False
    except subprocess.CalledProcessError as e:
        # Este bloco é executado se o comando 'flask db upgrade' retornar um erro.
        logging.error("Ocorreu um erro ao executar 'flask db upgrade'.")
        logging.error(f"Código de Saída: {e.returncode}")
        logging.error(f"Saída (stdout):\n{e.stdout}")
        logging.error(f"Erro (stderr):\n{e.stderr}")
        return False
    except Exception as e:
        logging.error(f"Um erro inesperado ocorreu durante a migração da base de dados: {e}")
        return False

if __name__ == '__main__':
    # Executa a migração da base de dados antes de iniciar o servidor.
    # A aplicação só irá iniciar se a migração for bem-sucedida.
    if run_db_upgrade():
        # Obtém o host e a porta do ficheiro de configuração
        host = config.get('APP_HOST', '0.0.0.0')
        port = int(config.get('APP_PORT', 5000))

        logging.info(f"A iniciar o servidor em http://{host}:{port}")
        
        # Usa o servidor de desenvolvimento padrão no Windows para evitar o conflito com eventlet
        if platform.system() == "Windows":
            logging.warning("A executar em modo de desenvolvimento no Windows. O monkey-patch do eventlet foi ignorado.")
            extensions.socketio.run(app, host=host, port=port)
        else:
            # Para outros sistemas (Linux, etc.), continua a usar a configuração de produção com Gunicorn
            logging.info("A executar em modo de produção com Gunicorn e Gevent.")
            
            # log_output=False instrui o servidor WSGI a não logar cada requisição no stdout
            extensions.socketio.run(app, host=host, port=port, log_output=False)

    else:
        logging.critical("A aplicação não será iniciada devido a uma falha na migração da base de dados.")
