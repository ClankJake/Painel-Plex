# API de Convites para Bots — Vínculo automático de Telegram ID

Endpoint dedicado a integrações automatizadas (bots do Telegram, scripts, n8n,
etc.) que precisam de gerar convites já vinculados a um usuário do Telegram.

Quando o convite é resgatado, o perfil do novo usuário é criado **já com o
Telegram ID associado**, dispensando qualquer vinculação manual posterior.

---

## Autenticação

A rota não usa sessão de navegador (um bot não tem uma). É protegida por uma
chave de API enviada num cabeçalho:

```
X-API-Key: SUA_CHAVE
```

ou, em alternativa:

```
Authorization: Bearer SUA_CHAVE
```

### Onde encontrar a chave

No painel, vá a **Configurações → Geral → Chave de API (Integrações)**:

1. Clique no ícone do **olho** para revelar a chave
2. Use o botão **Copiar**

A chave é gerada automaticamente na primeira execução. Se a sua instalação for
antiga e ainda não tiver uma, ela é criada na primeira vez que abrir essa página.

> Por segurança, a chave **não é enviada** no carregamento normal das
> configurações — só é obtida quando você clica em mostrar ou copiar.

### Gerar uma chave nova

No mesmo cartão existe o botão **Gerar Nova Chave**.

> ⚠️ **Isto invalida a chave anterior imediatamente.** Todos os bots e
> integrações que a usam deixam de funcionar até serem atualizados com a nova.
> A chave nova aparece já visível na página, para você a copiar de imediato.

Trate esta chave como uma senha: quem a tiver pode criar convites no seu
servidor. A comparação usa `secrets.compare_digest`, para não revelar informação
através do tempo de resposta.

---

## Criar convite

```
POST /api/invites/bot/create
Content-Type: application/json
```

### Parâmetros

| Campo | Tipo | Obrigatório | Padrão | Descrição |
|---|---|:---:|---|---|
| `telegram_id` | string \| int | **sim** | — | ID do chat/usuário no Telegram. Aceita número ou texto. |
| `libraries` | lista de strings | não | *todas* | Bibliotecas a compartilhar, pelo NOME. Se omitido, usa **todas** as do servidor. Um nome que não exista no servidor é recusado com `400`; a diferença entre maiúsculas e minúsculas não importa (`"filmes"` encontra `"Filmes"`). |
| `screens` | int (0–6) | não | `0` | Limite de telas simultâneos. |
| `allow_downloads` | bool | não | `false` | Permitir downloads/sync. |
| `expires_in_minutes` | int | não | `null` | Validade do convite. `null` = não expira. |
| `trial_duration_minutes` | int | não | `0` | Duração do período de teste. `0` = sem teste. |
| `overseerr_access` | bool | não | `false` | Criar também acesso no Seerr (Overseerr / Jellyseerr). Ver [integracao-seerr.md](integracao-seerr.md). |
| `custom_code` | string | não | *aleatório* | Código personalizado para o convite. |
| `max_uses` | int | não | `1` | Número de utilizações permitidas. |

### Limites dos valores

`expires_in_minutes` e `trial_duration_minutes` vão até **cinco anos** em
minutos (`2 628 000`) e `max_uses` até **1000**. Não é uma regra de negócio: é
o que uma data consegue representar. Antes destes limites, um número grande o
suficiente somado a "agora" levantava `OverflowError` e a rota respondia `500`
— e, no caso do período de teste, só no momento em que alguém tentava resgatar
o convite.

### Exemplo

```bash
curl -X POST https://o-seu-painel/api/invites/bot/create \
  -H "X-API-Key: SUA_CHAVE" \
  -H "Content-Type: application/json" \
  -d '{
        "telegram_id": 123456789,
        "screens": 1,
        "trial_duration_minutes": 60,
        "expires_in_minutes": 1440
      }'
```

### Resposta — sucesso (`201 Created`)

```json
{
  "success": true,
  "code": "aBcD1234EfGh",
  "invite_url": "https://o-seu-painel/invite/aBcD1234EfGh",
  "telegram_id": "123456789",
  "message": "Código de convite criado com sucesso."
}
```

Basta enviar `invite_url` ao usuário no Telegram.

### Respostas de erro

| Código | Situação |
|---|---|
| `400` | Corpo inválido (ex.: `telegram_id` em falta ou vazio), uma biblioteca que não existe no servidor, ou não foi possível determinar as bibliotecas automaticamente. |
| `401` | Chave de API em falta ou incorreta. |
| `409` | **Conflito de unicidade** — ver abaixo. |
| `429` | Limite de pedidos excedido (30 por minuto). |

