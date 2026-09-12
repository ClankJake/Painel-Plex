# app/blueprints/api/decorators.py

import logging
from functools import wraps
from flask import jsonify, request
from flask_babel import gettext as _
from pydantic import ValidationError

from ...extensions import media_server
from ...utils.identity import normalize_user_id

logger = logging.getLogger(__name__)


def _do_corpo(corpo, chave):
    """Lê uma chave do corpo do pedido, tolerando um corpo ausente."""
    return corpo.get(chave) if isinstance(corpo, dict) else None


def user_lookup_by_id(f):
    """Encontra o utilizador pelo seu ID no servidor de média e injeta-o na rota."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 🐛 `request.json` LEVANTA 400 (com uma página de erro em HTML) quando o
        # pedido se diz JSON e não traz corpo — e o `fetchAPI` do painel manda
        # sempre o cabeçalho 'Content-Type: application/json', mesmo num GET.
        # Antes isto não acontecia porque o acesso estava do lado direito de um
        # `or`, que o Python não avalia quando o ID já veio no URL; ao passar a
        # ler o corpo ANTES da cadeia, todas as rotas GET com este decorador
        # começaram a responder 400 com HTML em vez de JSON.
        # `get_json(silent=True)` nunca levanta: devolve None e seguimos.
        corpo = request.get_json(silent=True) or {}

        # 'plex_user_id' é o nome antigo do campo. Continua a ser aceite para
        # não partir integrações já feitas contra esta API (o painel tem uma
        # API de convites usada por bots); o nome a usar é 'media_user_id'.
        media_user_id = (
            kwargs.get('media_user_id')
            or _do_corpo(corpo, 'media_user_id')
            or _do_corpo(corpo, 'plex_user_id')
        )

        if not media_user_id:
            logger.warning("Nenhum ID de utilizador do Plex fornecido no pedido.")
            return jsonify({"success": False, "message": _("ID do usuário não fornecido.")}), 400
        
        # A identidade é texto (o Plex usa inteiros, o Jellyfin GUIDs), por
        # isso o que se valida é que veio alguma coisa — não que é um número.
        media_user_id = normalize_user_id(media_user_id)
        if not media_user_id:
            return jsonify({"success": False, "message": _("ID do usuário inválido.")}), 400

        user = media_server.get_user_by_id(media_user_id)
        
        if not user:
            logger.warning(f"Utilizador com ID '{media_user_id}' não encontrado.")
            return jsonify({"success": False, "message": _("Usuário não encontrado.")}), 404
        
        if 'media_user_id' in kwargs:
             del kwargs['media_user_id']

        return f(user=user, *args, **kwargs)
    return decorated_function


def validate_json(schema):
    """
    Decorator para validar o JSON de entrada de uma requisição contra um esquema Pydantic.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            json_data = request.get_json()
            if not json_data:
                return jsonify({"success": False, "message": "Corpo da requisição JSON não encontrado ou vazio."}), 400
            
            try:
                validated_data = schema(**json_data)
                kwargs['validated_data'] = validated_data
                return f(*args, **kwargs)
            except ValidationError as e:
                errors = {err['loc'][0]: err['msg'] for err in e.errors()}
                logger.warning(f"Falha na validação da API para o endpoint '{request.path}': {errors}")
                # O detalhe entra também na 'message' porque é isso que a interface
                # mostra no toast — sem ele, o utilizador via apenas "dados
                # inválidos" e não ficava a saber qual campo corrigir.
                detalhe = "; ".join(f"{campo}: {msg}" for campo, msg in errors.items())
                return jsonify({
                    "success": False,
                    "message": f"Dados de entrada inválidos. {detalhe}".strip(),
                    "errors": errors
                }), 400
        return wrapper
    return decorator

