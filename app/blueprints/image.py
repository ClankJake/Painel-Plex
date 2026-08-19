# app/blueprints/image.py

import logging
import requests
import os
import hashlib
import base64
import binascii
import tempfile
import socket
import ipaddress
from pathlib import Path
from typing import Tuple, Optional

from flask import Blueprint, request, abort, send_from_directory, redirect
from requests.adapters import HTTPAdapter
from urllib.parse import urlparse, parse_qs, urljoin

# Importa os gestores para aceder às configurações e tokens de forma segura
from ..extensions import plex_manager, tautulli_manager, limiter

logger = logging.getLogger(__name__)
image_bp = Blueprint('image', __name__)

# --- CONFIGURAÇÃO DO CACHE EM DISCO ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# Mantido em /config/cache/images para permitir controlo fácil por parte do utilizador
IMAGE_CACHE_DIR = BASE_DIR / 'config' / 'cache' / 'images'

def ensure_cache_dir():
    """Cria o diretório de cache de forma preguiçosa (lazy) para evitar erros de permissão no arranque (Docker)."""
    try:
        IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Erro ao criar diretório de cache de imagens {IMAGE_CACHE_DIR}: {e}")

# Sessão persistente para acelerar múltiplos downloads da mesma fonte
session = requests.Session()
session.headers.update({'Accept': 'image/webp,image/png,image/jpeg,image/*,*/*'})

# =====================================================================
# BLOCO DE SEGURANÇA: PREVENÇÃO CONTRA SSRF (Server-Side Request Forgery)
# =====================================================================

def is_private_ip(ip_str: str) -> bool:
    """Verifica se o IP resolvido pertence a uma rede privada, loopback ou reservada."""
    try:
        ip = ipaddress.ip_address(ip_str)
        # 'is_unspecified' (0.0.0.0 / ::) é tratado à parte porque não está coberto
        # por 'is_private' em todas as versões do módulo ipaddress.
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast \
           or ip.is_reserved or ip.is_unspecified:
            return True
        # Endereços IPv6 que mapeiam um IPv4 (::ffff:127.0.0.1) têm de ser
        # reavaliados na sua forma IPv4, senão passariam despercebidos.
        mapped = getattr(ip, 'ipv4_mapped', None)
        if mapped is not None:
            return is_private_ip(str(mapped))
        return False
    except ValueError:
        return True # Falha de forma segura se o IP não for válido


def resolve_and_validate_host(hostname: str, port: int):
    """
    Resolve um hostname e valida TODOS os endereços devolvidos (IPv4 e IPv6).

    Usa 'getaddrinfo' em vez de 'gethostbyname' porque este último devolve apenas
    UM endereço IPv4: um domínio com vários registos A (round-robin) ou com um
    registo AAAA poderia passar na validação e, no pedido real, resolver para um
    endereço interno diferente.

    Devolve o primeiro IP validado, que será usado para fixar a ligação (ver
    _fetch_url_safely) — impedindo o ataque de "DNS rebinding", em que o atacante
    devolve um IP público na validação e um IP interno no pedido seguinte.
    """
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f"Não foi possível resolver o hostname: {hostname}")

    if not infos:
        raise ValueError(f"O hostname não devolveu endereços: {hostname}")

    resolved_ips = []
    for info in infos:
        ip = info[4][0]
        if is_private_ip(ip):
            raise ValueError(f"O hostname ({hostname}) resolve para o IP privado/local {ip}, bloqueado por segurança.")
        resolved_ips.append(ip)

    return resolved_ips[0]


def validate_external_url(url_str: str) -> str:
    """
    Valida URLs externas para evitar que o servidor seja usado como proxy
    para atacar a própria rede interna (Vulnerabilidade SSRF).
    """
    parsed = urlparse(url_str)

    # 1. Apenas aceita HTTP e HTTPS
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f"Esquema de URL não suportado: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Hostname ausente no URL")

    # 2. Resolve o domínio e valida TODOS os IPs devolvidos
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    resolve_and_validate_host(hostname, port)

    return url_str


