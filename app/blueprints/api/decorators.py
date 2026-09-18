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


def chave_de_api_necessaria(escopo):
    """Protege uma rota que é chamada por uma integração, não por um navegador.

    Um bot ou um webhook não tem sessão, por isso a prova é uma chave enviada em
    `X-API-Key` ou em `Authorization: Bearer <chave>`.

    ⚠️ **O `escopo` é o que esta rota exige**, e é o que faz de uma chave uma
    promessa: "esta só cria convites". Havia UMA chave para tudo, partilhada
    pelo endpoint de convites e pelo webhook do Seerr — regenerá-la porque um
    bot foi comprometido derrubava também o Seerr, e a chave do bot podia
    aceitar webhooks em nome do painel.

    ⚠️ **E já não há nenhuma que valha para tudo.** A `INTERNAL_TRIGGER_KEY`
    foi removida do config.json, com as rotas e o cartão que a mostravam: uma
    chave sem o escopo desta rota é recusada como qualquer outra chave
    inválida, e é a tabela `api_keys` a única coisa que este decorador
    consulta.

    ⚠️ Isto existia duas vezes, copiado, e as duas cópias já tinham divergido:
    uma aceitava o `Authorization` sem o prefixo `Bearer` e a outra não. A
    interface do Seerr chama "Authorization" ao campo onde se escreve a chave e
    mais nada, por isso as duas formas passam.

    Quem compara é o `api_keys.verificar`, com `secrets.compare_digest`, para o
    tempo de resposta não revelar a chave.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            fornecida = _chave_do_pedido()

            if not fornecida or not _autorizada(fornecida, escopo):
                logger.warning(
                    f"Pedido a '{request.path}' recusado: chave de API inválida, "
                    f"revogada ou sem a permissão '{escopo}' (IP: {request.remote_addr})."
                )
                return jsonify({
                    "success": False,
                    "message": _("Chave de API inválida ou ausente."),
                }), 401

            return f(*args, **kwargs)
        return wrapper
    return decorator


def _chave_do_pedido():
    """A chave que veio nos cabeçalhos, em qualquer das duas formas."""
    fornecida = (request.headers.get('X-API-Key') or '').strip()
    if fornecida:
        return fornecida

    cabecalho = (request.headers.get('Authorization') or '').strip()
    # O prefixo é opcional: a interface do Overseerr chama "Authorization" ao
    # campo, e quem o preenche escreve lá a chave e mais nada.
    if cabecalho.lower().startswith('bearer '):
        return cabecalho[7:].strip()
    return cabecalho


def _autorizada(fornecida, escopo):
    from ...services import api_keys

    try:
        return api_keys.verificar(fornecida, escopo) is not None
    except Exception as e:
        # ⚠️ Uma falha a consultar a tabela NUNCA pode ser um "sim". O lado
        # seguro do erro aqui é recusar.
        logger.error(f"Falha ao verificar a chave de API: {e}", exc_info=True)
        return False


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

