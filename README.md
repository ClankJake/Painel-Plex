# Painel de Gestão Plex

![Status do Projeto](https://img.shields.io/badge/status-ativo-brightgreen)
![Linguagem](https://img.shields.io/badge/python-3.8%2B-blue)
![Framework](https://img.shields.io/badge/flask-2.x-orange)

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
-   **Interface Moderna**: Frontend reativo construído com JavaScript moderno e Tailwind CSS, oferecendo uma experiência de usuário rápida e agradável, incluindo tema claro e escuro.                                                                                                   |
## Imagens
<p align="center">
  <img width="400" alt="Imagem 2" src="https://github.com/user-attachments/assets/6a0eb80c-ca2e-4fc0-a183-1c08d4c084a2" />
  <img width="400" alt="Imagem 1" src="https://github.com/user-attachments/assets/ca2e94ad-a3b0-48c9-b053-48b3d86a2744" />
</p>

## Pré-requisitos

Antes de começar, certifique-se de que você tem os seguintes softwares instalados e configurados:

1.  **Python**: Versão 3.8 ou superior.
2.  **Plex Media Server**: Totalmente configurado e acessível.
3.  **Tautulli**: Instalado e funcionando, pois é essencial para as estatísticas e controle de sessões.

## Instalação

Siga estes passos para colocar a aplicação em funcionamento:

1.  **Clone o repositório:**
    ```bash
    git clone https://github.com/seu-usuario/painel-plex.git
    cd painel-plex
    ```

2.  **Crie e ative um ambiente virtual (recomendado):**
    ```bash
    # Windows
    python -m venv venv
    .\venv\Scripts\activate

    # macOS / Linux
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Instale as dependências:**
    O projeto utiliza as bibliotecas listadas no arquivo `requirements.txt`.
    ```bash
    pip install -r requirements.txt
    ```

4.  **Primeira Execução:**
    Execute a aplicação pela primeira vez para que ela crie o arquivo de configuração `config.json` e o banco de dados `app_data.db`.
    ```bash
    python run.py
    ```

## Configuração

A configuração inicial é feita de forma simples e guiada através da interface web.

1.  Após a primeira execução, acesse `http://127.0.0.1:5000`. Você será redirecionado para a página de setup.