### `400` ou `409`: a diferença importa

Toda a recusa traz uma chave `erro` **estável**, pensada para o seu código
ler — a `message` é texto escrito para uma pessoa e pode ser reescrito a
qualquer momento:

| `erro` | Código | O que significa |
|---|:---:|---|
| `conflito` | `409` | O pedido está certo; é o estado que não deixa. Tentar de novo **com outro código** (ou depois de revogar o convite que já existe) funciona. |
| `invalido` | `400` | O pedido está errado. Tentar de novo igual dá o mesmo. |

```json
{
  "success": false,
  "erro": "conflito",
  "message": "Este Telegram ID já está vinculado ao usuário 'joao'."
}
```

---

## Consultar um convite

```
GET /api/invites/bot/invite/{code}
```

Responde `404` se o código não existir. Um convite **expirado ou esgotado
continua a ser encontrado** — é essa a resposta que se veio buscar.

```json
{
  "success": true,
  "invite": {
    "code": "aBcD1234EfGh",
    "invite_url": "https://o-seu-painel/invite/aBcD1234EfGh",
    "active": false,
    "expired": false,
    "exhausted": true,
    "created_at": "2026-09-10T18:00:00+00:00",
    "expires_at": "2026-09-11T18:00:00+00:00",
    "claimed_at": "2026-09-10T19:22:31+00:00",
    "use_count": 1,
    "max_uses": 1,
    "uses_left": 0,
    "claimed_by": ["joao"],
    "trial_duration_minutes": 60,
    "screens": 1,
    "allow_downloads": false,
    "overseerr_access": false,
    "telegram_id": "123456789"
  }
}
```

`active` é `false` quando o convite expirou **ou** esgotou as vagas — é o que
o seu bot deve perguntar, em vez de repetir as duas regras e ter de conhecer o
formato da data.

> 🔒 A resposta não traz os nomes das bibliotecas: são infraestrutura do
> servidor, e não fazem falta para mandar um link a alguém.

Limite: 60 pedidos por minuto.

---

## Revogar um convite

```
DELETE /api/invites/bot/invite/{code}
```

Para o link que já foi enviado. Responde `404` se o código não existir.

> ⚠️ Revogar **não** apaga quem já resgatou. Quem entrou, entrou — a conta
> dessa pessoa não é assunto deste pedido. O que deixa de valer é o link.

Limite: 30 pedidos por minuto.

---

## Os convites de um Telegram ID

```
GET /api/invites/bot/invites?telegram_id=123456789
```

Devolve `invites`, do mais recente para o mais antigo, com a mesma forma da
consulta acima. Uma lista vazia significa que nunca foi gerado nenhum.

É a pergunta a fazer **antes** de gerar outro convite: a criação recusa-se
(`409`) quando já existe um ativo para aquele ID, e sem isto o seu bot só
descobre ao levar com o erro — sem saber qual é o link que já mandou, nem se a
pessoa já o usou.

```bash
curl "https://o-seu-painel/api/invites/bot/invites?telegram_id=123456789" \
     -H "X-API-Key: SUA_CHAVE"
```

Limite: 60 pedidos por minuto.

---

## Regras de unicidade

O sistema impede que dois usuárioes fiquem ligados ao mesmo chat do Telegram,
verificando em **três** momentos:

1. **Ao criar o convite** — recusa (`409`) se o `telegram_id` já estiver vinculado
   a um usuário existente.
2. **Ao criar o convite** — recusa (`409`) se já existir outro convite **ativo e
   não expirado** para o mesmo `telegram_id`. Convites já usados ou expirados não
   bloqueiam.
3. **Ao resgatar o convite** — o ID é revalidado. Se, entretanto, tiver sido
   vinculado a outra conta, o **registro continua normalmente**, mas o vínculo do
   Telegram é ignorado e fica um aviso no log. Isto evita que um convite antigo
   "roube" o chat de outro usuário.

### Normalização

O `telegram_id` é normalizado (convertido para texto e sem espaços) tanto ao
gravar como ao pesquisar. Assim, `123`, `"123"` e `" 123 "` são tratados como o
**mesmo** identificador — o que garante que a verificação de duplicados funciona
independentemente de como o bot envia o valor.

---

## Nota técnica sobre os nomes dos campos

Existem dois campos com nomes parecidos, em tabelas diferentes:

- `invitations.telegram_id` — o ID pré-atribuído ao convite.
- `user_profiles.telegram_user` — o ID efetivamente vinculado ao usuário.

São normalmente lidos através de `data_manager.get_user_profile_by_telegram()`,
que já trata a normalização e sabe qual coluna consultar. Evite comparar estes
campos diretamente em código novo.
