# app/blueprints/image_proxy.py

import logging
import requests
import os
import hashlib
from flask import Blueprint, request, Response, abort, current_app, send_from_directory
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from ..config import CONFIG_DIR

logger = logging.getLogger(__name__)
image_proxy_bp = Blueprint('image_proxy', __name__)

# --- CONFIGURAÇÃO DO CACHE EM DISCO ---
IMAGE_CACHE_DIR = os.path.join(CONFIG_DIR, 'cache', 'images')
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({'Accept': 'image/webp,image/png,image/jpeg,image/*,*/*'})

def get_cache_filepath(url):
    """Gera um nome de ficheiro seguro e único para uma URL de imagem."""
    if not url:
        return None
    # Usa um hash SHA256 da URL para criar um nome de ficheiro único e de comprimento fixo
    url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
    return os.path.join(IMAGE_CACHE_DIR, url_hash)

@image_proxy_bp.route('/')
def proxy_image():
    """
    Atua como um proxy para imagens do Plex, utilizando um cache em disco para
    performance e ocultando o token de acesso.
    """
    image_url = request.args.get('url')
    if not image_url:
        abort(400, "URL da imagem não fornecida.")

    cache_filepath = get_cache_filepath(image_url)

    # 1. Tenta servir a imagem a partir do cache em disco
    if os.path.exists(cache_filepath):
        logger.debug(f"A servir a imagem '{image_url}' a partir do cache em disco.")
        return send_from_directory(
            os.path.dirname(cache_filepath),
            os.path.basename(cache_filepath),
            mimetype='image/jpeg', # A maioria das imagens será jpeg, mas o browser lida com isso
            max_age=86400 # Cache no browser por 24 horas
        )

    # 2. Se não estiver em cache, busca a imagem do Plex/Tautulli
    try:
        # Remove qualquer token existente para garantir que estamos a usar o do servidor
        parsed_url = urlparse(image_url)
        query_params = parse_qs(parsed_url.query)
        query_params.pop('X-Plex-Token', None)
        
        # Adiciona o token do servidor Plex a partir da configuração
        from ..extensions import plex_manager
        if plex_manager and plex_manager.plex:
             query_params['X-Plex-Token'] = [plex_manager.plex._token]
        
        # Reconstrói a URL sem o token original e com o novo
        new_query_string = urlencode(query_params, doseq=True)
        final_url = urlunparse(parsed_url._replace(query=new_query_string))

        # Faz a requisição para a imagem
        response = session.get(final_url, stream=True, timeout=15)
        response.raise_for_status()
        
        image_content = response.content
        content_type = response.headers.get('Content-Type', 'image/jpeg')

        # 3. Guarda a imagem no cache em disco
        with open(cache_filepath, 'wb') as f:
            f.write(image_content)
        logger.info(f"Imagem '{image_url}' obtida e armazenada na cache em disco.")
        
        # 4. Retorna a imagem para o cliente com cabeçalhos de cache
        return Response(image_content, mimetype=content_type, headers={
            'Cache-Control': 'public, max-age=86400' # Cache no browser por 24 horas
        })

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao obter a imagem via proxy '{image_url}': {e}")
        # Retorna uma imagem de placeholder em caso de erro
        placeholder_response = requests.get("https://placehold.co/150x225/1F2937/E5E7EB?text=Erro")
        return Response(placeholder_response.content, mimetype="image/png")

