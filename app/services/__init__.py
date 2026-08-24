# app/services/__init__.py

"""
Este ficheiro transforma a pasta 'services' num pacote Python
e expõe as classes dos gestores para que possam ser facilmente importadas
noutras partes da aplicação.
"""

from .data_manager import DataManager
from .tautulli_manager import TautulliManager
from .plex_manager import PlexManager
from .notifier_manager import NotifierManager
from .efi_manager import EfiManager
from .mercado_pago_manager import MercadoPagoManager
from .overseerr_manager import OverseerrManager
from .link_shortener import LinkShortener
from .gates2b_manager import Gates2bManager
from .stream_manager import StreamManager
from .pricing_manager import PricingManager
from .backup_manager import BackupManager
from .referral_manager import ReferralManager

__all__ = [
    'DataManager',
    'TautulliManager',
    'PlexManager',
    'NotifierManager',
    'EfiManager',
    'MercadoPagoManager',
    'OverseerrManager',
    'StreamManager',
    'LinkShortener',
    'Gates2bManager',
    'PricingManager',
    'BackupManager',
    'ReferralManager',
]
