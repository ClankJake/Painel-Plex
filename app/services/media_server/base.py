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

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


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

    def get_machine_identifier(self) -> Optional[str]: ...


@runtime_checkable
class UserDirectory(Protocol):
    """Os utilizadores com acesso ao servidor e as permissões de cada um.

    ⚠️ Os nomes com 'plex' são herdados e mantêm-se nesta fase para que nenhum
    dos ~100 pontos de chamada existentes tenha de mudar. A renomeação para
    nomes agnósticos é mecânica e fica para a fase seguinte; o contrato que
    conta para quem escreve um backend novo é o `MediaServerBackend` abaixo,
    que já usa os nomes definitivos.
    """

    def get_all_plex_users(self, force_refresh_signal: Any = None) -> Optional[List[Dict[str, Any]]]: ...

    def get_user_by_id(self, plex_user_id: Any) -> Optional[Dict[str, Any]]: ...

    def get_user_libraries(self, plex_user_id: Any) -> Dict[str, Any]: ...

    def update_user_libraries(self, plex_user_id: Any, library_titles: List[str], allow_sync: Optional[bool] = None) -> Dict[str, Any]: ...

    def block_user(self, plex_user_id: Any, reason: str = 'manual') -> Dict[str, Any]: ...

    def unblock_user(self, plex_user_id: Any) -> Dict[str, Any]: ...

    def remove_user(self, plex_user_id: Any) -> Dict[str, Any]: ...

    def invalidate_user_cache(self) -> None: ...


@runtime_checkable
class AccountProvisioning(Protocol):
    """Como é que alguém de fora passa a ter acesso ao servidor.

    No Plex isto é convidar uma conta plex.tv; noutro servidor será criar a
    conta local. O painel só conhece o ciclo de vida do *convite* — o código,
    as vagas, o resgate — e delega o ato de dar acesso ao backend.
    """

    def create_invitation(self, **kwargs: Any) -> Dict[str, Any]: ...

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

    def renew_subscription(self, plex_user_id: Any, months_to_add: int, **kwargs: Any) -> Dict[str, Any]: ...

    def add_days_to_subscription(self, plex_user_id: Any, days: int) -> Dict[str, Any]: ...

    def schedule_user_expiration(self, plex_user_id: Any, expiration_date: Any) -> Any: ...

    def end_user_trial(self, plex_user_id: Any) -> Any: ...

    def check_user_expiration(self, plex_user_id: Any) -> Any: ...


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

    # --- Ciclo de vida ---
    def init_app(self, app: Any) -> None: ...

    def reload_connections(self, from_job: bool = False) -> Tuple[bool, str]: ...

    def check_status(self) -> Dict[str, str]: ...

    # --- Servidor ---
    def get_libraries(self) -> List[Dict[str, str]]: ...

    def get_server_identifier(self) -> Optional[str]: ...

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

    # --- Sessões ---
    def get_active_sessions(self) -> Dict[str, Any]: ...


__all__ = [
    'AccountProvisioning',
    'ConnectionBackend',
    'MediaServerBackend',
    'MediaServerCapabilities',
    'SubscriptionScheduler',
    'UserDirectory',
]
