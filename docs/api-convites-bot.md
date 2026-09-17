# API de Convites para Bots — Vínculo automático de contato

Endpoint dedicado a integrações automatizadas (bots do Telegram e do Discord,
scripts, n8n, etc.) que precisam de gerar convites já vinculados a uma pessoa.

Quando o convite é resgatado, o perfil do novo usuário é criado **já com o
contato associado**, dispensando qualquer vinculação manual posterior — é por
ele que os avisos de vencimento e o link de pagamento chegam.

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

### Criar uma chave

Em **Configurações → Geral → Chaves por integração**, dê um nome à chave e
marque as permissões que ela precisa:

| Permissão | O que abre |
|---|---|
| `convites` | Criar, consultar e revogar convites (as rotas deste documento). |
| `webhooks` | Receber o webhook do Overseerr/Jellyseerr. |

Clique em **Criar Chave**. Ela aparece numa caixa verde, com um botão de
copiar; a caixa fica no ecrã até você a fechar.

> 🛡️ **A chave é mostrada uma única vez, ao ser criada.** O painel guarda
> apenas um resumo dela e não a consegue mostrar de novo — se a perder, revogue
> essa e crie outra.

Uma chave por integração significa que um bot de Telegram não pode aceitar
webhooks em nome do painel, e que revogar a chave de um bot comprometido não
derruba as outras integrações. Cada linha da lista mostra o nome, as permissões
e quando a chave foi usada pela última vez — que é como se descobre qual delas
já ninguém usa.

### Revogar uma chave

Na mesma lista, o botão **Revogar** desliga a chave imediatamente: a integração
que a usa passa a receber `401`. A linha fica na lista, marcada como revogada —
"esta chave foi revogada em março" é uma resposta, "esta chave nunca existiu"
não é.

> ⚠️ **A chave única acabou.** Até à versão anterior existia também uma
> **Chave de API (Integrações)** — uma só, que servia para tudo. Ela foi
> removida: valia para todos os escopos, era a mesma para todas as integrações,
> e regenerá-la porque um bot tinha sido comprometido derrubava também o Seerr.
> Se os seus bots ainda a usam, crie uma chave com a permissão `convites` e
> troque-a; se o Seerr a usa, crie outra com `webhooks`.

Trate cada chave como uma senha: quem tiver uma com a permissão `convites` pode
criar convites no seu servidor. A comparação usa `secrets.compare_digest`, para
não revelar informação através do tempo de resposta.

---

## Criar convite

```
POST /api/invites/bot/create
Content-Type: application/json
```

### Parâmetros

| Campo | Tipo | Obrigatório | Padrão | Descrição |
|---|---|:---:|---|---|
| `telegram_id` | string \| int | **um dos dois** | — | ID do chat/usuário no Telegram. Aceita número ou texto. |
| `discord_id` | string \| int | **um dos dois** | — | ID do usuário no Discord. Aceita número ou texto. |
| `libraries` | lista de strings | não | *todas* | Bibliotecas a compartilhar, pelo NOME. Se omitido, usa **todas** as do servidor. Um nome que não exista no servidor é recusado com `400`; a diferença entre maiúsculas e minúsculas não importa (`"filmes"` encontra `"Filmes"`). |
| `screens` | int (0–6) | não | `0` | Limite de telas simultâneos. |
| `allow_downloads` | bool | não | `false` | Permitir downloads/sync. |
| `expires_in_minutes` | int | não | `null` | Validade do convite. `null` = não expira. |
| `trial_duration_minutes` | int | não | `0` | Duração do período de teste. `0` = sem teste. |
| `overseerr_access` | bool | não | `false` | Criar também acesso no Seerr (Overseerr / Jellyseerr). Ver [integracao-seerr.md](integracao-seerr.md). |
| `custom_code` | string | não | *aleatório* | Código personalizado para o convite. |
| `max_uses` | int | não | `1` | Número de usos permitidos. |
| `note` | string | não | — | Para quem é o convite. Só o administrador a vê, no painel; serve para saber de quem era o código três meses depois. Até 200 caracteres. |

> É preciso **um** dos dois, não os dois. Um pedido sem nenhum é recusado com
> `400`: um convite deste endpoint existe para ficar atribuído a alguém, e um
> sem contato nenhum seria só um convite normal criado pelo caminho errado.

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

# Ou, para um bot de Discord:
curl -X POST https://o-seu-painel/api/invites/bot/create \
  -H "X-API-Key: SUA_CHAVE" \
  -H "Content-Type: application/json" \
  -d '{"discord_id": "987654321098765432", "screens": 2}'
```

### Resposta — sucesso (`201 Created`)

```json
{
  "success": true,
  "code": "aBcD1234EfGh",
  "invite_url": "https://o-seu-painel/invite/aBcD1234EfGh",
  "telegram_id": "123456789",
  "discord_id": null,
  "message": "Código de convite criado com sucesso."
}
```

Basta enviar `invite_url` ao usuário no Telegram.

### Respostas de erro

| Código | Situação |
|---|---|
| `400` | Corpo inválido (ex.: sem `telegram_id` nem `discord_id`), uma biblioteca que não existe no servidor, ou não foi possível determinar as bibliotecas automaticamente. |
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
    "telegram_id": "123456789",
    "discord_id": null,
    "note": "João do grupo do WhatsApp"
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

## Os convites de uma pessoa

```
GET /api/invites/bot/invites?telegram_id=123456789
GET /api/invites/bot/invites?discord_id=987654321098765432
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

O sistema impede que dois usuários fiquem ligados ao mesmo contato — seria uma
pessoa a receber as notificações da outra, e entre elas está o link de
pagamento, que funciona para quem o tiver. A verificação acontece em **três**
momentos, e vale igual para o Telegram e para o Discord:

1. **Ao criar o convite** — recusa (`409`) se o ID já estiver vinculado a um
   usuário existente.
2. **Ao criar o convite** — recusa (`409`) se já existir outro convite **ativo e
   não expirado** para o mesmo ID. Convites já usados ou expirados não bloqueiam.
3. **Ao resgatar o convite** — o ID é revalidado. Se, entretanto, tiver sido
   vinculado a outra conta, o **registro continua normalmente**, mas esse vínculo
   é ignorado e fica um aviso no log. Isto evita que um convite antigo "roube" o
   contato de outro usuário.

### Normalização

Os IDs são normalizados (convertidos para texto e sem espaços) tanto ao gravar
como ao pesquisar. Assim, `123`, `"123"` e `" 123 "` são tratados como o
**mesmo** identificador — o que garante que a verificação de duplicados funciona
independentemente de como o bot envia o valor.

---

## Nota técnica sobre os nomes dos campos

Os nomes das colunas **divergem** entre o convite e o perfil:

| Canal | No convite | No perfil |
|---|---|---|
| Telegram | `invitations.telegram_id` | `user_profiles.telegram_user` |
| Discord | `invitations.discord_id` | `user_profiles.discord_user_id` |

É uma divergência histórica, e já custou um bug: havia código a ler
`profile.get("telegram_id")` — que devolve sempre vazio, porque essa coluna não
existe no perfil — e a parecer funcionar por causa de um valor alternativo à
frente.

Quem faz a ponte é o mapa `CONTACTOS`, em
`app/services/media_server/invitations.py`, e a leitura passa por
`data_manager.get_user_profile_by_contacto(canal, valor)`. Evite comparar estes
campos diretamente em código novo.
