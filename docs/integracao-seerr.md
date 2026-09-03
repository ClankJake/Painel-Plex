# Integração com o Seerr (Pedidos de Filmes e Séries)

Guia para ligar o **Seerr** (antigo Overseerr / Jellyseerr) ao Painel Plex.

A integração faz duas coisas:

1. **Cria e remove usuários** no Seerr automaticamente, acompanhando quem tem
   acesso ao seu servidor Plex.
2. **Mostra os pedidos** do usuário na área de conta dele — e, opcionalmente,
   **notifica-o no Telegram, WhatsApp ou Discord** sempre que o estado de um
   pedido muda.

> **Sobre o nome:** o Overseerr foi descontinuado e o projeto continua como
> **Seerr** ([seerr-team/seerr](https://github.com/seerr-team/seerr)). A API não
> mudou — mesmo prefixo `/api/v1`, mesmo cabeçalho `X-API-Key`, mesmos campos de
> webhook. Por isso esta integração funciona com **Seerr, Overseerr e
> Jellyseerr** sem qualquer alteração.

---

## 1. Obter a chave de API do Seerr

1. Aceda ao seu Seerr → **Settings** (Configurações) → **General**
2. Localize o campo **API Key**
3. Copie o valor

---

## 2. Configurar no Painel Plex

Vá a **Configurações → Conexões → Seerr**:

| Campo | Descrição |
|---|---|
| **Ativar** | Liga o módulo de pedidos. |
| **URL do Seerr** | Endereço completo, ex: `http://192.168.1.10:5055` |
| **Chave da API (Seerr)** | A chave copiada no passo 1. |

Clique em **Testar Conexão** antes de gravar.

> ⚠️ **Não inclua `/api/v1` no URL.** O painel acrescenta esse caminho
> automaticamente. Escreva apenas `http://host:5055`.

Depois de ativar, os usuários com a permissão **"Acesso ao Seerr"** (definida
por usuário na página de Usuários) passam a ver a aba **Pedidos** na área
de conta.

---

## 3. Notificações de pedidos (opcional, mas recomendado)

Esta é a parte que avisa o usuário, no canal pessoal dele, quando o pedido
muda de estado — com a capa do filme/série incluída.

### 3.1. Obter a chave de API do painel

No Painel Plex, vá a **Configurações → Geral → Chave de API (Integrações)** e
copie a chave (use o botão do olho para a revelar).

### 3.2. Configurar o webhook no Seerr

No Seerr: **Settings → Notifications → Webhook**

| Campo | Valor |
|---|---|
| **Enable Agent** | Ativado |
| **Webhook URL** | `https://SEU-PAINEL/api/system/webhook/overseerr` |
| **Authorization Header** | A chave de API do passo 3.1 |
| **JSON Payload** | Deixe o modelo **padrão** |

Em **Notification Types**, selecione os eventos que quer notificar (ver secção 4).

> Os eventos de **Issues** (problemas reportados pelos usuários) chegam pelo mesmo
> webhook, mas não são pedidos: o painel ignora-os sem gerar erro.

Clique em **Test** para confirmar. Deve aparecer sucesso no Seerr.

> **Porquê `/webhook/overseerr` e não `/webhook/seerr`?** O caminho foi mantido
> para não quebrar instalações que já o tinham configurado antes da mudança de
> marca. É apenas um nome — funciona igual com o Seerr.

### 3.3. Como o usuário é identificado

O painel liga a notificação ao usuário através do **e-mail** registado no
Seerr, que tem de corresponder ao e-mail da conta Plex.

Se não houver correspondência, a notificação é simplesmente ignorada (sem erro) e
fica um aviso no log. O usuário continua a poder ver os pedidos na área de
conta — só não recebe a mensagem.

---

## 4. Mensagens por tipo de evento

O painel tem uma mensagem própria para cada situação, editável em
**Configurações → Notificações**, dentro da sub-aba de cada canal (Telegram,
Discord, WhatsApp), na secção **"Pedidos (Seerr)"**.

| Evento no Seerr | Mensagem padrão |
|---|---|
| `MEDIA_PENDING` | 🍿 *Novo Conteúdo Solicitado* |
| `MEDIA_APPROVED` | ✅ *Pedido Aprovado* |
| `MEDIA_AUTO_APPROVED` | ✅ *Pedido Aprovado* (mesma mensagem) |
| `MEDIA_AVAILABLE` | 🎉 *Já Está Disponível!* |
| `MEDIA_DECLINED` | ❌ *Pedido Recusado* |
| `MEDIA_FAILED` | ⚠️ *Falha no Pedido* |

**Comportamento de recurso:** se um template ficar em branco, ou se o Seerr enviar
um evento que o painel ainda não conhece (numa versão futura, por exemplo), é
usada a mensagem genérica de pedidos. O usuário nunca fica sem ser avisado.

### Variáveis disponíveis

| Variável | Conteúdo |
|---|---|
| `{title}` | Título com o ano, ex: `Duna: Parte Dois (2024)` |
| `{overview}` | Sinopse |
| `{status}` | Estado do pedido |
| `{username}` | Nome do usuário que pediu |
| `{media_url}` | Link direto para o item no Seerr |
| `{event}` | Descrição do evento enviada pelo Seerr |
| `{name}` | Nome do usuário no painel |

### Exemplo de mensagem recebida

```
🍿 Novo Conteúdo Solicitado

No mundo inteiro, só você se conectou a mim (2026)

📝 Lin Chia Kai possui uma habilidade misteriosa que lhe permite
enxergar os laços emocionais entre as pessoas como fios coloridos...

━━━━━━━━━━━━━━━
👤 Usuário: maikonc1
📊 Status: PENDING
━━━━━━━━━━━━━━━

🚀 Acesse o pedido:
https://seerr.exemplo.com/tv/239830
```

A capa do filme/série é enviada como imagem junto da mensagem no Telegram e no
WhatsApp. No Discord vai como *embed*.

---

## 5. Gestão automática de usuários

Com o módulo ativo, o painel mantém o Seerr sincronizado:

- **Ao aceitar um convite** — se a opção *"Acesso ao Seerr"* estiver marcada no
  convite, o usuário é importado para o Seerr automaticamente.
- **Ao remover um usuário** — a conta correspondente no Seerr também é
  removida.

A correspondência é sempre feita pelo **e-mail**.

---

## 6. Resolução de problemas

**"Módulo de pedidos desativado no servidor" (na aba Pedidos do usuário)**
Confirme que o módulo está ativado em Configurações → Conexões → Seerr e que o
URL e a chave estão preenchidos. Confirme também que o usuário tem a permissão
*"Acesso ao Seerr"* marcada na página de Usuários.

**`Cannot POST /api/system/webhook/overseerr` (erro 404 no Seerr)**
A rota do webhook não existe na versão do painel que está a correr. Atualize o
painel e reinicie o contêiner. Para confirmar que a rota responde:

```bash
curl -X POST https://SEU-PAINEL/api/system/webhook/overseerr \
  -H "X-API-Key: SUA_CHAVE" \
  -H "Content-Type: application/json" \
  -d '{"notification_type":"TEST_NOTIFICATION"}'
```

Deve devolver `{"success": true, ...}`. Se continuar a dar 404 depois de
atualizar, verifique se algum proxy reverso à frente do painel está a bloquear
esse caminho.

**Erro 401 no webhook**
O *Authorization Header* configurado no Seerr não corresponde à Chave de API do
painel. Copie-a novamente em Configurações → Geral.

**O teste do webhook passa, mas o usuário não recebe nada**
1. O e-mail no Seerr tem de ser igual ao da conta Plex.
2. O usuário precisa de ter um canal configurado (Telegram, celular ou
   Discord) na área de conta dele.
3. Esse canal tem de estar ativo em Configurações → Notificações.
4. Procure nos logs por `Webhook do Overseerr` para ver o motivo.

**A lista de pedidos aparece vazia mas o usuário tem pedidos**
A procura é feita pelo e-mail. Confirme que o e-mail do usuário no Seerr
coincide com o do Plex.

**Um pedido aparece com um estado que parece errado**
A etiqueta combina o estado do *pedido* (Pendente, Aprovado, Recusado, Falhou,
Concluído) com o estado da *média* no servidor (Pendente, Processando,
Parcialmente Disponível, Disponível, Bloqueado, Removido). Quando o conteúdo já
está disponível, é isso que aparece — mesmo que o pedido tenha ficado noutro
estado.

**Um pedido aparece como "Título indisponível"**
A consulta de detalhes ao TMDB falhou para aquele item. O pedido continua visível
com o estado correto — é um comportamento intencional, para que nenhum pedido
desapareça da lista por causa de uma falha momentânea.

---

## 7. Notas técnicas

- **API usada:** `/api/v1` do Seerr (compatível com Overseerr e Jellyseerr).
- **Autenticação:** cabeçalho `X-API-Key`.
- **Procura do usuário:** feita com `GET /user?q=<email>` — uma única chamada.
  Em versões antigas que não conheçam o parâmetro, o painel recai em percorrer a
  listagem página a página.
- **Caches em memória:** os detalhes de cada filme/série (título, ano, capa) são
  guardados 24 h e o ID do usuário 10 min. Isto reduz o número de chamadas à
  API por carregamento da página de pedidos de 12 para 1 nos acessos seguintes.
  O **estado** dos pedidos nunca é guardado em cache — é sempre lido em tempo real.
  Gravar a configuração do Seerr esvazia as duas caches, para que a mudança de
  servidor tenha efeito imediato.
- **Paginação:** a lista de pedidos é paginada, com botão para carregar mais.
- **Webhook:** protegido por chave de API e isento de rate limit (o Seerr pode
  enviar rajadas de notificações). Responde sempre HTTP 200 em caso de erro
  interno, para que o Seerr não fique a repetir indefinidamente — os erros ficam
  registados nos logs do painel.
