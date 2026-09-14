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
`IsDisabled`, repõem-se as bibliotecas do perfil, e não há nada para aceitar. Se
a conta já tiver sido apagada pelo `removal_job`, o Jellyfin devolve
`success: False` em vez de dar por reposto um acesso que não existe — recriá-la
seria inventar uma palavra-passe que o painel teria de entregar.

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

- num painel Plex, o Tautulli (`services/tautulli/api_client.py`);
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
  suporta-as **e** a fonte está ligada. É por esta que se escondem o menu, as
  páginas e as sub-abas; um painel Plex sem Tautulli tem a capacidade e não tem
  os dados.

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
faz-se pelo nome da conta do dono (`conn.account`).

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

O caminho normal — um backup **mais antigo**, que é o de quem vem do painel
só-Plex — passa de propósito: as migrações levam-no para a frente no arranque, e
é para isso que elas existem. `a9f3c17b2e04` é quem trata desse salto (identidade
para texto e `media_server_type = 'plex'` nas linhas que já existiam), e o
`MEDIA_SERVER_TYPE` em falta no `config.json` antigo resolve-se sozinho no
`_set_default`. O manifesto do ZIP diz a versão da base de dados e o servidor,
para se perceber meses depois o que ali está.

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

Os templates (Telegram, Discord, WhatsApp, webhook) vivem no config.json e os
padrões estão em `DEFAULT_TEMPLATES` (`notifier_manager.py`) e em
`load_or_create_config()`. Os dois têm de ser mudados juntos.

⚠️ **A marca aparecia no meio das frases entregues ao utilizador** — "O seu
acesso ao Plex está prestes a expirar", "aceite o convite no link abaixo". Num
painel Jellyfin era a marca errada a chegar a quem paga, e "aceitar um convite"
é um passo que ali nem existe. Use `{server_name}`, que vem do
`SHORT_NAME` do backend. Quem já reescreveu os templates na página de
Configurações mantém o que lá tem: um padrão só vale para quem não escolheu.

`{invite_link}` deixou de ser "o link do convite do Plex" e passou a ser "o
endereço para voltar a aceder" — é o `link` que `restaurar_acesso` devolve, e
muda por servidor.

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
