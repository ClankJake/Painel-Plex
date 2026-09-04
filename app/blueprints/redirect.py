# app/blueprints/redirect.py

import logging
from flask import Blueprint, redirect, render_template, url_for
from ..extensions import link_shortener
from ..utils.log_sanitizer import mask_code, mask_link

redirect_bp = Blueprint('redirect', __name__)
logger = logging.getLogger(__name__)

@redirect_bp.route('/s/<code>')
def redirect_to_url(code):
    """
    Rota de redirecionamento para links curtos.
    """
    original_url = link_shortener.get_original_url(code)
    if original_url:
        logger.info(f"Redirecionando código '{mask_code(code)}' para '{mask_link(original_url)}'")
        return redirect(original_url)
    else:
        logger.warning(f"Código de link curto não encontrado: '{mask_code(code)}'")
        # Em vez de abort(404), renderizamos uma página amigável que redireciona
        return render_template('link_expired.html', login_url=url_for('auth.login'))