class _PinnedIPAdapter(HTTPAdapter):
    """
    Adaptador HTTP que força a ligação a um IP JÁ VALIDADO, em vez de deixar o
    'requests' resolver o DNS outra vez.

    É esta a peça que fecha a janela de "DNS rebinding" (TOCTOU): sem ela, entre a
    validação do endereço e o pedido real existe uma segunda resolução de DNS que o
    atacante pode manipular (devolvendo um IP público na primeira e 127.0.0.1 na
    segunda, com um TTL muito baixo).

    O nome original do host continua a ser enviado no cabeçalho 'Host' e usado no
    SNI/validação do certificado TLS, por isso os certificados continuam a ser
    verificados corretamente.
    """
    def __init__(self, pinned_ip, *args, **kwargs):
        self.pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        parsed = urlparse(request.url)
        hostname = parsed.hostname
        if hostname and self.pinned_ip:
            # Preserva o Host original (necessário para vhosts e para o SNI)
            request.headers.setdefault('Host', parsed.netloc)
            self.poolmanager.connection_pool_kw['server_hostname'] = hostname
            self.poolmanager.connection_pool_kw['assert_hostname'] = hostname
        return super().send(request, **kwargs)


def _fetch_url_safely(url: str, params: dict, max_redirects: int = 3):
    """
    Descarrega uma URL externa com proteção contra SSRF em todas as fases.

    Porque é que não basta validar a URL inicial: o 'requests' segue redirecionamentos
    automaticamente, por isso um atacante só precisa de alojar uma URL pública que
    responda com "302 Location: http://169.254.169.254/" (serviço de metadados da
    cloud) para contornar por completo a validação inicial. Aqui os redirecionamentos
    são desativados e seguidos manualmente, revalidando cada salto.
    """
    current_url = url
    for _hop in range(max_redirects + 1):
        parsed = urlparse(current_url)
        if parsed.scheme not in ('http', 'https'):
            raise ValueError(f"Esquema de URL não suportado no redirecionamento: {parsed.scheme}")

        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Hostname ausente no URL de destino")

        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        pinned_ip = resolve_and_validate_host(hostname, port)

        # Sessão dedicada com o IP fixado — não reutilizamos a sessão global para
        # que o "pin" de um pedido nunca contamine outro.
        with requests.Session() as pinned_session:
            pinned_session.headers.update(session.headers)
            pinned_session.mount('http://', _PinnedIPAdapter(pinned_ip))
            pinned_session.mount('https://', _PinnedIPAdapter(pinned_ip))

            response = pinned_session.get(
                current_url, params=params, stream=True, timeout=10,
                allow_redirects=False  # 🔒 seguimos manualmente, revalidando cada salto
            )

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get('Location')
                response.close()
                if not location:
                    raise ValueError("Redirecionamento sem cabeçalho 'Location'.")
                # Resolve destinos relativos em relação à URL atual
                current_url = urljoin(current_url, location)
                params = {}  # os parâmetros originais não devem seguir para outro destino
                continue

            return response

    raise ValueError("Demasiados redirecionamentos ao obter a imagem.")

# =====================================================================

def get_cache_filepath(unique_identifier: str) -> Path:
    """Gera um caminho de ficheiro seguro e único para uma URL de imagem."""
    url_hash = hashlib.sha256(unique_identifier.encode('utf-8')).hexdigest()
    return IMAGE_CACHE_DIR / url_hash

def build_final_url(source: str, image_path: str) -> Tuple[Optional[str], dict]:
    """Isola a lógica de construção de URLs, com injeção de tokens e proteção SSRF."""
    final_url = None
    params = {}
    
    if source == 'plex':
        if plex_manager and plex_manager.plex:
            final_url = plex_manager.plex.url(image_path, includeToken=False)
            params['X-Plex-Token'] = plex_manager.plex._token
            
    elif source == 'plex_account':
         if plex_manager and plex_manager.account:
            # FIX DE SEGURANÇA: Obriga as imagens a serem relativas a plex.tv
            if image_path.startswith('http://') or image_path.startswith('https://'):
                parsed = urlparse(image_path)
                if 'plex.tv' in parsed.netloc:
                    image_path = parsed.path + ("?" + parsed.query if parsed.query else "")
                else:
                    raise ValueError("URL absoluto inválido para o prefixo plex_account.")
            
            if not image_path.startswith('/'):
                image_path = '/' + image_path
                
            final_url = f"https://plex.tv{image_path}"
            params['X-Plex-Token'] = plex_manager.account._token
            
    elif source == 'url':
        # FIX DE SEGURANÇA: Valida rigorosamente as URLs de avatares de terceiros
        final_url = validate_external_url(image_path)
        
    elif source == 'tautulli':
        # Protege contra Tautulli não configurado/carregado no boot
        if tautulli_manager and getattr(tautulli_manager, 'api_client', None) and tautulli_manager.api_client.is_configured:
            parsed_path = urlparse(image_path)
            query_params = parse_qs(parsed_path.query)
            final_url = f"{tautulli_manager.api_client.base_url}/api/v2"
            params['apikey'] = tautulli_manager.api_client.api_key
            params['cmd'] = 'pms_image_proxy'
            for key, values in query_params.items():
                params[key] = values[0]
            
    return final_url, params

