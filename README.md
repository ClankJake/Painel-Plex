# Painel de Gestão Plex

![Status do Projeto](https://img.shields.io/badge/status-ativo-brightgreen)
![Linguagem](https://img.shields.io/badge/python-3.8%2B-blue)
![Framework](https://img.shields.io/badge/flask-2.x-orange)
[![Build and Publish Docker Image to GHCR](https://github.com/ClankJake/Painel-Plex/actions/workflows/docker-publish.yml/badge.svg?branch=stable)](https://github.com/ClankJake/Painel-Plex/actions/workflows/docker-publish.yml)

O Painel de Gestão Plex é uma aplicação web completa projetada para simplificar a administração de servidores Plex. Ele oferece uma interface centralizada para gerenciar usuários, convites, assinaturas, finanças e visualizar estatísticas detalhadas de uso, tudo com uma experiência de usuário moderna e interativa.

## Principais Funcionalidades

-   **Dashboard de Admin**: Visão geral em tempo real com streams ativos, contagem de usuários, receita mensal e próximas renovações.
-   **Gestão de Usuários**: Visualize, filtre, pesquise e gerencie todos os usuários do seu servidor. Aplique ações como bloqueio, desbloqueio, remoção e edição de perfis.
-   **Sistema de Convites**: Crie links de convite seguros e personalizáveis com data de expiração, limite de telas, acesso a bibliotecas específicas e períodos de teste.
-   **Portal do Usuário**: Uma área dedicada para seus usuários visualizarem suas próprias estatísticas de uso, gerenciarem configurações de privacidade e renovarem o acesso.
-   **Integração com Pagamentos**: Processe renovações de assinatura via PIX com integração nativa com a **Efí** e o **Mercado Pago**.
-   **Controle Financeiro**: Um dashboard financeiro para administradores acompanharem a receita mensal, o histórico de transações e as renovações futuras.
-   **Estatísticas Detalhadas**: Integração com o Tautulli para fornecer gráficos e rankings de conteúdo mais assistido, atividade por dia da semana e gêneros favoritos.
-   **Notificações Automatizadas**: Envie notificações de vencimento, renovação e fim de teste para os usuários através do Telegram e/ou Webhooks (compatível com Discord).
-   **Tarefas Agendadas**: Processos automatizados em segundo plano para verificar expirações, remover usuários bloqueados e enviar lembretes.
-   **Interface Moderna**: Frontend reativo construído com JavaScript moderno e Tailwind CSS, oferecendo uma experiência de usuário rápida e agradável, incluindo tema claro e escuro.

## Imagens
<p align="center">
  <img width="400" alt="Imagem 2" src="https://github.com/user-attachments/assets/6a0eb80c-ca2e-4fc0-a183-1c08d4c084a2" />
  <img width="400" alt="Imagem 1" src="https://github.com/user-attachments/assets/ca2e94ad-a3b0-48c9-b053-48b3d86a2744" />
</p>

## Instalação com Docker Compose (Recomendado)

Esta é a forma mais simples e rápida de colocar a aplicação em funcionamento.

### Pré-requisitos

-   **Docker** e **Docker Compose** instalados na sua máquina.
-   **Plex Media Server** e **Tautulli** a funcionar e acessíveis na sua rede.

### Passos

1.  **Crie o arquivo `docker-compose.yml`:**
    Crie um arquivo `docker-compose.yml` e cole o seguinte conteúdo:

    ```yaml
    # docker-compose.yml
    version: '3.8'

    services:
      painel-plex:
        image: ghcr.io/clankjake/painel-plex:stable
        container_name: painel-plex
        ports:
          - "5000:5000"
        volumes:
          - ./config:/app/config
          - ./certs:/app/certs
        environment:
          - PUID=1000
          - PGID=1000
          - APP_PORT=8080 # opcional
          - PYTHONIOENCODING=utf-8
        restart: unless-stopped
    ```

2.  **Inicie a Aplicação:**
    No mesmo diretório onde criou o arquivo, execute o comando:
    ```bash
    docker-compose up -d
    ```
    O Docker irá baixar a imagem mais recente e iniciar o conteiner em segundo plano.

3.  **Configuração:**
    Abra o seu navegador e aceda a `http://SEU_ENDERECO_IP:5000`. Será redirecionado para a página de configuração inicial, onde poderá conectar a sua conta Plex, Tautulli e outros serviços.

    -   O aplicativo irá criar automaticamente uma pasta `config` no mesmo local do seu `docker-compose.yml`. É aqui que o seu ficheiro `config.json` e a base de dados `app_data.db` serão guardados de forma persistente.
    -   Se utilizar pagamentos via Efí, coloque o seu arquivo de certificado `.pem` na pasta `certs` que também será criada.

## Instalação Manual (Desenvolvimento)

Esta abordagem é recomendada apenas se pretender contribuir para o desenvolvimento da aplicação.

1.  **Pré-requisitos:** Instale Python 3.8+, Node.js e npm.
2.  **Clone o repositório:** `git clone https://github.com/ClankJake/Painel-Plex.git`
3.  **Crie um ambiente virtual e instale as dependências Python:** `pip install -r requirements.txt`
4.  **Instale as dependências Frontend:** `npm install`
5.  **Inicie o processo de build do CSS:** `npm run watch:css`
6.  **Noutro terminal, inicie a aplicação:** `python run.py`
