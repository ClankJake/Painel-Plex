# Painel de Gestão Plex

![Status do Projeto](https://img.shields.io/badge/status-ativo-brightgreen)
![Linguagem](https://img.shields.io/badge/python-3.8%2B-blue)
![Framework](https://img.shields.io/badge/flask-2.x-orange)
[![Build and Publish Docker Image to GHCR](https://github.com/ClankJake/Painel-Plex/actions/workflows/docker-publish.yml/badge.svg?branch=stable)](https://github.com/ClankJake/Painel-Plex/actions/workflows/docker-publish.yml)

O Painel de Gestão Plex é uma aplicação web completa projetada para simplificar a administração de servidores Plex. Ele oferece uma interface centralizada para gerenciar usuários, convites, assinaturas, finanças e visualizar estatísticas detalhadas de uso, tudo com uma experiência moderna e interativa.

## Principais Funcionalidades

### Gestão e Acesso
-   **Dashboard de Admin**: Visão geral em tempo real com streams ativos, contagem de usuários, receita mensal e próximas renovações.
-   **Gestão de Usuários**: Visualize, filtre, pesquise e gerencie todos os usuários do seu servidor. Aplique ações como bloqueio, desbloqueio, remoção e edição de perfis.
-   **Sistema de Convites**: Crie links de convite seguros e personalizáveis com data de expiração, limite de telas, acesso a bibliotecas específicas e períodos de teste.
-   **Fontes de Mídia Online do Plex**: Opcionalmente, o painel desliga a TV ao Vivo, os Filmes e Programas de TV gratuitos e as restantes fontes da própria Plex na conta do usuário no momento em que ele aceita o convite — o aplicativo dele abre direto no seu conteúdo. É uma preferência da conta do usuário (ele pode reativá-la em plex.tv), não um bloqueio do servidor. Configure em **Configurações → Conexões**.
-   **API para Bots**: Gere convites automaticamente a partir de bots do Telegram ou outras automações, já vinculados ao ID do usuário.
-   **Portal do Usuário**: Área dedicada para o usuário ver suas estatísticas, gerenciar privacidade, acompanhar pedidos e renovar o acesso.
-   **Controle de Telas**: Limite de streams simultâneos com encerramento automático da sessão excedente.

### Pagamentos e Assinaturas
-   **Três gateways PIX**: **Efí**, **Mercado Pago** e **Gates2b**, com QR Code gerado dentro do próprio painel.
-   **Upgrade proporcional (pro-rata)**: O usuário pode aumentar o número de telas no meio do ciclo pagando apenas a diferença dos dias restantes, sem alterar o vencimento.
-   **Cupons de desconto** com percentual ou valor fixo, e limite de utilizações.
-   **Controle Financeiro**: Dashboard com receita mensal, histórico de transações e renovações futuras.
-   **Cobrança consistente**: O dia de vencimento é preservado ao longo das renovações, mesmo passando por meses curtos como fevereiro.

### Engajamento
-   **Indique e Ganhe**: Cada usuário recebe um link próprio. Quando um amigo assina por ele, o indicador ganha dias grátis ou crédito — configurável pelo administrador.
-   **Gamificação**: Sistema de XP e níveis totalmente personalizáveis (adicione, remova ou renomeie níveis), com conquistas e reset periódico por temporada.
-   **Plex Wrapped**: Retrospectiva anual em modo história, com os destaques do ano do usuário e cartão compartilhável.
-   **Estatísticas Detalhadas**: Integração com o Tautulli para gráficos e rankings de conteúdo mais assistido, atividade por dia da semana e gêneros favoritos.
-   **Recomendações "Porque assistiu X, pode gostar de Y"**: O painel cruza o histórico de todos os usuários e encontra os títulos que costumam ser vistos pelas mesmas pessoas (filtro colaborativo item-item, com semelhança de cosseno para que "o filme que todo mundo viu" não seja recomendado a todo mundo). Em servidores pequenos, um plano B por gênero entra em ação. Cada sugestão explica o motivo e leva direto ao título no Plex — e quem ativou "esconder do ranking" deixa de influenciar as recomendações dos outros.

### Notificações
-   **Quatro canais**: **Telegram**, **WhatsApp** (via Evolution API, GOWA ou WAHA), **Discord** e **Webhook genérico**.
-   **Mensagens personalizáveis** por evento: vencimento, renovação, reativação, fim de teste e avisos em massa.
-   **Disparo em massa com relatório real**: O console de envio mostra, por usuário, quais canais entregaram e quais falharam (com o motivo) — e o ritmo do disparo é ajustável em Comunicações > Avisos em Massa.
-   **Pedidos do Seerr**: O usuário é avisado no canal pessoal dele — com a capa do filme/série — sempre que o pedido muda de estado (pendente, aprovado, disponível, recusado).

### Operação
-   **Backup automático** do banco de dados e configurações, com restauração pelo próprio painel.
-   **Tarefas Agendadas**: Verificação de expirações, remoção de usuários bloqueados, lembretes e sincronização de perfis.
-   **Interface Moderna**: Frontend reativo com Tailwind CSS, tema claro e escuro, e layout adaptado para celular.

## Imagens
<p align="center">
  <img width="400" alt="Imagem 2" src="https://github.com/user-attachments/assets/6a0eb80c-ca2e-4fc0-a183-1c08d4c084a2" />
  <img width="400" alt="Imagem 1" src="https://github.com/user-attachments/assets/ca2e94ad-a3b0-48c9-b053-48b3d86a2744" />
</p>

## Instalação com Docker Compose (Recomendado)

Esta é a forma mais simples e rápida de colocar a aplicação em funcionamento.

### Pré-requisitos

-   **Docker** e **Docker Compose** instalados na sua máquina.
-   **Plex Media Server** em funcionamento e acessível na sua rede.
-   **Tautulli** (opcional, mas necessário para as estatísticas e a gamificação).

### Passos

1.  **Crie o arquivo `docker-compose.yml`:**

    ```yaml
    # docker-compose.yml
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
          - TZ=America/Sao_Paulo
          - APP_PORT=5000 # opcional
          - PYTHONIOENCODING=utf-8
        restart: unless-stopped
    ```

    > O `restart: unless-stopped` é **necessário** para que a restauração de backup funcione: o painel reinicia sozinho após restaurar.

2.  **Inicie a Aplicação:**
    ```bash
    docker compose up -d
    ```

3.  **Configuração inicial:**
    Acesse `http://SEU_ENDERECO_IP:5000`. Você será levado ao assistente de configuração, onde poderá conectar sua conta Plex, escolher o servidor e ligar os serviços opcionais.

    -   Uma pasta `config` é criada automaticamente. É onde ficam o `config.json` e o banco `app_data.db`.
    -   Se usar a Efí, coloque o certificado `.pem` na pasta `certs`.
    -   Já tem um backup de uma instalação anterior? O assistente permite **restaurá-lo logo no primeiro passo**, sem precisar reconfigurar tudo.

4.  **URL Base da Aplicação:**
    Em **Configurações → Geral**, preencha o endereço público do painel (ex.: `https://painel.seudominio.com`).

    > ⚠️ Este passo é **essencial** se você usar pagamentos. É a partir dele que os webhooks são construídos — sem um endereço público válido, os pagamentos são criados mas **nunca são confirmados automaticamente**.

## Guias de Configuração

Cada integração tem um guia próprio, com passo a passo e resolução de problemas:

| Integração | Guia | O que cobre |
|---|---|---|
| **Efí Bank** (PIX) | [docs/integracao-efi.md](docs/integracao-efi.md) | Certificado digital, escopos, mTLS vs. HMAC |
| **Mercado Pago** (PIX) | [docs/integracao-mercadopago.md](docs/integracao-mercadopago.md) | Access Token, webhook assinado, reembolsos |
| **Gates2b** (PIX) | [docs/integracao-gates2b.md](docs/integracao-gates2b.md) | Chave de API, valor mínimo, migração da BPIX |
| **Seerr** (pedidos) | [docs/integracao-seerr.md](docs/integracao-seerr.md) | Pedidos no portal, notificações com capa |
| **API de Convites** | [docs/api-convites-bot.md](docs/api-convites-bot.md) | Criar convites via bot, vínculo de Telegram ID |

As demais funcionalidades (notificações, gamificação, indicações, backup) são configuradas diretamente em **Configurações**, com explicações na própria interface.

## Atualização

O painel não atualiza sozinho. Para atualizar:

```bash
docker compose pull
docker compose up -d
```

As migrações do banco de dados são aplicadas automaticamente no arranque. Suas configurações e dados são preservados, pois ficam na pasta `config`.

> 💡 Antes de atualizar, vale gerar um backup em **Configurações → Automações e Tarefas → Baixar Backup Agora**.

## Instalação Manual (Desenvolvimento)

Recomendada apenas para quem pretende contribuir com o desenvolvimento.

1.  **Pré-requisitos:** Python 3.8+, Node.js e npm.
2.  **Clone o repositório:**
    ```bash
    git clone https://github.com/ClankJake/Painel-Plex.git
    cd Painel-Plex
    ```
3.  **Instale as dependências:**
    ```bash
    pip install -r requirements.txt
    npm install
    ```
4.  **Compile o CSS** (em um terminal separado):
    ```bash
    npm run watch:css
    ```
5.  **Inicie a aplicação:**
    ```bash
    python run.py
    ```

### Testes

A suíte de testes usa **pytest** e corre sem depender de um servidor Plex, do
Tautulli ou de qualquer gateway de pagamento — as integrações externas são
substituídas por duplos de teste.

```bash
# instala as dependências de desenvolvimento (inclui as de produção)
pip install -r requirements-dev.txt

# executa todos os testes
pytest

# apenas um ficheiro, ou um teste específico
pytest tests/test_pricing_manager.py
pytest -k proration

# com relatório de cobertura
pytest --cov=app --cov-report=term-missing
```

Os testes nunca tocam na sua instalação: a variável de ambiente
`PAINEL_PLEX_CONFIG_DIR` é apontada para uma pasta temporária, por isso o
`config/config.json` e a base de dados reais ficam intactos. Essa mesma variável
pode ser usada em produção para guardar os dados noutro diretório.

Os testes correm automaticamente no GitHub Actions em cada push e pull request
para as branches `main` e `stable` (ver `.github/workflows/tests.yml`).

### Notas para desenvolvedores

-   O painel roda com **1 worker Gunicorn** de propósito. O Flask-SocketIO é usado sem `message_queue`, então múltiplos workers fariam os eventos de tempo real se perderem entre processos.
-   O modo assíncrono é **gevent**. Não misture com eventlet — o monkey-patching entra em conflito.
-   O CSS é compilado do `app/static/css/input.css` para `app/static/dist/output.css`. Alterações no primeiro exigem rebuild.

## Estrutura de Pastas

```
Painel-Plex/
├── config/          # config.json e bancos de dados (criado automaticamente)
├── certs/           # certificado da Efí, se usado
├── docs/            # guias de configuração das integrações
├── tests/           # suíte de testes (pytest)
└── app/
    ├── blueprints/  # rotas (páginas e API)
    ├── services/    # integrações: Plex, Tautulli, gateways, notificações
    ├── templates/   # HTML (Jinja2)
    └── static/      # CSS, JavaScript
```

## Licença

Consulte o arquivo de licença do repositório.
