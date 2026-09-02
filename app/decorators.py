from functools import wraps

from flask import abort
from flask_login import current_user


def admin_required(f):
    """
    Restringe uma rota a administradores.

    ⚠️ Este decorador é o equivalente "genérico" (responde 403) do que existe em
    `app.blueprints.auth`, usado pelas rotas que preferem redirecionar. Ambos
    devem tomar a MESMA decisão.

    🐛 CORREÇÃO: a versão anterior testava `if not current_user.is_admin:`. Como
    `is_admin` é um MÉTODO (tanto em `models.User` como em `MyAnonymousUser`), a
    expressão avaliava o objeto do método — que é sempre verdadeiro — e portanto
    `not current_user.is_admin` era sempre False. O decorador nunca bloqueava
    ninguém: qualquer visitante anónimo passava. Hoje nenhuma rota o usa (todas
    importam a versão de `auth.py`), mas bastava um `from ..decorators import
    admin_required` para abrir uma rota de administração a toda a gente.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)

        is_admin = getattr(current_user, 'is_admin', False)
        # Aceita tanto o método (`models.User.is_admin()`) como uma eventual
        # propriedade booleana, para não voltar a depender do formato exato.
        if callable(is_admin):
            is_admin = is_admin()

        if not is_admin:
            abort(403)  # Lança um erro de "Acesso Proibido"
        return f(*args, **kwargs)
    return decorated_function
