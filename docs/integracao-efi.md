# Integração com a Efí Bank (PIX)

Guia para configurar o gateway de pagamentos **Efí Bank** (antiga Gerencianet) no
Painel Plex.

A Efí é a integração mais completa do painel: além de gerar o QR Code do PIX
dentro do próprio site, o painel **regista o webhook automaticamente** na conta
da Efí — não é preciso colar URLs manualmente no painel deles.

> **Nota:** apenas o **PIX** está integrado. A Efí também suporta boleto, cartão
> e outros métodos, que não fazem parte desta integração.

---

## 1. Criar a aplicação na Efí

1. Aceda a **https://app.efipay.com.br** e faça login
2. Vá a **API** → **Minhas Aplicações** → **Nova Aplicação**
3. Dê um nome (ex.: `Painel Plex`)
4. Nos **escopos**, ative pelo menos:
   - **PIX** → *Cob* (Enviar e alterar cobranças) — **leitura e escrita**
   - **PIX** → *Webhooks* — **leitura e escrita**
   - **PIX** → *Payload Location* — **leitura e escrita**
5. Guarde

Após criar, terá acesso ao **Client ID** e ao **Client Secret**. Note que existem
credenciais separadas para **Produção** e **Homologação (sandbox)**.

---

## 2. Gerar o certificado

A Efí exige um **certificado digital** (`.p12` ou `.pem`) para autenticar os
pedidos — não basta o Client ID/Secret.

1. Ainda em **API** → **Minhas Aplicações**, selecione a aplicação
2. Vá a **Certificados** → **Criar novo certificado**
3. Escolha o ambiente (Produção ou Homologação)
4. Descarregue o arquivo `.p12`

### 2.1. Converter para `.pem`

O painel usa o formato `.pem`. Converta com o OpenSSL:

```bash
openssl pkcs12 -in seu-certificado.p12 -out certificado.pem -nodes
```

Quando pedir a senha, basta pressionar **Enter** (os certificados da Efí
não têm senha).

### 2.2. Colocar o certificado no servidor

Copie o `.pem` para a pasta `certs/` do painel:

```
painel-plex/
├── config/
├── certs/
│   └── certificado.pem     ← aqui
└── docker-compose.yml
```

No `docker-compose.yml`, garanta que a pasta está montada:

```yaml
volumes:
  - ./config:/app/config
  - ./certs:/app/certs      ← esta linha
```

> A pasta `certs/` está no `.dockerignore` de propósito: o certificado **não deve
> ser incluído na imagem Docker**, apenas montado como volume. Assim não vai
> parar a um registro de imagens por engano.

---

## 3. Configurar no Painel Plex

Vá a **Configurações → Pagamentos → Efí**:

| Campo | Descrição |
|---|---|
| **Ativar Efí** | Liga o gateway. |
| **Client ID** | Da aplicação criada no passo 1. |
| **Client Secret** | Da mesma aplicação. |
| **Caminho do Certificado** | `/app/certs/certificado.pem` (caminho **dentro** do contêiner). |
| **Modo Sandbox** | Ative para testes com credenciais de homologação. |
| **Chave PIX** | A chave PIX da sua conta Efí que vai receber os pagamentos. |
| **Usar mTLS** | **Recomendado: ativado.** Ver secção 4. |
| **Segredo HMAC do Webhook** | Só necessário se o mTLS estiver desativado. |

Também é obrigatório ter o **URL Base da Aplicação** preenchido em
**Configurações → Geral** — é a partir dele que o painel constrói o endereço do
webhook.

Clique em **Testar Conexão** antes de gravar.

---

## 4. Webhook e segurança (mTLS vs. HMAC)

Ao gravar as configurações, o painel **regista automaticamente** o webhook na
Efí, apontando para:

```
https://SEU-DOMINIO/api/payments/webhook/efi
```

Existem dois modos de proteger essa comunicação:

### Modo mTLS (recomendado, padrão)

A Efí apresenta um certificado de cliente e a conexão é autenticada mutuamente.
É o modo mais seguro e não exige configuração extra no painel.

**Requisito:** o seu servidor tem de aceitar a validação mTLS da Efí. Em algumas
configurações de proxy reverso (Nginx, Traefik, Cloudflare) isto exige ajustes
adicionais no proxy.

### Modo HMAC (alternativa)

Se o mTLS não funcionar na sua infraestrutura, desative **Usar mTLS**. O painel
passa a registar o webhook com um segredo na URL:

```
https://SEU-DOMINIO/api/payments/webhook/efi?hmac=SEU_SEGREDO&ignorar=
```

