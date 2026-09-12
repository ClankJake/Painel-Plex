# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

O código, os comentários, as mensagens de commit e a documentação deste projeto
estão em português. Mantenha esse idioma no que escrever.

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

# Executar a aplicação (aplica `flask db upgrade` antes de subir)
python run.py

# Migrações
flask db migrate -m "descrição"
flask db upgrade
```

Não há linter nem formatador configurados. O CI (`.github/workflows/tests.yml`)
corre apenas o pytest, em Python 3.11 e 3.12.

## Arquitetura

Flask com *application factory* (`create_app()` em `app/__init__.py`), SQLite,
Jinja2 + Tailwind, e JavaScript ES modules sem framework. O painel administra um
servidor Plex: usuários, convites, assinaturas, pagamentos PIX, notificações e
estatísticas vindas do Tautulli.

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
  vive em nenhum deles.

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
existe para evitar. E está limitada a 10 por minuto, porque é a única rota do
painel onde se podem testar palavras-passe. Isso muda o fluxo do
convite (passa a pedir utilizador e palavra-passe) e enfraquece o anti-abuso de
períodos de teste — uma conta nova não custa nada e nada a liga à mesma pessoa.
Um convite de teste num servidor destes deve exigir um contacto verificável.

Em troca, o bloqueio é muito melhor: `Policy.IsDisabled` é um booleano, e o
utilizador mantém as bibliotecas. No Plex é preciso retirar as partilhas,
guardar quais eram e repô-las depois.

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
mensagem e regista a auditoria.

`update_screen_limit()` na fachada é a **única porta** para mudar o limite de
alguém: grava o perfil e leva-o ao plugin. Quem escrever
`profile['screen_limit']` à mão espalha de novo por seis sítios uma decisão que
é de um só (foi o que aconteceu: rotas de administração, upgrades pró-rata e
renovações, cada uma à sua maneira).

A deteção dos plugins do Jellyfin é partilhada em `plugins.py` (o StreamLimiter
e o Playback Reporting precisam do mesmo), com cache de 10 minutos — instalar
um plugin obriga a reiniciar o Jellyfin, por isso não muda sozinho. Procura-se
pelo GUID **e** pelo nome: o GUID é estável, o nome é o que se lê no log.

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

O campo JSON de entrada chama-se `media_user_id`; `plex_user_id` continua a ser
aceite em `user_lookup_by_id` para não partir integrações já feitas.

`TautulliManager` segue o mesmo padrão sobre `app/services/tautulli/`
(`api_client`, `stats_handler`, `recommendations_handler`).

### Estatísticas, histórico e aparelhos: três coisas, não uma

O Tautulli só fala com o Plex, mas não é tudo o que ele dá que fica de fora.

**As ESTATÍSTICAS ficam** (pódio, XP, conquistas, recomendações, Wrapped): saem
do registo por reprodução que só o Tautulli guarda. A capacidade
`estatisticas` diz onde existem; onde é falsa, escondem-se — as ligações do
menu, o cartão do Tautulli nas Conexões e as sub-abas Conquistas, XP e
Recomendações da Gamificação ("Indique e Ganhe" é de pagamentos e fica). As
páginas `/statistics` e `/wrapped` redirecionam: esconder a ligação não chega,
um marcador nos favoritos dava uma página vazia sem explicação.

⚠️ Ao escondê-las, a casa de quem não é administrador deixa de existir — e
estava escrita à mão em cinco sítios. `endpoint_inicial_do_utilizador()`
(`app/utils/navigation.py`) responde por todos.

**O HISTÓRICO e os APARELHOS não ficam**: são do contrato
(`get_watch_history`, `get_user_devices`) e cada backend responde à sua
maneira. No Plex vão ao Tautulli; no Jellyfin, a `jellyfin/history.py`, que os
tira do próprio servidor. Duas diferenças a ter presentes:

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

⚠️ **O ADMINISTRADOR NÃO TEM PERFIL LOCAL**, por desenho: o login dele devolve
na primeira ramificação de `_autorizar_e_iniciar_sessao`, antes da parte que
cria perfis, e `_sync_plex_and_local_profiles` salta-o de propósito. As rotas da
"Minha Conta" (`/account/details`, `/account/profile`, `/account/privacy`)
assumiam um dicionário e rebentavam com `AttributeError` assim que ele abria a
página. As que gravam criam o perfil em falta; a que lê usa um dicionário vazio.
`/payments/options` seguia a mesma armadilha por outro caminho: respondia 400
("utilizador não especificado"). Sem assinatura não há planos, e isso não é um
erro do PEDIDO — só um token inexistente o é.

⚠️ **Na página da conta, um pedido secundário derrubava a página inteira.** O
`fetchAPI` levanta em qualquer resposta que não seja 2xx, e os pedidos iniciais
corriam num `Promise.all` — que rejeita com a PRIMEIRA falha. O
`if (paymentOptions.success)` que trata a ausência de planos já lá estava; só
nunca chegava a correr. Isso atingia também um painel sem preços configurados,
que responde 404. Um teste percorre agora todas as rotas que a página chama,
com uma sessão de administrador, porque corrigi-las uma a uma foi precisamente
o que não chegou à primeira vez.

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
