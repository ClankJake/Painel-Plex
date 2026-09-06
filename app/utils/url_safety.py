# app/utils/url_safety.py
"""
Comparação de domínios para decisões de segurança.

Um teste como ``'plex.tv' in url`` parece inofensivo, mas aceita
``https://plex.tv.atacante.com/`` e ``https://naoeplex.tv/``: a substring pode
aparecer em qualquer posição do texto. As funções deste módulo comparam sempre
pela fronteira do rótulo DNS, que é a única comparação que corresponde ao que
queremos dizer com "este URL pertence ao domínio X".
"""

from typing import Iterable, Optional
from urllib.parse import urlparse


def normalize_host(hostname: Optional[str]) -> str:
    """Normaliza um hostname para comparação (minúsculas, sem o ponto final da raiz)."""
    return (hostname or '').strip().lower().rstrip('.').lstrip('.')


def host_matches(hostname: Optional[str], domain: str) -> bool:
    """Devolve True se `hostname` for exatamente `domain` ou um subdomínio seu."""
    host = normalize_host(hostname)
    dominio = normalize_host(domain)
    if not host or not dominio:
        return False
    return host == dominio or host.endswith('.' + dominio)


def match_domain(hostname: Optional[str], domains: Iterable[str]) -> Optional[str]:
    """Devolve o primeiro domínio da lista que cobre `hostname`, ou None."""
    for domain in domains:
        if host_matches(hostname, domain):
            return normalize_host(domain)
    return None


def is_plex_tv_host(hostname: Optional[str]) -> bool:
    """True se o hostname pertencer ao plex.tv (inclui subdomínios)."""
    return host_matches(hostname, 'plex.tv')


def url_host_matches(url_str: Optional[str], domain: str) -> bool:
    """Versão de `host_matches` que recebe um URL completo em vez do hostname."""
    if not url_str:
        return False
    try:
        return host_matches(urlparse(url_str).hostname, domain)
    except ValueError:
        # URLs malformados (por exemplo, porta inválida) nunca correspondem.
        return False
