# app/services/link_shortener.py

import secrets
import logging
import os
from typing import Optional

from flask import url_for
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import ShortLink
from ..config import load_or_create_config
from ..utils.log_sanitizer import mask_code, mask_link

logger = logging.getLogger(__name__)

class LinkShortener:
    """Serviço para criar e resolver links curtos com alta resiliência."""

    def _generate_short_code(self, length: int = 7) -> str:
        """Gera um código curto e único."""
        while True:
            code = secrets.token_urlsafe(length)[:length]
            # Verifica colisão na base de dados (altamente improvável, mas seguro)
            if not ShortLink.query.filter_by(short_code=code).first():
                return code

    def create_short_link(self, original_url: str) -> str:
        """
        Cria um novo link curto para a URL especificada e apaga quaisquer 
        links curtos antigos que apontassem para o mesmo destino.
        Se falhar, faz fallback automático para a URL original.
        """
        try:
            # 1. AUTO-LIMPEZA: Apaga em massa (Bulk Delete)
            # Mais eficiente que iterar, pois é executado numa única query SQL
            deleted_count = ShortLink.query.filter_by(original_url=original_url).delete(synchronize_session=False)
            if deleted_count > 0:
                logger.debug(f"{deleted_count} link(s) antigo(s) apagado(s) para evitar duplicações de '{mask_link(original_url)}'.")
            
            # 2. Cria o novo link curto
            code = self._generate_short_code()
            new_link = ShortLink(short_code=code, original_url=original_url)
            db.session.add(new_link)
            
            # 3. Commit atómico (apaga e adiciona ao mesmo tempo)
            db.session.commit()
            logger.info(f"Novo Link curto '{mask_code(code)}' criado com sucesso para: {mask_link(original_url)}")
            
        except SQLAlchemyError as e:
            db.session.rollback()
            logger.error(f"Erro na Base de Dados ao criar link curto para {mask_link(original_url)}: {e}")
            # FALLBACK DE SEGURANÇA: Se não conseguir gerar o curto, devolve o longo para não quebrar a aplicação
            return original_url
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro inesperado ao criar link curto: {e}")
            return original_url

        # --- LÓGICA DE MONTAGEM DA URL PÚBLICA ---
        config = load_or_create_config()
        app_base_url = config.get("APP_BASE_URL", "").strip().rstrip('/')
        
        if app_base_url:
            try:
                # Tenta obter apenas o caminho da rota (ex: /s/AbCdEf) e junta com o domínio oficial
                path = url_for('redirect.redirect_to_url', code=code, _external=False)
                return f"{app_base_url}{path}"
            except RuntimeError:
                # Se estiver a correr em Background (Sem Request Context / Tarefa Cron)
                return f"{app_base_url}/s/{code}"
        else:
            # Fallback antigo caso o administrador não tenha definido nenhum domínio nas configurações
            try:
                return url_for('redirect.redirect_to_url', code=code, _external=True)
            except RuntimeError:
                port = os.environ.get('PORT', '5000')
                logger.warning(f"APP_BASE_URL não está configurada e o sistema está a correr em background. O link curto usará localhost:{port}.")
                return f"http://localhost:{port}/s/{code}"

    def get_original_url(self, short_code: str) -> Optional[str]:
        """Obtém a URL original a partir de um código curto."""
        try:
            link = ShortLink.query.filter_by(short_code=short_code).first()
            return link.original_url if link else None
        except SQLAlchemyError as e:
            logger.error(f"Erro na Base de Dados ao resolver link curto '{short_code}': {e}")
            return None
