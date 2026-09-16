# Notificações Push (celular e navegador)

Guia para ligar as **notificações push** do Painel Plex — os avisos que chegam
ao aparelho **mesmo com o painel fechado**, entregues pelo sistema do celular ou
pelo navegador do computador.

O sino do painel só avisa quem está com a página aberta. Com o push ligado:

- **Você, administrador**, é avisado de cada **pagamento confirmado** e de cada
  **pedido de conteúdo novo** esperando aprovação;
- **Cada usuário** é avisado do **vencimento**, da **renovação**, da
  **reativação**, do **fim do teste**, dos **avisos em massa** e do
  **andamento dos pedidos** dele.

> 💰 **É gratuito e não exige cadastro em lugar nenhum.** Quem entrega a
> mensagem é o serviço de push do próprio navegador (Google, Mozilla, Apple), e
> o painel se identifica com um par de chaves que ele mesmo gera. Não há conta
> no Firebase, no OneSignal nem no programa de desenvolvedor da Apple.

---

## 1. Antes de começar

| Requisito | Por quê |
|---|---|
| **O painel tem que estar em HTTPS** | É exigência do navegador: em `http://` a API de notificações nem existe. A única exceção é `localhost`, para testes. |
| **Endereço Base da Aplicação preenchido** | Em **Configurações → Geral**. É de onde sai o link de pagamento que a notificação de vencimento abre. |
| **No iPhone e no iPad: painel na tela de início** | O Safari só libera push para sites adicionados à tela de início (iOS 16.4 ou mais novo). Numa aba normal o botão não aparece — e o painel explica isso no lugar dele. |

> ⚠️ **Atrás de proxy reverso (Nginx, Traefik, Cloudflare)?** Nada de especial a
> fazer — o push sai do painel *para fora*, não entra. O que importa é só o
> HTTPS chegar até o navegador.

---

## 2. Ligar no painel

Vá em **Configurações → Notificações → aba `Push (Celular)`**:

| Campo | O que faz |
|---|---|
| **Habilitar Notificações Push** | Liga o canal. Na primeira vez que você salva com isto ligado, o painel **gera o par de chaves** sozinho. |
| **Avisar sobre pagamentos** | Manda ao *seu* aparelho cada pagamento confirmado (renovação, reativação e upgrade de plano). |
| **Avisar sobre pedidos novos** | Manda ao *seu* aparelho cada pedido de conteúdo que chega do Seerr. |
| **Contato de quem envia** | Opcional. Um `mailto:voce@exemplo.com` ou um endereço — é por onde o serviço de push entra em contato se algo der errado do lado dele. Em branco, o painel usa o Endereço Base da Aplicação. |

Clique em **Salvar Alterações**. **Não é preciso reiniciar o painel.**

> ⚠️ **As chaves são geradas uma vez e nunca são trocadas.** Elas ficam
> registradas em cada aparelho no momento em que ele é ativado: gerar um par
> novo faria **todo mundo parar de receber de uma vez** — e sem erro visível,
> porque o serviço de push simplesmente recusa. Por isso elas não aparecem no
> formulário e não há botão para regenerá-las.

---

## 3. Ativar em cada aparelho

Ligar a opção no painel **não ativa nada sozinho**: cada pessoa autoriza no
próprio aparelho, e cada navegador conta como um aparelho separado.

1. Abra o painel no aparelho e faça login;
2. Clique no **sino** (canto superior direito);
3. No rodapé do painel de notificações, clique em
   **"Ativar notificações neste aparelho"**;
4. O navegador pede permissão — aceite.

Pronto. O botão passa a dizer *"Desativar notificações neste aparelho"*.

### Android (e computador)

Funciona direto no navegador. Para virar aplicativo de verdade — ícone na tela
de início, abre sem barra de endereço — use o menu do Chrome →
**Instalar aplicativo** / **Adicionar à tela de início**.

### iPhone e iPad

Aqui a instalação é **obrigatória**, não opcional:

1. Abra o painel no **Safari** (não funciona pelo Chrome no iOS);
2. Toque em **Compartilhar** → **Adicionar à Tela de Início**;
3. Abra o painel **pelo ícone criado** (não pela aba do Safari);
4. Faça login e siga os passos do sino acima.

Enquanto isso não for feito, o painel mostra no lugar do botão:
*"No iPhone, adicione o painel à tela de início para receber notificações."*

### Testar

