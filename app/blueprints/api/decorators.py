# app/blueprints/api/decorators.py

import logging
from functools import wraps
from flask import jsonify, request
from flask_babel import gettext as _
from pydantic import ValidationError

from ...extensions import plex_manager

logger = logging.getLogger(__name__)

def user_lookup_by_id(f):
    """Decorator para encontrar um utilizador pelo seu ID do Plex e injetá-lo na rota."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        plex_user_id = kwargs.get('plex_user_id') or request.json.get('plex_user_id')

        if not plex_user_id:
            logger.warning("Nenhum ID de utilizador do Plex fornecido no pedido.")
            return jsonify({"success": False, "message": _("ID do utilizador não fornecido.")}), 400
        
        try:
            plex_user_id = int(plex_user_id)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": _("ID do utilizador inválido.")}), 400

        user = plex_manager.get_user_by_id(plex_user_id)
        
        if not user:
            logger.warning(f"Utilizador com ID '{plex_user_id}' não encontrado.")
            return jsonify({"success": False, "message": _("Usuário não encontrado.")}), 404
        
        if 'plex_user_id' in kwargs:
             del kwargs['plex_user_id']

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
                return jsonify({
                    "success": False,
                    "message": "Dados de entrada inválidos.",
                    "errors": errors
                }), 400
        return wrapper
    return decorator

