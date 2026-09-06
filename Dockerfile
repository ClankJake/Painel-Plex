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
# 'bookworm' (Debian 12) e não 'bullseye' (Debian 11): o LTS do bullseye terminou
# em 31/08/2026 e ele deixou de receber atualizações de segurança.
FROM python:3.12-slim-bookworm

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

# Instala as dependências de sistema necessárias.
#
# CORREÇÃO SSL (histórica): o Debian 10/11 impunha
# 'CipherString = DEFAULT@SECLEVEL=2' no /etc/ssl/openssl.cnf, exigindo 112 bits
# e recusando certificados assinados com SHA-1 ou chaves RSA/DH abaixo de 2048
# bits. Isso podia derrubar o handshake com a API da Efí ('ca md too weak',
# 'dh key too small'), daí o ajuste para SECLEVEL=1.
#
# No bookworm essa imposição NÃO existe: o build 270 provou que não havia nada
# para o 'sed' alterar. O bloco abaixo mantém-se por segurança — se uma imagem
# base futura voltar a impor o nível 2, ele baixa-o — mas já não assume que a
# diretiva está lá.
#
# ⚠️ Quando o ajuste se aplica, ele é GLOBAL ao processo: afeta todas as ligações
# de saída do painel (Plex, Tautulli, Overseerr, Telegram…), não só a Efí. O
# 'MinProtocol' da mesma secção nunca é tocado.
#
# O 'grep' de diagnóstico escreve o estado real no log do build, para ninguém
# ter de adivinhar outra vez; a verificação final falha o build se o SECLEVEL=2
# sobreviver ao 'sed' (por exemplo, escrito noutro formato).
RUN apt-get update && apt-get install -y \
    libjpeg-dev \
    zlib1g-dev \
    libwebp-dev \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/* && \
    if [ -f /etc/ssl/openssl.cnf ]; then \
        echo "--- /etc/ssl/openssl.cnf ---"; \
        grep -nE 'SECLEVEL|CipherString|MinProtocol' /etc/ssl/openssl.cnf \
            || echo "(sem diretivas SECLEVEL/CipherString/MinProtocol)"; \
        sed -i 's/SECLEVEL=2/SECLEVEL=1/g' /etc/ssl/openssl.cnf; \
        if grep -qE 'SECLEVEL[[:space:]]*=[[:space:]]*2' /etc/ssl/openssl.cnf; then \
            echo "ERRO: SECLEVEL=2 sobreviveu ao ajuste - formato novo, rever o sed"; \
            exit 1; \
        fi; \
    else \
        echo "AVISO: /etc/ssl/openssl.cnf nao existe nesta imagem base - nada a ajustar"; \
    fi

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
COPY --from=frontend-builder /build/node_modules/chart.js/dist/chart.umd.js.map ./app/static/dist/chart.umd.js.map
COPY --from=frontend-builder /build/node_modules/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js ./app/static/dist/chartjs-adapter-date-fns.bundle.min.js
COPY --from=frontend-builder /build/node_modules/socket.io-client/dist/socket.io.min.js ./app/static/dist/socket.io.min.js
COPY --from=frontend-builder /build/node_modules/socket.io-client/dist/socket.io.min.js.map ./app/static/dist/socket.io.min.js.map


# Expor a Porta: Informa ao Docker que a aplicação irá escutar na porta definida pela variável de ambiente.
EXPOSE ${APP_PORT}

# Comando de Execução: Executa a migração da base de dados e depois inicia o Gunicorn.
# O Gunicorn agora usa a variável de ambiente $APP_PORT para definir a porta de escuta.
# ADICIONADO: --preload flag para inicializar a app antes de fazer fork dos workers.
CMD ["sh", "-c", "flask db upgrade && gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 --worker-connections 1000 --timeout 120 --preload --bind 0.0.0.0:${APP_PORT} run:app"]
