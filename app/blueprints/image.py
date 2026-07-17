# app/blueprints/image.py

import os
import base64
import hashlib
import time
import socket
import logging
import urllib.parse
from ipaddress import ip_address

from flask import Blueprint, request, send_file, abort
import requests

logger = logging.getLogger(__name__)
image_bp = Blueprint('image', __name__)

# Configuração da diretoria de cache de imagens
IMAGE_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'image_cache')
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

def is_private_ip(ip_str):
    """
    Verifica se um IP é privado, local ou de loopback.
    Essencial para prevenir ataques SSRF (Server-Side Request Forgery).
    """
    try:
        ip = ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
    except ValueError:
        # Se não conseguir fazer parse do IP, assume que é perigoso
        return True 

def validate_external_url(url):
    """
    Garante que a URL solicitada aponta para a internet pública 
    e não para serviços internos da rede (SSRF Protection).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise ValueError("Esquema inválido. Apenas HTTP e HTTPS são permitidos.")
        
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Hostname inválido ou ausente.")
            
        # Resolve o domínio para IP e verifica se é interno
        ip_addr = socket.gethostbyname(hostname)
        if is_private_ip(ip_addr):
            raise ValueError(f"Acesso negado a IPs internos ou privados ({ip_addr}).")
            
        return True
    except Exception as e:
        logger.error(f"Bloqueio de Segurança SSRF ativado para URL '{url}': {e}")
        abort(403) # Devolve "403 Forbidden" em caso de tentativa maliciosa

@image_bp.route('/')
def proxy_image():
    """
    Proxy que serve, descarrega e faz cache de imagens do Plex, Tautulli 
    ou de URLs externas seguras.
    """
    source_b64 = request.args.get('source')
    if not source_b64:
        return abort(400)
        
    try:
        # O input base64 vem como urlsafe sem padding, logo adicionamos '==' para compensar
        decoded_source = base64.urlsafe_b64decode(source_b64 + '==').decode('utf-8')
        
        # O formato do payload esperado é "tipo:url"
        if ':' not in decoded_source:
             return abort(400)
             
        source_type, final_url = decoded_source.split(':', 1)
        
        if source_type not in ('url', 'plex', 'plex_account', 'tautulli'):
            return abort(400)

        # Gera uma chave única MD5 para o ficheiro no disco
        cache_key = hashlib.md5(decoded_source.encode('utf-8')).hexdigest()
        cache_path = os.path.join(IMAGE_CACHE_DIR, cache_key)
        
        # Se a imagem já existe no cache local, serve-a imediatamente e poupa a rede
        if os.path.exists(cache_path):
            return send_file(cache_path)

        session = requests.Session()
        params = {}
        
        from app.config import load_or_create_config
        config = load_or_create_config()
        
        # Adiciona autenticações dependendo de onde vem a imagem
        if source_type == 'plex':
            final_url = f"{config.get('PLEX_URL', '').rstrip('/')}{final_url}"
            params['X-Plex-Token'] = config.get('PLEX_TOKEN', '')
        elif source_type == 'tautulli':
            final_url = f"{config.get('TAUTULLI_URL', '').rstrip('/')}{final_url}"
            params['apikey'] = config.get('TAUTULLI_API_KEY', '')
            
        try:
            # 🛡️ GARANTIA DE SEGURANÇA (FIX SSRF GitHub)
            # A validação é feita *exatamente* antes do session.get()
            if source_type == 'url':
                validate_external_url(final_url)
                
            # Timeout rigoroso e stream=True para não prender threads
            response = session.get(final_url, params=params, stream=True, timeout=10)
            response.raise_for_status()
            
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            
            # Atomic Write: Escreve num .tmp e só depois de concluído renomeia.
            # Isto previne que um utilizador apanhe uma imagem cortada a meio
            temp_cache_path = f"{cache_path}.tmp.{time.time()}"
            with open(temp_cache_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            os.replace(temp_cache_path, cache_path)
            
            return send_file(cache_path, mimetype=content_type)
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de rede ao buscar imagem na origem: {e}")
            return abort(502) # Bad Gateway
            
    except Exception as e:
        logger.error(f"Erro interno no processamento do proxy de imagem: {e}")
        return abort(500)
