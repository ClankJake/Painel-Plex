# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

O código, os comentários, as mensagens de commit e a documentação deste projeto
estão em português. Mantenha esse idioma no que escrever.

⚠️ **O que a PESSOA lê é português do BRASIL.** O painel sempre falou brasileiro
("Usuário", "Senha", "telas", "Você"), mas funcionalidades novas foram entrando
com vocabulário europeu — "palavra-passe", "utilizador", "aceder", "A
guardar..." — e o resultado eram duas variantes na mesma página. Isto vale para
os literais dentro de `_()` e para os modelos de notificação, que chegam ao
telefone de quem paga; os comentários e as docstrings ficam de fora, são
internos. Há um teste que percorre tudo o que é visível e recusa o vocabulário
europeu, dizendo logo qual a forma a usar
(`tests/test_textos_em_portugues_do_brasil.py`).

⚠️ **O JavaScript também fala com a pessoa**, e a varredura não chegava lá. Os
textos dele vêm quase todos do HTML (`data-i18n-*`), mas cada um tem uma
ALTERNATIVA escrita no próprio ficheiro — o `i18n.x || 'texto'` que aparece
quando a chave falta. Oito delas ficaram em português europeu ("A guardar...",
"A enviar...", "descarregue filmes", "ficheiro de backup"). O teste percorre
agora também `app/static/js`.

⚠️ **E a lista de marcas tinha buracos por onde o fluxo do convite passava
inteiro**: "registado", "aceite" como particípio, e o rótulo de carregamento,
cuja lista de verbos era escrita à mão — "A reativar...", "A restaurar...", "A
zerar..." passavam todos. O rótulo vale agora para QUALQUER verbo no
infinitivo, que é o que define a forma, e a família do "registo" é apanhada
pelo 'r' que a distingue de "registro" (`regist(?!r)`), o que inclui as formas
que ninguém se lembraria de listar.

⚠️ **E o maior buraco era uma palavra que nunca esteve na lista: "cupão".**
Estava em 22 sítios visíveis — a página financeira inteira, as respostas da API
e as notificações de renovação que chegam a quem paga. Entraram com ela mais
oito marcas: "Tem a certeza" (o artigo é europeu), o RÓTULO "Guardar/Gravar"
(pela maiúscula inicial, como o "A guardar" — o verbo "guardar" no meio de uma
frase é brasileiro correto), a vogal fechada (`bónus`→`bônus`), "em falta",
"ao fim de", "consoante", "ligação" (no Brasil é uma chamada; a rede tem
"conexão") e "realizador" (→ "diretor", que aparece nas conquistas).

⚠️ **Três coisas visíveis que a varredura não via, e agora vê**: uma mensagem
de API que não passa por `_()` (`"message": "Cupão apagado com sucesso."`, que
o painel mostra num toast), a mensagem de um `raise ValueError` de validador
(sai no corpo do 400, por baixo do campo) e o texto escrito À MÃO dentro da
marcação do JavaScript — `<p>Nenhum cupão ativo ou criado.</p>` e
`title="Apagar Cupão"`. Do JS lê-se o que é TEXTO (entre `>` e `<`, ou num
`title`/`alt`/`placeholder`) e não toda a string do ficheiro: um nome de classe
do Tailwind não é uma frase.

🐛 **E uma dessas palavras estava presa a uma comparação.** A descrição de uma
renovação por cupom de 100% é gravada em TEXTO FIXO (`payments.py`), fica na
base de dados e nunca é reescrita; o `financial.js` decidia o rótulo do plano
com `descricao.includes('cupão')`. Corrigir a palavra no servidor fazia a
verificação deixar de reconhecer as renovações NOVAS, e corrigi-la só no JS
fazia-a deixar de reconhecer as ANTIGAS. Ela aceita agora as duas grafias
(`/cup(om|ão)|coupon/i`), que é o que um histórico com anos de uso tem lá
dentro.

## Comandos

```bash
# Dependências (o requirements-dev.txt já inclui o requirements.txt)
pip install -r requirements-dev.txt
npm install

# Testes
pytest                                   # suíte completa
pytest tests/test_pricing_manager.py     # um ficheiro
pytest tests/test_invites.py::test_nome  # um teste
pytest -k proration                      # por expressão
pytest -m integration                    # só os que criam a app Flask + BD
pytest --cov=app --cov-report=term-missing   # o que o CI executa

# Frontend — nada em app/static/dist/ está versionado, é preciso gerar
npm run build          # CSS + bibliotecas (socket.io, chart.js) — é o que falta
                       # a quem vê 'io is not defined' no navegador
npm run build:css      # só o CSS
npm run watch:css      # em desenvolvimento, num terminal à parte
npm run lint           # referências do JS (uma regra: `no-undef`)

# Executar a aplicação (aplica `flask db upgrade` antes de subir)
python run.py

# Migrações
flask db migrate -m "descrição"
flask db upgrade
```

Não há formatador configurado, e o ESLint que existe **não é um linter de
estilo**: tem DUAS regras, `no-undef` e `import-x/namespace`. O CI (`.github/workflows/tests.yml`) tem
dois jobs: o **pytest**, em Python 3.11 e 3.12, e o **build do frontend**
(`npm ci` + `npm run lint` + `npm run build`).

🐛 **Uma referência pendurada num módulo ES só levanta quando a LINHA CORRE.**
O `renderInvites()` tinha duas listas — tudo o que estava em cache e o que a
aba mostra — e elas colapsaram numa só quando a filtragem passou para o
servidor; o nome antigo (`allInvites`) ficou para trás dentro do `onclick` do
botão "Detalhes". A página carregava sem uma queixa e o servidor não registava
nada: o modal simplesmente não abria, e o erro ficava na consola do navegador
de quem usa o painel. Nem o pytest (não executa JavaScript) nem o build do
Tailwind (lê os templates à procura de NOMES DE CLASSES, não analisa os
módulos) conseguem ver isto. ⚠️ Os globais do `eslint.config.mjs` são
declarados à mão de propósito: o que não estiver na lista é um erro, por isso
uma biblioteca nova carregada por `<script>` no template tem de ser
acrescentada ali — que é exatamente o momento em que alguém deve reparar que
ela passou a existir.

🐛 **E a segunda regra existe porque a primeira não via o irmão desse erro.**
`import * as ui` traz só o que o módulo EXPORTA, e `ui.showToast` era uma
função que o `ui.js` apenas IMPORTAVA do `utils.js`: para o `no-undef` o `ui`
existe e está tudo certo. O `TypeError: ui.showToast is not a function` só
aparecia quando um pagamento entrava e o socket disparava o `user_list_updated`
— e o aviso perdido era o menor dos estragos, porque a exceção matava a linha
SEGUINTE, o `ui.loadStatus(true)` que recarrega a lista: o painel aberto não
atualizava quando o pagamento chegava. `import-x/namespace` compara cada
`NS.membro` com os exports reais. ⚠️ `allowComputed` fica LIGADO: o
`api[endpoint]` do `handleTestConnection` é despachagem dinâmica de propósito e
já trata o nome desconhecido, e um guarda com exceções espalhadas pelo código
deixa de ser lido. ⚠️ E a saída não é reexportar o que falta — `showToast` é do
`utils.js`, e pô-lo a sair também pelo `ui.js` faria deste um passa-culpas de
uma função que não é dele; quem precisa dele importa-o de onde ele vive.

O contrato (o script, as duas regras, o plugin, o alcance, o passo do CI) está
preso por `tests/test_referencias_do_javascript.py`, porque um guarda desligado
em silêncio deixa de ser um guarda.

⚠️ **O `package-lock.json` tem de vir do registo PÚBLICO.** O que estava
versionado registava os hashes de tarballs RE-EMPACOTADOS por um espelho: 68 dos
70 pacotes tinham um `integrity` que o `registry.npmjs.org` não serve, e o
`npm install` morria com `EINTEGRITY` para quem clonasse o repositório. Se ele
voltar a acusar "tarball seems to be corrupted", é isto — e a correção é
regenerar o lockfile fora desse espelho.

**Três coisas o deixaram passar despercebido, e as três estão fechadas**: o CI
não corria npm (agora corre), o Dockerfile copiava só o `package.json` e
resolvia de fresco (agora leva o lockfile e usa `npm ci`), e nenhum teste
chegava a correr o build — os de `test_assets_frontend.py` verificavam as
DECLARAÇÕES, não o resultado. O que fecha esse último é
`test_os_assets_pedidos_estao_mesmo_no_disco`, que salta em desenvolvimento (o
`dist/` não é versionado) e, com `PAINEL_EXIGE_DIST=1`, exige que o build tenha
mesmo corrido — um passo que existe para apanhar um build partido não pode
passar sem correr.

## Arquitetura

Flask com *application factory* (`create_app()` em `app/__init__.py`), SQLite,
Jinja2 + Tailwind, e JavaScript ES modules sem framework. O painel administra um
servidor Plex ou Jellyfin: usuários, convites, assinaturas, pagamentos PIX,
notificações e estatísticas de visualização.

### Managers como singletons de módulo

`app/extensions.py` declara as extensões Flask **e** os `managers` a `None`.
`create_app()` instancia cada um e atribui de volta ao módulo. O resto do código
faz `from ..extensions import media_server` — nunca instancie um manager
diretamente numa rota ou num job.

A ordem de construção importa: há injeções tardias porque as dependências são
circulares. O `ReferralManager` só recebe o `subscription_manager` depois do
`PlexManager` existir; o `PlexManager` só recebe o `stream_manager` depois de o
`StreamManager` ser construído a partir da conexão dele.

### Servidor de média: um backend, escolhido pela configuração

O painel não fala com o Plex diretamente: fala com um **backend de servidor de
média**, escolhido por `MEDIA_SERVER_TYPE` no config.json.

- `app/services/media_server/base.py` — o contrato (`MediaServerBackend` e os
  protocolos dos submanagers) e as `MediaServerCapabilities`.
- `app/services/media_server/factory.py` — o **único** sítio que sabe que existe
  mais do que um servidor possível. Registar um backend novo é uma entrada em
  `_BACKENDS`.

⚠️ **Trocar de servidor obriga a reiniciar a aplicação.** O backend é escolhido
no arranque e os blueprints guardam a referência POR VALOR
(`from ..extensions import media_server`): substituir o objeto em memória
deixaria metade do painel a falar com o servidor antigo. O assistente valida a
ligação com um backend temporário, grava, cria a sessão (que vive num cookie e
sobrevive) e reinicia com `_agendar_reinicio()`.
- `app/services/media_server/plex/` — o backend do Plex, e o único sítio onde
  vive conhecimento sobre a API do Plex.
- `app/services/media_server/jellyfin/` — o mesmo para o Jellyfin.
- `app/services/media_server/invitations.py` — o ciclo de vida de um convite
  (código, vagas, validade), que é igual em todos os servidores e por isso não
  vive em nenhum deles. 🐛 **Também o que o RESGATE faz além de dar acesso**:
  agendar o fim de um período de teste (`agendar_fim_do_teste`) e resolver a
  indicação pendente (`resolver_indicacao_pendente`). Nada nelas é do Plex — é
  a sessão, o config e o agendador — mas viviam lá, e o backend do Jellyfin
  nasceu sem: um convite de teste criava uma conta que **nunca expirava**, e o
  `referred_by` ficava sempre vazio, por isso o "Indique e Ganhe" nunca podia
  pagar. Um backend novo herda-as em vez de as reescrever.

#### O convite: o que já correu mal, e o que passou a ser contrato

⚠️ **Os campos de tempo somam-se a `now()`, e uma data tem limites.**
`timedelta(minutes=10**12)` levanta `OverflowError` — era um 500 com traceback
numa rota que tinha acabado de validar a entrada. O `trial_duration_minutes`
era o pior: a criação PASSAVA e só o resgate rebentava, dentro de
`agendar_fim_do_teste`, na cara de quem estava a entrar. O teto está nos
esquemas (`MAX_MINUTOS`), para as rotas, **e** em `_minutos_seguros`, para o
que já está gravado — ali o 500 já não é de quem cria o convite.

⚠️ **Reativar renova a validade; não a apaga.** `expires_at = None` quer dizer
"não expira", e era o que `reset_invitation_usage` fazia a um convite vencido:
um promocional de 24 horas reativado por engano passava a valer para sempre,
com a mensagem a dizer "validade estendida". Hoje recebe outra vez a MESMA
janela que teve à partida (`created_at` → `expires_at`), contada de agora.

