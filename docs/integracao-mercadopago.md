# Integração com o Mercado Pago (PIX)

Guia para configurar o gateway de pagamentos **Mercado Pago** no Painel Plex.

A integração usa o **Checkout Transparente** (API de Pagamentos), o que significa
que o QR Code do PIX é gerado e apresentado dentro do próprio painel — o
usuário nunca sai do seu site.

> **Nota:** apenas o **PIX** está integrado. O Mercado Pago também suporta cartão
> de crédito, boleto e outros métodos, mas não fazem parte desta integração.

---

## 1. Obter o Access Token

1. Aceda a **https://www.mercadopago.com.br/developers/panel**
2. Vá a **Suas integrações** e selecione (ou crie) a sua aplicação
3. No menu lateral, entre em **Credenciais de produção**
4. Copie o **Access Token**

O token começa por `APP_USR-...`

> ⚠️ **Credenciais de teste vs. produção**
> O painel de programador oferece dois conjuntos de credenciais. Use as de
> **produção** para receber pagamentos reais. As de teste só funcionam com
> usuários de teste e não movimentam dinheiro.

> 🔒 Trate o Access Token como uma senha: quem o tiver pode criar
> cobranças e consultar movimentos da sua conta. No Painel Plex ele é guardado
> mascarado e nunca é devolvido em claro pela API de configurações.

---

## 2. Configurar o Webhook

O webhook é **essencial**: é através dele que o painel fica a saber que um
pagamento foi confirmado e renova a subscrição automaticamente.

### 2.1. Registar o URL

1. No painel do Mercado Pago, entre na sua aplicação
2. Vá a **Webhooks** → **Configurar notificações**
3. No campo **URL de produção**, coloque:

```
https://SEU-DOMINIO/api/payments/webhook/mercadopago
```

4. Em **Eventos**, selecione apenas **Pagamentos** (`payment`)
5. Guarde

### 2.2. Copiar a assinatura secreta (recomendado)

Depois de guardar, o Mercado Pago mostra uma **Assinatura secreta**
(*clave secreta* / *secret key*). Copie-a — vai precisar dela no passo seguinte.

Esta chave permite ao painel confirmar que as notificações vêm mesmo do Mercado
Pago, e não de alguém a tentar abusar do endpoint.

---

## 3. Configurar no Painel Plex

Vá a **Configurações → Pagamentos → Mercado Pago**:

| Campo | Descrição |
|---|---|
| **Ativar Mercado Pago** | Liga o gateway. Enquanto estiver desligado, nada é inicializado. |
| **Access Token** | O token copiado no passo 1. |
| **Chave secreta do Webhook** | A assinatura secreta do passo 2.2. Opcional, mas recomendada. |
| **Valor mínimo aceite (R$)** | Padrão `1.00`. Ver secção 5. |

Clique em **Testar Conexão** antes de gravar.

---

## 4. Segurança do Webhook

O painel aplica **duas camadas** de proteção nas notificações recebidas:

### Camada 1 — Validação da assinatura

Se a *Chave secreta do Webhook* estiver configurada, cada notificação é validada
por HMAC-SHA256 usando o algoritmo oficial do Mercado Pago:

```
manifesto = id:<data.id>;request-id:<x-request-id>;ts:<ts>;
assinatura = HMAC_SHA256(manifesto, chave_secreta)
```

Notificações sem assinatura válida são rejeitadas com HTTP 401.

Se a chave **não** estiver configurada, esta validação é ignorada (para não
quebrar instalações que ainda não a definiram) — mas a camada 2 continua ativa.

### Camada 2 — Reconfirmação na API

O painel **nunca** confia no conteúdo da notificação. Ao receber um webhook, ele
consulta a API do Mercado Pago para saber o estado real do pagamento antes de
renovar seja o que for.

Isto significa que **é impossível forjar um pagamento** enviando uma notificação
falsa. A camada 1 existe sobretudo para evitar que alguém sobrecarregue a sua
conta com consultas desnecessárias à API.

---

## 5. Valor mínimo

O Mercado Pago recusa transações abaixo de um valor mínimo. O painel valida esse
limite **antes** de contactar a API, para dar uma mensagem clara em vez de um
erro cru do gateway.

Isto é relevante em três situações:

