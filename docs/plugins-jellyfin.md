# Plugins do Jellyfin — limite de telas e histórico

Dois plugins **opcionais** do Jellyfin que o Painel usa automaticamente quando
estão instalados. Nenhum é obrigatório: sem eles o painel funciona na mesma, com
as limitações descritas aqui.

| Plugin | O que resolve | Sem ele |
|---|---|---|
| **StreamLimiter** | Faz o limite de telas ser cumprido em **qualquer** aplicativo | O painel corta a transmissão, mas alguns reprodutores ignoram a ordem |
| **Playback Reporting** | Histórico por **reprodução**, com o aparelho usado | Histórico por item, sem a coluna do reprodutor |

> Esta página é só para quem usa **Jellyfin**. Num painel ligado ao Plex nada
> disto se aplica — lá, o histórico e as estatísticas vêm do Tautulli, e o
> limite de telas é cumprido pelo próprio painel.

O painel detecta os plugins sozinho, com uma cache de 10 minutos. Como instalar
ou remover um plugin obriga a reiniciar o Jellyfin, o painel reconhece a mudança
no máximo 10 minutos depois de o servidor voltar.

---

## 1. StreamLimiter — limite de telas que ninguém consegue contornar

### 1.1 O problema que ele resolve

O painel já limita telas simultâneas sem plugin nenhum: detecta o excesso e
manda o servidor encerrar a transmissão a mais.

**O problema é que mandar parar depende de o aplicativo obedecer.** O reprodutor
integrado do aplicativo Android do Jellyfin (ExoPlayer) recebe a ordem, o
servidor confirma que a aceitou — e a transmissão continua. Pelo navegador o
mesmo corte funciona. Outros reprodutores externos (Infuse, Swiftfin) têm o
mesmo comportamento.

E o Jellyfin **não tem um limite de streams nativo**. A opção
*"Número máximo de sessões de usuários simultâneas"*, na política do usuário,
**não serve** para isto: ela limita **autenticações**, não reproduções. Quem já
está conectado continua assistindo à vontade, e a pessoa ainda fica impedida de
entrar no painel (entrar no painel também ocupa uma sessão). O painel não usa
esse campo.

### 1.2 Como o plugin resolve

Ele não manda parar. **Intercepta o pedido HTTP da mídia e responde `403` antes
de servir um único byte.** Não há aplicativo que consiga ignorar um erro no
pedido do próprio arquivo.

Detalhes que valem a pena saber:

- Um aparelho que já está transmitindo **mantém a vaga**: trocar de episódio,
  mudar a qualidade ou avançar não conta como transmissão nova.
- **Transmissão pausada continua ocupando vaga.**
- As vagas são liberadas quando o cliente avisa que parou, quando a sessão
  termina, ou após 60 segundos sem tráfego.
- Aplicativos nativos (Swiftfin, Infuse, Streamyfin) são bloqueados, mas mostram
  a mensagem de erro **deles** — nenhum plugin de servidor consegue mudar a tela
  de um aplicativo nativo. Mensagem personalizada só funciona no navegador.

### 1.3 ⚠️ Confira a sua versão do Jellyfin antes

O bloqueio HTTP — a única parte que resolve o caso do ExoPlayer — **só existe da
versão 1.1.0.0 do plugin em diante**, e essa exige **Jellyfin 10.11** (ou a
1.1.1.0 para o Jellyfin 12).

**Se você estiver no Jellyfin 10.10 ou anterior**, o servidor vai instalar a
versão 1.0.0.x, que **não tem** o bloqueio HTTP. Instalá-lo não resolveria o
problema dos reprodutores que ignoram o comando de parar.

Veja a sua versão em **Painel do Jellyfin → Painel de Controle**, no rodapé.

> O bloqueio HTTP está marcado como **BETA** pelo autor do plugin, e o plugin é
> de terceiros (JellyboxAD), não oficial do Jellyfin. Vale pesar isso num
> servidor em produção.

### 1.4 Instalação