⚠️ **As bibliotecas são validadas na CRIAÇÃO.** Eram aceites quaisquer nomes e
a falha só aparecia no resgate ("Nenhuma biblioteca válida foi encontrada para
compartilhar") — quem pagava o engano do administrador era quem tinha acabado
de clicar no link. Fica gravada a grafia do SERVIDOR: o backend do Plex compara
`s.title in library_titles` exatamente, e um convite criado com "filmes" num
servidor que tem "Filmes" nascia inútil. ⚠️ Mas **não saber não é saber que não
existe**: com o servidor em baixo, `get_libraries()` devolve `[]` — o mesmo que
devolve um servidor sem bibliotecas — e daí o vazio vale por "não sei" e a
criação segue.

⚠️ **Acrescentar o ID ao convite não é mexer nas vagas.** Onde as contas são
LOCAIS, a vaga é reservada antes de a conta existir, logo sem ID; isso era
corrigido com um `release` seguido de um `reserve`, e entre os dois a vaga fica
LIVRE. Com o worker gevent, outro resgate ficava com ela, o `reserve` seguinte
devolvia `False` (que ninguém verificava) e o ID nunca chegava ao convite — sem
ele, nem o resgate duplicado nem o abuso de período de teste voltavam a
reconhecer aquela pessoa. É para isso que existe
`registar_identidade_no_convite`.

🛡️ **Criar um convite concede acesso ao servidor, e isso vai para a
auditoria** (`convite.criar`, `.apagar`, `.reativar`, `.resgatar`). Duas
armadilhas: no RESGATE só entram os campos escolhidos à mão — o corpo desse
pedido traz a palavra-passe que a pessoa acabou de escolher, e a auditoria vai
dentro do ZIP de backup —, e ao APAGAR lê-se a linha CRUA
(`data_manager.get_invitation`), porque `get_invitation_by_code` recusa um
convite expirado e são esses os que mais se apagam: com a porta errada, a
auditoria não ficava vazia, ficava com os valores por omissão de um dicionário
vazio, que é uma mentira de aspeto plausível.

⚠️ **Um link que SAI do painel não se monta com `url_for(_external=True)`** —
ele lê o endereço do PEDIDO. Um bot que chama o painel pelo nome interno da
rede de contentores recebia `http://painel:5000/invite/abc` e mandava-o para o
Telegram de quem ia entrar. `endereco_publico()` (`utils/enderecos.py`) é a
porta única: a `APP_BASE_URL` manda, o `url_for` externo é o recurso.

🔒 **A chave de API tem uma verificação só** (`chave_de_api_necessaria`, em
`api/decorators.py`), usada pelo endpoint de convites para bots e pelo webhook
do Overseerr. Estava copiada nos dois e as cópias já tinham divergido — uma
aceitava o `Authorization` sem o prefixo `Bearer` e a outra não, e a interface
do Overseerr chama "Authorization" ao campo onde se escreve a chave e mais
nada. O decorador recebe o **escopo** que a rota exige (ver as chaves de API,
mais abaixo).

⚠️ **A recusa diz de que TIPO é, e o código HTTP sai daí.** `CONFLITO` (409: o
pedido está certo, é o estado que não deixa), `PEDIDO_INVALIDO` (400: tentar de
novo dá o mesmo) e `CREDENCIAIS` (401). O endpoint dos bots respondia 409 a
tudo o que falhasse — inclusive a "informe pelo menos uma biblioteca" — e o
resgate respondia 401 a uma senha curta demais, o que fazia o cliente concluir
que as credenciais estavam erradas quando o problema era o formato do corpo. A
`message` não serve para isto: é texto escrito para uma pessoa ler e um dia
será traduzido. `ESTADO_HTTP` traduz o motivo num sítio só.

⚠️ **A API de bots não é só criar.** `GET /bot/invite/<code>` diz o estado,
`DELETE` revoga e `GET /bot/invites?telegram_id=` lista os de uma pessoa. Três
coisas que a implementação guarda: a consulta lê a linha CRUA (a
`get_invitation_by_code` é a porta do RESGATE e devolve `None` para um convite
expirado — e "expirado" é a resposta que se veio buscar); `active` e
`uses_left` vêm calculados, para cada integração não repetir as duas regras; e
🔒 a resposta não leva os nomes das bibliotecas, que são infraestrutura do
servidor. O caminho é `/bot/invite/<code>` e não `/bot/<code>` porque um código
personalizado pode ser a palavra `create`.

📌 **A documentação da API está presa ao código por um teste**
(`tests/test_documentacao_da_api_de_bots.py`): todo o campo do esquema tem de
estar na tabela de parâmetros, toda a rota documentada tem de existir, e a
documentação não pode prometer um campo que o Pydantic descarta em silêncio —
que é o pior dos dois erros, porque quem escreve o bot a partir dela não
percebe porque é que o convite não saiu como pedido.

🛡️ **Apagar um convite é uma remoção SUAVE**, e a razão não é óbvia:
`get_user_claim_date` — a única resposta do painel ao "desde quando é que esta
pessoa está aqui" — procura o username dentro de `claimed_by_users`, e não há
outra fonte. O botão que existe para arrumar a lista de convites gastos
destruía em silêncio o histórico de entrada de cada pessoa que os tinha
resgatado. É a leitura do "membro desde" a única que NÃO filtra `deleted_at`.

⚠️ Isso obriga a decidir o que acontece a um **código personalizado
reutilizado**: o código é a chave primária e a linha removida continua lá, por
isso `create_invitation` lê também os apagados (`incluir_apagados=True`). Um
convite removido que ninguém resgatou não guarda histórico nenhum e o código
volta a estar livre; um que foi resgatado recusa, dizendo porquê.

E a tabela não cresce para sempre: o `cleanup_job` apaga os convites SEM USO
que expiraram ou foram removidos há mais de `INVITE_CLEANUP_DAYS`. Os
resgatados ficam, tenham a idade que tiverem — a mesma distinção que já valia
entre uma cobrança PIX abandonada, que é lixo, e um pagamento recebido, que
aconteceu.

⚡ **O polling da página de utilizadores não carrega a lista de convites.**
`/list` devolvia a tabela inteira, com o histórico de resgates de cada um, de
dez em dez segundos — para responder a "já foi usado algum?" e para filtrar as
duas abas do lado do navegador. São duas perguntas e têm duas rotas:
`/summary` são dois `COUNT(*)` e é o que o polling pede (e só recarrega a lista
quando a contagem MUDA, senão cada volta reescrevia a lista e fechava os menus
abertos); `/list` aceita `estado`, `pagina` e `por_pagina`, e ecoa os valores
EFETIVOS — devolver os pedidos fazia a interface calcular as páginas sobre um
tamanho que não foi o usado. ⚠️ As datas são comparadas como TEXTO no SQL, e
isso funciona porque tudo o que o painel escreve nessas colunas vem de
`datetime.now(timezone.utc).isoformat()`; uma data escrita à mão cai na aba
errada, e quem decide se o convite vale continua a ser o `convite_expirado`,
em Python.

⚠️ **O que é uma "conta" no resgate muda com o servidor, e a rota já não sabe
qual é.** `/api/invites/claim` importava `plexapi.myplex.MyPlexAccount` — uma
rota a saber que o servidor é o Plex, e um painel Jellyfin a carregar a
biblioteca do Plex para nada. É `conta_a_partir_de_credenciais`, do contrato,
que interpreta o corpo do pedido. Um teste impede blueprints NOVOS de
importarem `plexapi` e nomeia os dois que ainda o fazem (o fluxo de PIN no
`auth.py`, a validação da ligação no assistente) — a lista existe para
encolher e nunca para crescer.

⚠️ E a validação das credenciais locais é uma função de MÓDULO
(`jellyfin/account_manager.py`), não um método: não precisa de nada do manager,
e assim os duplos de backend dos testes chamam-na tal e qual. Um duplo com a
sua própria cópia das regras deixa de testar o que a aplicação faz — foi o que
aconteceu quando os limites de tamanho passaram da rota para o backend.

#### As chaves de API: uma por integração, com escopo

🛡️ Havia **UMA chave para tudo** (`INTERNAL_TRIGGER_KEY`, no config.json),
partilhada pelo endpoint de convites e pelo webhook do Seerr. Regenerá-la
porque um bot foi comprometido derrubava também o Seerr, e a chave dada ao bot
podia aceitar webhooks em nome do painel. **Foi removida**, com as duas rotas
(`/api/system/api-key`, `.../regenerate`) e o cartão "Chave de API
(Integrações)" que a mostrava: dois modelos de chave lado a lado só adiavam a
escolha, e o cartão antigo era o que estava mais à vista.

⚠️ **E a remoção é dos DOIS lados.** Tirar o cartão e deixar a verificação de
pé seria o pior dos mundos — uma credencial que continua a abrir a porta e que
o painel já não mostra, não regenera nem revoga. O `_remover_obsoleta` do
`load_or_create_config` apaga-a também do config.json de quem já a tinha: um
segredo que não abre porta nenhuma não fica a ocupar um ficheiro que vai
inteiro dentro do ZIP de backup. Quem a tivesse nos bots cria uma chave com o
escopo que aquela integração usa — é a única migração que isto pede, e está
dita nas duas páginas de documentação (`docs/api-convites-bot.md`,
`docs/integracao-seerr.md`).

⚠️ O rótulo de `chave_api.regenerar` FICA no `audit.js` mesmo sem ninguém
gravar essa ação: a auditoria não se apaga, e um painel com meses de uso tem
essas linhas na tabela. Sem rótulo, elas passariam a mostrar a chave crua.

`app/services/api_keys.py` dá-lhes nome, escopo (`ESCOPOS_DE_API`, em
`dominios.py`) e revogação individual. Quatro coisas que o módulo guarda:

- 🛡️ **fica o RESUMO, não a chave**, como em `password_resets`. Ela aparece
  UMA vez, ao ser criada; quem lesse a tabela — ou um ZIP de backup, que é só
  um ficheiro — ficava com uma porta aberta por cada integração ligada. Pela
  mesma razão não entra na auditoria: fica o nome, o prefixo e as permissões;
- 🛡️ **uma chave sem o escopo é recusada como se não existisse.** Dizer
  "existe mas não pode" confirmava a quem tenta que acertou na chave;
- ⚠️ **uma chave revogada FICA na tabela**: "revogada em março" é diferente de
  "nunca existiu", e é a primeira que responde a quem vai perceber, meses
  depois, o que deixou de funcionar;
- 🐛 **o prefixo não pode sair do `token_urlsafe`**, cujo alfabeto inclui o
  `_` — o separador da chave. Um prefixo como `-v_wYCdF` partia a chave em
  quatro pedaços e a leitura ficava com `-v` no lugar do prefixo: a chave era
  criada com sucesso e nunca mais reconhecida, em cerca de um terço dos casos.

🛡️ **O resumo é um HMAC com um segredo do painel** (`API_KEYS_PEPPER`, gerado
com a primeira chave e nunca substituído — trocá-lo invalida todas de uma vez e
em silêncio, a mesma regra do par VAPID). Quem leia só a base de dados fica com
resumos que não consegue verificar.

⚠️ **E NÃO é um KDF lento, de propósito.** O CodeQL marca isto como "hash fraco
sobre dados sensíveis" e, para uma palavra-passe escolhida por uma pessoa,
teria razão. Aqui o que se resume é `pnl_<prefixo>_<token_urlsafe(32)>` — 256
bits do `secrets` —, e contra isso não há força bruta que um hash lento trave,
porque não há espaço de palpites para encarecer. E sairia caro onde não compra
nada: o resumo é calculado a cada pedido cujo PREFIXO encontre uma chave viva
(`verificar` sai antes disso quando não encontra), e o prefixo não é segredo —
está à vista no painel e dentro da própria chave. Um deles chega para forçar o
trabalho em `/api/system/webhook/overseerr`, que é `@limiter.exempt`, e o painel
corre com UM worker gevent, onde trabalho de CPU não cede a vez a ninguém: cada
hash lento ali é o painel inteiro parado.

⚡ `last_used_at` não é escrito a cada pedido (um webhook bate dezenas de vezes
por minuto). 🐛 E é escrito pela `db.session`, **não** por uma ligação própria:
a ligação própria é o que o `audit.registar` faz e aqui seria pior, porque a
auditoria corre DEPOIS do commit do chamador e isto corre a meio — com o
ficheiro trancado, a segunda ligação espera o `busy_timeout` inteiro, trinta
segundos para gravar uma data. Não há nada pendente a arrastar: isto corre
antes do corpo da rota, e os dois `before_request` do painel só leem.

⚠️ **O contacto pré-atribuído a um convite é por CANAL, e o mapa é a porta
única.** `CONTACTOS` (em `media_server/invitations.py`) liga cada canal às suas
duas colunas, que **têm nomes diferentes dos dois lados**:
`invitations.telegram_id` contra `user_profiles.telegram_user`,
`invitations.discord_id` contra `user_profiles.discord_user_id`. A divergência
já custou um bug — código a ler `profile.get("telegram_id")`, que devolve
sempre `None` porque essa coluna não existe no perfil, e a parecer funcionar
por causa de um `or` à frente. Acrescentar um canal é uma linha no mapa, e não
a terceira cópia das mesmas quatro verificações.

🛡️ **Duas pessoas não podem ficar no mesmo contacto**, e é por isso que a
criação recusa (409) um ID já vinculado ou já num convite ATIVO: as
notificações de uma iriam para a outra, e entre elas vai o link de pagamento,
que funciona para quem o tiver. Um convite gasto ou expirado não bloqueia — já
não vai vincular ninguém.

🐛 **E `resolver_contactos_do_convite` é do ciclo de vida partilhado porque o
Jellyfin nasceu sem ele.** Vivia só no backend do Plex, como
`_handle_telegram_linking`: um convite gerado por um bot para um contacto
concreto criava a conta no Jellyfin e o perfil ficava SEM o vínculo — a pessoa
entrava e nunca mais recebia um aviso de vencimento, porque o painel não sabia
por onde lhe falar. A mesma família do `agendar_fim_do_teste` e do
`resolver_indicacao_pendente`. ⚠️ Ele revalida no RESGATE: entre gerar o
convite e usá-lo o ID pode ter passado a ser de outra pessoa, e nesse caso o
registo prossegue — o que se ignora é só o vínculo.

🔔 **E o resgate PERGUNTA por onde falar com quem acabou de entrar.**
`resolver_contactos_do_convite` só resolve o que um bot pré-atribuiu ao convite;
quem entra por um link público ficava com o perfil vazio, e aí
`_prepare_and_send` não tem por onde tentar e todas as notificações morrem em
silêncio. ⚠️ **Não é só o aviso de fim de teste**: `trial_duration_minutes` é 0
por omissão, por isso num convite normal não há teste nenhum (nem sequer
`expiration_date`) — mas é essa pessoa que vai ter vencimento e link de
pagamento. Por isso a recolha corre em TODO resgate, e o que muda com
`is_trial` é só a frase.

`CANAIS_DO_RESGATE` (ao lado do `CONTACTOS`) diz o que se pode pedir, e
`contactos_do_resgate.py` grava. ⚠️ **Os dois mapas não são o mesmo**: o
WhatsApp está no primeiro e não no segundo, porque não há
`invitations.phone_number` para o pré-atribuir — juntá-los faria
`resolver_contactos_do_convite` ler uma coluna que não existe. E só se pede o
canal que o painel consegue mesmo usar (`canais_ativos_no_resgate`: o Discord
ligado sem `DISCORD_WEBHOOK_URL` não conta), pela mesma razão do cartão do
Tautulli num assistente de Jellyfin.

🛡️ **A autorização é a SESSÃO do navegador que resgatou**, e a escolha não é
indiferente: a pessoa ainda não tem sessão de utilizador, e o que NÃO pode
servir é o `payment_token` que a resposta do resgate também leva — ele viaja por
Telegram e WhatsApp e fica no histórico dessas conversas para sempre, por isso
quem apanhasse um link antigo passaria a poder apontar as notificações de outra
pessoa (e com elas o link de pagamento) para si. A marca vale duas horas e sai
do cookie quando expira. 🛡️ E a regra dos contactos duplicados vale MAIS aqui do
que na criação do convite: ali quem escreve o ID é o administrador, aqui é quem
acabou de entrar, num formulário público — daí `phone_number` ter entrado no
`get_user_profile_by_contacto`.

⚠️ **E isto nunca derruba o resgate**: a conta já existe quando corre, e o link
do Telegram monta-se com uma chamada de rede. Sem o `try`, um bot fora do ar
fazia a pessoa ver um erro depois de a conta estar criada — e tentar de novo
dava "você já resgatou este convite".

#### O Telegram é o único que um formulário digitado não resolve

⚠️ Por **duas** razões independentes, e as duas têm de cair ao mesmo tempo:

1. o painel manda `chat_id = telegram_id or telegram_user` direto para a API, e
   um `@username` **não endereça uma conversa privada** — só um canal. O que
   serve ali é o id NUMÉRICO do chat, que a pessoa não conhece;
2. mesmo com o id certo, **um bot não pode iniciar uma conversa**: enquanto ela
   não abrir o bot e tocar em "Começar", qualquer envio devolve 403.

É por isso que o `telegram_id` dos convites vem sempre de um bot que JÁ falou
com a pessoa. O WhatsApp e o Discord não têm nada disto (um número vira
`{phone}@s.whatsapp.net`; o Discord publica no canal do webhook a mencionar o
id), e é por isso que só este canal tem um módulo — `telegram_vinculo.py`.

O deep link `t.me/<bot>?start=<codigo>` resolve os dois: ela toca (ponto 2) e o
`/start <codigo>` que o bot recebe traz o `chat.id` (ponto 1).

🛡️ **E lê-se com `getUpdates` SEM offset, de propósito.** O Telegram só descarta
o que já foi lido quando é chamado com um offset maior; sem ele devolve o que
está pendente e não confirma nada. É isso que permite ao painel ler **sem roubar
as atualizações de um bot que o administrador já tenha no mesmo token** — e este
painel tem uma API de convites para bots, por isso o caso não é hipotético. Com
um webhook registado o Telegram responde 409, e aí o painel DIZ que não dá:
nunca um `deleteWebhook`, que partiria em silêncio o bot de quem administra. Há
um teste que recusa essas duas chamadas.

⚠️ O `long_polling_timeout` é 0 porque isto corre DENTRO de um pedido HTTP num
painel com um worker gevent. E "ainda não chegou" (200 com `vinculado: false`)
não é o mesmo que "não dá" (503): a página precisa da distinção para dizer a
coisa certa a quem está à espera.

🐛 **E o `is_trial` faltava no `user_data` do Jellyfin.** O `invite.js` decide
com `if (isTrial && userData.payment_token)` se mostra o botão de pagamento no
fim do resgate, e o `user_data` do Jellyfin é o perfil gravado — que tem
`trial_end_date` e não essa chave. Num painel Jellyfin, quem resgatava um
convite de TESTE via a data de fim e não via como pagar.

🐛 **E o texto do e-mail no formulário de registo mentia**: dizia "Usado para
avisos de vencimento e para recuperar seu acesso", e as duas afirmações eram
falsas — o painel nunca enviou e-mail (não há SMTP em lado nenhum) e o link de
redefinição vai pelos contactos registados. O e-mail serve para encontrar a
pessoa no Seerr, e é isso que ele passa a dizer.

🔔 **Um convite resgatado avisa o administrador por push**
(`send_invite_claimed_admin_notification`, com o seu interruptor
`PUSH_ADMIN_INVITES`). O sino do painel só avisa quem está com ele aberto, e
quem gera convites por um bot não ficava a saber de todo. A nota do convite vai
no corpo porque é ela que diz PARA QUEM ele era — "a ana entrou pelo convite do
João do grupo" é uma informação; "alguém resgatou um convite" não é.

Nos templates, o contexto global expõe `media_server.type`, `.name` e
`.capabilities` — use-os para esconder o que não se aplica
(`{% if media_server.capabilities.fontes_media_online %}`).

Quando uma funcionalidade não existe em todos os servidores (convites nativos,
Fontes de Mídia Online, login delegado), pergunte pelas
`media_server.capabilities` em vez de comparar o tipo de servidor — a interface
cresce por capacidades, não por uma cascata de exceções por marca. Uma
capacidade em falta esconde a funcionalidade; nunca deve dar erro.

**A diferença que não é técnica**: no Plex, o utilizador traz a conta dele e o
painel convida-a. No Jellyfin as contas são **locais**, o painel CRIA-as e
passa a ser responsável por entregar as credenciais.

Isso divide o login em dois: o que MUDA é apenas como se prova a identidade
(PIN do plex.tv, ou `authenticate()` do backend contra o servidor). Tudo o que
vem depois — é o administrador? tem acesso? o perfil está ativo? é preciso
mandar pagar? — é partilhado em `_autorizar_e_iniciar_sessao`
(`app/blueprints/auth.py`). Nunca duplique essa metade por backend.

A rota `/auth/login/credentials` recusa-se a funcionar quando
`capabilities.login_delegado` é verdade: no Plex, aceitar credenciais seria
pedir a palavra-passe do plex.tv a quem entra — exatamente o que o fluxo de PIN
existe para evitar. É a única rota do painel onde se podem testar
palavras-passe, e por isso tem três travões: 10 por minuto por ENDEREÇO
(Flask-Limiter), um contador por CONTA e outro, mais largo, por endereço
(`utils/tentativas_de_login.py`), e um limite ao tamanho do que se aceita antes
de tocar na rede. ⚠️ O limite por IP sozinho não chegava: quem ataca uma conta
concreta tinha as dez por minuto todas para ela, e mudar de endereço dava-lhe
outras dez. E conta-se o nome ESCRITO, exista ou não — travar só contas reais
diria quais existem neste servidor, o oposto da mensagem de erro única. Isso muda o fluxo do
convite (passa a pedir utilizador e palavra-passe) e enfraquece o anti-abuso de
períodos de teste — uma conta nova não custa nada e nada a liga à mesma pessoa.
Um convite de teste num servidor destes deve exigir um contacto verificável.

Em troca, o bloqueio é muito melhor: `Policy.IsDisabled` é um booleano, e o
utilizador mantém as bibliotecas. No Plex é preciso retirar as partilhas,
guardar quais eram e repô-las depois.

⚠️ **Reativar não é "enviar um convite".** Era o que estava escrito no
`PlexSubscriptionManager` — que os DOIS backends usam, porque o que lá vive é a
política de vencimentos, igual em todos. Um pagamento de reativação num painel
Jellyfin ia parar a `invites.send_invite()`, que ali quer dizer **criar uma
conta**: com a conta a existir já, a tentativa era uma conta duplicada com o
email por nome e uma palavra-passe que ninguém veria — e a notificação levava um
`clients.plex.tv/.../accept` a quem nunca teve conta no Plex.

Repor o acesso é agora do contrato (`restaurar_acesso`): no Plex convida-se
outra vez e fica um `link_pendente` (é ele que faz aparecer o botão de
confirmação manual na página de pagamento); onde as contas são locais tira-se o
`IsDisabled`, repõem-se as bibliotecas do perfil, e não há nada para aceitar.

⚠️ **"As bibliotecas do perfil" só lá estão se alguém as puser.** Quem as grava
é `update_user_libraries`, e a gravação dele está atrás de um
`if perfil is not None` — no RESGATE o perfil ainda não existe, por isso a
escrita era saltada em silêncio. O convite aplicava-as no servidor e o perfil
ficava vazio; ao reativar, a conta voltava aberta e sem ver nada. Hoje é o
`_criar_perfil_local` que as grava, na criação do perfil.

🐛 **O `allow_downloads` andou a ser escrito e descartado em silêncio.** O
convite guarda-o e o resgate mandava-o para `set_user_profile`, mas
`user_profiles` não tinha a coluna — o SQLAlchemy ignorava a chave. O sintoma só
aparecia meses depois, porque `restaurar_acesso` lê a permissão do PERFIL: quem
tinha download e era bloqueado voltava, depois de pagar, sem poder descarregar
nada. Valia nos dois backends. A coluna existe desde `d8f2b4e91c37`, a começar
em 0 para quem já cá estava (conceder uma permissão que ninguém pediu era pior).

⚠️ **As duas metades da mesma decisão andam juntas**: o que a pessoa pode ver
(`libraries`) e se pode levar consigo (`allow_downloads`). `update_user_libraries`
— a porta única para as mudar — grava as duas; gravar só a primeira fazia a
reativação repor as bibliotecas e perder o download.

⚠️ **E se a conta já tiver sido apagada, é RECRIADA** — o `removal_job` apaga-a
mesmo ao fim de `DAYS_TO_REMOVE_BLOCKED_USER` dias, e quem paga tem de voltar a
entrar. Isso obriga a três coisas que andam sempre juntas (`_recriar_conta`):

- ⚠️ **a identidade MUDA.** O Jellyfin atribui o GUID ao criar a conta e não
  aceita que se lhe imponha um: para o servidor, é outra pessoa. O perfil é
  migrado com `data_manager.migrar_identidade()`, que arrasta tudo o que aponta
  para ele — pagamentos, conquistas, cortes, `referred_by` e a lista JSON
  `invitations.claimed_by_ids`. Uma coluna nova que cite
  `user_profiles.media_user_id` tem de entrar em `_TABELAS_COM_IDENTIDADE`, ou
  fica a apontar para um perfil que já não existe. Sem isto, quem pagou
  reaparecia como um estranho: sem histórico e sem o vencimento que acabou de
  pagar;
- ⚠️ **quem chama tem de SEGUIR o identificador novo.** `restaurar_acesso`
  devolve `media_user_id`, e é por isso que ele é reposto ANTES do resto da
  renovação: feito depois, a data de vencimento, a tarefa de expiração e o
  limite de telas ficavam gravados num identificador que já não existe. O
  `_process_successful_payment` relê a linha do pagamento (que a migração já
  moveu) pela mesma razão. E nunca se grava o perfil antigo sob a chave nova
  sem confirmar que ela existe: `username` é único, e o INSERT rebentava depois
  de o pagamento já ter sido aceite;
- 🛡️ **a palavra-passe é nova e o painel tem de a entregar.** Vai numa
  notificação própria (`send_credentials_notification`, evento `credentials`,
  com template por canal), que é o ÚNICO sítio do painel por onde uma
  palavra-passe viaja — e não vai para o log em lado nenhum. Por isso só se
  recria a conta quando há por onde a entregar (Telegram, Discord ou WhatsApp
  no perfil): uma conta com uma palavra-passe que ninguém vai receber é pior do
  que conta nenhuma, e o administrador tem de saber que ficou por fazer.

⚠️ E os Protocols do contrato verificam os NOMES dos métodos, não as
assinaturas: `send_invite` tinha `media_user_id` no Plex e `plex_user_id` no
Jellyfin, e quem chamava pelo nome levava com um `TypeError` nas duas
reativações. Há agora um teste que compara as assinaturas dos dois backends
(`tests/test_media_server_contract.py`).

`PlexManager` (em `media_server/plex/backend.py`) é uma fachada sobre os
submanagers: `.conn`, `.users`, `.invites`, `.subscriptions`, `.online_media`.
Lógica nova de Plex vai no submanager correspondente, não na fachada.

**A fachada é a fronteira.** Fora de `media_server/plex/` nada deve saber que o
servidor é o Plex: não há objetos `plexapi` acessíveis a partir dela, e há um
teste que falha se um método com 'plex' no nome reaparecer na sua superfície
pública (`tests/test_media_server_contract.py`). Quando falta alguma coisa,
acrescente-se um método ao contrato — foi assim que nasceram `is_connected()`,
`get_base_url()` e `authorize_image_url()`, em vez de o painel ir buscar
`.conn.plex`, `._baseurl` e `._token` por fora.

Cuidado com a diferença entre `users.list_users()` (leitura crua do servidor) e
`media_server.get_all_users()` (a mesma lista tratada para a interface). Foram
o mesmo nome em duas camadas durante muito tempo.

### Streams: política e vocabulário, separados

`StreamManager` (`app/services/stream_manager.py`) já não fala com nenhum
servidor. Recebe um `SessionsProvider` que lhe entrega `MediaSession` — uma
reprodução já traduzida (quem está a ver, estado, progresso, plataforma, capa)
— e sabe encerrá-las.

- **Política, no `StreamManager`**: quantas telas são permitidas, quem é
  cortado primeiro, o anti-spam dos cortes, o debounce dos eventos e a cache
  curta do "Reproduzindo Agora".
- **Vocabulário, no provider** (`media_server/plex/sessions.py`): onde está o
  estado do leitor, qual o campo da capa, como se reconhece um Chromecast, o
  formato das notificações do websocket e a filtragem dos pings de progresso.

`MediaSession.raw` leva o objeto original do servidor, e existe apenas para o
provider o receber de volta em `terminate()`. Nada fora do provider o deve
inspecionar, ou volta a haver conhecimento do Plex espalhado pelo painel.

⚠️ **A sessão e a reprodução não são a mesma coisa.** `MediaSession` tem duas
chaves: `session_key` é o que se usa para MANDAR PARAR (o endereço do comando),
`playback_key` é o que identifica ESTA reprodução. No Plex coincidem (o
`sessionKey` muda a cada play); no Jellyfin o `Id` da sessão é do APARELHO e
sobrevive a parar e recomeçar. O anti-repetição dos cortes usa a
`playback_key` — com a da sessão, quem recomeçasse logo a seguir a um corte
ficava toda a janela sem ser incomodado.

`deduplicate_sessions()` é do provider porque só ele sabe se o seu servidor
devolve DUAS entradas para uma só reprodução — o Plex lista o telemóvel que
comanda um Chromecast ao lado do Chromecast; o Jellyfin não, porque quem apenas
comanda vem sem `NowPlayingItem` e já é descartado antes.

⚠️ **Fundir a mais é pior do que não fundir**: o que se funde deixa de contar
para o limite de telas. Enquanto o filtro do Plex era aplicado a todos os
servidores e bastava "mesmo utilizador + mesmo título", duas reproduções
genuínas da mesma mídia contavam como uma e o limite nunca era aplicado. O Plex
exige agora também a mesma POSIÇÃO de reprodução. Na dúvida, devolva a lista
intacta.

`terminate()` devolve `False` quando a reprodução ainda não pode ser encerrada
(a carregar, sem identificador interno no servidor). Quem chama reagenda em vez
de dar o corte por feito — não é um erro, é um "ainda não". Devolve `False`
também quando o servidor RECUSA a ordem: durante muito tempo o backend do
Jellyfin devolvia `True` em todos os casos, e a recusa desaparecia sem deixar
rasto, porque quem chama trata `True` como "feito" e não volta a tentar.

⚠️ **Aceitar a ordem não é obedecer-lhe.** Há clientes que recebem o `Stop`, o
servidor confirma, e a reprodução continua — o leitor integrado da aplicação
Android do Jellyfin (ExoPlayer) é um deles; pelo navegador o mesmo corte
funciona.

### O limite de telas: do painel, e do servidor quando há plugin

⚠️ **Nenhum dos servidores sabe limitar reproduções simultâneas de origem.** O
Plex não tem nada. O Jellyfin parece ter — `Policy.MaxActiveSessions`, que a
interface dele chama "Número máximo de sessões de usuários simultâneas" — e
**não é isso**: limita AUTENTICAÇÕES. Já foi tentado, e falha nos dois sentidos:

* não corta ninguém. Só recusa entradas NOVAS, por isso quem já estava ligado
  continua a reproduzir à vontade — exatamente o caso que se queria travar;
* tranca a pessoa fora do PAINEL. Entrar no painel autentica-se contra o
  servidor e ocupa uma sessão, por isso quem tivesse os aparelhos ligados tinha
  de sair de um para conseguir entrar.

**O plugin StreamLimiter resolve-o, e é a única coisa que resolve.** Não manda
parar: intercepta o PEDIDO HTTP da mídia (um `IAsyncResourceFilter`) e recusa-o
antes de servir um byte. Nenhum cliente pode ignorar um erro no pedido do
próprio ficheiro — nem o leitor integrado da aplicação Android, que é onde tudo
o resto falha. `stream_limit.py` escreve lá o que o painel decidiu
(`POST /StreamLimit/SetUserStreamLimit`, tudo em query string), e o
`cleanup_job` chama `sync_screen_limits()` para repor o que divergir: quem
instala o plugin DEPOIS de já ter os limites no painel não tinha quem os
escrevesse, e o painel é a fonte da verdade para esses valores.

⚠️ **"0" quer dizer coisas diferentes dos dois lados.** No painel é ILIMITADO;
no plugin apaga o limite próprio e passa a valer o `DefaultMaxStreams` do
servidor — que, existindo, é um limite e não a ausência dele. Não há como dizer
"sem limite para esta pessoa" enquanto o padrão existir: fica um WARNING a
dizê-lo, em vez de o painel mostrar "Ilimitado" sobre alguém que não está.

O corte do painel continua a ser preciso: o plugin trava o que COMEÇA, e não
sabe nada de assinaturas vencidas nem de bloqueios — e é o painel quem envia a
mensagem.

🛡️ **O que o plugin bloqueia entra na auditoria pelo LOG do servidor**
(`stream_gate_log.py`, importado de 5 em 5 minutos pelo `server_block_import_job`,
através de `importar_bloqueios_do_servidor()` no contrato). Quem bloqueia é ele,
dentro do processo do Jellyfin: o painel não participa, e a "Auditoria de
Cortes" mostrava só os cortes dela própria — o limite era cumprido sem deixar
rasto nenhum aqui. E o log é a única fonte possível: a API do plugin é só sobre
limites (`SetUserStreamLimit`, `GetAllStreamLimits`), não sobre o que já
aconteceu. A linha é esta:

    [2026-09-12 20:23:28.756 -03:00] [INF] [97] Jellyfin.Plugin.StreamLimit.Gate.StreamGateFilter: Stream gate denied playback negotiation. User: 44874bdd-..., device: "7e0fa1c8...", limit: 1

Quatro coisas que ela exige e que têm teste de regressão:

- ⚠️ **a hora vem no fuso do SERVIDOR** (`-03:00`). Guardá-la como está punha o
  corte três horas no futuro — e à frente da marca de água, o que faria a
  importação seguinte ignorar tudo o que viesse a seguir;
- ⚠️ **o GUID vem com hífenes** e o painel guarda-o sem (`chave_de`, como em
  todo o resto do Jellyfin);
- **só se lê o que ainda não se leu**, retomando sempre num FIM DE LINHA (o
  ficheiro está a ser escrito enquanto o lemos). Pede-se com `Range`; se o
  servidor mandar o ficheiro inteiro, o corte é feito no painel;
- **quem impede a repetição é a auditoria**, não a posição de leitura: a marca
  de água é o último registo com a razão `plugin_limit_blocked`
  (`get_last_termination_timestamp`). Perder a cache faz reler, não duplicar.

Não há título nessas linhas — o plugin recusa ANTES de haver reprodução — por
isso a auditoria mostra o limite atingido no lugar dele, e o nome do aparelho
(de `GET /Devices`) no lugar da plataforma.

`update_screen_limit()` na fachada é a **única porta** para mudar o limite de
alguém: grava o perfil e leva-o ao plugin. Quem escrever
`profile['screen_limit']` à mão espalha de novo por seis sítios uma decisão que
é de um só (foi o que aconteceu: rotas de administração, upgrades pró-rata e
renovações, cada uma à sua maneira).

A deteção dos plugins do Jellyfin é partilhada em `plugins.py` (o StreamLimiter
e o Playback Reporting precisam do mesmo), com cache de 10 minutos — instalar
um plugin obriga a reiniciar o Jellyfin, por isso não muda sozinho. Procura-se
pelo GUID **e** pelo nome: o GUID é estável, o nome é o que se lê no log.

🔇 **E o log dela só fala quando o estado MUDA.** "Plugin StreamLimiter
encontrado: o limite de telas passa a ser imposto pelo servidor" descreve um
ESTADO, mas era escrito a cada vez que a cache expirava — a mesma linha de dez
em dez minutos, para sempre, a empurrar para fora do ecrã o que interessa. Hoje
`_ja_anunciado` (memória do PROCESSO, não a cache: um painel que arranca deve
dizer uma vez com que plugins conta, e guardá-lo em disco calava esse arranque)
faz com que se anuncie uma vez ter passado a contar com o plugin, e uma vez ter
deixado de poder contar — esta última em falta até agora, o que fazia de
desinstalar o StreamLimiter uma mudança silenciosa no que o painel consegue
garantir. ⚠️ Duas coisas que NÃO são mudanças de estado: uma falha de rede
(`_procurar` devolve `None` e não se toca no que foi anunciado — não saber não é
o mesmo que não existir) e um painel que nunca teve o plugin (a ausência de algo
opcional não é um acontecimento, e anunciá-la seria trocar um ruído por outro).

⚠️ **Não tente trazer o bloqueio do StreamLimiter para dentro do painel.** É a
pergunta óbvia e a resposta é não, por uma razão de arquitetura e não de
esforço: o que faz o plugin funcionar é ele correr DENTRO do processo do
Jellyfin. Ele regista-se no pipeline de MVC do próprio servidor
(`PostConfigure<MvcOptions>`, em `PluginServiceRegistrator`) e vê cada pedido de
mídia antes de a resposta começar. O painel é outro processo, noutra porta: o
pedido `cliente → jellyfin:8096/Videos/.../stream` nunca passa por ele.

Para o interceptar, o painel teria de ser um proxy reverso à frente do Jellyfin
— todos os bytes de todas as reproduções a atravessar o Flask, que corre em
gevent com **1 worker** de propósito. Seria o painel a tornar-se o ponto único
de falha do servidor de média.

E a API do núcleo não dá alternativa: não existe rota para matar uma
transcodificação nem para recusar uma reprodução (confirmado no OpenAPI). Há o
`Playing/Stop`, que o cliente pode ignorar, e a revogação do aparelho. O plugin
mata a transcodificação e recusa o `PlaybackInfo` porque tem acesso aos
serviços internos do servidor — coisa que nenhum cliente HTTP tem.

(À parte disso, o plugin é GPL-3.0: copiar o código dele obrigaria o painel
inteiro à mesma licença. Mas mesmo com isso resolvido, o mecanismo não é
portável.)

`clear_session_limits()` (no `cleanup_job`, guardado por
`JELLYFIN_SESSION_LIMIT_CLEARED`) tira do servidor o `MaxActiveSessions` que o
painel lá pôs enquanto durou a ideia errada — de UMA vez. Repeti-la todos os
dias desfaria, às escondidas, um limite que o administrador tenha posto de
propósito na interface do Jellyfin.

⚠️ **Autenticar abre uma sessão no servidor, e deitar o token fora não a
fecha.** Cada entrada no painel deixava uma sessão órfã na lista do Jellyfin.
`authenticate()` fecha-a com `POST /Sessions/Logout` — que encerra a sessão de
QUEM CHAMA, por isso o pedido vai com o token do utilizador (`api.request(...,
token=...)`) e não com a chave de API do painel.

Sem o StreamLimiter, o corte do painel é a única defesa — e havendo clientes
que o ignoram, o último recurso é `force_terminate()`: no Jellyfin revoga o acesso do APARELHO
(`DELETE /Devices?id=`), o que invalida as credenciais dele e mata a reprodução
obedeça o cliente ou não. Chega-se lá ao fim de `TENTATIVAS_ANTES_DE_FORCAR`
pedidos à mesma `playback_key`, e só com `FORCE_STREAM_TERMINATION` ligado
(**desligado por omissão**: obriga a pessoa a autenticar-se de novo naquele
aparelho e não se desfaz a partir do painel). O Plex devolve `False` — sem nada
mais forte a oferecer, o motor volta a pedir.

⚠️ **Nem todos os servidores TÊM um último recurso, e a interface tem de o
dizer.** A definição "Forçar o encerramento em aparelhos que ignoram o comando"
aparecia num painel Plex, onde não faz nada — e, desligada, o log ainda
aconselhava a ligá-la. Quem decide é `capabilities.corte_forcado`: a definição
esconde-se onde não há o que ligar, e o aviso passa a dizer a verdade ("este
servidor não tem nada mais forte a oferecer").

O mesmo facto é declarado em DOIS sítios, porque são dois consumidores
diferentes: a capacidade do backend serve os templates, e
`sessions.suporta_corte_forcado()` serve o `StreamManager`, que fala com o
provider e não com o backend. Um teste de contrato compara os dois — divergirem
seria a definição a aparecer sem fazer nada, ou a desaparecer onde faz. E
escondê-la não a apaga: `save_settings` só escreve os campos que vêm no pedido,
por isso gravar noutro separador não desliga em silêncio uma defesa já pedida.

⚠️ **O `DeviceId` da sessão é o que o CLIENTE diz ser**, e nem sempre há um
aparelho registado com esse id — o `DELETE /Devices` responde então 400 com um
corpo vazio, que não diz nada a quem lê o log. Por isso confirma-se primeiro na
lista real (`GET /Devices`) e usa-se a grafia do SERVIDOR. E o motor só tenta o
último recurso UMA vez por reprodução: sem essa trava, um aparelho que o
servidor recusa dava um pedido falhado e um ERROR de 15 em 15 segundos, para
sempre. Quando não resulta, fica um ERROR a dizer que naquele cliente o limite
não vai ser cumprido — que é a verdade, e é melhor do que insistir em silêncio.

Os URLs de imagens passam todos por `app/utils/image_proxy.py`; o prefixo
(`plex:`, `plex_account:`, `url:`) é escolhido pelo backend.

### Identidade do utilizador: texto, não inteiro

O Plex identifica as contas por um inteiro, o Jellyfin por um GUID. A coluna
chama-se `media_user_id` e é `VARCHAR(64)` em todas as tabelas.

A normalização vive no **tipo da coluna** (`UserId`, em `app/models.py`), e não
nos métodos do `DataManager`: assim aplica-se sozinha ao que é gravado e ao que
é comparado num `WHERE`, incluindo em consultas que ainda ninguém escreveu.
Para comparar identidades em Python use `same_user()` / `normalize_user_id()`
de `app/utils/identity.py` — nunca `==` directo nem `int()`.

A armadilha que isto fecha: **no SQLite, uma consulta feita com o inteiro 123
não encontra a linha guardada como `'123'`**, e o ID chega ao painel em
formatos diferentes conforme a origem (inteiro da API do Plex, texto da sessão,
texto de um URL). Quando um dicionário indexado por ID parece "não encontrar
nada" sem dar erro, é quase sempre isto — foi assim que os utilizadores
bloqueados deixaram de ser expulsos durante esta migração.

⚠️ **A base de dados normaliza sozinha; a lista do SERVIDOR não.** A plexapi
identifica as contas por um inteiro, e juntar essa lista aos perfis com `in` ou
`==` dá sempre falso sem dar erro: o painel conclui que cada utilizador do
servidor é NOVO e que cada perfil guardado já lá não está. Na página de
utilizadores isso dava o cartão sem nome, sem vencimento, com zero telas e sem
link de pagamento — e o perfil verdadeiro marcado como `inactive`. É o que se
vê ao restaurar o backup de um painel só-Plex, em que os IDs eram inteiros dos
dois lados. Por isso a tradução é da **fachada**: `get_all_users()` e
`get_user_by_id()` devolvem o `id` já em texto, como tudo o resto do painel.
O mesmo vale para o que vem do Tautulli (`user_id` inteiro), normalizado em
`get_watch_stats` — era por ele que o pódio ia buscar o nível de XP e o avatar.

O campo JSON de entrada chama-se `media_user_id`; `plex_user_id` continua a ser
aceite em `user_lookup_by_id` para não partir integrações já feitas.

`StatsManager` (`app/services/stats_manager.py`) segue o mesmo padrão sobre
`app/services/tautulli/` (`api_client`, `stats_handler`,
`recommendations_handler`) — e a agregação que lá vive é partilhada por todos
os servidores. Ver a secção das estatísticas.

### Estatísticas, histórico e aparelhos: três coisas, não uma

**As ESTATÍSTICAS** (pódio, XP, conquistas, recomendações, Wrapped) saem todas
de uma coisa só: **uma lista de reproduções**. A agregação vive em
`services/tautulli/stats_handler.py` e é a MESMA para todos os servidores; o
que muda é a FONTE que a alimenta, injetada no `StatsManager`:

- num painel Plex, o Tautulli quando está configurado e o **próprio servidor**
  quando não (`media_server/plex/stats_api.py` despacha entre os dois, a cada
  pergunta — configurar o Tautulli não obriga a reiniciar o painel);
- num painel Jellyfin, o próprio servidor
  (`media_server/jellyfin/stats_api.py`).

Uma fonte tem de saber responder a `get_history`, `get_recently_added`,
`get_metadata` e `image_payload`, e dizer se está `is_configured`. Quem a
escolhe é o `_fonte_de_estatisticas()` do `create_app`, pelo tipo de servidor.

⚠️ **Escrever uma segunda agregação era a alternativa, e teriam divergido no
primeiro ajuste ao XP.** Foi por isso que o prefixo das capas deixou de estar
escrito à mão (`tautulli:...`) e passou a vir de `image_payload`: era a única
coisa, em toda a agregação, que sabia de que servidor se tratava.

Onde não há de onde as tirar, escondem-se — as ligações do menu e as sub-abas
Conquistas, XP e Recomendações da Gamificação ("Indique e Ganhe" é de pagamentos
e fica). As páginas `/statistics` e `/wrapped` redirecionam: esconder a ligação
não chega, um marcador nos favoritos dava uma página vazia sem explicação. E o
`sync_xp_job` desiste logo: sem fonte, dava um erro por utilizador (com
repetições) todas as madrugadas.

⚠️ **São três perguntas parecidas, e trocá-las dá erros opostos:**

- `media_server.capabilities.estatisticas` — o servidor SUPORTA-AS. Hoje é
  verdadeira nos dois.
- `media_server.capabilities.estatisticas_externas` — a fonte é um serviço À
  PARTE, que o administrador tem de configurar (o Tautulli). É ela que decide
  se o cartão do Tautulli aparece nas Conexões e no estado do sistema: num
  painel Jellyfin não há ali nada para configurar, e pedir credenciais de um
  serviço que não vai ser usado é pior do que não as pedir.
- `estatisticas_disponiveis()` (`app/utils/estatisticas.py`, exposto aos
  templates como `media_server.estatisticas`) — EXISTEM AGORA: o servidor
  suporta-as **e** há uma fonte ligada. É por esta que se escondem o menu, as
  páginas e as sub-abas. 🐛 Num painel Plex ela exigia o Tautulli, e sem ele
  escondia tudo — o mesmo engano que o histórico já tinha corrigido, e sem a
  mesma desculpa: o Plex SABE o que cada pessoa viu. Hoje basta o servidor
  estar ligado; esconde-se só quando não há NENHUMA das duas fontes, que é o
  caso do servidor em baixo.

⚠️ **`api_client` e `fonte_externa` não são a mesma coisa, e trocá-las tem
sintomas opostos.** A fonte do Plex é um despachante: o `is_configured` dela
responde "há estatísticas", que passou a ser verdade sem Tautulli.
`stats_manager.fonte_externa` é o cliente do Tautulli, e é a ele que se pergunta
pelo ESTADO, pelo endereço e pelo teste de ligação — senão o estado do sistema
mostrava o Tautulli OFFLINE num painel a funcionar perfeitamente sem ele, e
`_tautulli_ativo()` mandava o histórico bater a um Tautulli que não existe.

⚠️ **O nome da marca aparece no meio das frases** ("Ver no Plex", "O Seu Plex
Wrapped"). Estava escrito à mão nas páginas de estatísticas, que eram só do
Plex — num painel Jellyfin passava a ser a marca errada, como aconteceu com o
`default.svg`. Use `media_server.short_name` (`SHORT_NAME` no backend). Pela
mesma razão, o link "Ver no ..." das recomendações deixou de ser montado na
rota: `link_para_item()` é do contrato, porque o endereço é do servidor (uma
página de app.plex.tv, ou a interface web do próprio Jellyfin).

⚠️ Ao escondê-las, a casa de quem não é administrador deixa de existir — e
estava escrita à mão em cinco sítios. `endpoint_inicial_do_utilizador()`
(`app/utils/navigation.py`) responde por todos.

**O HISTÓRICO e os APARELHOS não ficam**: são do contrato
(`get_watch_history`, `get_user_devices`) e cada backend responde à sua
maneira. No Plex vão ao Tautulli quando ele existe e, quando não,
a `plex/history.py`; no Jellyfin, a `jellyfin/history.py`. Duas diferenças a
ter presentes:

- O histórico do Jellyfin tem DUAS fontes, e a boa é opcional (ver a seguir).
  Sem o plugin, é por **item** e não por reprodução (`GET /Items` com
  `Filters=IsPlayed` e `SortBy=DatePlayed`, lendo o `UserData`): ver o mesmo
  episódio três vezes dá uma linha, e a coluna do reprodutor vem vazia porque o
  núcleo não guarda em que aparelho cada item foi visto — inventar seria pior.
  `PlayedPercentage` só vem preenchido a meio de uma reprodução: um item com
  `Played` é 100%, não 0.
- Os aparelhos do Jellyfin são melhores do que os do Plex: `GET /Devices` dá os
  que estão REGISTADOS na conta. No Plex não há como pedi-los (a API do
  plex.tv só lista os do dono), por isso são deduzidos do histórico.

⚠️ **O avatar vive numa CÓPIA dentro do cookie da sessão**, tirada no momento
do login — e isso dá dois problemas que parecem um só:

- quem já estava autenticado quando o formato do thumb mudou continuava a
  carregar o caminho cru do servidor, e o browser pedia-o ao PAINEL (404). O
  `load_user()` passa tudo o que vem da sessão por
  `media_server.thumb_para_interface()`, que TEM de ser idempotente: recebe
  tanto o formato antigo como o novo;
- quem põe a imagem de perfil DEPOIS de entrar nunca a via, porque a sessão não
  se reescreve sozinha — era o "?" do administrador. O `/account/details` já
  fala com o servidor, por isso traz o avatar atual E grava-o na sessão, que é
  de onde o `base.html` o lê em todas as outras páginas.

⚠️ **Tudo o que sai da fachada com uma imagem passa por
`_thumb_para_a_interface()`** — `get_all_users()`, `get_user_by_id()`,
`get_owner_account()` e o `authenticate()`. Foram descobertos um a um, cada um
com o seu 404: é a armadilha do `list_users()` (cru) contra o que a interface
consome, e o sintoma nunca é um erro no log, é só a imagem em falta.

⚠️ **`GET /Devices?userId=` NÃO filtra por dono.** Filtra pelos aparelhos que
aquele utilizador TEM PERMISSÃO DE USAR (`CanAccessDevice`) — e como toda a
gente tem `EnableAllDevices` por omissão, deixa passar tudo: cada pessoa via a
lista inteira de aparelhos do servidor, igual para todos. Quem diz quem usou o
aparelho é o `LastUserId`, e o filtro é do painel.

⚠️ **O mesmo GUID aparece com e sem hífenes.** O `Id` de um utilizador vem sem;
os campos declarados `format: uuid` no OpenAPI (o `LastUserId`, as chaves do
StreamLimiter) vêm com. Comparar com `==` dá sempre falso, e o sintoma não é um
erro — é uma lista vazia ou um filtro que não filtra. Use `mesma_conta()` /
`chave_de()` de `jellyfin/identity.py`. Isto não pertence ao
`app/utils/identity.py`: lá a identidade é texto e serve o Plex, que identifica
por inteiro; tirar hífenes é conhecimento do Jellyfin.

⚠️ **`default.svg` é o LOGÓTIPO DO PLEX.** O catálogo de ícones de plataforma
veio do painel original, e o ícone de recurso é a marca do Plex — num painel
Jellyfin era a marca errada em todos os aparelhos. Agora há um `jellyfin.svg`
(o logótipo oficial, CC BY-SA 4.0), `plataforma_de()` devolve `'jellyfin'` em
vez de `'plex'` para os clientes dele, e o `.platform-default` segue o servidor.

⚠️ **Quem classifica o ícone é o servidor, não o browser.** A lista de
aparelhos leva um `platform_key` além do `platform`: o primeiro é a classe CSS,
o segundo é o texto que a pessoa lê. A interface adivinhava a classe pela
PRIMEIRA palavra do `platform` — com o Jellyfin dava sempre "jellyfin"
("Jellyfin Web", "Jellyfin Android"...), que nem existia no catálogo.

⚠️ **O ADMINISTRADOR não tem perfil local até entrar.** O login dele devolve na
primeira ramificação de `_autorizar_e_iniciar_sessao`, antes da parte que cria
perfis, e `_sync_plex_and_local_profiles` salta-o de propósito (no Plex ele nem
aparece na lista de amigos). Isso deixou de servir quando ele passou a contar
nas estatísticas — o XP precisa de onde ficar guardado —, e hoje
`_garantir_perfil_do_administrador()` cria-lho NO LOGIN: inteiro, no único
momento em que se sabe o nome, o email e o servidor. Não atualiza um perfil que
já exista: o que lá está foi escolhido na "Minha Conta".

**O código defensivo continua todo a fazer falta**, porque o perfil só aparece
no primeiro login DEPOIS desta versão — e num painel já a correr isso pode
demorar. As rotas da "Minha Conta" (`/account/details`, `/account/profile`,
`/account/privacy`) assumiam um dicionário e rebentavam com `AttributeError`
assim que ele abria a página. As que gravam criam o perfil em falta; a que lê
usa um dicionário vazio. `/payments/options` seguia a mesma armadilha por outro
caminho: respondia 400 ("utilizador não especificado"). Sem assinatura não há
planos, e isso não é um erro do PEDIDO — só um token inexistente o é.

E as ESTATÍSTICAS caíram na mesma pela terceira vez, com três sintomas
diferentes: `/api/statistics/user/<id>` rebentava com um `AttributeError`
(`profile.get(...)` sobre `None`), `/api/statistics/wrapped/<id>` respondia 404
"Usuário não encontrado", e o `StatsManager` devolvia tudo vazio com um WARNING
a dizer que não encontrou o perfil. O perfil local guarda o XP, a privacidade e
o nome — **o histórico é do servidor**, e existe à mesma. Quem não tem perfil
também não pediu privacidade, e o nome (que só serve para as notificações de
conquistas) vem do servidor por `_nome_do_utilizador()`. Quem não é
administrador tem sempre perfil: sem ele, o `load_user` encerra-lhe a sessão.

⚠️ **O administrador é o dono, e a interface passou a dizê-lo.** Ele não foi
convidado (não tem "membro desde"), não tem plano nem vencimento, e o painel
não lhe impõe limite de telas — mostrar esses campos vazios só levantava a
pergunta de porquê. A "Minha Conta" identifica-o com uma etiqueta e esconde o
que não se lhe aplica (`is_admin` em `/account/details`), e o pódio marca a
linha dele. ⚠️ E dá-lhe cara: o dono NÃO está na lista de utilizadores do
servidor, por isso era o único com o avatar "?" — vem de
`get_owner_account()`, passado por `thumb_para_interface()` (idempotente, e
sabe o formato de cada servidor).

⚠️ E logo a seguir veio o quarto sintoma: **gravar o XP CRIA o perfil**, e
`username` é NOT NULL. O administrador abria as estatísticas e o log ficava com
um `IntegrityError` sobre um INSERT de trinta colunas — sem XP e sem pista.
`sync_user_xp` escreve o nome na CRIAÇÃO (só aí: sincronizar XP não é sítio
para renomear quem já cá está), e `set_user_profile` passou a recusar-se a
criar um perfil sem nome com uma mensagem que diz o que falta.

⚠️ **Na página da conta, um pedido secundário derrubava a página inteira.** O
`fetchAPI` levanta em qualquer resposta que não seja 2xx, e os pedidos iniciais
corriam num `Promise.all` — que rejeita com a PRIMEIRA falha. O
`if (paymentOptions.success)` que trata a ausência de planos já lá estava; só
nunca chegava a correr. Isso atingia também um painel sem preços configurados,
que responde 404. Um teste percorre agora todas as rotas que a página chama,
com uma sessão de administrador, porque corrigi-las uma a uma foi precisamente
o que não chegou à primeira vez.

#### O Plex sem Tautulli

O Tautulli é opcional. Sem ele, `plex/history.py` lê o histórico e os aparelhos
do próprio servidor — mais devagar e com menos detalhe, mas a alternativa era
uma página a dizer que a pessoa nunca viu nada, que é diferente de "não sei".
O cartão das Conexões diz ao administrador o que está a trocar:

- **é mais lento**: cada página é uma consulta ao servidor, em vez de vir da
  base de dados já indexada do Tautulli;
- **não há percentagem**: o Plex só regista uma entrada quando o item é dado
  por VISTO — o que ficou a meio não aparece de todo. Por isso a barra vai a
  100%: é o que a entrada significa, não uma estimativa;
- **a pesquisa é sobre uma janela** (`JANELA`, as reproduções mais recentes):
  `/status/sessions/history/all` não aceita filtro por título — só `accountID`,
  `viewedAt`, `librarySectionID`, `metadataItemID` e `sort`.

O histórico traz apenas o `deviceID`; quem lhe dá um nome é `/devices`, e as
contas vêm de `/accounts` — ambos com cache de 5 minutos, e sem guardar o
resultado de uma falha de rede (a mesma regra dos plugins do Jellyfin).

⚠️ **O id do DONO não é o mesmo dos dois lados.** Em `/accounts` o dono é a
conta **1**; só as contas partilhadas lá aparecem com o id de plex.tv que o
painel guarda. Filtrar o histórico do administrador pelo id dele devolvia
sempre uma lista vazia — sem erro nenhum, que é o pior dos casos. A tradução
faz-se pelo nome da conta do dono (`conn.account`), nos dois sentidos:
`_id_de_conta()` para perguntar ao servidor e `ids_do_painel()` para dizer de
quem é cada reprodução que veio de lá. Sem o segundo, as do administrador
ficavam agrupadas sob o id "1" e ele aparecia no pódio como um estranho — sem
nível, sem cara e sem se ligar ao perfil dele.

**E as ESTATÍSTICAS também saem daqui** (`plex/stats_api.py`), pela mesma razão
e com as mesmas perdas. `/status/sessions/history/all` traz o título e pouco
mais: a duração, o ano, os géneros e o realizador vêm de uma segunda chamada a
`/library/metadata/<k1>,<k2>,...`, em blocos e uma vez por página — nunca uma
por linha. Três coisas a ter presentes:

- ⚠️ **o Plex conta em MILISSEGUNDOS** e a agregação em segundos, como o
  Tautulli devolve. Sem dividir, uma hora de filme valia mil vezes o XP;
- **uma reprodução é um item DADO POR VISTO**, e conta inteiro: o tempo é a
  duração do item, não o tempo que a pessoa lá esteve. É a mesma aproximação
  que o Jellyfin sem o plugin faz. A percentagem é 100 pela mesma razão da
  barra do histórico — e um 0 aqui fazia o mínimo de percentagem das
  recomendações deitar fora todas as linhas;
- **é uma janela** (`LIMITE_DE_LINHAS`), das reproduções mais recentes para
  trás: num servidor com anos de uso, o Wrapped de há três anos não está lá.

⚠️ **Omitir o `accountID` não é o mesmo que mandá-lo vazio.** O pódio e as
recomendações pedem o histórico de toda a gente, e com a chave presente e sem
valor o servidor não devolve nada — por isso `entradas()` recebe `None` e a
chave nem chega a ser montada.

🔇 E não configurar o Tautulli deixou de ser um WARNING em cada arranque: em
branco é uma escolha legítima (num painel Jellyfin nem se aplica). O aviso fica
para quem o preenche só a meio, que é um engano de verdade.

#### As estatísticas do Jellyfin: duas fontes, uma boa e uma aproximada

`jellyfin/stats_api.py` traduz o que o servidor sabe para a lista de
reproduções que a agregação consome:

- **com o plugin Playback Reporting**, é exato: uma linha por reprodução, com
  os SEGUNDOS vistos, o cliente e o aparelho. A consulta é a mesma
  `submit_custom_query` do histórico, com as mesmas trancas de SQL;
- **sem ele**, o núcleo só sabe que ITENS cada pessoa deu por vistos
  (`Filters=IsPlayed`): conta-se uma reprodução por item, com a duração do
  próprio item. Serve para o pódio e para o XP, e está dito no cartão do
  Jellyfin nas Conexões — quem quiser exatidão instala o plugin.

⚠️ **O `UserId` do plugin vem com hífenes e o painel guarda-o sem.** O pódio
junta o histórico aos perfis por esse id: com as duas grafias misturadas, a
mesma pessoa aparecia duas vezes — ou nenhuma. Tudo o que sai dali passa por
`chave_de()`, e a consulta procura pelas DUAS grafias, porque não se sabe qual
delas a versão instalada do plugin gravou.

🐛 Havia DUAS consultas ao plugin e só uma fazia isso: o HISTÓRICO comparava
com uma grafia só. Com a errada, devolvia zero linhas — que não é `None`, por
isso nem caía para o registo do núcleo: a página ficava vazia para sempre, sem
erro nenhum. Hoje a rotina é uma só (`condicao_de_utilizador`, em
`playback_reporting.py`, ao lado do `GUID_VALIDO` e do escape de SQL), usada
pelos dois.

⚠️ **A data do plugin não traz fuso** (`2026-09-12 20:23:28`). Lê-se como UTC,
que é o que `history.py` já fazia com as mesmas linhas — o que não pode haver é
duas leituras diferentes da mesma data no mesmo painel.

Os géneros, o ano, o realizador e as capas não estão no registo do plugin: vêm
de uma segunda chamada ao núcleo (`/Items?ids=`), em blocos e só para os itens
que aparecem. Um item apagado da biblioteca conta na mesma, com o nome que o
plugin gravou e sem capa.

#### O plugin Playback Reporting, quando existe

`playback_reporting.py` dá o histórico por REPRODUÇÃO — a mesma mídia vista
três vezes dá três linhas, cada uma com o APARELHO em que foi vista. É
detectado em `GET /Plugins` (com cache de 10 minutos, porque instalar um plugin
obriga a reiniciar o Jellyfin) e, quando não consegue responder,
`get_watch_history` cai para o registo do núcleo — nunca para uma página vazia.

⚠️ **Não saber não é o mesmo que não existir.** A deteção só guarda em cache
uma resposta que o servidor deu mesmo: gravar o "não" de uma falha de rede
deixava o histórico dez minutos na fonte pior sem razão nenhuma.

🛡️ **O plugin não tem rota de listagem paginada.** A única forma de ler a
tabela `PlaybackActivity` é `POST /user_usage_stats/submit_custom_query`, que
corre **SQL cru e não aceita parâmetros ligados**. Por isso:

- o id do utilizador é validado contra `GUID_VALIDO` ANTES de tocar no SQL — um
  id que não pareça um GUID não é um engano de escrita, é um ataque, e aí
  nem se tenta a consulta;
- tudo o resto passa por `_literal_sql()`, que duplica a plica (o escape do
  SQLite — não há barra invertida) e corta o comprimento.

O teste que guarda isto **corre o SQL gerado num SQLite real** com a tabela do
plugin e verifica que ela continua de pé: contar plicas não prova nada, porque
`DROP TABLE` continua a aparecer no texto — dentro do literal, inofensivo.

O plugin guarda os SEGUNDOS vistos (`PlayDuration`), não a percentagem. Ela é
calculada com a duração do item, pedida numa segunda chamada ao núcleo — uma só
por página, com todos os ids de uma vez. Um item apagado da biblioteca não volta
daí: a linha fica na mesma, com o nome que o plugin gravou e sem capa.

⚠️ O erro do SQLite vem dentro de uma resposta **200**, na chave `message` (e a
das colunas chama-se mesmo `colums`, com o erro de escrita). Sem olhar para ela,
uma consulta inválida passava por uma lista vazia — "este utilizador nunca viu
nada", e ninguém dava por isso.

Duas armadilhas do backend Jellyfin, ambas com teste de regressão:

- `POST /Users/{id}/Policy` **substitui a política inteira**. Enviar só os
  campos alterados repõe os restantes nos valores por omissão — um
  administrador perdia a flag de administrador ao mudar de biblioteca. Leia
  sempre a política, altere por cima, grave inteira.
- A política guarda os **ItemId** das bibliotecas, não os nomes. E
  "todas as bibliotecas" é o campo `EnableAllFolders`: sem o marcar, uma
  biblioteca criada depois fica invisível para quem devia ver tudo.

### Configuração: config.json, não variáveis de ambiente

Tudo é configurado pela interface e vive em `config/config.json`, cujo esquema
completo (com os valores padrão) está em `load_or_create_config()` em
`app/config.py` — é lá que se acrescenta uma definição nova. `CONFIG_DIR` é
redirecionável por `PAINEL_PLEX_CONFIG_DIR`, e é assim que os testes não tocam
na instalação real.

⚠️ **Uma definição nova precisa de DUAS listas em `save_settings`**
(`app/blueprints/api/system.py`): `fields_to_update`, ou não é gravada de todo —
foi o que aconteceu ao `JELLYFIN_URL` e à `JELLYFIN_API_KEY`, editáveis nas
Conexões e descartadas em silêncio ao gravar, com a recarga seletiva a nunca
disparar. E, se for uma credencial, `sensitive_keys`, ou desce em claro para o
navegador na resposta das definições — a chave do Jellyfin era a única que o
fazia, e dá acesso de administrador a todo o servidor de média. (Os campos do
Plex não estão em `fields_to_update` porque têm tratamento próprio: chegam em
minúsculas, do assistente.)

Os managers leem o config de duas formas, e a diferença é fonte de bugs:
guardam uma cópia em `self.config` no `__init__` **ou** chamam
`load_or_create_config()` a cada uso. Quem guarda a cópia precisa de um
`reload_credentials()`, e esse método tem de ser chamado a partir da **recarga
seletiva** em `save_settings` (`app/blueprints/api/system.py`), que compara as
chaves antigas com as novas e só recarrega o que mudou — para não reconectar ao
Plex e refazer webhooks de pagamento a cada gravação de uma mensagem de
Telegram. Uma credencial nova que não entre nessa comparação fica ignorada em
memória até o próximo reinício.

O estado relacional (perfis, pagamentos, cupões, convites, conquistas) fica no
SQLite via `DataManager` (`app/services/data_manager.py`) e nos modelos de
`app/models.py`. As migrações que mexem no tipo ou no nome de colunas com
chaves estrangeiras precisam de `PRAGMA legacy_alter_table=ON` no SQLite, e de
repor a chave estrangeira numa segunda passagem depois da renomeação — ver
`b7d4e82a16c9`, onde ambos os detalhes custaram uma tabela sem chaves. `User` é a exceção: não é uma tabela, é um objeto do
Flask-Login reconstruído a partir da sessão (`load_user`).

⚠️ **Um perfil novo fica marcado com o servidor que o criou**
(`media_server_type`, gravado por `set_user_profile` a partir do config). A
coluna existe para um painel que troque de servidor não confundir um ID do Plex
com um GUID do Jellyfin que por acaso coincida — e ficava a NULL em tudo o que
o painel criava. Quem passa o valor explicitamente manda.

#### As chaves estrangeiras são IMPOSTAS, e dividem as tabelas em duas famílias

⚠️ **`PRAGMA foreign_keys` vem DESLIGADO por omissão no SQLite**, e durante
muito tempo ninguém o ligava: as chaves estavam declaradas nos modelos e não
valiam nada. Apagar um perfil deixava para trás os pagamentos, os bloqueios e
os registos de corte que lhe apontavam — órfãos silenciosos —, e era possível
gravar um pagamento com um `media_user_id` que nunca existiu. Hoje
`set_sqlite_pragma` liga-o, depois de `e1c7a4f92db6` limpar o que ficou.

A limpeza obrigou a separar as tabelas por aquilo que elas são:

- **ESTADO** (`blocked_users`, `notifications`, `coupon_usages`,
  `unlocked_achievements`, `password_resets`) — não faz sentido sem o perfil,
  por isso tem `ON DELETE CASCADE`. E `ON UPDATE CASCADE`, que é o que permite
  ao `migrar_identidade` trocar a chave primária de um perfil com filhas;
- **HISTÓRICO** (`pix_payments`, `stream_termination_logs`) — **não tem chave
  estrangeira nenhuma, de propósito.** Um pagamento recebido aconteceu:
  apagá-lo porque a conta foi removida falsifica o relatório financeiro do mês
  em que entrou. É a mesma decisão que já valia para `pix_payments.coupon_code`.

⚠️ **As migrações correm com as chaves DESLIGADAS** (`_chaves_estrangeiras_desligadas`,
em `migrations/env.py`): no SQLite, alterar uma tabela é copiá-la e trocar os
nomes, e com as chaves ligadas esse baile dispara violações sobre um estado que
só é intermédio. ⚠️ E o PRAGMA tem de ser posto no evento `connect` do engine,
não na ligação já aberta: **dentro de uma transação o SQLite ignora-o em
silêncio**, e a transação aberta à frente do Alembic fazia o `upgrade` inteiro
ser desfeito no fim sem erro nenhum — a base de dados ficava no esquema inicial
com a `alembic_version` VAZIA.

#### Restrições de domínio: o que a coluna aceita

⚠️ **O SQLite não impõe o comprimento de um VARCHAR** (um `String(20)` aceita
200 caracteres) e não havia um único `CHECK`. `status = 'qualquer-coisa'`
passava, e a partir daí a pessoa não era nem ativa nem inativa: não aparecia
nas listagens, não era bloqueada, não era removida. Um estado inventado não dá
erro, dá um utilizador invisível. O mesmo valia para um pagamento de valor
NEGATIVO, que subtraía da receita do mês.

Os valores aceites vivem em `app/dominios.py` — fonte única, usada pelos
`CheckConstraint` dos modelos. ⚠️ **Uma migração NÃO importa daqui**: é uma
fotografia do esquema no momento em que foi escrita, e tem de continuar a
correr igual quando a lista mudar (ver `b9e4a7c15fd2`, que repete os valores).

🐛 **O telefone é guardado só com dígitos** (`@validates` em `UserProfile`, e
`validar_telefone` nos schemas). O destinatário do WhatsApp é montado como
`{phone_number}@s.whatsapp.net`: um número escrito da forma natural —
`(11) 99999-9999` — produzia um identificador inválido e a mensagem não chegava
a ninguém, sem erro nenhum. ⚠️ O **email** é NORMALIZADO mas nunca recusado no
modelo: grande parte dos que entram vem do SERVIDOR de média, e recusar um
partia a sincronização de perfis. Quem o escreve à mão passa pelos schemas, e
esses recusam.

#### Índices: declarado não é o mesmo que USADO

⚠️ `user_profiles.username` e `coupons.code` estavam indexados desde sempre e o
índice **nunca era usado**: todas as consultas comparam `func.lower(...)` /
`func.upper(...)`, e o SQLite não usa o índice de uma coluna quando a comparação
é sobre uma EXPRESSÃO dela. A correção é um índice sobre a mesma expressão
(`ix_user_profiles_username_lower`, `ix_coupons_code_upper`).

⚠️ Os índices de `expiration_date`, `trial_end_date` e `deleted_at` são
**PARCIAIS**. Um índice completo sobre uma coluna maioritariamente NULL indexa
sobretudo nada, e o SQLite — estimando que teria de ler quase a tabela toda —
continuava a varrê-la: ficava um índice a custar escritas e a não servir
ninguém.

🐛 **E um `batch_alter_table` deita fora os índices que não sabe reflectir.** Um
índice sobre uma expressão não é reflectido (fica um `SAWarning` e segue), por
isso a tabela reconstruída nasce sem ele. `b9e4a7c15fd2` repõe-nos no fim;
`tests/test_esquema_migrado.py` compara o esquema que as MIGRAÇÕES constroem com
o que o `create_all` constrói e falha se divergirem — foi assim que
`coupon_usages` andou anos sem o UNIQUE que impede o mesmo cupão de ser usado
duas vezes pela mesma pessoa (nos testes existia, em produção não).

#### Remoção suave: o que se apaga não sai da base de dados

🛡️ `pix_payments`, `coupons` e `stream_termination_logs` têm `deleted_at`. São
as três que mais custa perder — o histórico financeiro, o registo de quem usou
cada cupão, a auditoria de cortes — e as três tinham um botão que as apagava
para sempre, sem confirmação e sem volta. Agora saem das leituras (que filtram
`deleted_at IS NULL`) e ficam na tabela; `restaurar_pix_payment` devolve-as.

⚠️ Duas exceções que são exceções de propósito: **as cobranças abandonadas
continuam a ser apagadas mesmo** (`delete_old_pending_payments` — um QR code
que ninguém pagou não é histórico, é lixo), e **a marca de água da importação
de cortes conta os apagados** (`get_last_termination_timestamp` não mostra
nada; serve para não reimportar o que já foi importado, e ignorá-los fazia a
importação seguinte duplicar tudo).

⚠️ E apagar um cupão deixou de arrastar `coupon_usages` pelo cascade do ORM: é
esse registo que impede a mesma pessoa de o usar outra vez, e apagar um cupão
para o recriar — que é o que se faz para lhe corrigir o valor — dava a toda a
gente um segundo desconto.

#### Auditoria: `app/services/audit.py`

🛡️ O único rasto de uma ação administrativa era uma linha no `app.log` — um
ficheiro que roda e que as Configurações têm um botão para truncar. E as
mudanças que mais interessam nem lá chegavam: gravar as Configurações reescreve
preços e credenciais e não escrevia nada sobre o que tinha mudado.

`audit.registar(acao, alvo_tipo, alvo_id, detalhes)` grava em `audit_logs`, e
`audit.diferenca(antes, depois)` reduz dois dicionários ao que MUDOU. Três
regras que o módulo existe para guardar:

- 🛡️ **nenhum segredo entra**, em três camadas. Uma regra sobre o NOME da
  chave (`TOKEN`, `KEY`, `SECRET`, `PASSWORD`, `SENHA`, `AUTHORIZATION`,
  `WEBHOOK_URL`) regista que mudou, nunca o valor; uma regra sobre o VALOR
  esconde um URL com credenciais embutidas (`https://user:senha@…`) chame-se a
  chave como se chamar; e o `_serializavel` repete a verificação, mais
  grosseira, para o caso de alguém chamar `registar` com um dicionário que não
  passou pela `diferenca`.
  🐛 **A regra do VALOR usa o `urlsplit`, nunca uma expressão regular.** A
  primeira versão era um `[^/@\s]+:[^/@\s]+@` cujos quantificadores se
  sobrepõem — o `:` pertence à própria classe —, o que dá retrocesso
  quadrático: **4,65 segundos** com 40 KB de entrada, contra 0,0002 do
  `urlsplit`. E por ali passam os `detalhes` de qualquer ação, onde entram
  nomes escolhidos por quem cria a conta no servidor de mídia — bastava alguém
  chamar-se assim para segurar o worker, e o painel corre com UM de propósito.
  🐛 **A regra do nome sozinha já falhou uma vez**: os cinco pedaços iniciais
  deixavam passar em claro o `DISCORD_WEBHOOK_URL` (o token do canal está
  DENTRO do caminho), o `WEBHOOK_URL` (pode trazer `user:senha@`) e o
  `WEBHOOK_AUTHORIZATION_HEADER`. Bastava editá-los para o valor antigo E o
  novo ficarem em texto puro numa tabela que vai dentro do ZIP de backup. ⚠️ O
  `WEBHOOK_URL` como pedaço é escolhido para NÃO apanhar os
  `WEBHOOK_*_MESSAGE_TEMPLATE`, que são formatos e não segredos — e que
  interessa muito poder auditar, porque são o que chega ao telefone de quem
  paga. Há um teste que percorre o esquema real do config e exige que toda a
  chave com credencial esteja coberta;
- ⚠️ **falhar a registar nunca derruba a ação**: um erro vira um aviso no log.
  Perder a linha de auditoria é mau; perder o pagamento que ela descreve é pior;
- ⚠️ **a escrita vai por uma ligação PRÓPRIA**, não pela `db.session`. Pela
  sessão partilhada, o `commit` da auditoria levava consigo tudo o que o
  chamador tivesse pendente. Por isso também se chama sempre DEPOIS de a ação
  estar feita — o que fica registado aconteceu mesmo.

A tabela não tem chave estrangeira para `user_profiles`: a auditoria de uma
conta tem de continuar a responder depois de a conta deixar de existir, que é
precisamente quando alguém vai perguntar. Lê-se em `GET /api/system/audit-logs`
(só administradores) e **não há rota para a apagar**, ao contrário do `app.log`.

A interface é a aba **Auditoria** das Configurações (`settings/tabs/audit.html`
+ `settings_modules/audit.js`), ao lado da de Logs e de propósito: são as duas
listas de "o que aconteceu", e a diferença entre elas é que esta não tem botão
de limpar. Há um teste que a prende (`tests/test_aba_de_auditoria.py`) — se
alguém lhe acrescentar um, ele falha.

Quatro coisas que essa aba tem de respeitar, todas com teste:

- ⚠️ **os rótulos das ações vivem no template, a chave no código.** A chave
  (`utilizador.bloquear`) é o identificador estável por onde se filtra; o
  rótulo ("Usuário bloqueado") é o que a pessoa lê. Uma ação nova sem rótulo
  aparece com a chave crua — que é vocabulário interno e nem sequer é
  brasileiro. O teste compara os `audit.registar(...)` do código com a tabela
  `ROTULOS` e com os `data-i18n-*` do `settings.html`;
- ⚠️ **a data é UTC "nua" e leva um 'Z' antes do `new Date`.** Sem ele o
  JavaScript lê-a como hora LOCAL e cada registo aparece três horas adiantado
  no Brasil. É a mesma convenção da Auditoria de Cortes no painel principal;
- ⚠️ **as classes do Tailwind são escritas por extenso** (o mapa `CORES`), e
  não montadas com `bg-${cor}-100`: o compilador procura nomes de classes
  LITERAIS nos ficheiros, e uma classe montada em tempo de execução nunca
  chega ao CSS — o ícone ficava sem cor, sem erro nenhum;
- 🛡️ **tudo o que vem da resposta é escapado.** O `username` que aparece nos
  detalhes é escolhido por quem cria a conta no servidor de mídia, e a lista é
  montada com `innerHTML`: alguém chamado `<img src=x onerror=…>` executava
  código na sessão de um administrador.

A lista é buscada ao ABRIR a aba (como o polling dos logs), e não no arranque
da página: é uma aba que quase ninguém abre, e um pedido a cada visita às
Configurações seria trabalho para nada. Não faz polling — ao contrário dos
logs, o que aqui entra não muda enquanto se está a olhar.

⚠️ **O botão "Salvar Alterações" esconde-se aqui**, porque não há nada para
salvar. Quem o decide é a ABA, com `data-somente-leitura="true"` no seu
`tab-content`, e não uma dedução do JavaScript — deduzir pela presença de
campos de formulário dá a resposta errada nas DUAS pontas: a Auditoria TEM um
`<select>` (o filtro por ação) e não grava nada, e a de Logs PARECE só leitura
mas guarda o nível de log no `LOG_LEVEL`, por isso escondê-lo lá tirava a única
forma de o mudar. Um teste confirma que nenhuma aba marcada assim contém um id
que esteja no `fieldMap`.

#### O `payment_token` é uma credencial portadora, e agora expira

🛡️ Quem tiver o link `/pay/<token>` vê o nome e o vencimento de quem lá está e
pode gerar uma cobrança. Ele viaja por Telegram, Discord e WhatsApp e fica no
histórico dessas conversas para sempre — e **nunca expirava nem mudava**: o
primeiro link enviado a alguém continuava a funcionar anos depois, e um dump da
base de dados entregava o de toda a gente pronto a usar.

`payment_token_expires_at` dá-lhe validade (`PAYMENT_TOKEN_VALIDITY_DAYS`, 30
dias; 0 desliga). `perfil_por_payment_token` é a **porta única** para o
resolver — enquanto cada rota fazia o seu `filter_by(payment_token=...)`,
acrescentar a verificação obrigava a lembrar-se dela em cinco sítios, e o
esquecido não dava erro, dava um token eterno. `garantir_payment_token` renova
no momento em que um link é ENVIADO (é isso que faz a validade ser utilizável
sem ser um incómodo) e `rodar_payment_token` troca-o depois de um pagamento
confirmado.

⚠️ NULL continua a querer dizer "sem validade": é o que os perfis anteriores a
esta coluna têm. Invalidar de repente os links que estão no telemóvel de toda a
gente seria uma migração a cortar o acesso a quem quer pagar.

#### Permissões dos ficheiros

🛡️ `app/utils/ficheiros.py` põe a 0600 o `config.json`, as bases de dados
SQLite e os ZIPs de backup — nasciam com o que o umask ditasse. O que lá está
não é pouco: o `config.json` guarda em texto puro a SECRET_KEY, o token do
Plex, a chave de administrador do Jellyfin e as credenciais dos três gateways,
e o ZIP de backup leva o `config.json` inteiro mais as bases de dados.

⚠️ `proteger_base_de_dados` trata também do `-wal` e do `-shm`: em modo WAL as
escritas mais recentes vivem no diário, e proteger só o `.db` deixava à vista
tudo o que ainda não tinha sido integrado. ⚠️ E falhar aqui nunca derruba nada
— num sistema de ficheiros sem permissões POSIX o `chmod` levanta, e um painel
que não arranca por causa disso é muito pior.

### Restaurar um backup de outra versão

🛡️ Restaurar é **substituir a base de dados por baixo da aplicação**; o esquema
só é acertado no arranque seguinte, pelo `flask db upgrade` que está tanto no
`run.py` como no `CMD` do Dockerfile. Há dois backups que fariam esse arranque
falhar, e um painel que não arranca é muito pior do que um restauro recusado —
por isso `validate_backup_zip` lê a `alembic_version` de dentro do ZIP e recusa:

- um backup de uma versão **MAIS RECENTE** (revisão que não existe nas
  migrações deste painel: o Alembic pára com "Can't locate revision");
- um backup **com dados e sem registo de versão** (instalação anterior às
  migrações: o `upgrade` tentaria criar tabelas que já lá estão).

🛡️ **E há uma migração histórica que APAGA o que não conseguir mapear**
(`a0b1c2d3e4f5`, a que troca a chave de `username` pelo id do Plex). O
mapeamento vem de uma chamada ao servidor VIVO, e quem não estiver no mapa não é
copiado para a tabela nova — por isso, com o Plex inacessível, ela deitava fora
todos os perfis, pagamentos e histórico **sem uma única linha de erro**.
Acontecia precisamente a quem restaura um backup com o servidor em baixo. Hoje
recusa-se quando há perfis e o mapa vem vazio: a base de dados fica intacta e a
migração corre outra vez assim que o Plex responder. ⚠️ Zero perfis continua a
passar — é uma instalação nova, não há nada a perder, e abortar aí impedia o
painel de arrancar de todo (foi o que aconteceu quando o `plex_manager` mudou de
nome). Quem for descartado com o Plex a responder é NOMEADO no aviso — e o aviso
vai para o stderr além do log, porque o `create_app()` que a própria migração
chama reconfigura o logging por cima do Alembic e cala-o a meio.

O caminho normal — um backup **mais antigo**, que é o de quem vem do painel
só-Plex — passa de propósito: as migrações levam-no para a frente no arranque, e
é para isso que elas existem. `a9f3c17b2e04` é quem trata desse salto (identidade
para texto e `media_server_type = 'plex'` nas linhas que já existiam), e o
`MEDIA_SERVER_TYPE` em falta no `config.json` antigo resolve-se sozinho no
`_set_default`. O manifesto do ZIP diz a versão da base de dados e o servidor,
para se perceber meses depois o que ali está.

🐛 **O que ficava a acordar depois do fim.** O assistente manda-se reiniciar a
si próprio (`_agendar_reinicio` → `os.kill(SIGTERM)`, dois segundos depois de
gravar), e logo a seguir a uma instalação BEM-SUCEDIDA o log recebia isto:

    File "threading.py", line 1111, in _delete
      del _active[get_ident()]
    KeyError: 271255370948160
    <Greenlet ...: <bound method Thread._bootstrap of
     <Timer(Thread-1, stopped ...)>>> failed with KeyError

O `Thread-1` é a pista — é o PRIMEIRO thread do processo. Vem do armazenamento
em memória do Flask-Limiter (`limits.storage.MemoryStorage`), que arranca um
`threading.Timer` no construtor e **volta a marcá-lo a cada pedido** que passe
pelo limitador. Sob gevent isso é um greenlet embrulhado na contabilidade do
`threading`: com o processo a terminar, ele acorda com o `threading._active` já
desmontado.

`_parar_o_limitador()` cancela-o nos dois caminhos de encerramento. ⚠️ **Cancelar
não o mata; acorda-o** — o `Timer.cancel()` só marca o evento `finished`, e a
saída acontece quando lhe derem a vez. O `join` a seguir é que torna a correção
determinística: obriga essa saída a acontecer AGORA, com o `_active` ainda de
pé. E o `timer` não é API pública do `limits`, por isso vai tudo dentro de um
`try`: se desaparecer, o pior que acontece é o traceback voltar.

Pela mesma razão, o debounce do `StreamManager` deixou de ser um
`threading.Timer` e passou a `gevent.spawn_later` — num painel a correr, era o
outro que ficava em voo em cada encerramento.

⚠️ **E ainda assim não chegou, porque faltavam duas coisas ao diagnóstico.** Em
Docker o erro voltou, e com ele um pior: escolher o Jellyfin no assistente
gravava tudo certo — a ligação validada, o aviso de que o tipo de servidor
mudou — e o painel voltava com a página de login do **Plex**, e ficava assim.

As duas eram a mesma coisa: o **`--preload` do gunicorn**, que estava no `CMD` do
Dockerfile. Com ele, o `create_app()` corre no processo MESTRE e cada worker é um
`fork` dessa memória — a aplicação é lida UMA vez, no arranque do contentor, e
nunca mais. Mas o reinício do painel é o processo a pedir a própria morte
(`os.kill(os.getpid(), SIGTERM)`), e quem morre é o WORKER: o mestre levanta
outro a partir da memória pré-carregada, **com o config antigo lá dentro**. E o
`Thread-1` do traceback era o timer do limitador criado no MESTRE, antes do
`fork`: o worker herda-o já marcado como "stopped" pelo `threading._after_fork`
(que também o tira do `_active`), por isso o `_parar_o_limitador()` — que cancela
o timer ATUAL — nunca lhe tocava.

Sem `--preload`, os dois desaparecem: cada worker lê a aplicação de fresco (a
troca de servidor passa a valer) e não há nada em voo para herdar. Não se perde
nada — ele serve para poupar memória entre VÁRIOS workers, e aqui há **um** de
propósito. Em troca, o agendador passa a correr no worker em vez de ficar no
mestre, que é onde o `fork` o deixava (as threads não sobrevivem a um, por isso
o worker ficava com um `scheduler.running` a dizer que sim sobre uma thread que
já não existia).

⚠️ **E o SIGTERM do painel nunca chega ao `shutdown_scheduler`.** Sob gunicorn o
worker instala os handlers dele por cima dos nossos a seguir ao `fork`, e o que
corre é o encerramento gracioso do gunicorn. Por isso a limpeza não pode
depender do sinal: é o próprio `_agendar_reinicio` que chama
`parar_servicos_de_fundo()` antes de mandar terminar, com o processo ainda de
pé. (O `shutdown_scheduler` continua a fazer falta — é o caminho de quem corre
`python run.py` ou usa systemd.)

⚠️ **Quem responde enquanto o processo antigo não sai é o processo ANTIGO.** O
worker só termina quando não houver ligações a ser servidas, e uma ligação
keep-alive do navegador nunca fecha sozinha: são os 30 segundos do
`--graceful-timeout` por inteiro, sempre que há um separador aberto. O JavaScript
esperava oito segundos fixos e recarregava — a tempo de apanhar o painel a
morrer, que é como quem escolheu o Jellyfin via a página do Plex. Hoje cada
arranque tem uma marca (`BOOT_ID`, em `create_app`), quem pede o reinício
recebe-a na resposta, e `aguardarReinicio()` (`static/js/reinicio.js`) pergunta a
`/api/system/status` até ela MUDAR. A rota está fora do limitador e isenta do
assistente — é pedida de segundo a segundo e, num restauro, ainda não há
configuração —, e não diz nada que a página de login já não mostre.

🔇 **E um ERROR que não era nosso**, a repetir-se a cada worker que nascia:

    Control server error: [Errno 13] Permission denied: '/.gunicorn'

A partir da 25.1.0 o gunicorn abre por omissão um socket de gestão para a
ferramenta `gunicornc`, em `$XDG_RUNTIME_DIR` ou, faltando esse, em
`$HOME/.gunicorn/`. Num contentor lançado com `--user` (o PUID/PGID desta
imagem) o Docker põe `HOME=/`, e criar `/.gunicorn` sem ser root é proibido. O
painel nunca usa o `gunicornc` — reinicia-se por SIGTERM —, por isso o `CMD`
leva `--no-control-socket`. ⚠️ Isso obriga a `gunicorn>=25.1.0` no
`requirements.txt`: nas versões anteriores a flag não existe e o gunicorn
RECUSA-SE A ARRANCAR ("unrecognized arguments"), o que é muito pior do que o
aviso que se queria calar. As duas pontas têm um teste que as prende.

⚠️ **Primeiro calar, depois trocar.** `_restaurar_backup()` chama
`parar_servicos_de_fundo()` ANTES de substituir os ficheiros. Sem isso, o
agendador — que continua vivo — relia o jobstore restaurado, encontrava lá as
tarefas com a hora de execução no PASSADO (a do momento em que o backup foi
feito) e tentava submetê-las todas de uma vez, mesmo a tempo de apanhar o
reinício que o próprio restauro agenda: o log enchia-se de `RuntimeError:
cannot schedule new futures after shutdown`, um por tarefa, logo a seguir a um
restauro BEM-SUCEDIDO. Pelo mesmo motivo, `_parar_o_agendador()` faz `pause()`
antes do `shutdown()` — o `shutdown` fecha os executores, mas o ciclo pode
estar nesse instante a submeter o que está na hora. E as ligações à base de
dados são descartadas a seguir ao restauro (`db.engine.dispose()`): o que resta
do processo antigo estaria a ler de um ficheiro que já nem tem nome.

As tarefas por utilizador que vierem atrasadas do backup são descartadas pelo
`misfire_grace_time` — quem apanha esses casos é a varredura diária
(`removal_job`, `expiration_notification_job`), que não depende delas.

### O assistente de instalação

`setup.html` + `setup.js`. É o único sítio do painel onde **ainda não há backend
construído a quem perguntar** — o servidor só é escolhido ali — por isso o que
noutras páginas se esconde com `media_server.capabilities` tem de ser escondido
pelo JavaScript, no momento da escolha (`selecionarTipoDeServidor`).

🐛 **O cartão do Tautulli aparecia sempre.** Ele só fala com o Plex: num
assistente de Jellyfin pedia credenciais de um serviço que nunca ia ser usado, e
quem as preenchesse ficava convencido de que tinha ligado alguma coisa. O mesmo
`selecionarTipoDeServidor` repõe o texto do passo 2 (no Plex escolhe-se um
SERVIDOR, no Jellyfin uma CONTA) e limpa o estado do servidor anterior — quem
experimentasse um e mudasse de ideias levava consigo o nome do administrador do
primeiro.

⚠️ Ele **corre uma vez no arranque**. Sem isso, o que está visível é o que o HTML
tiver escrito à mão, e o ecrã passa a depender de duas verdades diferentes.

⚠️ Um campo escondido não deve ser ENVIADO: `save_setup` preserva as chaves de
API que cheguem vazias, mas não os URLs — mandar `TAUTULLI_URL` em branco
apagava o que estivesse num config.json restaurado.

Há um teste que prende o contrato entre os dois ficheiros: toda a chave
`i18n.x` / `urls.x` que o script procura tem de existir como `data-*` no
template (`tests/test_assistente_por_servidor.py`). É a armadilha que já deu
`undefined` por extenso na página de convite.

### Blueprints

Páginas em `app/blueprints/` (`main`, `auth`, `image`, `redirect`), API em
`app/blueprints/api/` sob `/api/*`. O `before_request` global força o assistente
de instalação enquanto `IS_CONFIGURED` for falso (com uma lista de endpoints
isentos) e desvia utilizadores não-admin do painel de administração para
`/statistics`.

Rotas de API usam `@admin_required` (`app/decorators.py`) e
`@validate_json(Schema)` (`app/blueprints/api/decorators.py`), com os esquemas
Pydantic em `app/blueprints/api/schemas.py` — ainda no estilo `@validator` do
Pydantic v1, cujo aviso de depreciação está silenciado no `pytest.ini`.

### Notificações

Os templates (Telegram, Discord, WhatsApp, webhook, push) vivem no config.json e
os padrões estão em `DEFAULT_TEMPLATES` (`notifier_manager.py`) e em
`load_or_create_config()`. Os dois têm de ser mudados juntos.

⚠️ **O push tem DUAS chaves por evento**, `PUSH_<EVENTO>_TITLE_TEMPLATE` e
`PUSH_<EVENTO>_MESSAGE_TEMPLATE`, porque o sistema operativo trata as duas
partes de maneira diferente: o título aparece em negrito e é o que se lê de
relance, o corpo é cortado ao fim de duas linhas no ecrã de bloqueio.
Reaproveitar o texto do Telegram dava um aviso ilegível.

⚠️ **A marca aparecia no meio das frases entregues ao utilizador** — "O seu
acesso ao Plex está prestes a expirar", "aceite o convite no link abaixo". Num
painel Jellyfin era a marca errada a chegar a quem paga, e "aceitar um convite"
é um passo que ali nem existe. Use `{server_name}`, que vem do
`SHORT_NAME` do backend. Quem já reescreveu os templates na página de
Configurações mantém o que lá tem: um padrão só vale para quem não escolheu.

`{invite_link}` deixou de ser "o link do convite do Plex" e passou a ser "o
endereço para voltar a aceder" — é o `link` que `restaurar_acesso` devolve, e
muda por servidor.

🔔 **Estender o período de teste avisa quem está em teste** (o evento
`trial_extended`, com texto nos cinco canais). Era a única mudança de acesso que
o painel fazia em silêncio: o administrador estendia, o `end_trial_job` era
remarcado, e do outro lado a pessoa continuava a contar com a data antiga — o
único aviso que alguma vez lhe chegava sobre o teste era o do FIM, que é a má
notícia. ⚠️ **A hora faz parte da data aqui**, ao contrário do vencimento de uma
assinatura: um teste estende-se por horas ou minutos, e "vai até 20/09/2026"
sobre uma extensão de duas horas não diz a quem lê quando é que fica sem acesso.
⚠️ E avisar **nunca derruba a extensão** — quando isto corre, o teste já está
estendido e a tarefa já foi remarcada —, mas a resposta da rota diz ao
administrador se o aviso saiu (`notificado`, e a mensagem do toast), porque um
aviso que não saiu não pode parecer que saiu.

#### As notificações push: um quinto canal, com um destinatário diferente

O sino do painel só avisa quem está com ele aberto. A notificação push é o mesmo
aviso entregue pelo sistema operativo — no Android, no iPhone com o painel
adicionado ao ecrã inicial, e no navegador do computador. Vive em três ficheiros:

- `app/services/web_push.py` — a **cifra e a entrega**, sem biblioteca nenhuma;
- `app/services/push_manager.py` — **quem recebe o quê**, e as chaves;
- `app/static/js/push.js` + `service-worker.js` — o lado do navegador.

⚠️ **Não há aqui uma biblioteca, e é por não haver nenhuma que instale.** A
`pywebpush` arrasta a `http-ece`, cujo `setup.py` já não compila com o
setuptools atual (`AttributeError: install_layout`) — uma dependência que não
instala é um contentor que não arranca. O que falta fazer é pouco e está todo
especificado (RFC 8188, 8291 e 8292), e o `cryptography` já é dependência
fixada. 🛡️ Uma cifra própria sem vetor de teste seria um "funciona no meu
navegador": `tests/test_notificacoes_push.py` reproduz o exemplo da **RFC 8291**
byte a byte. Se esse teste falhar, nenhuma notificação chega a lado nenhum — e o
log não diz porquê, porque do lado de cá corre tudo bem.

⚠️ **O par de chaves VAPID é gerado UMA vez e nunca muda.** É ele que identifica
este painel perante o serviço de push, e fica registado em cada aparelho no
momento em que ele subscreve: gerar um par novo invalida, de uma vez, TODAS as
subscrições existentes — toda a gente deixa de receber e ninguém dá por isso,
porque o serviço responde 403 e mais nada. Por isso `garantir_chaves()` só gera
quando não há nada utilizável, as chaves NÃO estão em `fields_to_update` (não
vêm do formulário; nasce no momento em que o interruptor é ligado), e a privada
está em `sensitive_keys`.

⚠️ **`media_user_id` a NULL é o ADMINISTRADOR** em `push_subscriptions`, a mesma
convenção de `notifications` — e de propósito: quem recebe o aviso no sino é
quem o deve receber no telemóvel. É também o que permite ao dono do painel
subscrever antes de ter perfil local (só passa a tê-lo no primeiro login depois
da versão que o cria), coisa que uma chave estrangeira obrigatória impediria.
🛡️ **Quem é o dono do aparelho decide-o o SERVIDOR**, pela sessão: aceitar um
dono vindo do corpo do pedido deixava qualquer pessoa autenticada receber os
avisos de pagamento do administrador.

🛡️ **O endereço de entrega tem de ser de um serviço de push CONHECIDO**
(`SERVICOS_DE_PUSH`, em `web_push.py`). Ele é escolhido por quem subscreve e o
painel faz-lhe POST de DENTRO da rede: enquanto só se verificava o esquema
`https`, qualquer pessoa com sessão — sem ser administrador — registava um
aparelho a apontar para um serviço interno e usava o painel para lhe bater, com
a rota `/push/test` por gatilho. É o mesmo SSRF que a `ALLOWED_IMAGE_HOSTS` do
proxy de imagens já existia para fechar, e a resposta é a mesma: o pedido
ESCOLHE uma entrada da lista, nunca define um destino novo. A comparação é
`match_domain` (fronteira do rótulo DNS), porque
`fcm.googleapis.com.atacante.net` contém o domínio sem ser o domínio. ⚠️ A
verificação está em DOIS sítios — no schema, para dar o erro a quem subscreve,
e em `web_push.enviar`, que é a que vale: uma linha pode ter entrado na tabela
por um backup restaurado de antes desta versão. E a lista NÃO vive no
config.json: pô-la na interface faria de uma sessão de administrador tomada uma
forma de alargar o SSRF, por isso o acrescento é pela variável de ambiente
`PUSH_ALLOWED_HOSTS`, como no proxy de imagens.

🛡️ **A palavra-passe e o link de reposição NUNCA vão por push** (`EVENTOS_SEM_PUSH`).
Não é esquecimento: uma notificação push aparece no ecrã de bloqueio, à vista de
quem estiver por perto, e fica guardada pelo sistema operativo fora do painel. Os
dois continuam a ir pelos canais que a pessoa registou, onde há pelo menos uma
conversa a proteger.

⚠️ **O push é o único canal cujo destinatário não está no perfil** — está na
tabela dos aparelhos. Por isso `_prepare_and_send` recebe `aparelhos_push` e o
envio em massa carrega-os TODOS de uma vez (`_aparelhos_por_pessoa`): sem entrar
em `_split_by_reachability`, quem só ligou as notificações do telemóvel era
contado como "sem contacto" e ignorado em silêncio, com o canal ligado.

🐛 **Uma subscrição morta responde 410 para sempre.** Deixá-la na tabela era um
erro no log por cada notificação, a cada pagamento, sem nada a fazer sobre ele —
por isso `PushExpirado` (404/410) APAGA a linha, enquanto uma recusa temporária
(403, 429, servidor em baixo) a deixa ficar.

⚠️ **O service worker passou a ser servido da RAIZ** (`/service-worker.js`, a
rota `serve_sw` que já existia e ninguém usava). O alcance de um service worker
é a pasta de onde ele vem: em `/static/js/` ele não controlava a `start_url` do
manifesto, o que fazia o Android não oferecer instalar o painel e impedia uma
notificação clicada de encontrar a aba já aberta. ⚠️ Isso obrigou a mudar a
estratégia de cache no mesmo passo: a antiga procurava TUDO no cache primeiro, e
com alcance na raiz passaria a servir páginas e respostas da API guardadas — o
painel mostraria a lista de utilizadores de ontem. Hoje só se trata do que está
em `/static/`; o resto segue para a rede sem o service worker se meter.

⚠️ **O navegador exige HTTPS**, e no iPhone exige mais: o Safari só expõe o
`PushManager` depois de o painel ser adicionado ao ecrã inicial. A ausência do
botão TEM explicação, e por isso ali ela aparece escrita — noutros navegadores
sem suporte não aparece nada, porque não há nada a fazer.

O aviso de um pedido novo ao administrador vem ANTES da desistência por "não há
perfil local" (`_avisar_administrador_do_pedido`): quem aprova é ele, e quer
saber que entrou um pedido mesmo que quem o fez ainda não tenha perfil no
painel. Era aí que o webhook morria em silêncio. E só os eventos de ENTRADA
(`MEDIA_PENDING`, `MEDIA_AUTO_APPROVED`) o incomodam — os seguintes são o estado
a andar, e quem quer saber é quem pediu.

### A palavra-passe: uma só, e é a do servidor

⚠️ **Não há uma palavra-passe do painel e outra do servidor.** Num servidor de
contas locais há UMA, a do servidor de média: é com ela que a pessoa entra na
aplicação dele e também neste painel, porque o painel autentica contra ele
(`authenticate()`). O painel não guarda palavra-passe nenhuma, em sítio nenhum —
`definir_palavra_passe()` escreve direto no servidor. Mudá-la no painel muda-a
nos dois, e é isso que a "Minha Conta" diz a quem lá está.

Há dois caminhos para lá chegar, e a diferença entre eles é só como se prova a
identidade:

- **na "Minha Conta"** (`/api/users/account/password`), pedindo a palavra-passe
  ATUAL e confirmando-a contra o servidor. Uma sessão do painel esquecida aberta
  num computador partilhado não pode bastar para tomar a conta;
- **pelo "esqueci-me"**, com um link de uso único enviado pelas notificações
  (abaixo).

🛡️ A palavra-passe entra pelo contrato e não sai: nem para o log, nem para a
resposta. E a sessão do painel SOBREVIVE a uma mudança — é um cookie assinado
pelo painel e não guarda credenciais —, por isso a mensagem não pode prometer
que a pessoa vai ser desligada.

### Esqueci-me da palavra-passe

⚠️ **Só existe onde as contas são LOCAIS** (`capabilities.cria_contas`). Num
painel Plex a palavra-passe vive no plex.tv — o painel nunca a vê, é para isso
que o fluxo de PIN existe — e a ligação, a página, o cartão da "Minha Conta" e
as rotas escondem-se todos.

A regra de negócio vive em `app/services/password_reset.py`; o backend grava
com `definir_palavra_passe()`, que é do contrato. **Não sai email daqui**: o
painel nunca enviou emails, por isso o link vai pelos contactos que a pessoa já
registou (Telegram, Discord, WhatsApp, webhook), como os avisos de vencimento.
Quem não registou nenhum não tem por onde o receber, e isso fica no log em vez
de ficar em silêncio.

Quatro decisões que o módulo existe para guardar:

- 🛡️ **a resposta é sempre a mesma.** Exista a conta ou não, tenha contacto ou
  não, quem pede recebe "se existir uma conta, enviámos o link". Um
  "utilizador não encontrado" fazia da rota um oráculo sobre quem tem conta
  neste servidor — o oposto da mensagem única do login;
- 🛡️ **o que fica na base de dados é o RESUMO do token** (`PasswordReset`).
  Quem lesse a base de dados — ou um ZIP de backup, que é só um ficheiro —
  ficava com uma porta aberta por cada pedido válido;
- 🛡️ **o link vale minutos e serve uma vez.** Pedir de novo invalida o anterior,
  e um pedido cuja notificação NENHUM canal aceitou é descartado: um token
  válido à solta sem dono é pior do que não ter pedido nenhum;
- 🛡️ **pedir tem um intervalo mínimo por conta.** Sem ele, a rota era um botão
  para encher o Telegram de outra pessoa com mensagens que o painel assina.

⚠️ **No Jellyfin são DOIS pedidos, e entre eles a conta fica sem palavra-passe.**
O painel não conhece a antiga e o servidor exige-a para a trocar; a saída é a da
interface do próprio Jellyfin: `ResetPassword: true` apaga-a, e só então se
grava a nova. Se o segundo pedido falhar fica um ERROR a dizê-lo em voz alta —
deixar a pessoa a pensar que está tudo bem, com a conta aberta, seria pior.

### Pedidos de mídia (Overseerr / Jellyseerr)

`app/services/overseerr_manager.py` é a integração com o Seerr, e serve os DOIS
servidores. O que muda entre eles é pequeno e está todo em dois sítios.

⚠️ **Cada servidor entra no Seerr pela SUA porta.** Não há endpoint genérico:
`/user/import-from-plex` recebe `plexIds` (inteiros do plex.tv) e
`/user/import-from-jellyfin` recebe `jellyfinUserIds` (GUIDs). A escolha é uma
entrada em `IMPORTACAO_POR_SERVIDOR`, e `import_user(user_info, tipo_servidor)`
é a **única porta** para importar — mandar um GUID do Jellyfin para a rota do
Plex não importava ninguém, e o Seerr nem sempre dava erro.

⚠️ **O email não é obrigatório em toda a parte.** Nas contas locais do Jellyfin
o convite pede-o como OPCIONAL, e procurar só por email deixava essas pessoas
invisíveis: a aba "Meus Pedidos" vazia para sempre (sem erro nenhum, como se
nunca tivessem pedido nada), o acesso impossível de retirar, e a notificação do
webhook a não chegar a ninguém. Por isso tudo o que procura alguém no Seerr
aceita também o NOME — `find_user(email=..., username=...)`, `remove_user`,
`get_user_requests` — e o webhook cai para `requestedBy_username` quando o
`requestedBy_email` vem vazio.

⚠️ **A pesquisa do Seerr (`?q=`) é PARCIAL**, e o nome não é único como o email:
procurar "ana" traz também "joana". Confirma-se o resultado contra `username`,
`plexUsername`, `jellyfinUsername` e `displayName` — aceitar o primeiro mostrava
os pedidos de OUTRA pessoa na conta desta.

🐛 **Dar o acesso por garantido era mentira duas vezes.** Ao resgatar um convite
com "Acesso ao Seerr" marcado, o backend do Plex gravava
`overseerr_access = True` mesmo quando a importação falhava (Seerr em baixo,
chave errada): o painel mostrava o acesso ligado, a pessoa não conseguia pedir
nada, e desligar-e-ligar era a única forma de o repor. E o backend do Jellyfin
nem sequer importava — `toggle_overseerr_access` era um `False` fixo a dizer
"ainda não disponível". Hoje os dois têm um `_dar_acesso_aos_pedidos` que
devolve o que aconteceu de facto, e que **nunca derruba o resgate**: a conta no
servidor já existe e o acesso à mídia é o que interessa; a falha fica no log e o
administrador liga o acesso pela página de utilizadores.

O `overseerr_url` vai na resposta do resgate porque é dele que o "Como começar"
da página de convite monta o passo dos pedidos (`invite.js`) — e 🔒 só vai para
quem ganhou mesmo o acesso: é infraestrutura, e a página do convite é pública.

### Pagamentos

Três gateways (`efi_manager`, `mercado_pago_manager`, `gates2b_manager`) com a
mesma forma: credenciais do config, `reload_credentials()`, criação de cobrança
e um webhook próprio em `app/blueprints/api/payments.py`. Os webhooks são
`@limiter.exempt` e cada um valida a autenticidade à sua maneira (mTLS ou HMAC
na Efí, assinatura no Mercado Pago). Todos convergem em
`_process_successful_payment(txid)`, que é onde vive a lógica de renovação,
consumo de cupão e crédito de indicação — mudanças de comportamento de
pagamento pertencem aí, não no gateway.

Os webhooks só funcionam com `APP_BASE_URL` preenchido: é a partir dele que as
URLs de retorno são construídas.

#### Indique e Ganhe: a recompensa é RETIDA até quem indicou pagar

🛡️ **Quem ainda não é assinante não recebe já.** A indicação conta e fica
registada — o mérito de ter trazido alguém é de quem indicou —, mas a entrega
espera até ele próprio ter um pagamento confirmado. Sem isto, uma conta de
TESTE acumulava dias e crédito sem nunca pagar: num servidor de contas locais
ela não custa nada de criar e nada a liga à mesma pessoa, que é exatamente a
fraqueza que o CLAUDE.md já nota no anti-abuso de períodos de teste.

⚠️ **A pergunta é `user_has_completed_payment`, e NÃO "está em teste".** As duas
quase sempre coincidem, mas é a do pagamento que a mensagem promete a quem lê
("liberado quando você fizer o pagamento") — e quem recebeu acesso à mão, sem
nunca pagar, está na mesma situação. Uma renovação por cupom de 100% conta: ela
grava um pagamento de valor 0.

⚠️ **Não há tabela nova: o `referral_rewarded` a FALSO É o "por entregar".** Ao
reter, `reward_referrer_on_payment` sai ANTES do `claim_referral_reward` —
marcá-lo ali queimava a única oportunidade que aquele indicado tem de gerar
prémio, e a recompensa nunca mais saía.

🎁 **O mesmo pagamento tem dois papéis**, e por isso há duas chamadas lado a
lado em `_process_successful_payment`: em `reward_referrer_on_payment` quem
paga é o INDICADO (e quem recebe é outra pessoa); em
`liberar_recompensas_retidas` quem paga é o INDICADOR, e o que se procura é o
que ele já ganhou enquanto ainda não podia receber. Ela reentra no caminho
normal em vez de repetir as regras — a reserva do direito, o teto de
recompensas e a notificação ficam num sítio só.

⚠️ **E corre DEPOIS do `update_pix_payment_status(txid, 'CONCLUIDA')` e do
commit.** A condição é lida da tabela dos pagamentos: feita antes, este
pagamento ainda não lá estava e a recompensa ficava retida exatamente pela
razão que ele acabou de resolver — até à renovação seguinte.

🔔 **E quem espera tem de saber porquê.** `get_referral_stats` devolve
`pode_receber` e `retidas`, e o cartão da "Minha Conta" mostra o aviso: sem
ele, a página dizia "Confirmado" ao lado de um saldo que nunca crescia.
`retidas` conta só os indicados que JÁ pagaram — um que ainda não pagou não é
uma recompensa à espera, é uma indicação por confirmar, e essa já está no
`pending`.

🐛 **E `add_days_to_subscription` passou a acabar o teste** (`_clear_trial_data`,
como a renovação já fazia). Dar vencimento deixando o `end_trial_job` de pé era
dar e tirar em silêncio: à hora marcada ele bloqueava a conta com o motivo
`trial_expired`, apagando na prática os dias atribuídos — e como o cartão da
página de utilizadores mostra a etiqueta de teste no `else` do
`if (user.expiration_date)`, essa pessoa desaparecia também da aba e do
contador de testes.

🐛 **O link curto do mesmo destino é REUTILIZADO, nunca recriado.**
`create_short_link` apagava os links antigos do mesmo destino "para evitar
duplicações", e com isso matava o link que a pessoa já tinha recebido. Não era
um caso raro: `garantir_payment_token` MANTÉM o token enquanto for válido (só
estende a validade), por isso o URL longo é idêntico entre envios e o
apagamento coincidia sempre — e o aviso de vencimento é diário, com uma trava
de 23 horas por pessoa. Quem recebia o lembrete de hoje ficava com o de ontem
morto, e ao rolar a conversa para cima tocava num "link expirado" cujo destino
continuava válido. Reutilizar cumpre o objetivo original melhor: UMA linha por
destino, e nenhuma mensagem entregue deixa de funcionar. ⚠️ E ao reutilizar
repõe-se o `created_at`, porque o `cleanup_job` apaga por essa data
(`SHORT_LINK_MAX_AGE_DAYS`, 30): sem isso um link reutilizado ao dia 29 morria
no dia 30, logo a seguir a ter sido enviado. Rodar o código curto não fecharia
porta nenhuma — por baixo está o mesmo `/pay/<token>`, que é a credencial de
facto.

🐛 **Uma falha do gateway não pode parecer "ainda não pagou".** A rota que a
página de pagamento faz polling (`GET /api/payments/status/<txid>`) consulta o
gateway e, se isso levantar, responde o estado GUARDADO — que é "aguardando
pagamento". O `except` ali era um `pass` mudo: quem está parado no QR code via
exatamente o mesmo que veria se não tivesse pago, e o log não tinha uma linha a
dizer porquê. Uma credencial expirada ou uma mudança de API do gateway era
indistinguível, do lado de cá, de um cliente que ainda não pagou. Continua a
NÃO derrubar o pedido (o webhook é o caminho principal de confirmação); o que
mudou é ficar rasto, com o txid mascarado por `mask_token` como no resto do
módulo.

⚠️ **`datetime.utcnow` está proibido** (`test_nenhum_datetime_utcnow_no_codigo_da_aplicacao`).
O Python 3.12 — que o CI já corre — diz "deprecated and scheduled for removal":
um dia o painel deixa de arrancar. Havia doze chamadas, onze delas o `default=`
de colunas `DateTime`. Use `agora_utc()`, de `app/models.py`. ⚠️ E NÃO a
substituição óbvia: as colunas deste esquema são todas SEM FUSO e o SQLite
guarda-as como texto, por isso um `datetime.now(timezone.utc)` sem o
`replace(tzinfo=None)` escreveria linhas que ordenam e comparam de forma
diferente das que já lá estão — e é por comparação de data que o `cleanup_job`
decide o que apagar.

⚠️ **Um `@limiter.limit` numa rota SUBSTITUI o padrão global**, e por isso
AFROUXA-A quase sempre sem que ninguém repare. O `RATELIMIT_DEFAULT` é
`"200 per day; 50 per hour"` e vale para toda a rota sem limite próprio.
Medido:

    @limiter.limit("100 per minute")                          → o 429 nunca chega
    @limiter.limit("100 per minute", override_defaults=False) → o 429 chega ao 51.º

O caso que doeu foi o `get_invite_details_route`: o docstring dele dizia que o
decorador existia para travar força bruta sobre códigos de convite, e o
decorador passava-a de 50/hora para 1800/hora — 36× mais depressa.

`override_defaults=False` faz os três tetos valerem, e o mais apertado ganha.
⚠️ **Mas não se aplica em bloco**: há rotas onde o teto diário parte o produto.
O PIN do Plex faz polling de 3 em 3 segundos e o estado do pagamento de 5 em 5
— um login de três minutos são 60 pedidos, e os 200/dia matariam o fluxo à
terceira tentativa. A API de bots é autenticada por chave e um bot ativo passa
os 200/dia sem esforço.

Por isso a regra é: **ou a rota soma (`override_defaults=False`), ou está
NOMEADA em `ACIMA_DO_PADRAO_DE_PROPOSITO`** (em
`tests/test_tetos_do_limitador.py`) com o motivo. O teste recusa as duas
distrações: a rota esquecida, e a exceção que ficou na lista depois de a rota
deixar de existir. E não lê só o texto do decorador — pergunta ao próprio
limitador (`limit_manager.resolve_limits`) o que ele vai mesmo aplicar, que é a
diferença entre ler a intenção e medir o efeito. Foi a ler a intenção que isto
passou.

⚠️ **E nunca um `except:` NU.** Sob gevent ele apanha o `GreenletExit`, que é
como um greenlet é morto — engoli-lo faz o worker deixar de conseguir encerrar
aquele pedido —, além do `KeyboardInterrupt` e do `SystemExit`, num painel que
se reinicia a si próprio por sinal. Havia quatro, todos à volta de um `int()`
ou de um `fromisoformat()`, onde o que se queria apanhar cabia em
`(ValueError, TypeError)`. `tests/test_erros_engolidos.py` percorre o `app/` e
recusa que volte a haver um.

#### A data de vencimento: o fuso é de quem escolhe

🐛 **A hora de parede não diz que instante é.** O formulário de vencimento
mandava `2026-09-05T23:59` e o painel fazia `datetime.fromisoformat(...)`, que
devolve uma data INGÉNUA — e o `astimezone(timezone.utc)` a seguir assume o
fuso do SISTEMA. Num contentor sem `TZ` definido, que é o padrão do Docker,
isso é UTC: um administrador no Brasil que marcasse as 23:59 ficava com o
vencimento às 20:59 dele.

E deslizava a CADA gravação, sempre no mesmo sentido, porque o modal reabre com
`new Date(expiration_date)` e mostra a hora já convertida para o fuso de quem
olha:

    volta 1: guardado 23:59Z  ->  o campo mostra 20:59
    volta 2: guardado 20:59Z  ->  o campo mostra 17:59
    volta 3: guardado 17:59Z  ->  o campo mostra 14:59

⚠️ Não era incondicional, e é por isso que sobreviveu tanto tempo: com
`TZ=America/Sao_Paulo` no contentor o servidor e o navegador concordavam. O
painel ASSUMIA isso (`get_app_timezone` diz "lê o fuso forçado no
docker-compose") sem nada o obrigar, e a falha era muda.

Hoje o navegador manda o DESLOCAMENTO (`comDeslocamentoLocal`, em `utils.js`) e
`_momento_do_vencimento` (`api/users.py`) lê um instante inequívoco. Quatro
coisas que isso obriga:

- ⚠️ **uma data SEM deslocamento continua a ser aceite**, e lida no fuso do
  painel — é o que chega de um navegador com o JavaScript antigo em cache, e
  recusá-la trocava um erro de três horas por um erro a gravar;
- ⚠️ **o deslocamento é o daquela DATA, não o de hoje**: onde há horário de
  verão os dois não são o mesmo, por isso pergunta-se ao `Date` construído com
  ela e nunca ao `new Date()` de agora;
- ⚠️ **a hora universal é do PAINEL, o dia é de quem escolheu.**
  `UNIVERSAL_EXPIRATION_TIME` é a mesma hora para toda a gente (é a que os
  `CronTrigger` usam), e quando está ligada o campo da hora aparece desativado
  — a pessoa só escolhe o DIA;
- ⚠️ **o `billing_day` é o dia da PESSOA.** 05/09 às 23:59 no Brasil é 06/09 em
  UTC: ancorar a faturação no 6 mudava o dia da cobrança de toda a gente que
  marcasse uma hora depois das 21:00.

⚠️ E o teste que guarda isto não pode importar o blueprint no topo do módulo:
`app/blueprints/auth.py` captura `media_server` e `data_manager` **por valor**,
e um import na RECOLHA do pytest acontece antes de a fixture `app` correr o
`create_app()` — o `auth.py` fica com `data_manager = None` para o resto do
processo, e quem rebenta são os testes do LOGIN, que não têm nada a ver com
isto. A fixture `app` é de âmbito *session* e não volta a importá-lo.

### Tarefas de fundo

`app/scheduler.py` com APScheduler (`BackgroundScheduler`, jobstore SQLAlchemy
em `config/scheduler_jobs.db`). Jobs recorrentes são registados em
`setup_scheduler()`; jobs por utilizador (`end_trial_job`, `end_subscription_job`)
são agendados dinamicamente. Cada job precisa de contexto explícito — a maioria
usa `_app.test_request_context('/')` porque chama `url_for`. `@single_instance_job`
(via `app/locks.py`, filelock) evita execuções sobrepostas.

O envio em massa não corre no pedido HTTP: grava um `Task` na base de dados e o
`task_processor_job` processa a fila.

`start_background_services()` é idempotente e é chamada tanto pelo `create_app()`
(se já configurado) como pelo fim do assistente de instalação.

🐛 **Uma tarefa DATADA que não corra na hora marcada desaparece**, e o
`misfire_grace_time` do APScheduler é UM SEGUNDO por omissão.
`agendar_fim_do_teste` era a única `add_job` do painel que não o passava — a
irmã dela, o `end_subscription_job`, dá uma hora. Bastava o painel não estar de
pé ao segundo certo (um reinício leva os 30 segundos do `--graceful-timeout` só
a largar as ligações abertas, e o assistente reinicia-se a si próprio) para o
fim do teste ser dado como perdido: a conta ficava aberta **para sempre**, e o
log do APScheduler dizia "was missed by" e mais nada.

🐛 **E não havia rede por baixo.** O comentário do índice parcial de
`trial_end_date` (`app/models.py`) fala das "varreduras diárias:
`get_all_user_expirations` e `get_all_trial_users`" — a primeira é mesmo usada
(pelo `get_users_within_notification_window` dos dois backends), a segunda
estava escrita e **não era chamada de lado nenhum**. O vencimento do teste só
era imposto noutro sítio: `_enforce_user_status_by_date`, que corre quando um
administrador grava aquele perfil à mão. O `trial_sweep_job` (de 15 em 15
minutos, sobre esse índice) fecha-o agora, reentrando no `end_trial_job` em vez
de repetir o bloqueio, o aviso e a limpeza do `trial_job_id`. Três coisas que
ele NÃO faz: tocar em quem tem `expiration_date` (passou a assinante, e o
`trial_end_date` que ficou é história — o mesmo "dar e tirar" do
`add_days_to_subscription`, visto do outro lado), bloquear quem já está
bloqueado (seria um aviso de quinze em quinze minutos, para sempre) e ler uma
data ingénua no fuso do sistema (`replace(tzinfo=utc)`, como os dois backends
já fazem nestas colunas).

### Tempo real

Flask-SocketIO em modo **gevent**, com **1 worker** de propósito (ver o `CMD` do
Dockerfile). Não há `message_queue`, por isso mais workers perderiam eventos
entre processos, e misturar com eventlet quebra o monkey-patching. `run.py`
chama `monkey.patch_all()` antes de qualquer import. `app/sockets.py` mantém a
tarefa de fundo que empurra o resumo do dashboard; o listener de eventos do
servidor vive no `SessionsProvider` e precisa de ser parado no encerramento
(`StreamManager.stop_listener()`, que também cancela o debounce pendente).

### Frontend

Sem framework. Cada página tem o seu `app/static/js/<pagina>.js`, e as páginas
grandes quebram em `*_modules/` com a mesma divisão (`api`, `config`, `dom`,
`handlers`, `ui`, `state`). Helpers partilhados em `utils.js` — `escapeHTML` /
`sanitizeHTML` escapam também aspas, porque o resultado é interpolado dentro de
atributos.

🛡️ **E tem de ser CHAMADO — o `username` é escolhido por quem entra.** Onde as
contas são locais, `conta_a_partir_de_credenciais` valida só o comprimento e o
formato do email: quem resgata um convite escolhe o nome que quiser, incluindo
`<img src=x onerror=…>`. Esse nome era interpolado cru em `innerHTML` em 29
sítios, e o pior deles é o **sino de notificações** — o painel escreve lá
"Renovação manual de %(username)s registrada", o sino vive no `base.html` e
portanto em TODAS as páginas, e o código corria na sessão do administrador sem
ele clicar em nada. Os outros eram o pódio e a tabela de `/statistics` (a casa
de quem não é administrador), o modal de envio em massa, e os `${error.message}`
espalhados pelos tratadores de erro, porque as mensagens do servidor também
levam nomes lá dentro.

⚠️ **O `thumb` e os `data-username` contam.** São interpolados DENTRO de
atributos, onde uma aspa fecha o atributo e abre outro — é por isso que o
`escapeHTML` escapa `"` e `'`. E escapar ao escrever o `data-*` não obriga a
escapar duas vezes ao lê-lo: o browser devolve o valor já decodificado no
`dataset`, e quem o volta a pôr em HTML escapa-o outra vez.

⚠️ **A exceção é o `message` dos modais de confirmação**
(`showConfirmationModal`): ali ele é um fragmento de HTML por contrato — quem
chama já faz `sanitizeHTML(user.username)` e acrescenta o `<strong>` à volta.
Escapá-lo lá dentro partia o negrito e escapava o nome duas vezes.

`tests/test_escape_de_nomes_no_javascript.py` percorre o `app/static/js` e
recusa uma interpolação de `.username`, `.original_username`, `.thumb` ou
`.message` numa linha com marcação sem um `escapeHTML` à volta.

📌 **As datas formatam-se num sítio só**: `formatarDataHora()` e
`formatarData()`, em `utils.js`. Havia TRÊS cópias de `formatDateTime` — uma em
`dashboard_modules`, duas em `users_modules` — e elas não concordavam: a do
painel principal pedia dia/mês/ano e hora:minuto explícitos, as da página de
usuários faziam `toLocaleString()`, que em pt-BR sai com vírgula e SEGUNDOS
(`15/09/2026, 20:48:33`). A mesma data de vencimento aparecia de duas maneiras
conforme a página. Os nomes antigos ficaram, a delegar, para os chamadores não
mudarem.

⚠️ **O idioma vem do `<html lang>`, não do navegador**: quem usa o painel em
português com o navegador em inglês via `09/15/2026, 08:48 PM` — o mês antes do
dia, que se lê ao contrário do que diz. E quem tem um valor que pode não
existir (a data de fim de teste, o "membro desde") passa `{ ausente: '...' }`
em vez de deixar um espaço em branco. Há um teste que falha se algum módulo
voltar a chamar `toLocale*String` sozinho para uma data
(`tests/test_aba_de_auditoria.py`).

📌 **A configuração da página lê-se num sítio só**: `lerConfiguracaoDoScript()`,
em `utils.js`. Ele recebe o id do `<script>` e devolve `{i18n, urls, config,
dataset}`.

⚠️ Isto estava escrito à mão em **doze** ficheiros, com **cinco** regras
diferentes para decidir o nome da chave: `data-url-x` numa página,
`data-urls-x` noutra, `data-x-url` numa terceira, e a de estatísticas a cortar
o SUFIXO `Url` em vez de um prefixo. Nenhuma estava errada — mas quem
trabalhasse em duas páginas tinha de se lembrar de qual era qual, e uma chave
que não resolve **não dá erro nenhum**: dá um `fetch` para `undefined`, ou um
texto em branco. O carregador único aceita os prefixos todos que já existiam
(`i18n`, `config`, `urls`, `url`, e a chave inteira para o resto), por isso
nenhum `data-*` teve de ser renomeado. ⚠️ A ordem dos ramos importa: `urls` é
testado ANTES de `url`, senão `data-urls-foo` ficava com um 's' a mais no nome.
Há um teste que recusa um leitor novo escrito à mão
(`test_so_o_utils_le_o_dataset_do_script`).

⚠️ **NUNCA termine um atributo `data-*` com traço e número.** O browser só come
o '-' quando o que vem a seguir é uma LETRA MINÚSCULA: `data-i18n-step-local-1`
chega ao `dataset` como `i18nStepLocal-1`, com o traço intacto, e o JavaScript
que pede `stepLocal1` recebe `undefined` — era isso que a página de convite
escrevia, por extenso, no "Como começar". Escreva o número por extenso
(`-one`, `-two`) ou junte-o à palavra (`step1-text`, que funciona porque o
traço vem ANTES do número). `chaveEmCamelCase()` (`utils.js`) faz a conversão
que come os traços que sobram, mas nem todos os ficheiros a usam ainda — há um
teste que percorre os templates e impede a armadilha
(`tests/test_assets_frontend.py`).

⚠️ **Um macro importado com `{% import %}` NÃO vê o contexto do template** — só
os argumentos que recebe. O símbolo do painel (`partials/logo.html`) é um macro
por isso mesmo: não lê nada do contexto, recebe as classes de quem o chama.
Um macro que precise de variáveis globais tem de ser importado
`{% import ... with context %}`, ou elas ficam indefinidas lá dentro, sem erro.

O Tailwind compila **apenas** `app/static/css/input.css`; os templates entram no
build só como fonte de nomes de classes. Um `@apply` dentro de um `<style>` de
template é descartado em silêncio pelo browser — classes partilhadas vão para o
`input.css`, estilo de uma página só vai como CSS normal. Há um teste que
verifica isto em toda a pasta de templates (`tests/test_templates_sem_apply.py`).

## Testes

`tests/conftest.py` aponta `PAINEL_PLEX_CONFIG_DIR` para uma pasta temporária,
fixa `TZ=UTC` e define uma `SECRET_KEY` previsível **antes** de qualquer import
da aplicação — é por isso que esse trabalho está no topo do módulo e não numa
fixture.

A fixture `app` é de âmbito *session* (as extensões globais só podem ser
inicializadas uma vez por processo); o isolamento entre testes vem de
`db_session`, que esvazia as tabelas no fim de cada teste, e de `config_file`,
que repõe o `config.json` de teste.

Para lógica de negócio (preços, indicações) prefira `fake_data_manager` — um
duplo em memória do `DataManager`, sem base de dados. Nenhum teste fala com o
Plex, o Tautulli ou um gateway real.

## Ao alterar comportamento

Vários comentários no código começam com 🐛, 🛡️ ou ⚡ e descrevem um bug real que
já aconteceu (o `SIGTERM` que não terminava o processo, o cookie *remember me*
sem `Secure`, a recarga total de serviços a cada gravação). Leia esses
comentários antes de mexer no código que eles protegem, e prefira acrescentar um
teste de regressão a removê-los.
