# app/services/media_server/factory.py

"""Escolhe e constrói o backend de servidor de média a partir da configuração.

Este é o único sítio do painel que sabe que existe mais do que um servidor
possível. Acrescentar um backend novo é registar uma entrada em `_BACKENDS` —
nada mais no resto da aplicação precisa de saber que ele existe.
"""

import logging

logger = logging.getLogger(__name__)

# Valor usado quando a configuração não diz nada (instalações anteriores à
# introdução da chave MEDIA_SERVER_TYPE, que são todas as que existem hoje).
DEFAULT_MEDIA_SERVER_TYPE = 'plex'


def _construir_plex(*, data_manager, stats_manager, notifier_manager, requests_manager):
    # Importado aqui dentro, e não no topo, para que registar um backend não
    # obrigue a carregar as dependências de TODOS os backends (o plexapi, por
    # exemplo) num painel que nem sequer usa esse servidor.
    from .plex import PlexManager

    return PlexManager(
        data_manager=data_manager,
        tautulli_manager=stats_manager,
        notifier_manager=notifier_manager,
        overseerr_manager=requests_manager,
    )


def _construir_jellyfin(*, data_manager, stats_manager, notifier_manager, requests_manager):
    from .jellyfin import JellyfinManager

    return JellyfinManager(
        data_manager=data_manager,
        stats_manager=stats_manager,
        notifier_manager=notifier_manager,
        requests_manager=requests_manager,
    )


# tipo -> construtor. As chaves são o que vai no config.json.
_BACKENDS = {
    'plex': _construir_plex,
    'jellyfin': _construir_jellyfin,
}


def tipos_suportados():
    """Os tipos de servidor que esta versão do painel sabe gerir."""
    return tuple(sorted(_BACKENDS))


def resolve_media_server_type(raw_type):
    """Normaliza o valor vindo do config.json e garante que é utilizável.

    🛡️ Um valor desconhecido (uma gralha na configuração, ou um config.json
    vindo de uma versão mais recente do painel) NÃO pode impedir a aplicação de
    arrancar: sem backend não há painel, e sem painel o administrador não tem
    sequer como corrigir a configuração. Nesse caso registamos o erro e
    seguimos com o Plex.
    """
    tipo = (raw_type or '').strip().lower()

    if not tipo:
        return DEFAULT_MEDIA_SERVER_TYPE

    if tipo not in _BACKENDS:
        logger.error(
            "Tipo de servidor de média desconhecido: '%s'. Os tipos suportados são: %s. "
            "A usar '%s' para que o painel arranque e a configuração possa ser corrigida.",
            raw_type, ', '.join(tipos_suportados()), DEFAULT_MEDIA_SERVER_TYPE,
        )
        return DEFAULT_MEDIA_SERVER_TYPE

    return tipo


def create_media_server(server_type, *, data_manager, stats_manager, notifier_manager, requests_manager):
    """Constrói o backend pedido.

    Os argumentos têm nomes agnósticos de propósito: `stats_manager` é o
    Tautulli hoje e pode ser outro amanhã; `requests_manager` é o
    Overseerr/Jellyseerr. O backend concreto é que sabe o que fazer com eles.
    """
    tipo = resolve_media_server_type(server_type)
    backend = _BACKENDS[tipo](
        data_manager=data_manager,
        stats_manager=stats_manager,
        notifier_manager=notifier_manager,
        requests_manager=requests_manager,
    )
    logger.info("Servidor de média ativo: %s (tipo '%s').", backend.DISPLAY_NAME, tipo)
    return backend
