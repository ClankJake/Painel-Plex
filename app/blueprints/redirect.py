# app/blueprints/redirect.py

import logging
from flask import Blueprint, redirect, abort
from ..extensions import link_shortener

redirect_bp = Blueprint('redirect', __name__)
logger = logging.getLogger(__name__)

@redirect_bp.route('/s/<code>')
def redirect_to_url(code):
    """
    Rota de redirecionamento para links curtos.
    """
    original_url = link_shortener.get_original_url(code)
    if original_url:
        logger.info(f"Redirecionando código '{code}' para '{original_url}'")
        return redirect(original_url)
    else:
        logger.warning(f"Código de link curto não encontrado: '{code}'")
        abort(404)
