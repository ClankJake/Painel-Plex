# Dockerfile para a aplicação Painel Plex

# --- Estágio 1: Build do Frontend ---
# Usar a imagem base 'bookworm' que é uma versão mais recente do Debian
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /build

# Copia os ficheiros de definição de dependências e configuração do frontend
COPY package*.json ./
COPY tailwind.config.js .

# Instala as dependências de frontend
RUN npm install

# Copia o código-fonte da aplicação que contém as classes do Tailwind
COPY app ./app

# Executa o build do CSS, colocando o resultado no diretório 'dist' para consistência
RUN npm run build:css


# --- Estágio 2: Aplicação Python ---
# Começamos com uma imagem Python leve e oficial.
FROM python:3.12-slim-bullseye

# Set default environment variables for user/group IDs
ENV PUID=1000
ENV PGID=1000

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

# Copia os assets construídos e as dependências do estágio de frontend para o diretório final
COPY --from=frontend-builder /build/app/static/dist/output.css ./app/static/dist/output.css
COPY --from=frontend-builder /build/node_modules/chart.js/dist/chart.umd.js ./app/static/dist/chart.umd.js
COPY --from=frontend-builder /build/node_modules/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js ./app/static/dist/chartjs-adapter-date-fns.bundle.min.js
COPY --from=frontend-builder /build/node_modules/socket.io-client/dist/socket.io.min.js ./app/static/dist/socket.io.min.js


# Expor a Porta: Informa ao Docker que a aplicação irá escutar na porta 5000.
EXPOSE 5000

# Comando de Execução: O comando que será executado quando o contentor iniciar.
CMD ["python", "run.py"]
