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
from urllib.parse import urlparse, parse_qs

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
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
    except ValueError:
        return True # Falha de forma segura se o IP não for válido

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

    # 2. Resolve o domínio para garantir que não aponta para a rede interna
    try:
        ip_address = socket.gethostbyname(hostname)
        if is_private_ip(ip_address):
            raise ValueError(f"O Hostname ({hostname}) resolve para um IP privado/local bloqueado por segurança.")
    except socket.gaierror:
        raise ValueError(f"Não foi possível resolver o hostname: {hostname}")

    return url_str

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
        # Timeout rigoroso para evitar bloqueio de threads
        response = session.get(final_url, params=params, stream=True, timeout=10)
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
