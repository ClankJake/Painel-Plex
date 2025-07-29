# Dockerfile para a aplicação Painel Plex

# 1. Imagem Base: Começamos com uma imagem Python leve e oficial.
FROM python:3.12.11-slim-buster

# 2. Instalar Git: Necessário para clonar o repositório do GitHub.
RUN apt-get update && apt-get install -y git --no-install-recommends && rm -rf /var/lib/apt/lists/*

# 3. Diretório de Trabalho: Define o diretório onde a aplicação irá correr dentro do contentor.
WORKDIR /app

# 4. Variáveis de Ambiente: Otimizações para Python em contentores.
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 5. Clonar o Repositório do GitHub:
# A URL do repositório será passada como um argumento durante a construção.
# Isto torna o Dockerfile mais flexível.
ARG GITHUB_REPO_URL
RUN git clone ${GITHUB_REPO_URL} .

# 6. Instalação de Dependências:
# O ficheiro requirements.txt agora virá do repositório clonado.
RUN pip install --no-cache-dir -r requirements.txt

# 7. Expor a Porta: Informa ao Docker que a aplicação irá escutar na porta 5000.
EXPOSE 5000

# 8. Comando de Execução: O comando que será executado quando o contentor iniciar.
CMD ["python", "run.py"]
