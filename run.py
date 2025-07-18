import logging
import subprocess
import sys
import os
from waitress import serve
from app import create_app
from app.config import load_or_create_config

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
        port = config.get('APP_PORT', 5000)

        logging.info(f"A iniciar o servidor de produção Waitress em http://{host}:{port}")
        
        # Executa a aplicação usando o Waitress
        serve(app, host=host, port=port)
    else:
        logging.critical("A aplicação não será iniciada devido a uma falha na migração da base de dados.")
        # Opcional: Adicionar um input para manter a janela do console aberta e ver o erro.
        # input("Pressione Enter para sair...")
