# app/blueprints/image.py

import logging
import requests
import os
import hashlib
import base64
import binascii
from flask import Blueprint, request, Response, abort, current_app, send_from_directory
from urllib.parse import urlparse

# Importa os gestores para aceder às configurações e tokens de forma segura
from ..extensions import plex_manager, tautulli_manager

logger = logging.getLogger(__name__)
image_bp = Blueprint('image', __name__)

# --- CONFIGURAÇÃO DO CACHE EM DISCO ---
IMAGE_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'config', 'cache', 'images')
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({'Accept': 'image/webp,image/png,image/jpeg,image/*,*/*'})

def get_cache_filepath(unique_identifier):
    """Gera um nome de ficheiro seguro e único para uma URL de imagem."""
    if not unique_identifier:
        return None
    # Usa um hash SHA256 para criar um nome de ficheiro único e de comprimento fixo
    url_hash = hashlib.sha256(unique_identifier.encode('utf-8')).hexdigest()
    return os.path.join(IMAGE_CACHE_DIR, url_hash)

@image_bp.route('/')
def proxy_image():
    """
    Atua como um proxy seguro para imagens do Plex e Tautulli.
    O parâmetro 'source' contém a fonte e o caminho da imagem, codificados em Base64.
    """
    b64_payload = request.args.get('source')

    if not b64_payload:
        abort(400, "Parâmetro 'source' é obrigatório.")

    try:
        # Descodifica o payload a partir de Base64
        decoded_payload = base64.urlsafe_b64decode(b64_payload.encode('utf-8')).decode('utf-8')
        # Separa a fonte (ex: 'plex') do caminho da imagem
        source, image_path = decoded_payload.split(':', 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        abort(400, "Parâmetro 'source' inválido ou mal formatado.")

    final_url = None
    params = {}
    
    # Constrói o URL final de forma segura no servidor
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
    
    if not final_url:
        abort(404, "Fonte da imagem não encontrada ou não configurada.")
        
    # A chave do cache é baseada no identificador único, sem segredos
    unique_identifier = decoded_payload
    cache_filepath = get_cache_filepath(unique_identifier)

    # 1. Tenta servir a imagem a partir do cache em disco
    if os.path.exists(cache_filepath):
        logger.debug(f"A servir a imagem '{unique_identifier}' a partir do cache.")
        return send_from_directory(
            os.path.dirname(cache_filepath),
            os.path.basename(cache_filepath),
            mimetype='image/jpeg',
            max_age=86400 # Cache no browser por 24 horas
        )

    # 2. Se não estiver em cache, busca a imagem da fonte original
    try:
        response = session.get(final_url, params=params, stream=True, timeout=15)
        response.raise_for_status()
        
        image_content = response.content
        content_type = response.headers.get('Content-Type', 'image/jpeg')

        # 3. Guarda a imagem no cache em disco
        with open(cache_filepath, 'wb') as f:
            f.write(image_content)
        logger.info(f"Imagem '{unique_identifier}' obtida e armazenada na cache.")
        
        # 4. Retorna a imagem para o cliente
        return Response(image_content, mimetype=content_type, headers={
            'Cache-Control': 'public, max-age=86400'
        })

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao obter a imagem via proxy '{final_url}': {e}")
        placeholder_response = requests.get("https://placehold.co/150x225/1F2937/E5E7EB?text=Erro")
        return Response(placeholder_response.content, mimetype="image/png")
