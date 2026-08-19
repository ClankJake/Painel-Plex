# API de Convites para Bots — Vínculo automático de Telegram ID

Endpoint dedicado a integrações automatizadas (bots do Telegram, scripts, n8n,
etc.) que precisam de gerar convites já vinculados a um utilizador do Telegram.

Quando o convite é resgatado, o perfil do novo utilizador é criado **já com o
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

**Onde encontrar a chave:** é o valor de `INTERNAL_TRIGGER_KEY` no ficheiro
`config/config.json`. É gerada automaticamente na primeira execução e **nunca é
devolvida pela API de configurações**, por isso tem de ser lida diretamente do
ficheiro no servidor.

> ⚠️ Trate esta chave como uma palavra-passe: quem a tiver pode criar convites.
> Se precisar de a rodar, altere o valor em `config.json` e reinicie a aplicação.

A comparação da chave usa `secrets.compare_digest`, para não revelar informação
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
| `telegram_id` | string \| int | **sim** | — | ID do chat/utilizador no Telegram. Aceita número ou texto. |
| `libraries` | lista de strings | não | *todas* | Bibliotecas a partilhar. Se omitido, usa **todas** as do servidor. |
| `screens` | int (0–6) | não | `0` | Limite de ecrãs simultâneos. |
| `allow_downloads` | bool | não | `false` | Permitir downloads/sync. |
| `expires_in_minutes` | int | não | `null` | Validade do convite. `null` = não expira. |
| `trial_duration_minutes` | int | não | `0` | Duração do período de teste. `0` = sem teste. |
| `overseerr_access` | bool | não | `false` | Criar também acesso no Overseerr/Jellyseerr. |
| `custom_code` | string | não | *aleatório* | Código personalizado para o convite. |
| `max_uses` | int | não | `1` | Número de utilizações permitidas. |

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

Basta enviar `invite_url` ao utilizador no Telegram.

### Respostas de erro

| Código | Situação |
|---|---|
| `400` | Corpo inválido (ex.: `telegram_id` em falta ou vazio), ou não foi possível determinar as bibliotecas automaticamente. |
| `401` | Chave de API em falta ou incorreta. |
| `409` | **Conflito de unicidade** — ver abaixo. |
| `429` | Limite de pedidos excedido (30 por minuto). |

Exemplo de `409`:

```json
{
  "success": false,
  "message": "Este Telegram ID já está vinculado ao utilizador 'joao'."
}
```

---

## Regras de unicidade

O sistema impede que dois utilizadores fiquem ligados ao mesmo chat do Telegram,
verificando em **três** momentos:

1. **Ao criar o convite** — recusa (`409`) se o `telegram_id` já estiver vinculado
   a um utilizador existente.
2. **Ao criar o convite** — recusa (`409`) se já existir outro convite **ativo e
   não expirado** para o mesmo `telegram_id`. Convites já usados ou expirados não
   bloqueiam.
3. **Ao resgatar o convite** — o ID é revalidado. Se, entretanto, tiver sido
   vinculado a outra conta, o **registo continua normalmente**, mas o vínculo do
   Telegram é ignorado e fica um aviso no log. Isto evita que um convite antigo
   "roube" o chat de outro utilizador.

### Normalização

O `telegram_id` é normalizado (convertido para texto e sem espaços) tanto ao
gravar como ao pesquisar. Assim, `123`, `"123"` e `" 123 "` são tratados como o
**mesmo** identificador — o que garante que a verificação de duplicados funciona
independentemente de como o bot envia o valor.

---

## Nota técnica sobre os nomes dos campos

Existem dois campos com nomes parecidos, em tabelas diferentes:

- `invitations.telegram_id` — o ID pré-atribuído ao convite.
- `user_profiles.telegram_user` — o ID efetivamente vinculado ao utilizador.

São normalmente lidos através de `data_manager.get_user_profile_by_telegram()`,
que já trata a normalização e sabe qual coluna consultar. Evite comparar estes
campos diretamente em código novo.
