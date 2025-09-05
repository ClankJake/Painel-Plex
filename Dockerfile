# Dockerfile para a aplicação Painel Plex

# --- Estágio 1: Build do Frontend ---
# Usar a imagem base 'bookworm' que é uma versão mais recente do Debian
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /build

# Copia os ficheiros de definição de dependências e configuração do frontend
COPY package.json ./
COPY tailwind.config.js .

# Instala as dependências de frontend
RUN npm install

# Copia o código-fonte da aplicação que contém as classes do Tailwind
COPY app ./app

# Executa o build do CSS, colocando o resultado no diretório 'dist'
RUN npm run build:css


# --- Estágio 2: Aplicação Python ---
# Começamos com uma imagem Python leve e oficial.
FROM python:3.12-slim-bullseye

# Set default environment variables for user/group IDs
ENV PUID=1000
ENV PGID=1000

# Define a porta padrão da aplicação como uma variável de ambiente.
# Isto permite que ela seja substituída ao iniciar o contentor.
ENV APP_PORT=5000

# Define o diretório onde a aplicação irá correr dentro do contentor.
WORKDIR /app

# Otimizações para Python em contentores.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalação de Dependências Python:
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia a Aplicação Python (backend) de forma explícita
COPY app ./app
COPY migrations ./migrations
COPY run.py .
COPY babel.cfg .

# Copia os assets construídos e as dependências do estágio de frontend para o diretório final correto
COPY --from=frontend-builder /build/app/static/dist/output.css ./app/static/dist/output.css
COPY --from=frontend-builder /build/node_modules/chart.js/dist/chart.umd.js ./app/static/dist/chart.umd.js
COPY --from=frontend-builder /build/node_modules/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js ./app/static/dist/chartjs-adapter-date-fns.bundle.min.js
COPY --from=frontend-builder /build/node_modules/socket.io-client/dist/socket.io.min.js ./app/static/dist/socket.io.min.js


# Expor a Porta: Informa ao Docker que a aplicação irá escutar na porta definida pela variável de ambiente.
EXPOSE ${APP_PORT}

# Comando de Execução: Executa a migração da base de dados e depois inicia o Gunicorn.
# O Gunicorn agora usa a variável de ambiente $APP_PORT para definir a porta de escuta.
CMD ["sh", "-c", "flask db upgrade && gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:${APP_PORT} run:app"]
