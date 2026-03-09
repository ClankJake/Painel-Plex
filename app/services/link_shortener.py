# app/services/link_shortener.py

import secrets
import logging
from flask import url_for
from ..extensions import db
from ..models import ShortLink
from ..config import load_or_create_config

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
        Cria um novo link curto para a URL especificada e apaga quaisquer 
        links curtos antigos que apontassem para o mesmo destino (Inutilização Segura).
        """
        try:
            # 🛡️ AUTO-LIMPEZA: Procura e apaga links antigos para esta mesma URL final
            old_links = ShortLink.query.filter_by(original_url=original_url).all()
            for old_link in old_links:
                db.session.delete(old_link)
                logger.info(f"Link curto antigo '{old_link.short_code}' apagado para evitar duplicações.")
            
            # Se encontrou links antigos, efetua o "commit" da eliminação
            if old_links:
                db.session.commit()
                
        except Exception as e:
            logger.warning(f"Erro ao tentar apagar links curtos antigos: {e}")
            db.session.rollback()

        # Cria o novo link curto "fresco"
        code = self._generate_short_code()
        new_link = ShortLink(short_code=code, original_url=original_url)
        db.session.add(new_link)
        db.session.commit()
        
        logger.info(f"Novo Link curto '{code}' criado com sucesso para: {original_url}")
        
        # --- LÓGICA DE MONTAGEM DA URL PÚBLICA ---
        config = load_or_create_config()
        app_base_url = config.get("APP_BASE_URL", "").strip().rstrip('/')
        
        if app_base_url:
            try:
                # Tenta obter apenas o caminho da rota (ex: /s/AbCdEf) e junta com o domínio oficial
                path = url_for('redirect.redirect_to_url', code=code, _external=False)
                return f"{app_base_url}{path}"
            except RuntimeError:
                # Se estiver a correr em Background (Sem Request Context), força manualmente a estrutura da URL
                return f"{app_base_url}/s/{code}"
        else:
            # Fallback antigo caso o administrador não tenha definido nenhum domínio nas configurações
            try:
                return url_for('redirect.redirect_to_url', code=code, _external=True)
            except RuntimeError:
                logger.warning("APP_BASE_URL não está configurada e o sistema está a rodar em background. O link curto usará localhost.")
                return f"http://localhost:5000/s/{code}"

    def get_original_url(self, short_code):
        """Obtém a URL original a partir de um código curto."""
        link = ShortLink.query.filter_by(short_code=short_code).first()
        return link.original_url if link else None
