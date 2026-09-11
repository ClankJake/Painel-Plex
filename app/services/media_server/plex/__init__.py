# app/services/media_server/plex/__init__.py

"""Implementação do backend de servidor de média para o Plex.

Este pacote é o *único* sítio onde vive o conhecimento sobre a API do Plex.
A fachada `PlexManager` (em `backend.py`) cumpre o contrato declarado em
`app/services/media_server/base.py`; lógica nova de Plex vai no submanager
correspondente, não na fachada.
"""

from .connection import PlexConnectionManager
from .invite_manager import PlexInviteManager
from .online_media import PlexOnlineMediaManager
from .subscription_manager import PlexSubscriptionManager
from .user_manager import PlexUserManager
from .backend import PlexManager

__all__ = [
    "PlexConnectionManager",
    "PlexInviteManager",
    "PlexManager",
    "PlexOnlineMediaManager",
    "PlexSubscriptionManager",
    "PlexUserManager",
]
