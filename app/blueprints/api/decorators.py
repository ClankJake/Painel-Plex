# app/blueprints/api/decorators.py

import logging
from functools import wraps
from flask import jsonify, request
from flask_babel import gettext as _
from pydantic import ValidationError

from ...extensions import plex_manager

logger = logging.getLogger(__name__)

def user_lookup(f):
    """Decorator para encontrar um utilizador por email ou username e injetá-lo na rota."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        identifier = kwargs.get('username') or kwargs.get('email')
        if not identifier:
             identifier = request.json.get('email')

        if not identifier:
            logger.warning("Nenhum identificador (username/email) fornecido no pedido.")
            return jsonify({"success": False, "message": _("Email ou username não fornecido.")}), 400
        
        logger.info(f"A procurar utilizador com o identificador: {identifier}")
        
        all_users = plex_manager.get_all_plex_users()
        if all_users is None:
            return jsonify({"success": False, "message": _("Não foi possível conectar ao servidor Plex para encontrar o utilizador.")}), 503

        user = next((u for u in all_users if u['username'] == identifier or u['email'] == identifier), None)
        
        if not user:
            logger.warning(f"Utilizador '{identifier}' não encontrado na lista de utilizadores do Plex.")
            return jsonify({"success": False, "message": _("Usuário não encontrado.")}), 404
        
        logger.info(f"Utilizador '{identifier}' encontrado: {user['username']} ({user['email']})")
        
        # Limpa os kwargs para não serem passados duplicados para a rota
        if 'username' in kwargs:
             del kwargs['username']
        if 'email' in kwargs:
            del kwargs['email']

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
                # Formata os erros de validação para uma resposta clara
                errors = {err['loc'][0]: err['msg'] for err in e.errors()}
                logger.warning(f"Falha na validação da API para o endpoint '{request.path}': {errors}")
                return jsonify({
                    "success": False,
                    "message": "Dados de entrada inválidos.",
                    "errors": errors
                }), 400
        return wrapper
    return decorator
