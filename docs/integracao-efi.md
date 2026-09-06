# Integração com a Efí Bank (PIX)

Guia para configurar o gateway de pagamentos **Efí Bank** (antiga Gerencianet) no
Painel Plex.

A Efí é a integração mais completa do painel: além de gerar o QR Code do PIX
dentro do próprio site, o painel **regista o webhook automaticamente** na conta
da Efí — não é preciso colar URLs manualmente no painel deles.

> **Nota:** apenas o **PIX** está integrado. A Efí também suporta boleto, cartão
> e outros métodos, que não fazem parte desta integração.

> **Usa Cloudflare à frente do painel?** Leia a [secção 5](#5-rodar-atrás-da-cloudflare)
> **antes** de gravar as configurações. Atrás do proxy da Cloudflare o modo mTLS
> **não funciona** e, pior, deixa o webhook sem qualquer validação.

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

> **Este certificado é do lado de saída** (painel → API da Efí). Ele não tem
> nada a ver com o certificado que a Efí apresenta ao chamar o seu webhook — ver
> [secção 5.1](#51-duas-direções-que-se-confundem-facilmente).

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
| **Usar mTLS** | Ativado por omissão — mas **desative se usar Cloudflare**. Ver secções 4 e 5. |
| **Segredo HMAC do Webhook** | Obrigatório quando o mTLS está desativado. |

Também é obrigatório ter o **URL Base da Aplicação** preenchido em
**Configurações → Geral** — é a partir dele que o painel constrói o endereço do
webhook.

Clique em **Testar Conexão** antes de gravar.

> **Quando é que o webhook é registado?** Sempre que gravar as configurações e
> algum destes campos tiver mudado: `Client ID`, `Client Secret`, `Certificado`,
> `Sandbox`, `Chave PIX`, `Ativar Efí`, `Usar mTLS`, `Segredo HMAC` ou
> `URL Base da Aplicação`. Se nada mudou, o painel **não** volta a chamar a Efí —
> para forçar um novo registo, altere um destes campos (ou gere um novo segredo
> HMAC) e grave.

---

## 4. Webhook e segurança (mTLS vs. HMAC)

Ao gravar as configurações, o painel **regista automaticamente** o webhook na
Efí, apontando para:

```
https://SEU-DOMINIO/api/payments/webhook/efi
```

Existem dois modos de proteger essa comunicação.

### Modo mTLS (padrão de fábrica)

A Efí apresenta um certificado de cliente e a conexão é autenticada mutuamente.
É o modo mais seguro **quando o TLS termina no seu próprio servidor**.

**Requisito absoluto:** o TLS da porta 443 tem de ser terminado por um servidor
que você controla e que consiga exigir e validar o certificado de cliente da
Efí. Isto elimina, na prática:

- **Cloudflare com proxy ativo (nuvem laranja)** — ver [secção 5](#5-rodar-atrás-da-cloudflare)
- **Cloudflare Tunnel (`cloudflared`)** — idem
- Qualquer outra CDN/WAF que termine o TLS (Fastly, Akamai, etc.)

Nestes cenários **use o modo HMAC**.

### Modo HMAC (obrigatório atrás de CDN/proxy que termina TLS)

Desative **Usar mTLS**. O painel passa a registar o webhook com um segredo na
URL:

```
https://SEU-DOMINIO/api/payments/webhook/efi?hmac=SEU_SEGREDO&ignorar=
```

Cada notificação recebida é validada contra esse segredo (com comparação de tempo
constante, para não permitir descobri-lo por tentativa).

> **Para que serve o `&ignorar=` no fim?** A Efí acrescenta `/pix` ao fim da URL
> registada no momento de disparar a notificação real. Se registássemos
> `.../webhook/efi`, a notificação chegaria a `.../webhook/efi/pix` — uma rota que
> não existe. Terminando a URL em `?ignorar=`, o `/pix` acrescentado cai dentro da
> *query string* (`?ignorar=/pix`) e o caminho fica intacto. É o truque
> documentado pela própria Efí para não ter de manter duas rotas.

> ⚠️ **Limitação deste modo:** o segredo viaja na *query string*, que costuma ser
> registada em logs de proxy reverso, em ferramentas de monitorização e — se usar
> Cloudflare — nos *Security Events* e no Logpush. É assim que a própria Efí
> documenta o mecanismo. Trate o segredo como algo que vaza com facilidade: veja
> em [5.6](#56-defesa-em-profundidade-não-dependa-só-do-hmac) como não depender
> apenas dele.

O painel gera um segredo aleatório por omissão. Se o mTLS estiver desativado e
não houver segredo definido, o webhook **não é registado** — de propósito, para
não deixar o endpoint desprotegido.

---

## 5. Rodar atrás da Cloudflare

Esta secção existe porque a combinação "Efí + Cloudflare" falha de forma
**silenciosa**: a cobrança é criada, o QR Code aparece, o cliente paga — e a
subscrição nunca renova, sem nenhum erro visível no painel. Abaixo está o porquê
e o que configurar.

### 5.1. Duas direções que se confundem facilmente

| Direção | O que acontece | A Cloudflare afeta? |
|---|---|---|
| **Saída:** painel → API da Efí (`/v2/cob`, `/v2/loc`, `/v2/webhook`) | O painel autentica-se com o certificado `.pem` da secção 2. | **Não.** É tráfego de saída do seu contêiner; a nuvem laranja só trata do que **entra**. |
| **Entrada:** Efí → painel (`POST /api/payments/webhook/efi`) | A Efí liga-se ao seu domínio para avisar do pagamento. | **Sim.** É aqui que tudo quebra. |

Quase todo o suporte sobre "Efí não funciona" cai na segunda linha. Se o QR Code
é gerado, a direção de saída está boa — o problema é a entrada.

> **Exceção da direção de saída:** se usar Cloudflare Zero Trust / WARP com
> *inspeção TLS* no servidor onde o painel corre, o proxy da Gateway substitui o
> certificado e o handshake mTLS com a Efí **quebra** (sintoma: `Erro interno ao
> comunicar com o servidor de pagamentos. Verifique o certificado SSL`). Adicione
> `*.api.efipay.com.br` / `*.gerencianet.com.br` à lista *Do Not Inspect*.

### 5.2. Por que o mTLS é impossível atrás do proxy laranja

Quando o registo do webhook é feito com mTLS, a Efí faz uma validação do seu
endpoint antes de aceitar o registo: envia um pedido **sem** o certificado
(esperando ser recusada) e outro **com** o certificado (esperando `200`). Ou
seja, o seu servidor tem de conseguir **ver, exigir e validar** o certificado de
cliente da Efí.

Com a nuvem laranja ativa, o handshake TLS termina no *edge* da Cloudflare, não
no seu servidor. A Cloudflare abre uma **segunda** conexão, sua, até à origem. O
certificado de cliente da Efí morre no *edge* e a sua aplicação recebe um pedido
HTTP comum, indistinguível de qualquer outro.

```
    SEM Cloudflare (mTLS funciona)
    Efí ──── TLS mútuo ────────────────────────────────► Nginx/painel
             (cert de cliente chega e é validado)

    COM Cloudflare laranja (mTLS impossível)
    Efí ──── TLS ────► Edge Cloudflare ──── TLS novo ───► Nginx/painel
             (cert morre aqui) ▲                          (não vê certificado)
                               └─ validar aqui exige mTLS com CA própria
```

Para validar o certificado da Efí **no edge**, seria preciso carregar a CA da
Efí como *Bring Your Own CA* do mTLS da Cloudflare — recurso **exclusivo de
contas Enterprise** (limite de 5 CAs por conta). A CA gerida da Cloudflare, essa
sim disponível em todos os planos, não serve: a Efí não emite certificados por
ela.

**Cloudflare Tunnel (`cloudflared`) tem o mesmo limite**, e por um motivo ainda
mais estrutural: o túnel é uma ligação **de saída** da sua máquina para a
Cloudflare. Não existe nenhum *listener* de entrada na origem a quem apresentar
um certificado de cliente.

**Conclusão prática:** com Cloudflare à frente (laranja ou túnel), o único modo
viável é o **HMAC**.

### 5.3. O erro mais perigoso: deixar o mTLS ligado atrás da Cloudflare

Vale a pena ser explícito, porque a configuração de fábrica (`Usar mTLS`
ativado) é exatamente a errada neste cenário.

O painel só valida o HMAC **quando o mTLS está desativado** — a lógica assume que
o mTLS já foi validado uma camada acima. Atrás da Cloudflare essa camada não
existe. O resultado é o pior dos dois mundos:

- a rota `/api/payments/webhook/efi` fica **sem nenhuma autenticação**;
- ela é pública e está **isenta de rate limit** (`@limiter.exempt`), por desenho,
  para não descartar notificações legítimas em picos;
- cada `POST` com um `txid` faz o painel chamar a API da Efí para reconfirmar.

O que **não** é possível: forjar um pagamento. O painel nunca confia no corpo da
notificação — consulta sempre `GET /v2/cob/{txid}` e só renova se a Efí disser
`CONCLUIDA`; e pagamentos já `CONCLUIDA` são ignorados (idempotência).

O que **é** possível: qualquer pessoa na internet disparar pedidos em massa nessa
rota e consumir o *rate limit* da sua conta na Efí, derrubando a confirmação
automática de pagamentos reais.

> **Regra:** atrás da Cloudflare, **desative `Usar mTLS` e defina um segredo
> HMAC**. Não é uma degradação de segurança em relação ao mTLS — o mTLS ali já
> não estava a acontecer.

### 5.4. Escolher a topologia

O painel constrói o URL do webhook a partir do **URL Base da Aplicação**, que é o
mesmo endereço usado nos convites, links curtos e notificações. Não é possível
pôr o webhook num subdomínio separado sem mudar o painel inteiro para lá. Logo, a
escolha é uma só, para todo o painel:

| Topologia | mTLS real? | Proteção da Cloudflare? | Quando escolher |
|---|---|---|---|
| **A.** DNS-only (nuvem cinzenta) + Nginx próprio com a CA da Efí | ✅ | ❌ (IP exposto) | Quer o mTLS de verdade e sabe administrar Nginx/TLS. |
| **B.** Nuvem laranja + HMAC + regras WAF | ❌ | ✅ | **Recomendado para a maioria.** |
| **C.** Cloudflare Tunnel + HMAC | ❌ | ✅ (e sem abrir portas) | Não tem IP público / está atrás de CGNAT. |

Para a topologia **A**, a Efí mantém um servidor Nginx de referência já preparado
para o mTLS dos webhooks: [`efipay/mtls-webhook`](https://github.com/efipay/mtls-webhook).
Ele fica à frente do painel e repassa o pedido só depois de validar o certificado.
Nesse caso mantenha `Usar mTLS` **ativado** e lembre-se de tratar o sufixo `/pix`
no `proxy_pass`.

O resto desta secção cobre as topologias **B** e **C**.

### 5.5. Configuração da Cloudflare, item a item

#### 5.5.1. A porta tem de ser proxiável

O painel escuta na `5000` por omissão — e a `5000` **não é proxiável** pela
Cloudflare. O proxy só aceita:

- **HTTPS:** 443, 2053, 2083, 2087, 2096, 8443
- **HTTP:** 80, 8080, 8880, 2052, 2082, 2086, 2095

Além disso, a Efí espera um webhook em **HTTPS na 443**. Não publique o painel
numa porta alternativa e sim:

- ponha um proxy reverso (Nginx, Traefik, NPM, Caddy) a escutar na 443 e a
  encaminhar para `127.0.0.1:5000`; **ou**
- use o Cloudflare Tunnel (secção 5.7), que dispensa portas abertas.

#### 5.5.2. SSL/TLS: modo **Full (strict)**

Em **SSL/TLS → Overview**:

- **Flexible** — ❌ a Cloudflare fala HTTP com a origem. Combinado com um
  "Always Use HTTPS" na origem, gera *redirect loop* (`ERR_TOO_MANY_REDIRECTS`) e
  a Efí desiste do registo.
- **Full** — aceitável, mas não valida o certificado da origem.
- **Full (strict)** — ✅ use este. Precisa de um certificado válido na origem
  (Let's Encrypt ou um *Cloudflare Origin Certificate*).

Se der **erro 526**, é a origem com certificado inválido/expirado para este modo.

#### 5.5.3. Bot Fight Mode — a causa nº 1 de webhooks perdidos

O cliente HTTP da Efí não executa JavaScript e não se parece com um navegador. O
**Bot Fight Mode** do plano *Free* apanha-o e devolve um desafio/`403`.

> ⚠️ **No plano Free, o Bot Fight Mode corre fora do motor de regras.** Nenhuma
> regra WAF de *skip*, Page Rule ou IP Access Rule consegue isentar um caminho.
> A única solução no Free é **desligar o Bot Fight Mode** para a zona inteira.

Nos planos **Pro ou superior**, o *Super* Bot Fight Mode já é isentável por uma
regra WAF com ação **Skip** (componente "Super Bot Fight Mode") no caminho do
webhook — ver 5.5.5.

#### 5.5.4. Browser Integrity Check e Security Level

Em **Security → Settings**, o *Browser Integrity Check* rejeita pedidos com
cabeçalhos/User-Agent "não-navegador" — outra armadilha para clientes de webhook.
Não precisa de o desligar na zona toda: crie uma **Configuration Rule** que o
desative apenas no caminho do webhook (expressão em 5.5.5), ou inclua o
componente no *skip* da regra WAF.

O mesmo vale para um *Security Level* alto (*High* / *I'm Under Attack*), que
apresenta desafios a IPs com má reputação.

#### 5.5.5. Regra WAF de isenção para o caminho do webhook

Em **Security → WAF → Custom rules**, crie uma regra com ação **Skip**:

**Expressão:**

```
(http.request.uri.path eq "/api/payments/webhook/efi")
```

Ou, mais apertada, se quiser aceitar apenas a origem da Efí:

```
(http.request.uri.path eq "/api/payments/webhook/efi" and ip.src eq 34.193.116.226)
```

**Componentes a saltar:**

- ☑ All managed rules (WAF Managed Rules)
- ☑ All rate limiting rules
- ☑ Browser Integrity Check
- ☑ Security Level
- ☑ Super Bot Fight Mode *(Pro+; no Free este item não existe — ver 5.5.3)*

> **Sobre o IP `34.193.116.226`:** é o endereço que a Efí divulga como origem das
> notificações e que ela própria recomenda combinar com o HMAC. **Confirme-o com
> o suporte da Efí antes de o usar como filtro exclusivo** — endereços de saída
> mudam sem aviso e um IP desatualizado transforma-se num bloqueio total e
> silencioso dos seus pagamentos. Se não quiser esse risco, use a expressão sem o
> `ip.src` e mantenha o HMAC como validação.

**Falso positivo a conhecer:** as notificações reais chegam com
`?hmac=...&ignorar=/pix` na query string. Alguns conjuntos de regras geridas
tratam um `/` dentro de um parâmetro como tentativa de *path traversal* e
bloqueiam. É mais um motivo para a regra de *skip* acima.

#### 5.5.6. Cache

A rota aceita `GET` (a Efí usa-o em algumas verificações). Uma *Cache Rule* mal
desenhada pode servir uma resposta em cache em vez de entregar a notificação. Em
**Caching → Cache Rules**, garanta **Bypass cache** para:

```
(starts_with(http.request.uri.path, "/api/"))
```

A configuração de fábrica da Cloudflare não guarda `/api/...` em cache — isto é
apenas para não ser mordido por uma regra sua.

#### 5.5.7. Cloudflare Access / Zero Trust

Se protegeu o painel com **Cloudflare Access**, a Efí recebe a página de login e
a notificação nunca chega. Crie uma política de **Bypass** para
`/api/payments/webhook/efi` (idealmente com o critério de IP da Efí), ou não
coloque a aplicação por trás do Access.

#### 5.5.8. Timeout (erro 524)

Nos planos Free/Pro/Business, a Cloudflare desiste da origem ao fim de ~100
segundos. O Gunicorn do painel tem `--timeout 120` — ou seja, a Cloudflare
desiste **primeiro** e a Efí vê um `524`. O tratamento do webhook é rápido (uma
consulta à API da Efí), por isso isto só aparece se o painel estiver em
sobrecarga ou se a API da Efí estiver lenta. Se acontecer, é sintoma de saturação
da origem, não de configuração da Cloudflare.

### 5.6. Defesa em profundidade: não dependa só do HMAC

Com o mTLS fora de jogo, o segredo na query string é a única credencial — e ela
fica escrita nos *Security Events* e no Logpush da Cloudflare, além dos logs do
seu proxy reverso. Some duas camadas simples:

**1. Feche a origem ao mundo.** Se o seu IP público continuar a aceitar a 443 de
qualquer lugar, toda a configuração WAF acima é contornável indo direto ao IP.
Aceite a 443 apenas dos ranges da Cloudflare:

```bash
# ufw — permite 443 apenas a partir da Cloudflare
for ip in $(curl -s https://www.cloudflare.com/ips-v4) $(curl -s https://www.cloudflare.com/ips-v6); do
  ufw allow from "$ip" to any port 443 proto tcp comment 'Cloudflare'
done
ufw deny 443/tcp
```

Com Cloudflare Tunnel isto é automático: não há porta aberta nenhuma.

**2. Bloqueie no edge o que não traz o segredo** (opcional). Uma regra WAF de
**Block** evita que ruído chegue sequer à aplicação:

```
(http.request.uri.path eq "/api/payments/webhook/efi" and not http.request.uri.query contains "hmac=SEU_SEGREDO")
```

Pesa a favor: filtra tudo no edge. Pesa contra: passa a existir uma segunda cópia
do segredo (na regra). Se adotar, **rode o segredo** ao mudar de mãos o acesso à
conta Cloudflare — gere um novo em Configurações → Pagamentos → Efí, grave (o
painel reregista o webhook) e atualize a regra.

### 5.7. Cloudflare Tunnel (`cloudflared`)

É a topologia mais simples quando não há IP público ou não se quer abrir portas —
e resolve de uma vez o problema da porta 5000 não ser proxiável.

```yaml
# ~/.cloudflared/config.yml
tunnel: <ID-DO-TUNEL>
credentials-file: /root/.cloudflared/<ID-DO-TUNEL>.json

ingress:
  - hostname: painel.seudominio.com
    service: http://127.0.0.1:5000
  - service: http_status:404
```

Ou, em Docker Compose, ao lado do painel:

```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel --no-autoupdate run --token ${CF_TUNNEL_TOKEN}
    # o serviço do painel fica acessível como http://painel:5000 na rede do compose
```

Notas:

- **Continua sem mTLS** (secção 5.2): mantenha o modo HMAC.
- Todas as regras de 5.5.3 a 5.5.7 continuam a aplicar-se — o tráfego passa pelo
  mesmo *edge*.
- O painel usa **WebSocket** (Socket.IO) para os dados em tempo real; o Tunnel
  suporta-o sem configuração extra. Se o dashboard ficar parado, procure algum
  proxy intermédio a cortar o `Upgrade`.
- O `Authenticated Origin Pulls` da Cloudflare **não** é compatível com o Tunnel
  (não há *listener* de entrada) — não tente usá-lo como substituto do mTLS.

### 5.8. IP real do visitante (ProxyFix)

O painel já corre atrás de um `ProxyFix(x_for=1, x_proto=1, x_host=1, x_prefix=1)`,
o que está **correto quando a Cloudflare fala diretamente com o painel** (caso do
Tunnel, ou de um proxy que não mexe no `X-Forwarded-For`).

Se houver **um proxy reverso entre a Cloudflare e o painel** (Nginx, Nginx Proxy
Manager, Traefik) com a configuração habitual
`X-Forwarded-For $proxy_add_x_forwarded_for`, a cadeia ganha um salto e o
`x_for=1` passa a ler o IP do *edge* da Cloudflare em vez do IP real. Consequências:

- todos os utilizadores partilham o mesmo balde de *rate limit*;
- o log `Webhook Efí bloqueado: HMAC inválido ou ausente. IP: ...` aponta para a
  Cloudflare, inutilizando o diagnóstico.

A correção **não exige mexer no código**: faça o proxy escrever o IP real que a
Cloudflare já lhe entrega no cabeçalho `CF-Connecting-IP`.

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Substitui a cadeia por um único IP — o do visitante real.
    # Mantém o ProxyFix(x_for=1) do painel correto.
    proxy_set_header X-Forwarded-For   $http_cf_connecting_ip;

    # WebSocket (Socket.IO)
    proxy_http_version 1.1;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

No **Nginx Proxy Manager**, cole o bloco `proxy_set_header` na aba *Advanced* do
host e ative *Websockets Support*.

> Para isto ser seguro, a origem tem de aceitar ligações **apenas** da Cloudflare
> (secção 5.6) — caso contrário qualquer pessoa forja o `CF-Connecting-IP`.

### 5.9. Testar sem esperar por um pagamento real

Corra isto **de fora** do servidor (a partir do seu computador), imitando a
notificação de teste da Efí:

```bash
# Modo HMAC — substitua o domínio e o segredo
curl -i -X POST \
  "https://painel.seudominio.com/api/payments/webhook/efi?hmac=SEU_SEGREDO&ignorar=/pix" \
  -H "Content-Type: application/json" \
  -d '{"evento":"teste_webhook"}'
```

**Esperado:** `HTTP/2 200` e `{"status":"received"}`.

| O que recebeu | O que significa |
|---|---|
| `200 {"status":"received"}` | ✅ O caminho está livre até à aplicação. |
| `403` com HTML / página de desafio | Cloudflare a bloquear: Bot Fight Mode (5.5.3), BIC ou WAF (5.5.4/5.5.5). |
| `403 {"message":"Invalid HMAC"}` | Chegou à aplicação — só o segredo é que está errado. Bom sinal para o caminho. |
| Página de login do Cloudflare Access | Falta a política de bypass (5.5.7). |
| `502` / `521` / `522` | A Cloudflare não alcança a origem (contêiner parado, porta errada, firewall). |
| `526` | SSL/TLS em *Full (strict)* com certificado inválido na origem (5.5.2). |
| `524` | A origem demorou mais de ~100 s (5.5.8). |
| Loop de redirecionamento | Modo *Flexible* (5.5.2). |

Confirme depois o mesmo teste **sem** o `hmac` — deve devolver `403 Invalid HMAC`.
Se devolver `200`, o `Usar mTLS` continua ligado: volte a 5.3.

Em paralelo, veja **Cloudflare → Security → Events** filtrando por
`URI Path = /api/payments/webhook/efi`. Se o teste falhou, o evento ali diz
exatamente qual serviço bloqueou (*Bot Fight Mode*, *Managed Rules*, *Browser
Integrity Check*…).

E nos logs do painel, procure por:

```
Webhook da Efí configurado com sucesso.        ← registo aceite pela Efí
Webhook Efí: Evento de validação concluído.    ← a Efí chegou até à aplicação
Pagamento ... confirmado via Webhook Efí       ← fluxo completo
```

---

## 6. Como funciona o fluxo

```
Usuário escolhe o plano
        ↓
Painel cria a cobrança:  POST /v2/cob   (pix_create_immediate_charge)
        ↓
Painel pede o QR Code:   GET  /v2/loc/{id}/qrcode
        ↓
Usuário paga
        ↓
Efí chama o webhook:     POST /api/payments/webhook/efi?...&ignorar=/pix
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

> Estas proteções são o que impede um prejuízo financeiro mesmo com o endpoint
> exposto. Não substituem a autenticação: ver [5.3](#53-o-erro-mais-perigoso-deixar-o-mtls-ligado-atrás-da-cloudflare).

---

## 7. Resolução de problemas

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
**Modo Sandbox** corresponde às credenciais que está a usar. Se o servidor passa o
tráfego de saída por uma inspeção TLS (Cloudflare Gateway/WARP, antivírus
corporativo), ela quebra o mTLS de saída — ver [5.1](#51-duas-direções-que-se-confundem-facilmente).

**Nos logs: "ALERTA CRÍTICO: a URL Base da Aplicação está configurada como local"**
O painel detetou `localhost` ou `127.0.0.1` no URL base. A Efí não consegue
enviar notificações para um endereço local — configure um domínio público em
Configurações → Geral. Sem isto, os pagamentos **nunca são confirmados
automaticamente**.

**Nos logs: "Falha ao tentar registar o Webhook na Efí"**
A Efí recusou o registo. Se usa Cloudflare e o `Usar mTLS` está ligado, esta é a
falha esperada — a validação de certificado da Efí não passa pelo *edge*. Desative
o mTLS e defina um segredo HMAC ([5.3](#53-o-erro-mais-perigoso-deixar-o-mtls-ligado-atrás-da-cloudflare)).
Fora esse caso, confirme os escopos de *Webhooks* na aplicação da Efí e que o
domínio responde publicamente em HTTPS na porta 443.

**Pagamento feito mas a subscrição não renovou**
1. Confirme que o URL Base é público e acessível pela internet.
2. Verifique nos logs se aparece `Webhook da Efí configurado com sucesso`.
3. Procure por `Pagamento ... confirmado via Webhook Efí` nos logs.
4. **Se usa Cloudflare:** corra o teste da [secção 5.9](#59-testar-sem-esperar-por-um-pagamento-real)
   e consulte *Security → Events*. Um `403` ali é a resposta.
5. Se não usa Cloudflare e o mTLS não entrega nada, experimente o modo HMAC
   (secção 4).

**Nos logs: "Webhook Efí bloqueado: HMAC inválido ou ausente"**
O segredo configurado não corresponde ao que está registado na Efí. Grave as
configurações novamente para reregistar o webhook com o segredo atual. Note que o
painel só reregista se algum campo relevante mudar (ver nota na secção 3).

**"Configuração Insegura: mTLS está desativado mas nenhum HMAC Secret foi definido"**
Defina um segredo no campo correspondente ou reative o mTLS. O painel recusa-se a
registar um webhook sem qualquer proteção.

**O webhook registou-se, mas nunca chega nada (sem erros nos logs)**
Sintoma clássico de bloqueio no *edge*. Pela ordem de probabilidade com
Cloudflare: Bot Fight Mode ([5.5.3](#553-bot-fight-mode--a-causa-nº-1-de-webhooks-perdidos)),
Cloudflare Access sem bypass ([5.5.7](#557-cloudflare-access--zero-trust)),
Browser Integrity Check ([5.5.4](#554-browser-integrity-check-e-security-level))
e regras geridas do WAF ([5.5.5](#555-regra-waf-de-isenção-para-o-caminho-do-webhook)).

**Todos os IPs nos logs são da Cloudflare**
Cadeia de `X-Forwarded-For` com um salto a mais. Ver [5.8](#58-ip-real-do-visitante-proxyfix).

---

## 8. Notas técnicas

- **API usada:** PIX API v2 da Efí (`/v2/cob`, `/v2/loc`, `/v2/webhook`).
- **SDK:** `efipay` (Python).
- **Autenticação (saída):** OAuth2 com certificado de cliente (mTLS).
- **Autenticação (entrada, webhook):** mTLS validado pelo seu proxy **ou** HMAC na
  query string — nunca ambos, e nunca nenhum.
- **TLS:** a imagem Docker do painel define `SECLEVEL=1` no OpenSSL
  propositadamente — os certificados da Efí usam algoritmos que as versões mais
  recentes do Debian rejeitariam por omissão. Sem esse ajuste, o handshake TLS
  falharia.
- **Sufixo `/pix`:** a Efí acrescenta `/pix` ao fim da URL registada ao disparar a
  notificação. O painel termina a URL em `?ignorar=` para que o sufixo caia na
  query string e o caminho da rota se mantenha.
- **Registro do webhook:** feito automaticamente quando as credenciais da Efí, o
  modo de segurança ou o URL Base mudam nas configurações. Não é preciso registar
  manualmente no painel da Efí.
- **Rate limit:** a rota do webhook é isenta (`@limiter.exempt`) para não descartar
  notificações legítimas em picos. Isso torna a autenticação (HMAC ou mTLS) e o
  filtro no edge ainda mais importantes.
- **Dados sensíveis:** a chave PIX e o segredo HMAC nunca são escritos em claro
  nos logs do painel. Mas o segredo HMAC **aparece** nos logs de quem estiver à
  frente (proxy reverso, Security Events e Logpush da Cloudflare).

### Referências externas

- [Webhooks — Documentação Técnica da API Efí](https://dev.efipay.com.br/docs/api-pix/webhooks/)
- [`efipay/mtls-webhook` — Nginx de referência para mTLS](https://github.com/efipay/mtls-webhook)
- [Portas de rede suportadas pelo proxy da Cloudflare](https://developers.cloudflare.com/fundamentals/reference/network-ports/)
- [Bring your own CA for mTLS (Enterprise) — Cloudflare](https://developers.cloudflare.com/ssl/client-certificates/byo-ca/)
- [Authenticated Origin Pulls e a incompatibilidade com o Tunnel — Cloudflare](https://developers.cloudflare.com/ssl/origin-configuration/authenticated-origin-pull/)
- [Erro 524 — Cloudflare](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524)