De volta a **Configurações → Notificações → Push**, clique em **Enviar Teste**.
A notificação vai para todos os aparelhos onde *você* já ativou.

- *"Enviada para N dispositivo(s)"* → está tudo funcionando;
- *"Nenhum dispositivo recebeu a notificação"* → você ainda não ativou em
  nenhum aparelho (passo 3), ou todos foram removidos.

---

## 4. Personalizar as mensagens

Na mesma aba, cada evento tem **dois campos**: **Título** e **Mensagem**.

> ⚠️ Não são um só texto por acaso. O sistema trata as duas partes de forma
> diferente: o **título** aparece em negrito e é o que se lê de relance; a
> **mensagem** é cortada depois de duas linhas na tela de bloqueio. Copiar o
> texto do Telegram para cá dá um aviso ilegível — deixe curto.

As mensagens estão divididas em duas sub-abas:

- **Assinatura** — Aviso de Vencimento, Renovação Confirmada, Conta Reativada,
  Fim do Período de Teste, Mensagem em Massa;
- **Pedidos (Seerr)** — Pendente, Aprovado, Disponível, Recusado, Falha, e um
  texto **padrão** que vale para qualquer evento sem mensagem própria.

Deixar um campo em branco faz o painel usar o texto padrão dele.

### Marcadores disponíveis

Servem em qualquer dos dois campos.

| Marcador | Onde vale |
|---|---|
| `{name}`, `{username}`, `{email}` | Todos |
| `{server_name}` | Todos — vira "Plex" ou "Jellyfin", conforme o seu servidor |
| `{price}`, `{plan_name}` | Todos |
| `{greeting}` | Todos — "Bom dia", "Boa tarde", "Boa noite" |
| `{days}`, `{date}` | Aviso de Vencimento |
| `{new_date}` | Renovação e Reativação |
| `{message}` | Mensagem em Massa |
| `{title}`, `{overview}`, `{status}`, `{media_url}` | Pedidos (Seerr) |

O **link não vai escrito no texto**: a notificação já é clicável. Os avisos de
cobrança abrem o link de pagamento da pessoa; os demais abrem a página da conta
dela; os de pedido abrem o item no Seerr.

---

## 5. Notificações de pedidos

