from functools import wraps
from flask import abort
from flask_login import current_user

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)  # Lança um erro de "Acesso Proibido"
        return f(*args, **kwargs)
    return decorated_function
