# Integração com a Gates2b (PIX)

Guia para configurar o gateway de pagamentos **Gates2b** no Painel Plex.

> **Migração da BPIX:** a Gates2b é a continuação do antigo gateway BPIX
> (`api.bpix.app` → `api.gates2b.com`). Se já tinha a BPIX configurada, as suas
> credenciais são migradas **automaticamente** no primeiro arranque após a
> atualização — não é preciso fazer nada. O antigo endereço de webhook
> (`/api/payments/webhook/bpix`) continua a funcionar, por isso integrações já
> registadas no painel do gateway não deixam de funcionar.

---

## 1. Obter a chave de API

1. Aceda a **https://painel.gates2b.com**
2. Vá a **Integração** (Integration)
3. Copie a sua **API Key**

A chave é usada no cabeçalho `Authorization: Bearer {chave}` de todos os pedidos.

> ⚠️ Trate a chave como uma palavra-passe. Quem a tiver pode criar cobranças na
> sua conta. No Painel Plex ela é guardada mascarada e nunca é devolvida em claro
> pela API de configurações.

---

## 2. Configurar o Webhook (obrigatório)

Sem webhook, o painel **não fica a saber** quando um pagamento é confirmado — as
renovações não seriam processadas automaticamente.

No painel da Gates2b, em **Integração → Webhook URL**, defina:

```
https://SEU-DOMINIO/api/payments/webhook/gates2b
```

Requisitos:

- Tem de ser acessível publicamente (HTTPS recomendado).
- Deve responder HTTP 2xx — o painel já o faz automaticamente.
- A Gates2b repete até 3 vezes em caso de falha; o painel trata repetições de
  forma idempotente (um pagamento já processado não é processado outra vez).

Este URL é configurado **uma única vez** ao nível da conta e serve para todas as
transações. Não é enviado a cada cobrança.

---

## 3. Configurar no Painel Plex

Vá a **Configurações → Pagamentos → Gates2b**:

| Campo | Descrição |
|---|---|
| **Ativar Gates2b** | Liga o gateway. Enquanto estiver desligado, nada é inicializado. |
| **Token de Autorização** | A API Key copiada no passo 1. |
| **Valor mínimo aceite (R$)** | Padrão `3.00`. Ver secção abaixo. |

Clique em **Testar Conexão** antes de gravar. O teste usa o endpoint dedicado
`/api-key/validate`, que confirma se a chave é válida e mostra a data de
expiração, se existir.

---

## 4. Valor mínimo

A Gates2b **recusa cobranças abaixo do valor mínimo** configurado na sua conta,
devolvendo HTTP 400. O mínimo por omissão é **R$ 3,00** — nenhuma conta tem taxa
inferior a esse valor.

O painel valida esse limite **antes** de contactar o gateway, para que o
utilizador receba uma mensagem clara em vez de um erro cru da API.

Isto é relevante em três situações:

- **Planos muito baratos** — se um plano custar menos que o mínimo, a cobrança
  falha sempre. Ajuste o preço ou use outro gateway para esse plano.
- **Cupões de desconto elevado** — um cupão que baixe o valor abaixo do mínimo
  impede a cobrança. Cupões de 100% são tratados à parte (renovação gratuita,
  sem contactar o gateway).
- **Upgrade proporcional (pro-rata)** — a diferença a pagar pode ser pequena.
  Configure o *Valor mínimo cobrável* do pro-rata (Configurações → Pagamentos)
  igual ou superior ao mínimo da Gates2b, para que valores muito baixos sejam
  concedidos gratuitamente em vez de gerarem uma cobrança recusada.

---

## 5. Como funciona o fluxo

```
Utilizador escolhe o plano
        ↓
Painel cria a cobrança:  POST https://api.gates2b.com/payments
        ↓
Gates2b devolve QR Code (imagem base64) + código copia-e-cola
        ↓
Utilizador paga
        ↓
Gates2b chama o webhook: POST /api/payments/webhook/gates2b
        ↓
Painel confirma o pagamento e renova a subscrição
```

### Dados enviados na cobrança

```json
{
  "amount": 30.00,
  "clientMode": "fillDataNow",
  "expire_at": "2026-08-24T12:20:00Z",
  "description": "Pagamento para utilizador - Renovacao Plex - 3 Telas",
  "name_client": "Nome do Utilizador",
  "email": "utilizador@exemplo.com"
}
```

O painel guarda o `transaction_pix_id` devolvido como identificador interno da
transação, e o `id` como referência externa.

### Payload recebido no webhook

O painel considera o pagamento confirmado quando recebe:

- `status` = `"Pagamento realizado"`, **ou**
- `international_status` = `"PAYMENT_RECEIVED"`

Outros estados possíveis: `WAITING_PAYMENT`, `PAID`, `DENIED`, `CANCELED`,
`REFUNDED`, `REFUND_IN_PROGRESS`, `REFUND_COMPLETED`, `REFUND_FAILED`,
`REFUND_CANCELLED`.

---

## 6. Resolução de problemas

**"O serviço de pagamento Gates2b não está configurado corretamente"**
O token está vazio ou o gateway está desativado. Verifique Configurações →
Pagamentos → Gates2b.

**Estado "OFFLINE" no painel de diagnóstico**
O gateway está ativado mas sem token válido. Use o botão *Testar Conexão* para
confirmar a chave.

**"Falha na autenticação: o Token parece ser inválido" (HTTP 401)**
A chave expirou ou foi revogada. Gere uma nova em painel.gates2b.com → Integração.

**Pagamento feito mas a subscrição não renovou**
Quase sempre é o webhook. Confirme que:
1. O URL está registado no painel da Gates2b.
2. O domínio é acessível a partir da internet (não `localhost` nem IP privado).
3. Nos logs do painel aparece `Pagamento ... confirmado via Webhook Gates2b`.

**"O valor mínimo aceite pelo gateway é de R$ 3,00"**
O valor da cobrança ficou abaixo do mínimo. Ver secção 4.

---

## 7. Nota sobre outros métodos de pagamento

A Gates2b também suporta **cartão de crédito** (`POST /payments/v1/card`) e
**criptomoedas** (`POST /payments/v2/crypto`, noutro domínio). Estes métodos
**não estão integrados** no painel — a integração cobre apenas PIX, tal como
acontecia com a BPIX.