Os avisos de pedido dependem do **webhook do Seerr** estar configurado — o mesmo
que já serve o Telegram, o WhatsApp e o Discord. Se ainda não estiver, siga a
[integração com o Seerr](integracao-seerr.md#3-notificações-de-pedidos-opcional-mas-recomendado).

Com ele configurado:

- **quem pediu** recebe cada mudança de estado (pendente, aprovado, disponível,
  recusado, falhou);
- **você** recebe só os pedidos que **entram** (pendente e aprovado
  automaticamente) — que são os que precisam da sua atenção. O resto é o estado
  andando, e quem quer saber é quem pediu.

> O aviso ao administrador é enviado **mesmo que quem pediu ainda não tenha
> perfil no painel**. Quem aprova é você.

---

## 6. O que nunca vai por push

🛡️ Dois avisos são entregues **só** pelos canais pessoais (Telegram, WhatsApp,
Discord, webhook), nunca por notificação push:

- a **senha nova** de uma conta que teve de ser recriada no servidor;
- o **link de "esqueci minha senha"**.

Não é limitação, é decisão: a notificação push aparece na tela de bloqueio, à
vista de quem estiver por perto, e fica guardada pelo sistema fora do painel.
Uma senha não pode viajar assim.

---

## 7. Resolução de problemas

**O botão não aparece no sino**
1. O painel está em `https://`? Em `http://` o navegador não oferece a opção.
2. A opção está ligada e salva em Configurações → Notificações → Push?
3. É um iPhone/iPad? Adicione à tela de início e abra pelo ícone.
4. Navegador muito antigo, ou uma janela anônima: o suporte simplesmente não
   existe e o botão fica oculto de propósito.

**"As notificações estão bloqueadas nas configurações do navegador"**
A permissão foi negada alguma vez nesse site — e o navegador não pergunta de
novo. É preciso liberar à mão:
- **Chrome/Edge:** cadeado na barra de endereço → *Notificações* → Permitir;
- **Firefox:** cadeado → *Permissões* → apagar o bloqueio;
- **Safari (iOS):** Ajustes → Notificações → o ícone do painel.

Depois recarregue a página e clique no botão de novo.

**Ativei, mas o teste diz que nenhum aparelho recebeu**
O teste envia só para *quem pediu*. Confirme que ativou no sino **com a mesma
conta** com que está abrindo as Configurações. Se ativou como administrador e
depois entrou como usuário comum no mesmo navegador, o aparelho passou a ser do
usuário — é assim mesmo.

**Recebia e parou de receber**
1. **As chaves mudaram?** Se alguém editou `PUSH_VAPID_PUBLIC_KEY` ou
   `PUSH_VAPID_PRIVATE_KEY` no `config.json`, todas as ativações anteriores
   valem nada. Cada pessoa precisa desativar e ativar de novo no sino.
2. **O painel mudou de endereço?** Uma ativação pertence ao domínio onde foi
   feita. Mudou de domínio, todo mundo reativa.
3. **O aparelho foi limpo** (dados do site apagados, aplicativo removido): o
   painel percebe sozinho na próxima tentativa e apaga o registro — basta
   reativar.

**Nos logs: `O endereço de entrega não é de um serviço de push conhecido`**
O navegador devolveu um endereço fora da lista aceita. Acontece com um serviço
de push próprio (acrescente o domínio em `PUSH_ALLOWED_HOSTS`) ou com um
navegador novo cujo serviço o painel ainda não conhece — nesse caso, abra um
relato com o domínio que aparece na mensagem.

**Um usuário diz que não recebe, mas outros recebem**
Ele provavelmente nunca ativou no aparelho dele. O push não segue a conta: segue
o aparelho, e é a pessoa que autoriza.

**Chega duas vezes**
São dois aparelhos ativados (o navegador e o painel instalado contam separado, e
cada navegador tem o seu). Desative no que não quiser.

**Nos logs: `O serviço de push respondeu 403`**
As chaves do painel não batem com as que o aparelho registrou — quase sempre um
`config.json` editado à mão ou restaurado pela metade. O painel conserta o par
sozinho no próximo arranque, mas as ativações antigas se perdem: é preciso
reativar.

---

## 8. Notas técnicas

- **Padrões:** Web Push (RFC 8030), cifra `aes128gcm` (RFC 8188 e 8291) e
  VAPID (RFC 8292). Implementados no painel sobre a biblioteca `cryptography`,
  **sem dependência externa** — a `pywebpush` arrasta a `http-ece`, que não
  compila mais com as versões atuais do setuptools.
- **Serviços de entrega:** `fcm.googleapis.com` (Chrome, Edge, Android),
  `updates.push.services.mozilla.com` (Firefox), `web.push.apple.com` (Safari).
  Nenhum cobra nem exige cadastro para Web Push com VAPID.
- **Onde ficam as ativações:** tabela `push_subscriptions` do banco de dados.
  Uma linha por aparelho, com `media_user_id` **nulo** significando "é do
  administrador" — a mesma convenção das notificações do sino.
- **Limites respeitados:** 4 KB por notificação (o painel corta em 3993 bytes e
  recusa acima disso), título em 80 caracteres e corpo em 300.
- **Faxina automática:** quando um serviço responde `404` ou `410`, o aparelho
  não existe mais e a linha é apagada. Uma recusa temporária (`429`, serviço
  fora do ar) não apaga nada — só falha aquela entrega.
- **Rotas da API:** `POST /api/notifications/push/subscribe`,
  `/push/unsubscribe` e `/push/test`. Todas exigem sessão, e **quem é o dono do
  aparelho é o servidor que decide**, pela sessão — nunca o corpo do pedido.
- **Só se aceita um endereço de serviço de push conhecido** (Google, Mozilla,
  Apple, Microsoft). O endereço de entrega é escolhido por quem subscreve e o
  painel faz-lhe POST de dentro da sua rede: sem essa lista, qualquer pessoa com
  conta podia apontá-lo para um serviço interno e usar o painel para lhe bater.
  Quem corre um serviço de push próprio acrescenta o domínio na variável de
  ambiente `PUSH_ALLOWED_HOSTS` (separada por vírgulas), ao lançar o contêiner —
  ela fica fora da interface de propósito, por ser uma fronteira de segurança e
  não uma preferência.
- **Service worker:** servido em `/service-worker.js` (na raiz, para o alcance
  cobrir o painel inteiro). É ele que recebe a notificação com o painel fechado,
  e quem reaproveita a aba já aberta quando você toca no aviso.
- **Backup:** as chaves vão no `config.json` e as ativações no banco de dados —
  ou seja, os dois entram no backup do painel. Restaurar um backup no **mesmo
  endereço** mantém tudo funcionando.
