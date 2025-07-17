# app/blueprints/api/decorators.py

import logging
from functools import wraps
from flask import jsonify, request
from flask_babel import gettext as _

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
