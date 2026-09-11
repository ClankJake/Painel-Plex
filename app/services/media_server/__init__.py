# app/services/media_server/__init__.py

"""Camada de abstração sobre o servidor de média.

`base.py` declara o contrato, `factory.py` escolhe a implementação e cada
subpacote (`plex/`) é um backend concreto.
"""

from .base import (
    AccountProvisioning,
    ConnectionBackend,
    MediaServerBackend,
    MediaServerCapabilities,
    SubscriptionScheduler,
    UserDirectory,
)
from .factory import (
    DEFAULT_MEDIA_SERVER_TYPE,
    create_media_server,
    resolve_media_server_type,
    tipos_suportados,
)

__all__ = [
    'AccountProvisioning',
    'ConnectionBackend',
    'DEFAULT_MEDIA_SERVER_TYPE',
    'MediaServerBackend',
    'MediaServerCapabilities',
    'SubscriptionScheduler',
    'UserDirectory',
    'create_media_server',
    'resolve_media_server_type',
    'tipos_suportados',
]
