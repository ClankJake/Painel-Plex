# app/blueprints/image.py

import logging
import requests
import os
import hashlib
import base64
import binascii
import tempfile
import shutil
from pathlib import Path
from typing import Tuple, Optional

from flask import Blueprint, request, Response, abort, send_from_directory, redirect
from urllib.parse import urlparse

# Importa os gestores para aceder às configurações e tokens de forma segura
from ..extensions import plex_manager, tautulli_manager, limiter

logger = logging.getLogger(__name__)
image_bp = Blueprint('image', __name__)

# --- CONFIGURAÇÃO DO CACHE EM DISCO (Usando Pathlib) ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent
IMAGE_CACHE_DIR = BASE_DIR / 'config' / 'cache' / 'images'
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Sessão persistente para acelerar múltiplos downloads da mesma fonte
session = requests.Session()
session.headers.update({'Accept': 'image/webp,image/png,image/jpeg,image/*,*/*'})

def get_cache_filepath(unique_identifier: str) -> Path:
    """Gera um caminho de ficheiro seguro e único para uma URL de imagem."""
    url_hash = hashlib.sha256(unique_identifier.encode('utf-8')).hexdigest()
    return IMAGE_CACHE_DIR / url_hash

def build_final_url(source: str, image_path: str) -> Tuple[Optional[str], dict]:
    """Isola a lógica de construção de URLs e injeção de tokens."""
    final_url = None
    params = {}
    
    if source == 'plex':
        if plex_manager and plex_manager.plex:
            final_url = plex_manager.plex.url(image_path, includeToken=False)
            params['X-Plex-Token'] = plex_manager.plex._token
    elif source == 'plex_account':
         if plex_manager and plex_manager.account:
            final_url = f"https://plex.tv{image_path}"
            params['X-Plex-Token'] = plex_manager.account._token
    elif source == 'tautulli':
        if tautulli_manager and tautulli_manager.api_client.is_configured:
            final_url = f"{tautulli_manager.api_client.base_url}{image_path}"
            params['apikey'] = tautulli_manager.api_client.api_key
            
    return final_url, params

@image_bp.route('/')
@limiter.exempt
def proxy_image():
    """
    Atua como um proxy seguro para imagens do Plex e Tautulli.
    Protegido contra picos de RAM através de download por chunks (streaming).
    """
    b64_payload = request.args.get('source')

    if not b64_payload:
        abort(400, "Parâmetro 'source' é obrigatório.")

    try:
        decoded_payload = base64.urlsafe_b64decode(b64_payload.encode('utf-8')).decode('utf-8')
        source, image_path = decoded_payload.split(':', 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        abort(400, "Parâmetro 'source' inválido ou mal formatado.")

    final_url, params = build_final_url(source, image_path)
    
    if not final_url:
        abort(404, "Fonte da imagem não encontrada ou não configurada.")
        
    cache_filepath = get_cache_filepath(decoded_payload)

    # 1. Tenta servir a imagem a partir do cache (Extremamente rápido)
    if cache_filepath.exists():
        return send_from_directory(
            str(cache_filepath.parent),
            cache_filepath.name,
            mimetype='image/jpeg', # Os browsers fazem 'sniff' do ficheiro real automaticamente
            max_age=86400 # Cache no browser do utilizador por 24 horas
        )

    # 2. Se não estiver em cache, descarrega em modo STREAMING e faz ATOMIC WRITE
    try:
        # stream=True diz ao Requests para NÃO carregar a imagem para a RAM
        response = session.get(final_url, params=params, stream=True, timeout=15)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', 'image/jpeg')

        # Cria um ficheiro temporário para evitar que múltiplos pedidos 
        # corrompam a mesma imagem lendo/escrevendo ao mesmo tempo
        fd, temp_path = tempfile.mkstemp(dir=str(IMAGE_CACHE_DIR))
        with os.fdopen(fd, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): # Guarda em pedaços de 8KB
                if chunk:
                    f.write(chunk)
                    
        # Substituição atómica (Mover o ficheiro temporário para o nome final)
        shutil.move(temp_path, str(cache_filepath))
        
        # Serve o ficheiro recém-descarregado diretamente do disco
        return send_from_directory(
            str(cache_filepath.parent),
            cache_filepath.name,
            mimetype=content_type,
            max_age=86400
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro proxy imagem '{final_url}': {e}")
        # OTIMIZAÇÃO MASSIVA: Em vez de o seu servidor gastar recursos a descarregar
        # um erro, diz ao navegador do utilizador para ir ele buscar o erro.
        return redirect("https://placehold.co/150x225/1F2937/E5E7EB?text=Erro+Capa")