- **Planos muito baratos** — se um plano custar menos que o mínimo, a cobrança
  falha sempre.
- **Cupões de desconto elevado** — um cupão que baixe o valor abaixo do mínimo
  impede a cobrança. Cupões de 100% são tratados à parte (renovação gratuita,
  sem contactar o gateway).
- **Upgrade proporcional (pro-rata)** — a diferença a pagar pode ser de cêntimos.
  Configure o *Valor mínimo cobrável* do pro-rata igual ou superior a este valor,
  para que diferenças pequenas sejam concedidas gratuitamente em vez de gerarem
  uma cobrança recusada.

---

## 6. Como funciona o fluxo

```
Usuário escolhe o plano
        ↓
Painel cria o pagamento:  POST https://api.mercadopago.com/v1/payments
   (payment_method_id: "pix", com chave de idempotência)
        ↓
Mercado Pago devolve QR Code (base64) + código copia-e-cola
        ↓
Usuário paga
        ↓
Mercado Pago chama o webhook: POST /api/payments/webhook/mercadopago
        ↓
Painel valida a assinatura → consulta a API → confirma → renova a subscrição
```

### Idempotência

Cada cobrança é criada com um cabeçalho `x-idempotency-key` derivado da própria
referência do pedido. Se um pedido for repetido (por exemplo, após um timeout de
rede em que a cobrança pode já ter sido criada), o Mercado Pago devolve a
cobrança **já existente** em vez de criar uma segunda — evitando cobranças
duplicadas ao mesmo cliente.

### Estados tratados

| Estado no Mercado Pago | O que o painel faz |
|---|---|
| `approved` | Confirma o pagamento e renova a subscrição |
| `refunded` | Marca como revertido e **notifica o administrador** |
| `charged_back` | Marca como revertido e **notifica o administrador** |
| `cancelled` | Marca como revertido e **notifica o administrador** |
| `pending`, `in_process` | Aguarda (nenhuma ação) |

> **Importante:** em reembolsos e chargebacks o acesso **não é revogado
> automaticamente**. Pode tratar-se de um reembolso parcial ou acordado, por isso
> a decisão fica com o administrador. Receberá uma notificação no painel a
> indicar o usuário e o valor.

---

## 7. Resolução de problemas

**"Credenciais do Mercado Pago não configuradas"**
O Access Token está vazio ou o gateway está desativado. Verifique
Configurações → Pagamentos → Mercado Pago.

**Estado "OFFLINE" no diagnóstico do painel**
O gateway está ativado mas o SDK não foi inicializado — normalmente Access Token
inválido. Confirme que copiou o token de **produção**, completo.

**Pagamento feito mas a subscrição não renovou**
Quase sempre é o webhook. Verifique:
1. O URL está registado no painel do Mercado Pago (secção 2.1).
2. O evento **Pagamentos** está selecionado.
3. O domínio é acessível a partir da internet (não `localhost` nem IP privado).
4. Nos logs do painel aparece `Pagamento ... confirmado via Webhook Mercado Pago`.

**Nos logs aparece "Webhook do Mercado Pago rejeitado (Assinatura inválida)"**
A *Chave secreta do Webhook* configurada no painel não corresponde à do Mercado
Pago. Copie-a novamente da secção Webhooks da sua aplicação. Se tiver gerado uma
chave nova no Mercado Pago, é preciso atualizar aqui também.

**"O valor mínimo aceite pelo Mercado Pago é de R$ X"**
O valor da cobrança ficou abaixo do mínimo. Ver secção 5.

**Erro ao criar cobrança com mensagem específica do gateway**
O painel mostra agora o motivo real devolvido pelo Mercado Pago (por exemplo,
CPF inválido ou conta sem PIX ativo), em vez de uma mensagem genérica. Use essa
descrição para identificar o problema na sua conta.

---

## 8. Notas técnicas

- **API usada:** Checkout Transparente (`POST /v1/payments`), com
  `payment_method_id: "pix"`.
- **SDK:** `mercadopago` (Python), versão 3.x.
- **Expiração da cobrança:** 20 minutos após a criação.
- **Descritor na fatura:** derivado do título da aplicação, limitado a 22
  caracteres alfanuméricos (limite do Mercado Pago).
- **Dados do pagador:** email e nome são enviados para melhorar a análise
  antifraude e a taxa de aprovação.
