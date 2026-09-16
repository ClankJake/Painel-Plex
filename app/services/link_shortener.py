# app/services/link_shortener.py

import secrets
import logging
import os
from typing import Optional

from flask import url_for
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import ShortLink, agora_utc
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
        O link curto deste destino: o que já existe, ou um novo.
        Se falhar, faz fallback automático para a URL original.

        🐛 Isto APAGAVA os links antigos do mesmo destino antes de criar outro,
        "para evitar duplicações" — e com isso matava o link que a pessoa já
        tinha recebido. Não era um caso raro: `garantir_payment_token` MANTÉM o
        token enquanto ele for válido (só estende a validade), por isso o URL
        longo é idêntico entre envios e o apagamento coincidia sempre. O aviso
        de vencimento é diário, com uma trava de 23 horas por pessoa: quem
        recebia o lembrete de hoje ficava com o de ontem morto, e ao rolar a
        conversa para cima tocava num "link expirado" cujo destino continuava
        perfeitamente válido — no fluxo do pagamento, que é onde menos se quer
        atrito.

        Reutilizar resolve a duplicação melhor do que apagar: fica UMA linha
        por destino (em vez de uma por envio) e todas as mensagens já
        entregues continuam a funcionar. E não se perde segurança nenhuma ao
        não rodar o código: por baixo está o mesmo `/pay/<token>`, que é a
        credencial de facto — trocar o código curto não fecharia porta nenhuma.
        """
        try:
            existente = ShortLink.query.filter_by(original_url=original_url).first()

            if existente:
                code = existente.short_code
                # ⚠️ A data é reposta de propósito. O `cleanup_job` apaga por
                # `created_at`, e sem isto um link reutilizado ao dia 29 morria
                # no dia 30 — logo a seguir a ter sido enviado. É a mesma regra
                # que `garantir_payment_token` já segue: o que conta é a data do
                # ÚLTIMO envio, não a do primeiro.
                existente.created_at = agora_utc()
                db.session.commit()
                logger.info(f"Link curto '{mask_code(code)}' reutilizado para: {mask_link(original_url)}")
            else:
                code = self._generate_short_code()
                db.session.add(ShortLink(short_code=code, original_url=original_url,
                                         created_at=agora_utc()))
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
