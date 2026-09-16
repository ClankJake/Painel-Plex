# Painel de Gestão Plex e Jellyfin

![Status do Projeto](https://img.shields.io/badge/status-ativo-brightgreen)
![Servidores](https://img.shields.io/badge/servidores-Plex%20%7C%20Jellyfin-blueviolet)
![Linguagem](https://img.shields.io/badge/python-3.8%2B-blue)
![Framework](https://img.shields.io/badge/flask-2.x-orange)
[![Build and Publish Docker Image to GHCR](https://github.com/ClankJake/Painel-Plex/actions/workflows/docker-publish.yml/badge.svg?branch=stable)](https://github.com/ClankJake/Painel-Plex/actions/workflows/docker-publish.yml)

Uma aplicação web completa para administrar o seu servidor de mídia. Ele oferece uma interface centralizada para gerenciar usuários, convites, assinaturas, finanças e visualizar estatísticas detalhadas de uso, tudo com uma experiência moderna e interativa.

> 🆕 **Agora com suporte a Jellyfin, além do Plex.** Você escolhe o servidor no assistente de instalação e o painel inteiro passa a falar com ele — a mesma gestão de usuários, os mesmos pagamentos, as mesmas notificações. O que muda entre os dois está logo abaixo.

## Plex ou Jellyfin

O painel não fala com uma marca: fala com o servidor de mídia que você escolher em **Configurações → Conexões** (ou no assistente, na primeira execução). A diferença de fundo não é técnica, é de quem é a conta:

-   **No Plex**, o usuário já tem uma conta no plex.tv e traz essa conta. O painel **convida** essa conta, e quem cuida da senha é a própria Plex — o painel nunca a vê.
-   **No Jellyfin**, as contas são **locais ao servidor**. O painel **cria** a conta ao resgatar o convite e passa a ser responsável por entregar as credenciais, o que ele faz pelos canais de notificação (Telegram, Discord ou WhatsApp).

Daí saem as diferenças que você vai notar na interface. Onde um recurso não existe no servidor escolhido, ele simplesmente **não aparece** — nada dá erro:

| Recurso | Plex | Jellyfin |
|---|:---:|:---:|
| Convites | Convida uma conta que já existe | **Cria** a conta e envia as credenciais |
| Login no painel | PIN do plex.tv | Usuário e senha do próprio servidor |
| "Esqueci minha senha" | — (a senha é da Plex) | ✅ Link de uso único pelas notificações |
| Trocar a senha pela "Minha Conta" | — | ✅ Muda no servidor, é a mesma dos dois lados |
| Bloquear sem perder as bibliotecas | — (remove os compartilhamentos e repõe depois) | ✅ Suspende a conta |
| Fontes de Mídia Online (TV ao Vivo, Discover) | ✅ | — (não existem) |
| Estatísticas, XP, conquistas e Wrapped | ✅ | ✅ |
| Fonte das estatísticas e do histórico | Tautulli, **ou o próprio Plex** se ele não estiver configurado | O próprio Jellyfin (exato com o plugin Playback Reporting) |
| Aparelhos do usuário | Deduzidos do histórico | Lista real de aparelhos registrados |
| Limite de telas | Encerramento pelo painel | Encerramento pelo painel **e** recusa na origem com o plugin StreamLimiter |
| Último recurso contra clientes que ignoram o comando de parar | — | ✅ Revoga o acesso do aparelho |

> ⚠️ **Trocar de servidor depois exige um reinício da aplicação**, e o painel faz isso sozinho ao salvar — aguarde alguns segundos e a página volta já apontada para o servidor novo.

> ⚠️ **Um convite de teste no Jellyfin merece cuidado extra.** Como o painel cria a conta ali mesmo, criar uma conta nova não custa nada e nada liga duas contas à mesma pessoa. Se você oferece período de teste, exija um contato verificável (Telegram, por exemplo) no convite.

## Principais Funcionalidades

### Gestão e Acesso
-   **Dashboard de Admin**: Visão geral em tempo real com streams ativos, contagem de usuários, receita mensal e próximas renovações.
-   **Gestão de Usuários**: Visualize, filtre, pesquise e gerencie todos os usuários do seu servidor. Aplique ações como bloqueio, desbloqueio, remoção e edição de perfis.
-   **Sistema de Convites**: Crie links de convite seguros e personalizáveis com data de expiração, limite de telas, acesso a bibliotecas específicas e períodos de teste. No Plex o convite chega à conta que o usuário já tem; no Jellyfin ele cria a conta na hora e as credenciais vão pelo canal de notificação do usuário.
-   **Fontes de Mídia Online** (somente Plex): Opcionalmente, o painel desliga a TV ao Vivo, os Filmes e Programas de TV gratuitos e as restantes fontes da própria Plex na conta do usuário no momento em que ele aceita o convite — o aplicativo dele abre direto no seu conteúdo. É uma preferência da conta do usuário (ele pode reativá-la em plex.tv), não um bloqueio do servidor. Configure em **Configurações → Conexões**.
-   **API para Bots**: Gere convites automaticamente a partir de bots do Telegram ou outras automações, já vinculados ao ID do usuário.
-   **Portal do Usuário**: Área dedicada para o usuário ver suas estatísticas, gerenciar privacidade, acompanhar pedidos e renovar o acesso.
-   **Controle de Telas**: Limite de streams simultâneos com encerramento automático da sessão excedente. Em servidores Jellyfin com o plugin **StreamLimiter**, o limite também é aplicado na origem — a reprodução é recusada antes do primeiro byte, o que nenhum aplicativo consegue ignorar.
-   **Senha e "esqueci minha senha"** (servidores de contas locais, como o Jellyfin): O usuário troca a própria senha pela "Minha Conta" e recupera o acesso por um link de uso único enviado nos canais que ele já cadastrou. O painel não guarda senha nenhuma — ela é sempre a do servidor de mídia.

### Pagamentos e Assinaturas
-   **Três gateways PIX**: **Efí**, **Mercado Pago** e **Gates2b**, com QR Code gerado dentro do próprio painel.
-   **Upgrade proporcional (pro-rata)**: O usuário pode aumentar o número de telas no meio do ciclo pagando apenas a diferença dos dias restantes, sem alterar o vencimento.
-   **Cupons de desconto** com percentual ou valor fixo, e limite de utilizações.
-   **Controle Financeiro**: Dashboard com receita mensal, histórico de transações e renovações futuras.
-   **Cobrança consistente**: O dia de vencimento é preservado ao longo das renovações, mesmo passando por meses curtos como fevereiro.

### Engajamento
-   **Indique e Ganhe**: Cada usuário recebe um link próprio. Quando um amigo assina por ele, o indicador ganha dias grátis ou crédito — configurável pelo administrador.
-   **Gamificação**: Sistema de XP e níveis totalmente personalizáveis (adicione, remova ou renomeie níveis), com conquistas e reset periódico por temporada.
-   **Wrapped**: Retrospectiva anual em modo história, com os destaques do ano do usuário e cartão compartilhável (o nome do seu servidor aparece no lugar certo, seja Plex ou Jellyfin).
-   **Estatísticas Detalhadas**: Gráficos e rankings de conteúdo mais assistido, atividade por dia da semana e gêneros favoritos. Num painel Plex elas vêm do Tautulli quando ele está configurado e **do próprio servidor quando não está**; num painel Jellyfin vêm sempre do próprio servidor, e ficam exatas com o plugin Playback Reporting.
-   **Recomendações "Porque assistiu X, pode gostar de Y"**: O painel cruza o histórico de todos os usuários e encontra os títulos que costumam ser vistos pelas mesmas pessoas (filtro colaborativo item-item, com semelhança de cosseno para que "o filme que todo mundo viu" não seja recomendado a todo mundo). Em servidores pequenos, um plano B por gênero entra em ação. Cada sugestão explica o motivo e leva direto ao título no seu servidor — e quem ativou "esconder do ranking" deixa de influenciar as recomendações dos outros.

### Notificações
-   **Cinco canais**: **Telegram**, **WhatsApp** (via Evolution API, GOWA ou WAHA), **Discord**, **Webhook genérico** e **notificação push**.
-   **Push no celular e no navegador**: Com o painel instalado no Android ou no iPhone (adicionado à tela de início), ou apenas com as notificações do navegador ligadas no computador, os avisos chegam **mesmo com o painel fechado**. Cada pessoa liga no próprio aparelho, pelo sino do painel. O **administrador** é avisado de cada pagamento confirmado e de cada pedido de conteúdo novo à espera de aprovação; o **usuário**, do vencimento, da renovação e do andamento dos pedidos dele. Não precisa de conta em serviço nenhum: o painel gera as próprias chaves na primeira vez que você liga a opção (Configurações > Notificações > Push). Só exige que o painel esteja em **HTTPS** — é o que o navegador pede.
    > 🛡️ A senha de uma conta recriada e o link de "esqueci minha senha" **nunca** vão por push: eles aparecem na tela de bloqueio, à vista de quem estiver perto. Continuam indo pelos canais pessoais.
-   **Mensagens personalizáveis** por evento: vencimento, renovação, reativação, fim de teste, credenciais de acesso, recuperação de senha e avisos em massa.
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
-   **Um servidor de mídia** em funcionamento e acessível na sua rede: **Plex Media Server** ou **Jellyfin**.
-   **Tautulli** (opcional, e **somente para Plex**). Sem ele as estatísticas e o histórico continuam funcionando, lidos do próprio Plex — mais lentos, sem porcentagem de progresso e sobre uma janela das reproduções mais recentes. O painel avisa o que você está trocando no cartão do Tautulli, em **Configurações → Conexões**.
-   **Plugins do Jellyfin** (opcionais, e somente para Jellyfin): o **StreamLimiter** faz o limite de telas valer em qualquer aplicativo, e o **Playback Reporting** dá histórico e estatísticas por reprodução. Veja [docs/plugins-jellyfin.md](docs/plugins-jellyfin.md).

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

    > **`IMAGE_PROXY_ALLOWED_HOSTS` (opcional).** O proxy de imagens só descarrega capas e avatares de uma lista de domínios conhecidos (`plex.tv`, `plex.direct`, `gravatar.com`, `tmdb.org`, entre outros, mais o endereço do seu servidor de mídia e do Tautulli). É essa lista que impede que o painel seja usado para fazer pedidos à sua rede interna (SSRF). Se alguma imagem legítima vier de outro domínio, acrescente-o aqui, separado por vírgulas: `IMAGE_PROXY_ALLOWED_HOSTS=cdn.exemplo.com,outro.net` (subdomínios são incluídos automaticamente).

    > **`IMAGE_PROXY_ALLOWED_PORTS` (opcional).** As portas aceitas são a 80, a 443, a 32400 e a porta do seu servidor de mídia e do seu Tautulli — quem usa uma porta diferente da padrão não precisa configurar nada. Só é necessário se as capas chegarem numa terceira porta (acontece quando a porta de acesso remoto do Plex difere da porta local): `IMAGE_PROXY_ALLOWED_PORTS=41234,8443`.

2.  **Inicie a Aplicação:**
    ```bash
    docker compose up -d
    ```

3.  **Configuração inicial:**
    Acesse `http://SEU_ENDERECO_IP:5000`. Você será levado ao assistente de configuração, onde o **primeiro passo é escolher o servidor de mídia**:

    -   **Plex**: você autoriza com a sua conta plex.tv (por PIN, sem digitar a senha aqui) e escolhe qual dos seus servidores o painel vai administrar.
    -   **Jellyfin**: você informa o endereço do servidor e uma **chave de API** (gerada no Jellyfin em *Painel → Chaves de API*) e escolhe qual conta será a administradora do painel.

    Depois é só ligar os serviços opcionais.

    -   Uma pasta `config` é criada automaticamente. É onde ficam o `config.json` e o banco `app_data.db`.
    -   Se usar a Efí, coloque o certificado `.pem` na pasta `certs`.
    -   Já tem um backup de uma instalação anterior? O assistente permite **restaurá-lo logo no primeiro passo**, sem precisar reconfigurar tudo.

4.  **URL Base da Aplicação:**
    Em **Configurações → Geral**, preencha o endereço público do painel (ex.: `https://painel.seudominio.com`).

    > ⚠️ Este passo é **essencial** se você usar pagamentos. É a partir dele que os webhooks são construídos — sem um endereço público válido, os pagamentos são criados mas **nunca são confirmados automaticamente**.

    > 🔶 **Usa Cloudflare (nuvem laranja ou Tunnel)?** O webhook da Efí precisa de configuração extra e o modo mTLS **não funciona** nesse cenário. Veja [Rodar atrás da Cloudflare](docs/integracao-efi.md#5-rodar-atrás-da-cloudflare) antes de ativar os pagamentos.

## Guias de Configuração

Cada integração tem um guia próprio, com passo a passo e resolução de problemas:

| Integração | Guia | O que cobre |
|---|---|---|
| **Efí Bank** (PIX) | [docs/integracao-efi.md](docs/integracao-efi.md) | Certificado digital, escopos, mTLS vs. HMAC, **rodar atrás da Cloudflare** |
| **Mercado Pago** (PIX) | [docs/integracao-mercadopago.md](docs/integracao-mercadopago.md) | Access Token, webhook assinado, reembolsos |
| **Gates2b** (PIX) | [docs/integracao-gates2b.md](docs/integracao-gates2b.md) | Chave de API, valor mínimo, migração da BPIX |
| **Seerr** (pedidos) | [docs/integracao-seerr.md](docs/integracao-seerr.md) | Pedidos no portal, notificações com capa |
| **API de Convites** | [docs/api-convites-bot.md](docs/api-convites-bot.md) | Criar convites via bot, vínculo de Telegram ID |
| **Plugins do Jellyfin** | [docs/plugins-jellyfin.md](docs/plugins-jellyfin.md) | Limite de telas que qualquer aplicativo respeita, histórico por reprodução |

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
4.  **Gere os assets do frontend** (CSS e bibliotecas):
    ```bash
    npm run build
    ```
    > Este passo não é opcional. Nada em `app/static/dist/` está versionado —
    > sem ele o navegador acusa `io is not defined` e `Chart is not defined`.

    Durante o desenvolvimento, deixe o CSS a recompilar sozinho num terminal
    separado:
    ```bash
    npm run watch:css
    ```
5.  **Inicie a aplicação:**
    ```bash
    python run.py
    ```

### Testes

A suíte de testes usa **pytest** e roda sem depender de um servidor Plex ou
Jellyfin, do Tautulli ou de qualquer gateway de pagamento — as integrações
externas são substituídas por duplos de teste.

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
    ├── services/    # servidores de mídia (Plex, Jellyfin), Tautulli,
    │                  gateways de pagamento, notificações
    ├── templates/   # HTML (Jinja2)
    └── static/      # CSS, JavaScript
```

## Licença

Consulte o arquivo de licença do repositório.
