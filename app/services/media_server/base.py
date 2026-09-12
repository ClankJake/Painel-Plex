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

    def claim_invitation(self, code: str, plex_user_account: Any) -> Dict[str, Any]: ...

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

    @property
    def capabilities(self) -> MediaServerCapabilities: ...

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

    def update_screen_limit(self, user_id: Any, screens: int) -> None: ...

    def invalidate_user_cache(self) -> None: ...

    # --- Imagens ---
    # Os prefixos que este backend reconhece no proxy `/image/?source=`, e a
    # construção do URL autenticado para cada um.
    IMAGE_SOURCES: Tuple[str, ...]

    def authorize_image_url(self, source: str, image_path: str) -> Tuple[Optional[str], Dict[str, Any]]: ...

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