1. No Jellyfin, vá a **Painel de Controle → Plugins → Repositórios**.
2. Clique em **+** e adicione:

   | Campo | Valor |
   |---|---|
   | Nome | `StreamLimiter` |
   | URL do manifesto | `https://raw.githubusercontent.com/JellyboxAD/Jellyfin.Plugin.StreamLimit/main/manifest.json` |

3. Vá ao separador **Catálogo**, procure **StreamLimit** e instale.
4. **Reinicie o Jellyfin.** Sem reiniciar, o plugin não carrega.

O servidor escolhe a versão certa sozinho a partir do manifesto.

### 1.5 Configuração — o que fazer e o que NÃO fazer

Depois de reiniciar, abra a página de configuração do plugin.

#### ⚠️ Coloque o "Default limit" em **0**

Esta é a parte que pode dar errado, e é importante entender porquê.

**O "0" quer dizer coisas diferentes nos dois lados:**

| Onde | O que "0" significa |
|---|---|
| No **Painel** (limite de telas do usuário) | **Ilimitado** |
| No **plugin** (limite individual) | Apaga o limite individual → passa a valer o **Default limit** |

Ou seja: se você definir um *Default limit* de 2 no plugin, um usuário que o
painel mostra como **"Ilimitado"** vai ficar **limitado a 2 telas** no servidor.
E não existe forma de dispensar o padrão para uma pessoa específica.

Quando isso acontece, o painel registra no log:

```
⚠️ O utilizador <id> está como ILIMITADO no painel, mas o StreamLimiter tem um
limite padrão de 2 tela(s) e não há como o dispensar para uma pessoa em
concreto. Ele vai continuar limitado pelo padrão.
```

**Recomendação: deixe o *Default limit* em 0** e administre os limites
individuais pelo Painel. É a única configuração em que "Ilimitado" no painel é
mesmo ilimitado.

#### Não edite os limites por usuário na página do plugin

O Painel é a fonte da verdade desses valores. Ele reescreve o que estiver
diferente uma vez por dia (na tarefa de limpeza) e sempre que alguém alterar o
limite pelo Painel. Uma alteração feita na página do plugin será desfeita.

#### As restantes opções

Os padrões do plugin são adequados. Se quiser afinar:

- **Hard block at the HTTP level** — deixe **ligado**. É a razão de instalar o plugin.
- **Refuse playback negotiation** — deixe **ligado**. Faz o cliente falhar logo,
  com erro claro, em vez de ficar rodando.
- **Kill transcode jobs** — deixe **ligado**. Rede de segurança.
- **Log out the offending device** — **desligado**. Revoga o token do aparelho e
  obriga a pessoa a entrar de novo nele. O painel já tem uma opção equivalente
  (ver secção 3); ligar as duas é redundante.
- **Blocked message** — escreva o texto que o seu usuário deve ver. Só aparece
  no navegador.

### 1.6 O que o painel faz com o plugin

- **Ao alterar o limite de um usuário** (Usuários → Gerenciar Limite de Telas, ou
  em massa, ou por um upgrade/renovação paga), o painel grava o valor no perfil
  **e** escreve no plugin.
- **Uma vez por dia**, na tarefa de limpeza, o painel repõe no plugin todos os
  limites que estiverem diferentes. É isto que resolve o caso de instalar o
  plugin **depois** de já ter os limites definidos no painel — sem essa
  sincronização, eles nunca chegariam lá.

Para confirmar que está funcionando, procure no log do painel:

```
Plugin StreamLimiter encontrado: o limite de telas passa a ser imposto pelo servidor.
Limite de 2 tela(s) aplicado no servidor para o utilizador <id>.
```

### 1.7 O corte do painel continua ativo — e é preciso

O plugin bloqueia o que **começa**. Ele não sabe nada sobre:

- assinatura vencida;
- usuário bloqueado manualmente;
- período de teste terminado;
- limite reduzido enquanto alguém já está assistindo.

Nesses casos quem age é o painel — e é ele quem envia a mensagem ao usuário e
registra a auditoria. Os dois trabalham juntos.

---

## 2. Playback Reporting — histórico por reprodução

### 2.1 O que muda