@image_bp.route('/')
@limiter.exempt
def proxy_image():
    """
    Atua como um proxy seguro para imagens do Plex e de Avatares Externos.
    Protegido contra SSRF e contra picos de RAM através de atomic writes em disco.
    """
    b64_payload = request.args.get('source')

    if not b64_payload:
        abort(400, "Parâmetro 'source' é obrigatório.")

    try:
        # Previne erros de Base64 calculando o padding necessário automaticamente
        padding = '=' * (-len(b64_payload) % 4)
        decoded_payload = base64.urlsafe_b64decode(b64_payload + padding).decode('utf-8')
        
        if ':' not in decoded_payload:
            abort(400, "Formato do payload inválido.")
            
        source, image_path = decoded_payload.split(':', 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        abort(400, "Parâmetro 'source' inválido ou mal formatado.")

    try:
        final_url, params = build_final_url(source, image_path)
    except ValueError as e:
        logger.warning(f"Tentativa de Proxy bloqueada por segurança (SSRF): {e}")
        return redirect("https://placehold.co/150x225/1F2937/E5E7EB?text=Bloqueado")
        
    if not final_url:
        abort(404, "Fonte da imagem não encontrada ou não configurada.")
        
    # Garante que a pasta existe apenas no momento de uso (resolve o Crash do Docker)
    ensure_cache_dir()
    
    cache_filepath = get_cache_filepath(decoded_payload)
    mime_filepath = cache_filepath.with_suffix('.mime')

    # 1. Tenta servir a imagem a partir do cache (Extremamente rápido)
    if cache_filepath.exists():
        # Tenta ler o mimetype correto (suporte a PNG/WebP além do JPEG)
        mimetype = 'image/jpeg'
        if mime_filepath.exists():
            try:
                mimetype = mime_filepath.read_text().strip()
            except OSError:
                pass
                
        return send_from_directory(
            str(cache_filepath.parent),
            cache_filepath.name,
            mimetype=mimetype,
            max_age=86400 # Cache no browser do utilizador por 24 horas
        )

    # 2. Se não estiver em cache, descarrega com STREAMING e faz ATOMIC WRITE
    temp_path = None
    try:
        # Timeout rigoroso para evitar bloqueio de threads.
        # 🔒 _fetch_url_safely revalida o destino em cada redirecionamento e fixa a
        # ligação ao IP já verificado (proteção completa contra SSRF).
        response = _fetch_url_safely(final_url, params)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', 'image/jpeg')

        # Cria ficheiro temporário para evitar corrupção por acessos simultâneos
        fd, temp_path = tempfile.mkstemp(dir=str(IMAGE_CACHE_DIR))
        with os.fdopen(fd, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): # Guarda em pedaços de 8KB
                if chunk:
                    f.write(chunk)
                    
        # Substituição atómica via os.replace (nativo do Linux/Docker)
        os.replace(temp_path, str(cache_filepath))
        
        # Corrige permissões restritivas (0600) do mkstemp para que o servidor web possa ler (0644)
        try:
            os.chmod(str(cache_filepath), 0o644)
        except OSError:
            pass
            
        # Guarda o mime_type de forma segura
        try:
            mime_filepath.write_text(content_type)
        except OSError:
            pass
        
        return send_from_directory(
            str(cache_filepath.parent),
            cache_filepath.name,
            mimetype=content_type,
            max_age=86400
        )

    except ValueError as e:
        # Bloqueio de segurança durante o download (ex: um redirecionamento a
        # apontar para a rede interna). Não é um erro do servidor — é a proteção
        # a funcionar — por isso respondemos com a imagem de "bloqueado".
        logger.warning(f"Download de imagem bloqueado por segurança (SSRF): {e}")
        return redirect("https://placehold.co/150x225/1F2937/E5E7EB?text=Bloqueado")
    except requests.exceptions.RequestException as e:
        logger.debug(f"Erro ao descarregar a imagem proxy '{final_url}': {e}")
        return redirect("https://placehold.co/150x225/1F2937/E5E7EB?text=Erro+Capa")
    finally:
        # Previne File Leaks apagando o temporário caso o download falhe a meio
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
