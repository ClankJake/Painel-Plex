# Dockerfile para a aplicação Painel Plex

# 1. Imagem Base: Começamos com uma imagem Python leve e oficial.
FROM python:3.12.11-slim-buster

# 2. Diretório de Trabalho: Define o diretório onde a aplicação irá correr dentro do contentor.
WORKDIR /app

# 3. Variáveis de Ambiente: Otimizações para Python em contentores.
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 4. Instalação de Dependências:
# Copiamos primeiro o ficheiro de requisitos para aproveitar o cache de camadas do Docker.
# Se o requirements.txt não mudar, o Docker não reinstalará tudo a cada build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copiar a Aplicação: Copia todos os ficheiros do projeto para o diretório de trabalho no contentor.
# Os ficheiros listados em .dockerignore serão ignorados.
COPY . .

# 6. Expor a Porta: Informa ao Docker que a aplicação irá escutar na porta 5000.
EXPOSE 5000

# 7. Comando de Execução: O comando que será executado quando o contentor iniciar.
# Inicia a aplicação através do run.py, que já está configurado para usar eventlet.
CMD ["python", "run.py"]
