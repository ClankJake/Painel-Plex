# app/services/link_shortener.py

import secrets
import logging
from flask import url_for
from ..extensions import db
from ..models import ShortLink

logger = logging.getLogger(__name__)

class LinkShortener:
    """Serviço para criar e resolver links curtos."""

    def _generate_short_code(self, length=7):
        """Gera um código curto e único."""
        while True:
            code = secrets.token_urlsafe(length)[:length]
            if not ShortLink.query.filter_by(short_code=code).first():
                return code

    def create_short_link(self, original_url):
        """
        Cria um novo link curto ou retorna um existente se a URL já foi encurtada.
        """
        # Opcional: Para evitar criar múltiplos links curtos para a mesma URL,
        # pode-se verificar se já existe um.
        # existing = ShortLink.query.filter_by(original_url=original_url).first()
        # if existing:
        #     return url_for('redirect.redirect_to_url', code=existing.short_code, _external=True)

        code = self._generate_short_code()
        new_link = ShortLink(short_code=code, original_url=original_url)
        db.session.add(new_link)
        db.session.commit()
        logger.info(f"Link curto '{code}' criado para a URL: {original_url}")
        return url_for('redirect.redirect_to_url', code=code, _external=True)

    def get_original_url(self, short_code):
        """Obtém a URL original a partir de um código curto."""
        link = ShortLink.query.filter_by(short_code=short_code).first()
        return link.original_url if link else None