O Jellyfin guarda, por usuário e por item, apenas a **data da última vez** que
foi assistido. Por isso, sem o plugin:

- assistir ao mesmo episódio três vezes aparece como **uma linha**;
- a coluna **Reprodutor** fica **vazia** — o servidor não guarda em que aparelho
  cada item foi visto, e inventar seria pior;
- a percentagem é a do item, não a daquela sessão.

Com o plugin instalado, o painel passa a mostrar **uma linha por reprodução**,
cada uma com o aparelho usado e a percentagem real daquela vez.

Isto afeta a aba **Histórico** em **Minha Conta**.

### 2.2 Instalação

Este é um plugin **oficial** do Jellyfin — não precisa adicionar repositório.

1. **Painel de Controle → Plugins → Catálogo**.
2. Procure **Playback Reporting** e instale.
3. **Reinicie o Jellyfin.**

O histórico começa a ser registrado **a partir da instalação**. O que foi
assistido antes não aparece com este detalhe — para esses itens o painel
continua mostrando o que o Jellyfin sabe.

### 2.3 Configuração

Não é preciso configurar nada para o painel usar. Nas opções do plugin dá para
escolher por quantos dias o histórico é guardado — o padrão serve.

### 2.4 Se o plugin falhar

O painel volta automaticamente ao histórico do Jellyfin (por item). A página
nunca fica vazia por causa disso. No log aparece:

```
O Playback Reporting não respondeu: o histórico volta ao registo do núcleo.
```

---

## 3. Se você não puder usar o StreamLimiter

Se estiver numa versão antiga do Jellyfin, ou preferir não instalar um plugin de
terceiros, resta uma opção dentro do próprio painel:

**Configurações → Comunicações → "Forçar o encerramento em aparelhos que ignoram
o comando"** (`FORCE_STREAM_TERMINATION`).

Depois de algumas tentativas ignoradas, o painel **revoga o acesso daquele
aparelho** — o que encerra a transmissão, obedeça o aplicativo ou não.

**Vem desligado de propósito.** O custo é real:

- a pessoa tem de entrar de novo **naquele aparelho** (os outros não são afetados);
- não se desfaz pelo painel;
- não é instantâneo: mata o token, e a transmissão morre quando o reprodutor
  fizer o pedido seguinte (segundos, em transcodificação; pode demorar mais em
  reprodução direta);
- em alguns servidores o Jellyfin recusa a revogação, porque o identificador do
  aparelho que a sessão informa não corresponde a nenhum aparelho registado.
  Nesse caso fica um erro no log a dizê-lo, e o limite realmente não será
  cumprido naquele cliente.

Com o StreamLimiter funcionando, esta opção deixa de ser necessária.

---

## 4. Resolução de problemas

**O painel não reconhece o plugin recém-instalado**
Reinicie o Jellyfin (obrigatório para o plugin carregar) e espere até 10
minutos — é o tempo que o painel guarda a resposta anterior em cache. Reiniciar
o painel também limpa essa cache imediatamente.

**Um usuário "Ilimitado" no painel está sendo limitado**
O *Default limit* do plugin não está em 0. Veja a secção 1.5.

**Alterei um limite na página do plugin e ele voltou ao valor antigo**
É o comportamento esperado: o Painel é a fonte da verdade. Altere pelo Painel,
em **Usuários → Gerenciar Limite de Telas**.

**A segunda transmissão não é bloqueada**
1. Confirme a versão do plugin instalada (precisa ser 1.1.0.0 ou superior) — ver
   secção 1.3.
2. Confirme que **Hard block at the HTTP level** está ligado.
3. Lembre-se de que o mesmo aparelho não gasta uma vaga nova ao trocar de
   episódio. Teste com **dois aparelhos diferentes**.

**A mensagem personalizada não aparece**
Só funciona em clientes web, e é preciso recarregar a página do navegador depois
de alterar o texto. Aplicativos nativos mostram sempre a mensagem de erro deles.

**O histórico continua sem a coluna do reprodutor**
O Playback Reporting só registra a partir da instalação. Assista a algo depois
de instalar e confirme que a linha nova traz o aparelho.
