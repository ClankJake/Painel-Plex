# app/blueprints/image.py

import logging
import requests
import os
import hashlib
import base64
import binascii
from flask import Blueprint, request, Response, abort, current_app, send_from_directory
from urllib.parse import urlparse
from PIL import Image
import io

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
    """Gera um nome de ficheiro seguro e único para uma imagem WebP em cache."""
    if not unique_identifier:
        return None
    # Usa um hash SHA256 para criar um nome de ficheiro único e de comprimento fixo
    url_hash = hashlib.sha256(unique_identifier.encode('utf-8')).hexdigest()
    return os.path.join(IMAGE_CACHE_DIR, f"{url_hash}.webp")

@image_bp.route('/')
def proxy_image():
    """
    Atua como um proxy seguro para imagens do Plex e Tautulli, com cache e otimização para WebP.
    Se o navegador não suportar WebP, a imagem original é servida sem otimização.
    """
    b64_payload = request.args.get('source')

    if not b64_payload:
        abort(400, "Parâmetro 'source' é obrigatório.")

    try:
        decoded_payload = base64.urlsafe_b64decode(b64_payload.encode('utf-8')).decode('utf-8')
        source, image_path = decoded_payload.split(':', 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        abort(400, "Parâmetro 'source' inválido ou mal formatado.")

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
    
    if not final_url:
        abort(404, "Fonte da imagem não encontrada ou não configurada.")
        
    unique_identifier = decoded_payload
    
    # Verifica se o browser do cliente aceita o formato WebP
    accepts_webp = 'image/webp' in request.headers.get('Accept', '')

    # Se o navegador não aceita WebP, simplesmente servimos a imagem original sem cache.
    if not accepts_webp:
        logger.debug(f"Navegador não suporta WebP. A servir a imagem original para '{unique_identifier}'.")
        try:
            response = session.get(final_url, params=params, stream=True, timeout=15)
            response.raise_for_status()
            return Response(response.content, mimetype=response.headers.get('Content-Type', 'image/jpeg'))
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro ao obter a imagem original (fallback) via proxy '{final_url}': {e}")
            placeholder_response = requests.get("https://placehold.co/150x225/1F2937/E5E7EB?text=Erro")
            return Response(placeholder_response.content, mimetype="image/png")

    # Se o navegador aceita WebP, usamos o sistema de cache.
    cache_filepath = get_cache_filepath(unique_identifier)

    # 1. Tenta servir a imagem a partir do cache WebP
    if os.path.exists(cache_filepath):
        logger.debug(f"A servir a imagem '{unique_identifier}' a partir do cache (WebP).")
        return send_from_directory(
            IMAGE_CACHE_DIR,
            os.path.basename(cache_filepath),
            mimetype='image/webp',
            max_age=86400 # Cache no browser por 24 horas
        )

    # 2. Se não estiver em cache, busca, converte e salva
    try:
        response = session.get(final_url, params=params, stream=True, timeout=15)
        response.raise_for_status()
        
        image_content = response.content
        
        # Abre a imagem com a Pillow para conversão
        img = Image.open(io.BytesIO(image_content))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # 3. Salva a imagem em cache no formato WebP
        img.save(cache_filepath, 'webp', quality=80)
        
        logger.debug(f"Imagem '{unique_identifier}' obtida, convertida para WebP e armazenada em cache.")
        
        # 4. Retorna a imagem recém-convertida para o cliente
        with open(cache_filepath, 'rb') as f:
            content_to_serve = f.read()
        
        return Response(content_to_serve, mimetype='image/webp', headers={
            'Cache-Control': 'public, max-age=86400'
        })

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao obter a imagem para conversão via proxy '{final_url}': {e}")
        placeholder_response = requests.get("https://placehold.co/150x225/1F2937/E5E7EB?text=Erro")
        return Response(placeholder_response.content, mimetype="image/png")
    except Exception as e:
        logger.error(f"Erro ao processar e converter a imagem '{unique_identifier}' para WebP: {e}", exc_info=True)
        # Se a conversão falhar, serve a imagem original para não quebrar a UI
        return Response(image_content, mimetype=response.headers.get('Content-Type', 'image/jpeg'))


