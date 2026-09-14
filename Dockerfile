# Dockerfile para a aplicação Painel Plex

# --- Estágio 1: Build do Frontend ---
# Usar a imagem base 'bookworm' que é uma versão mais recente do Debian
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /build

# Copia os ficheiros de definição de dependências e configuração do frontend
#
# ⚠️ O `package-lock.json` vem JUNTO de propósito. Sem ele, o `npm install`
# resolvia de fresco a cada build: a imagem podia sair com versões diferentes
# das de ontem sem que nada mudasse no repositório — e foi por isso que ninguém
# reparou que o lockfile versionado estava inutilizável (os hashes eram de
# tarballs re-empacotados por um espelho, e o registo público recusava-os).
COPY package.json package-lock.json ./
COPY tailwind.config.js .

# `npm ci` e não `npm install`: instala EXATAMENTE o que o lockfile fixa e
# recusa-se a continuar se ele estiver dessincronizado do package.json. Um build
# que falha alto é melhor do que uma imagem que ninguém sabe do que é feita.
RUN npm ci

# Copia o código-fonte da aplicação que contém as classes do Tailwind
COPY app ./app

# Gera o CSS e copia as bibliotecas de terceiros para 'dist'.
# É o mesmo comando que se corre em desenvolvimento: os caminhos das
# bibliotecas ficam só no package.json, e não repetidos aqui.
RUN npm run build


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

# Copia os assets construídos do estágio de frontend.
# 🐛 Estas eram seis linhas, a repetir os caminhos das bibliotecas que o
# package.json também precisava de saber. As duas listas divergiram: o
# Docker copiava as bibliotecas, o desenvolvimento local não, e quem corria
# o painel fora do contentor tinha 'io is not defined' e 'Chart is not
# defined' no navegador. Agora quem sabe os caminhos é só o package.json.
COPY --from=frontend-builder /build/app/static/dist ./app/static/dist


# Expor a Porta: Informa ao Docker que a aplicação irá escutar na porta definida pela variável de ambiente.
EXPOSE ${APP_PORT}

# Comando de Execução: aplica as migrações e sobe o Gunicorn na porta $APP_PORT.
#
# 🐛 **O `--preload` estava aqui e era a causa de dois erros de uma vez.** Com ele,
# o `create_app()` corre no processo MESTRE e os workers são um `fork` dessa
# memória — o que quer dizer que a aplicação é lida UMA vez, no arranque do
# contentor, e nunca mais.
#
# O painel conta com o contrário. Trocar de servidor de média obriga a reiniciar
# (os blueprints guardam a referência ao backend POR VALOR), e a forma de o
# fazer é o processo pedir a própria morte: `_agendar_reinicio()` manda um
# SIGTERM a si mesmo e conta com o supervisor para o levantar de novo. Só que
# quem morre é o WORKER, e o mestre volta a fazer `fork` da memória PRÉ-CARREGADA
# — com o config antigo lá dentro. Depois de escolher o Jellyfin no assistente, a
# página de login continuava a ser a do Plex, e continuaria a sê-lo até alguém
# reiniciar o contentor à mão.
#
# O segundo erro vinha do mesmo `fork`: o Flask-Limiter arranca um
# `threading.Timer` no construtor do armazenamento em memória, e sob gevent isso
# é um greenlet. Pré-carregar cria-o no MESTRE; o worker herda-o já marcado como
# "stopped" pelo `threading._after_fork` (que o tira do `_active`) e, quando ele
# acorda, o `Thread._bootstrap` faz `del _active[get_ident()]` sobre uma chave
# que já não existe:
#
#     KeyError: 271255370948160
#     <Greenlet ...: <bound method Thread._bootstrap of
#      <Timer(Thread-1, stopped ...)>>> failed with KeyError
#
# O `Thread-1` é a assinatura: é o PRIMEIRO thread do processo, criado antes do
# `fork`. Sem `--preload` não há nada em voo para herdar.
#
# E não se perde nada: o `--preload` serve para poupar memória entre VÁRIOS
# workers, e aqui há **um** de propósito (gevent + SocketIO sem `message_queue`).
# Em troca, o agendador passa a correr no worker — que é onde o resto do painel
# vive — em vez de correr no mestre, onde ficava por causa do `fork` (as threads
# não sobrevivem a um, por isso o worker ficava com um `scheduler.running` a
# dizer que sim sobre uma thread que já não existia).
#
# O `--graceful-timeout` é a outra metade do reinício. Por omissão são 30
# segundos, e o worker gasta-os todos SEMPRE que há um separador aberto: ele só
# sai quando não houver ligações a ser servidas, e uma ligação keep-alive (ou um
# websocket do dashboard) nunca fecha sozinha. O assistente dizia "aguarde" e o
# painel demorava meio minuto a voltar.
CMD ["sh", "-c", "flask db upgrade && gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 --worker-connections 1000 --timeout 120 --graceful-timeout 10 --bind 0.0.0.0:${APP_PORT} run:app"]
