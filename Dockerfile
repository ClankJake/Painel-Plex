# Dockerfile para a aplicação Painel Plex

# --- Estágio 1: Build do Frontend ---
FROM node:20-slim as frontend-builder

WORKDIR /frontend

# Copia os ficheiros de definição de dependências
COPY package*.json ./

# Limpa o cache do npm para evitar erros de integridade (EINTEGRITY) em ambientes de build
RUN npm cache clean --force

# Instala as dependências de frontend. 'npm install' é mais resiliente em ambientes de build.
RUN npm install

# Copia os ficheiros de configuração e o código-fonte do frontend necessários para o build
COPY tailwind.config.js .
COPY app/static/css/input.css ./app/static/css/input.css
COPY app/templates ./app/templates
COPY app/static/js ./app/static/js

# Executa o build do CSS
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

# Copia os assets construídos do estágio de frontend
COPY --from=frontend-builder /frontend/app/static/css/output.css ./app/static/css/output.css
COPY --from=frontend-builder /frontend/node_modules/chart.js/dist/chart.umd.js ./app/static/dist/chart.umd.js
COPY --from=frontend-builder /frontend/node_modules/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js ./app/static/dist/chartjs-adapter-date-fns.bundle.min.js
COPY --from=frontend-builder /frontend/node_modules/socket.io-client/dist/socket.io.min.js ./app/static/dist/socket.io.min.js


# Expor a Porta: Informa ao Docker que a aplicação irá escutar na porta 5000.
EXPOSE 5000

# Comando de Execução: O comando que será executado quando o contentor iniciar.
CMD ["python", "run.py"]
