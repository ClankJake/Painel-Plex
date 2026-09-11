# app/services/media_server/jellyfin/__init__.py

"""Backend do Jellyfin: o único sítio com conhecimento da API do Jellyfin."""

from .api_client import JellyfinApiClient, JellyfinApiError
from .connection import JellyfinConnectionManager
from .user_manager import JellyfinUserManager
from .sessions import JellyfinSessionsProvider
from .account_manager import JellyfinAccountManager
from .backend import JellyfinManager

__all__ = [
    "JellyfinAccountManager",
    "JellyfinApiClient",
    "JellyfinApiError",
    "JellyfinConnectionManager",
    "JellyfinManager",
    "JellyfinSessionsProvider",
    "JellyfinUserManager",
]
