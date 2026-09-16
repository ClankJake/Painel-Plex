# app/services/media_server/base.py

"""Contrato que qualquer servidor de média tem de cumprir para servir o painel.

Porque isto existe: até aqui o painel falava diretamente com o `PlexManager`, e
a única forma de suportar um segundo servidor (Jellyfin, Emby) era espalhar
`if servidor == 'plex'` pelas rotas, pelos jobs e pelos templates. Este módulo
declara a **forma** que o painel consome; cada servidor concreto vive no seu
subpacote (`media_server/plex/`) e limita-se a cumpri-la.

Duas notas sobre o desenho:

1. O contrato é declarado com `typing.Protocol` e não com herança. Assim o
   `PlexManager` não precisa de mudar de classe base para passar a ser um
   backend — o que mantém esta fase sem qualquer alteração de comportamento —
   e um backend futuro também não fica preso a uma hierarquia.

2. Nem tudo o que o Plex faz tem equivalente noutro servidor (convidar uma
   conta que já existe, desligar as Fontes de Mídia Online do plex.tv,
   autenticar por PIN). Em vez de o painel perguntar "que servidor é este?",
   pergunta "este servidor sabe fazer isto?" através de
   `MediaServerCapabilities`. É a diferença entre a interface crescer por
   capacidades ou por uma cascata de exceções por marca.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@dataclass(frozen=True)
class MediaServerCapabilities:
    """O que este servidor sabe (e não sabe) fazer.

    As rotas e os templates devem consultar estas bandeiras em vez de comparar
    o tipo de servidor. Quando uma capacidade é falsa, a funcionalidade
    correspondente esconde-se — não falha.
    """

    # O servidor convida contas que JÁ EXISTEM noutro sítio (o Plex convida uma
    # conta plex.tv). Falso num servidor onde as contas são locais.
    convites_nativos: bool

    # O painel é que cria a conta no servidor, e passa a ser responsável por
    # entregar as credenciais ao utilizador. É o oposto de `convites_nativos`
    # na prática, mas fica explícito porque muda o fluxo da página de convite.
    cria_contas: bool

    # Existe o conceito de "Fontes de Mídia Online" (Discover, Tidal) que o
    # painel desliga na conta do utilizador ao aceitar o convite.
    fontes_media_online: bool

    # A autenticação é delegada a um serviço externo (o PIN do plex.tv).
    # Quando é falso, o painel tem de pedir utilizador e palavra-passe.
    login_delegado: bool

    # É possível suspender a conta sem lhe retirar as partilhas. O Plex não
    # tem isto (bloquear = remover o acesso às bibliotecas); o Jellyfin tem
    # (`Policy.IsDisabled`), o que torna o desbloqueio trivial e fiável.
    desativa_conta: bool

    # O servidor expõe um identificador estável usado para montar links
    # profundos para o cliente web.
    links_profundos: bool

    # O servidor PODE ter estatísticas de visualização (pódio, XP, conquistas,
    # recomendações, Wrapped). O histórico e os aparelhos da página da conta
    # NÃO dependem disto: cada backend responde por eles à sua maneira.
    #
    # ⚠️ "Pode" não é "tem": num painel Plex sem Tautulli configurado esta
    # capacidade continua verdadeira — é ela que mantém o cartão do Tautulli
    # nas Conexões, sem o qual não haveria onde o configurar. Quem quiser saber
    # se as estatísticas existem AGORA pergunta `estatisticas_disponiveis()`.
    estatisticas: bool

    # A fonte das estatísticas é um serviço À PARTE, que o administrador tem de
    # configurar (o Tautulli). Quando é falso, elas saem do próprio servidor de
    # média e não há nada para configurar — é esta bandeira que decide se o
    # cartão do Tautulli aparece nas Conexões e no estado do sistema.
    estatisticas_externas: bool

    # Há alguma coisa MAIS FORTE do que pedir para parar. `terminate()` pede
    # educadamente e há clientes que não obedecem; `force_terminate()` usa o que
    # o servidor tiver de mais contundente (no Jellyfin, revogar o acesso do
    # aparelho). O Plex não tem nada: a definição "Forçar o encerramento em
    # aparelhos que ignoram o comando" aparecia lá na mesma e não fazia nada —
    # é por esta bandeira que ela se esconde. Tem valor por omissão porque um
    # backend que não a declare é um backend sem último recurso.
    corte_forcado: bool = False


@runtime_checkable
class ConnectionBackend(Protocol):
    """Ligação ao servidor e leitura do catálogo de bibliotecas."""

    def reload(self, from_job: bool = False) -> Tuple[bool, str]: ...

    def get_libraries(self) -> List[Dict[str, str]]: ...

    def get_server_identifier(self) -> Optional[str]: ...


@runtime_checkable
class UserDirectory(Protocol):
    """Os utilizadores com acesso ao servidor e as permissões de cada um.

    `list_users()` é a leitura CRUA do servidor — é o que o motor de streams e
    o resto do painel interno consomem. Não confundir com o
    `MediaServerBackend.get_all_users()` da fachada, que é a mesma lista já
    tratada para a interface (avatares reescritos para o proxy de imagens,
    sincronização periódica). Foram um único método com o mesmo nome em duas
    camadas durante muito tempo, e era uma fonte de enganos.
    """

    def list_users(self, force_refresh_signal: Any = None) -> Optional[List[Dict[str, Any]]]: ...

    def get_user_by_id(self, media_user_id: Any) -> Optional[Dict[str, Any]]: ...

    def get_user_libraries(self, media_user_id: Any) -> Dict[str, Any]: ...

    def update_user_libraries(self, media_user_id: Any, library_titles: List[str], allow_sync: Optional[bool] = None) -> Dict[str, Any]: ...

    def block_user(self, media_user_id: Any, reason: str = 'manual') -> Dict[str, Any]: ...

    def unblock_user(self, media_user_id: Any) -> Dict[str, Any]: ...

    def remove_user(self, media_user_id: Any) -> Dict[str, Any]: ...

    def invalidate_user_cache(self) -> None: ...


@runtime_checkable
class AccountProvisioning(Protocol):
    """Como é que alguém de fora passa a ter acesso ao servidor.

    No Plex isto é convidar uma conta plex.tv; noutro servidor será criar a
    conta local. O painel só conhece o ciclo de vida do *convite* — o código,
    as vagas, o resgate — e delega o ato de dar acesso ao backend.
    """

    def create_invitation(self, **kwargs: Any) -> Dict[str, Any]: ...

    def send_invite(self, identifier: str, library_titles: List[str], media_user_id: Any = None, allow_sync: bool = False) -> Dict[str, Any]: ...

    def get_invitation_by_code(self, code: str) -> Tuple[Optional[Dict[str, Any]], str]: ...

    def claim_invitation(self, code: str, account: Any) -> Dict[str, Any]: ...

    def conta_a_partir_de_credenciais(self, credenciais: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
        """A conta que vai resgatar o convite, a partir do que o formulário enviou.

        ⚠️ **É aqui que os dois mundos divergem, e é só aqui.** No Plex a conta
        JÁ EXISTE e o que chega é um token do plex.tv, que tem de ser validado
        contra a plex.tv; num servidor de contas locais a conta ainda não
        existe e o que chega são as credenciais que a pessoa acabou de
        escolher.

        Isto estava na rota `/api/invites/claim`, que para o fazer importava
        `plexapi.myplex.MyPlexAccount` — um blueprint a saber que o servidor é
        o Plex, exatamente o que a fachada existe para impedir. Num painel
        Jellyfin, esse import continuava a ser carregado para nada.

        Devolve `(conta, None)` ou `(None, mensagem)`. A mensagem é para a
        pessoa ler: nunca o texto de uma exceção, porque esta rota é PÚBLICA.
        """
        ...

    def list_invitations(self) -> List[Dict[str, Any]]: ...

    def delete_invitation(self, code: str) -> Any: ...

    def reactivate_invitation(self, code: str) -> Any: ...


@runtime_checkable
class SubscriptionScheduler(Protocol):
    """Vencimentos, renovações e os jobs que os fazem cumprir.

    Esta parte é quase toda agnóstica ao servidor — está aqui apenas porque a
    fachada a expõe e o backend precisa de a ligar ao seu gestor de
    utilizadores para bloquear/desbloquear no momento certo.
    """

    def renew_subscription(self, media_user_id: Any, months_to_add: int, **kwargs: Any) -> Dict[str, Any]: ...

    def add_days_to_subscription(self, media_user_id: Any, days: int) -> Dict[str, Any]: ...

    def schedule_user_expiration(self, media_user_id: Any, expiration_date: Any) -> Any: ...

    def end_user_trial(self, media_user_id: Any) -> Any: ...

    def check_user_expiration(self, media_user_id: Any) -> Any: ...




@dataclass
class OwnerAccount:
    """A conta que administra o servidor, no mínimo que o painel precisa.

    Existe para que a criação da sessão de administrador não tenha de receber
    um objeto da biblioteca do Plex. O `PlexManager` devolve a conta do plexapi
    (que já tem estes atributos) e o Jellyfin monta este objeto; quem consome
    só lê id, username, email e thumb.
    """

    id: str
    username: str
    email: Optional[str] = None
    thumb: Optional[str] = None

@dataclass
class MediaSession:
    """Uma reprodução a decorrer, já traduzida do vocabulário do servidor.

    O motor de streams (`app/services/stream_manager.py`) só conhece esta
    forma. Tudo o que é específico de um servidor — como se lê o estado de um
    leitor, onde está o progresso, que campo tem a capa — fica do lado do
    `SessionsProvider` que a produziu.

    `raw` é o objeto original do servidor. Existe apenas para que o provider o
    receba de volta em `terminate()`: nada fora do provider o deve inspecionar,
    ou volta a haver conhecimento do Plex espalhado pelo painel.
    """

    # Quem está a ver. `user_id` já vem normalizado (ver app/utils/identity.py);
    # `username_fallback` é o nome que o próprio servidor associa à sessão, usado
    # quando o utilizador não consta do diretório.
    user_id: Optional[str]
    username_fallback: str
    user_email: str

    # O que se usa para MANDAR PARAR esta reprodução.
    session_key: str

    # `media_title` é o título composto que vai para os logs e para a auditoria
    # ("Série S01E02 - Episódio"); `title`/`subtitle` são o que a interface mostra.
    media_title: str
    title: str
    subtitle: str
    media_type: str

    # Um de: playing, paused, buffering, stopped.
    state: str

    # `platform` é a classe de ícone já resolvida (chromecast, chrome, ios, ...).
    platform: str
    player: str

    progress: float
    view_offset: int
    duration: int

    # Prefixo + caminho no vocabulário de imagens do backend (ver
    # `MediaServerBackend.IMAGE_SOURCES`), ex. "plex:/library/metadata/1/thumb".
    # O painel limita-se a passá-lo ao proxy de imagens.
    artwork_source: Optional[str] = None

    stream_details: Dict[str, Any] = field(default_factory=dict)

    # O que identifica ESTA reprodução, por oposição à sessão que a serve.
    #
    # 🐛 Nem sempre são a mesma coisa, e confundi-los custou caro: no Plex, o
    # `sessionKey` muda a cada reprodução; no Jellyfin, o `Id` da sessão é do
    # APARELHO e sobrevive a parar e recomeçar. Como o painel guardava "já
    # cortei esta" pelo `session_key`, no Jellyfin quem recomeçasse o filme
    # logo a seguir ao corte ficava um minuto inteiro sem ser incomodado — e o
    # limite de telas parecia não funcionar.
    #
    # Fica igual ao `session_key` quando o servidor não distingue os dois.
    playback_key: Optional[str] = None

    raw: Any = None

    def __post_init__(self):
        if not self.playback_key:
            self.playback_key = self.session_key


@runtime_checkable
class SessionsProvider(Protocol):
    """Lê e encerra reproduções, e avisa quando alguma coisa muda.

    É a fronteira do tempo real. O motor de streams decide *o que fazer* (quem
    excede telas, quem está bloqueado, com que atraso reagir); o provider sabe
    *como falar* com o servidor.
    """

    def is_connected(self) -> bool: ...

    def reconnect(self) -> Tuple[bool, str]: ...

    def get_owner_id(self) -> Optional[str]: ...

    def list_sessions(self) -> List[MediaSession]: ...

    def terminate(self, session: MediaSession, reason: str) -> bool:
        """Encerra a sessão. Devolve False quando ainda não é possível.

        Uma sessão que o servidor ainda não registou por completo (a carregar,
        sem identificador interno) não pode ser encerrada: o provider diz que
        não, e quem chama volta a tentar mais tarde em vez de dar o corte por
        feito.
        """

    def force_terminate(self, session: MediaSession, reason: str) -> bool:
        """Último recurso, para clientes que IGNORAM a ordem de parar.

        `terminate()` pede educadamente — e há clientes que simplesmente não
        obedecem (o leitor integrado da aplicação Android do Jellyfin é um
        deles). Quando isso acontece, o painel fica a pedir para sempre e o
        limite de telas deixa de valer alguma coisa.

        Este método usa o que o servidor tiver de mais forte. É por natureza
        AGRESSIVO — pode obrigar o utilizador a autenticar-se de novo naquele
        aparelho — por isso só é chamado depois de a via educada ter falhado
        várias vezes, e só se o administrador o tiver autorizado.

        Devolve False quando o servidor não tem nada mais forte a oferecer.
        """
        return False

    def suporta_corte_forcado(self) -> bool:
        """Há mesmo alguma coisa mais forte, ou `force_terminate` é só um `False`?

        O motor de streams precisa de saber isto ANTES de tentar: com o último
        recurso desligado, era ele que aconselhava o administrador a ligar a
        definição `FORCE_STREAM_TERMINATION` — conselho inútil num servidor que
        não tem nada mais forte a oferecer.

        ⚠️ Tem de dizer o mesmo que `capabilities.corte_forcado` do backend, e
        há um teste de contrato que compara os dois.
        """
        return False

    def user_thumb_source(self, raw_thumb: Optional[str]) -> Optional[str]:
        """Converte o avatar cru do diretório num prefixo para o proxy de imagens."""

    def deduplicate_sessions(self, sessions: List[MediaSession]) -> List[MediaSession]:
        """Funde as sessões que são, na verdade, a MESMA reprodução.

        Alguns servidores devolvem duas entradas para uma só reprodução (o Plex
        lista o telemóvel que comanda um Chromecast ao lado do Chromecast). Só
        o provider sabe se o seu servidor faz isso e como reconhecer o par —
        por isso a decisão vive aqui, e não no motor de streams.

        ⚠️ Fundir a mais é PIOR do que não fundir: o que se funde deixa de
        contar para o limite de telas, e um utilizador a ver a mesma coisa em
        dois aparelhos escapa ao corte. Na dúvida, devolva a lista intacta.
        """

    # --- Tempo real ---

    def supports_realtime(self) -> bool: ...

    def is_listener_healthy(self) -> bool: ...

    def start_listener(self, on_change: Callable[[], None]) -> None:
        """Liga-se aos eventos do servidor e chama `on_change` quando algo muda.

        O provider é responsável por filtrar o ruído: os servidores reenviam o
        estado de cada sessão de poucos em poucos segundos, e `on_change` só
        deve ser chamado numa mudança real (sessão nova, play/pausa, fim).
        """

    def stop_listener(self) -> None: ...

@runtime_checkable
class MediaServerBackend(Protocol):
    """A fachada que o painel consome.

    Um backend novo tem de cumprir *isto*. Os nomes aqui já são os definitivos
    e agnósticos: quem escrever o backend do Jellyfin não deve ter de
    implementar nada chamado "plex".
    """

    # Identificação, para logs, para a interface e para a recarga seletiva.
    SERVER_TYPE: str
    DISPLAY_NAME: str
    # O nome curto, para o que a pessoa lê no meio de uma frase ("Ver no
    # Plex", "O Seu Jellyfin Wrapped"). Estava escrito à mão nas páginas das
    # estatísticas, que eram só do Plex.
    SHORT_NAME: str

    @property
    def capabilities(self) -> MediaServerCapabilities: ...

    def estatisticas_disponiveis(self) -> bool:
        """Há estatísticas de visualização NESTE MOMENTO?

        `capabilities.estatisticas` diz se o servidor as suporta; isto diz se a
        fonte delas está mesmo ligada — no Plex, se o Tautulli está
        configurado. A interface esconde-as por esta resposta, e não pela
        capacidade, ou um painel Plex sem Tautulli mostrava um pódio vazio e
        mandava quem não é administrador para uma página sem nada.
        """
        return bool(self.capabilities.estatisticas)

    # --- Submanagers ---
    conn: ConnectionBackend
    users: UserDirectory
    invites: AccountProvisioning
    subscriptions: SubscriptionScheduler
    sessions: SessionsProvider

    # --- Ciclo de vida ---
    def init_app(self, app: Any) -> None: ...

    def reload_connections(self, from_job: bool = False) -> Tuple[bool, str]: ...

    def check_status(self) -> Dict[str, str]: ...

    # --- Servidor ---
    def get_libraries(self) -> List[Dict[str, str]]: ...

    def get_server_identifier(self) -> Optional[str]: ...

    def get_base_url(self) -> Optional[str]: ...

    def authenticate(self, username: str, password: str) -> Optional[Any]:
        """Autentica um utilizador com credenciais próprias do servidor.

        Só faz sentido quando `capabilities.login_delegado` é falso. Num
        servidor que delega a autenticação (o Plex, pelo PIN do plex.tv) este
        método devolve None e o painel usa o fluxo delegado.

        Devolve um `OwnerAccount` (ou equivalente) em caso de sucesso e None
        quando as credenciais não servem — nunca levanta exceção por credenciais
        erradas, que é o caso normal e não um erro.
        """

    def get_owner_account(self) -> Optional[Any]:
        """A conta de administrador do servidor (ver `OwnerAccount`).

        None quando não há ligação ou o servidor não a sabe identificar — quem
        chama deve tratar isso como "ainda não sei quem é", não como erro.
        """

    def is_connected(self) -> bool: ...

    # --- Utilizadores ---
    def get_all_users(self, force_refresh: bool = False) -> List[Dict[str, Any]]: ...

    def get_user_by_id(self, user_id: Any) -> Optional[Dict[str, Any]]: ...

    def get_user_libraries(self, user_id: Any) -> Dict[str, Any]: ...

    def update_user_libraries(self, user_id: Any, library_titles: List[str], allow_sync: Optional[bool] = None) -> Dict[str, Any]: ...

    def block_user(self, user_id: Any, reason: str = 'manual') -> Dict[str, Any]: ...

    def unblock_user(self, user_id: Any) -> Dict[str, Any]: ...

    def remove_user(self, user_id: Any) -> Dict[str, Any]: ...

    def restaurar_acesso(self, media_user_id: Any, profile: Dict[str, Any],
                         libraries: Optional[List[str]] = None) -> Dict[str, Any]:
        """Devolve o acesso a quem pagou uma reativação. Muda por servidor.

        ⚠️ **Isto não é "enviar um convite".** Era o que estava escrito no
        `PlexSubscriptionManager`, que os DOIS backends usam: um pagamento de
        reativação num painel Jellyfin ia parar a `invites.send_invite()`, e o
        que vinha a seguir era um endereço `clients.plex.tv/.../accept` — ou,
        em falhando, `app.plex.tv/desktop` — mandado a quem nunca teve conta
        no Plex. Pior: no Jellyfin a conta já existe e "enviar convite" é
        CRIAR uma conta, pelo que a tentativa era uma conta duplicada com o
        email por nome e uma palavra-passe que ninguém veria.

        O que cada servidor faz é mesmo diferente:

        - no Plex, o acesso foi RETIRADO (as partilhas) e é preciso convidar a
          conta outra vez; fica um link por aceitar, que a notificação leva;
        - onde as contas são locais, a conta foi SUSPENSA e basta reativá-la —
          não há nada para aceitar, e o link é o do próprio servidor.

        Devolve `{"success", "message", "media_user_id", "link",
        "link_pendente"}` e, quando a conta teve de ser criada de novo,
        `credenciais`:

        - `link` é o que vai na notificação; `link_pendente` só existe quando
          sobra mesmo alguma coisa por aceitar — é o que faz aparecer o botão de
          confirmação manual na página de pagamento;
        - ⚠️ `media_user_id` pode NÃO ser o que entrou. Onde as contas são
          locais, uma conta apagada e recriada volta com um identificador novo
          (o servidor atribui-o e não aceita que se lhe imponha um): o perfil é
          migrado para ele, e quem chamou tem de passar a usá-lo;
        - 🛡️ `credenciais` (`username`, `password`) é a palavra-passe NOVA de
          uma conta recriada. Vem daqui para ser entregue pelas notificações e
          para mais lado nenhum — nunca para um log.
        """
        return {"success": True, "message": "", "media_user_id": media_user_id,
                "link": None, "link_pendente": None}

    def definir_palavra_passe(self, media_user_id: Any, nova: str) -> Dict[str, Any]:
        """Muda a palavra-passe de uma conta, sem conhecer a anterior.

        Só existe onde as contas são LOCAIS (`capabilities.cria_contas`): aí o
        painel criou-as e é responsável pelas credenciais, e é quem pode repor
        o acesso de quem se esqueceu. Onde a autenticação é delegada (o Plex), a
        palavra-passe vive no plex.tv e não há nada a fazer daqui — a resposta é
        um "não", não um erro.

        🛡️ A palavra-passe entra por aqui e não sai: nem para o log, nem para a
        resposta.
        """
        return {"success": False, "message": "Este servidor não gere palavras-passe."}

    # ⚠️ A ÚNICA porta para mudar o limite de telas de alguém. Grava o perfil e,
    # onde houver quem o imponha (no Jellyfin, o plugin StreamLimiter), leva-o
    # também ao servidor. Nenhum dos dois servidores sabe fazê-lo sozinho.
    def update_screen_limit(self, user_id: Any, screens: int) -> None: ...

    def sync_screen_limits(self) -> Dict[str, Any]:
        """Repõe no servidor os limites que divergirem do painel.

        Serve o caso de quem instala o que impõe os limites DEPOIS de já os ter
        definido no painel — nada os voltaria a escrever sozinho. Onde não há
        ninguém a impô-los, não há nada a fazer.
        """
        return {"success": True, "corrigidos": 0}

    # --- Histórico e aparelhos do utilizador ---
    # De onde vêm muda por servidor: no Plex é o Tautulli que os guarda, no
    # Jellyfin é o próprio servidor. Quem chama não precisa de saber.
    def get_user_devices(self, user_id: Any) -> Dict[str, Any]: ...

    def get_watch_history(self, user_id: Any, page: int = 1, length: int = 15,
                          search: str = "") -> Dict[str, Any]: ...

    def link_para_item(self, item_id: Any) -> Optional[str]:
        """O endereço onde a pessoa abre ESTE item no cliente do servidor.

        Estava escrito numa rota, a montar um URL de app.plex.tv à mão — o que
        num painel Jellyfin dava um botão "Ver no Plex" que levava a lado
        nenhum. Como tudo o que é vocabulário do servidor, vive no backend.
        None quando não há como o construir (sem ligação, ou sem item).
        """
        return None

    def importar_bloqueios_do_servidor(self) -> Dict[str, Any]:
        """Traz para a auditoria os cortes que o SERVIDOR deu sozinho.

        Só existe onde há quem corte além do painel — no Jellyfin, o plugin
        StreamLimiter, que recusa o pedido da mídia dentro do processo do
        servidor e não deixa rasto nenhum por aqui. Onde o painel é o único a
        cortar, não há nada a importar.
        """
        return {"success": True, "importados": 0}

    def clear_session_limits(self) -> Dict[str, Any]:
        """Reparação de uma vez: tira do servidor um limite que o painel lá pôs.

        O painel chegou a escrever o `MaxActiveSessions` do Jellyfin a pensar
        que era um limite de telas. Não é — limita autenticações — e deixou
        utilizadores sem conseguirem entrar no painel. Num servidor onde o
        painel nunca escreveu nada disto, não há nada a fazer.
        """
        return {"success": True, "limpos": 0}

    def invalidate_user_cache(self) -> None: ...

    # --- Imagens ---
    # Os prefixos que este backend reconhece no proxy `/image/?source=`, e a
    # construção do URL autenticado para cada um.
    IMAGE_SOURCES: Tuple[str, ...]

    def authorize_image_url(self, source: str, image_path: str) -> Tuple[Optional[str], Dict[str, Any]]: ...

    def thumb_para_interface(self, thumb: Optional[str]) -> Optional[str]:
        """Um caminho de imagem DESTE servidor no URL que o browser consegue pedir.

        Existe para quem tem um thumb sem saber de onde veio — o caso da sessão
        de quem já estava autenticado antes de o formato mudar. Tem de ser
        idempotente: o que já é um URL do proxy volta intacto.
        """
        return thumb

    def sync_profiles_from_server(self, only_missing: bool = True) -> Dict[str, Any]: ...

    # --- Sessões ---
    def get_active_sessions(self) -> Dict[str, Any]: ...


__all__ = [
    'AccountProvisioning',
    'ConnectionBackend',
    'MediaServerBackend',
    'MediaServerCapabilities',
    'MediaSession',
    'OwnerAccount',
    'SessionsProvider',
    'SubscriptionScheduler',
    'UserDirectory',
]