Cada notificação recebida é validada contra esse segredo (com comparação de tempo
constante, para não permitir descobri-lo por tentativa).

> ⚠️ **Limitação deste modo:** o segredo viaja na *query string*, que costuma ser
> registada em logs de proxy reverso e ferramentas de monitorização. É assim que
> a própria Efí documenta o mecanismo, mas é mais um motivo para preferir o mTLS
> sempre que possível.

O painel gera um segredo aleatório por omissão. Se o mTLS estiver desativado e
não houver segredo definido, o webhook **não é registado** — de propósito, para
não deixar o endpoint desprotegido.

---

## 5. Como funciona o fluxo

```
Usuário escolhe o plano
        ↓
Painel cria a cobrança:  POST /v2/cob   (pix_create_immediate_charge)
        ↓
Painel pede o QR Code:   GET  /v2/loc/{id}/qrcode
        ↓
Usuário paga
        ↓
Efí chama o webhook:     POST /api/payments/webhook/efi
        ↓
Painel valida (mTLS ou HMAC) → consulta a API → confirma → renova a subscrição
```

### Proteções aplicadas

- **Reconfirmação na API:** o painel nunca confia no conteúdo da notificação;
  consulta sempre `GET /v2/cob/{txid}` para saber o estado real. É por isso que
  **não é possível forjar um pagamento** com uma notificação falsa.
- **Idempotência:** se o webhook chegar repetido, pagamentos já marcados como
  `CONCLUIDA` são ignorados — não há risco de renovar duas vezes.
- **Evento de teste:** a Efí envia um `teste_webhook` ao registar o endereço; o
  painel responde corretamente para que o registro seja aceite.
- **Expiração:** as cobranças expiram 20 minutos após a criação.

---

## 6. Resolução de problemas

**"O provedor de pagamentos Efí não está disponível no momento"**
O cliente não foi inicializado. Verifique Client ID, Client Secret e se o
certificado existe no caminho indicado.

**"Certificado Efí não encontrado no caminho especificado"**
O arquivo não está onde o painel espera. Lembre-se de que o caminho é o de
**dentro do contêiner** (`/app/certs/...`), não o do seu computador. Confirme que
o volume `./certs:/app/certs` está no `docker-compose.yml`.

**"Erro interno ao comunicar. Verifique o certificado SSL"**
Normalmente indica certificado inválido, expirado ou do ambiente errado
(homologação a ser usado em produção, ou vice-versa). Confirme também que a opção
**Modo Sandbox** corresponde às credenciais que está a usar.

**Nos logs: "ALERTA CRÍTICO: a URL Base da Aplicação está configurada como local"**
O painel detetou `localhost` ou `127.0.0.1` no URL base. A Efí não consegue
enviar notificações para um endereço local — configure um domínio público em
Configurações → Geral. Sem isto, os pagamentos **nunca são confirmados
automaticamente**.

**Pagamento feito mas a subscrição não renovou**
1. Confirme que o URL Base é público e acessível pela internet.
2. Verifique nos logs se aparece `Webhook da Efí configurado com sucesso`.
3. Procure por `Pagamento ... confirmado via Webhook Efí` nos logs.
4. Se usa mTLS e nada chega, experimente o modo HMAC (secção 4).

**Nos logs: "Webhook Efí bloqueado: HMAC inválido ou ausente"**
O segredo configurado não corresponde ao que está registado na Efí. Grave as
configurações novamente para reregistar o webhook com o segredo atual.

**"Configuração Insegura: mTLS está desativado mas nenhum HMAC Secret foi definido"**
Defina um segredo no campo correspondente ou reative o mTLS. O painel recusa-se a
registar um webhook sem qualquer proteção.

---

## 7. Notas técnicas

- **API usada:** PIX API v2 da Efí (`/v2/cob`, `/v2/loc`, `/v2/webhook`).
- **SDK:** `efipay` (Python).
- **Autenticação:** OAuth2 com certificado de cliente (mTLS).
- **TLS:** a imagem Docker do painel define `SECLEVEL=1` no OpenSSL
  propositadamente — os certificados da Efí usam algoritmos que as versões mais
  recentes do Debian rejeitariam por omissão. Sem esse ajuste, o handshake TLS
  falharia.
- **Registro do webhook:** feito automaticamente sempre que as credenciais da Efí
  (ou o URL Base) são alteradas nas configurações. Não é preciso registar
  manualmente no painel da Efí.
- **Dados sensíveis:** a chave PIX e o segredo HMAC nunca são escritos em claro
  nos logs do painel.
