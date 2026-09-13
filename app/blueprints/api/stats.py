# app/blueprints/api/stats.py

import logging
import bleach
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from flask_babel import gettext as _

from ...extensions import media_server, stats_manager, data_manager
from ...utils.identity import normalize_user_id, same_user

logger = logging.getLogger(__name__)
stats_api_bp = Blueprint('stats_api', __name__)

def _dono_do_servidor():
    """O administrador do painel, com o avatar já pronto para a interface.

    Devolve None quando ainda não há um (instalação a meio) ou quando o
    servidor não o sabe dizer — nesse caso o pódio fica como estava.
    """
    from ...config import load_or_create_config

    admin_id = normalize_user_id(load_or_create_config().get('ADMIN_USER_ID'))
    if not admin_id:
        return None

    thumb = None
    try:
        conta = media_server.get_owner_account()
        # `thumb_para_interface` é idempotente e sabe o formato de cada
        # servidor: no Plex traduz o URL do plex.tv para o proxy do painel.
        thumb = media_server.thumb_para_interface(getattr(conta, 'thumb', None))
    except Exception as e:
        logger.debug(f"Não foi possível obter o avatar do administrador: {e}")

    return {'id': admin_id, 'thumb': thumb}


def _obfuscate_username(username):
    if len(username) <= 2: return username
    return f"{username[0]}{'*' * (len(username) - 2)}{username[-1]}"

@stats_api_bp.route('/')
@login_required
def get_statistics_data():
    days = request.args.get('days', 7, type=int)
    
    plex_users = media_server.get_all_users() or []
    plex_users_info = {u['id']: u['thumb'] for u in plex_users}

    # 🐛 O DONO do servidor não está na lista de utilizadores — no Plex nunca
    # esteve (a API só lista os amigos). Como ele agora aparece no pódio como
    # toda a gente, a linha dele ficava com o avatar "?" para quem a visse.
    dono = _dono_do_servidor()
    if dono:
        plex_users_info.setdefault(dono['id'], dono['thumb'])

    tautulli_data = stats_manager.get_watch_stats(days=days, plex_users_info=plex_users_info)

    if not tautulli_data.get("success"):
        return jsonify(tautulli_data)

    tautulli_user_ids = {user_stat['user_id'] for user_stat in tautulli_data.get("stats", [])}
    media_user_ids = {u['id'] for u in plex_users}
    all_user_ids = list(tautulli_user_ids.union(media_user_ids))

    all_profiles = data_manager.get_user_profiles_by_id(all_user_ids)
    
    processed_stats = []
    for user_stat in tautulli_data.get("stats", []):
        user_id = user_stat["user_id"]
        profile = all_profiles.get(user_id, {})
        
        is_private = profile.get('hide_from_leaderboard', False)
        user_stat["is_private"] = is_private
        user_stat["original_username"] = user_stat["username"]
        # Quem administra o painel aparece no pódio como os outros; dizer quem
        # é evita a pergunta "quem é este que vê tudo e não tem plano".
        user_stat["is_admin"] = bool(dono and same_user(user_id, dono['id']))

        if not current_user.is_admin() and is_private and current_user.id != str(user_id):
            user_stat["username"] = _obfuscate_username(user_stat["username"])
            user_stat["thumb"] = f"https://placehold.co/80x80/1F2937/E5E7EB?text=?"

        processed_stats.append(user_stat)
    
    tautulli_data["stats"] = processed_stats
    return jsonify(tautulli_data)

@stats_api_bp.route('/user/<media_user_id>')
@login_required
def get_user_statistics(media_user_id):
    """
    Obtém as estatísticas detalhadas de um utilizador, respeitando as configurações de privacidade.
    """
    # 🐛 Um perfil em falta rebentava aqui com um AttributeError: o
    # ADMINISTRADOR não tem perfil local (por desenho) e bastava-lhe abrir as
    # suas próprias estatísticas para levar com um 500. Quem não tem perfil
    # também não pediu privacidade — não há nada a esconder.
    profile = data_manager.get_user_profile(media_user_id) or {}
    is_private = profile.get('hide_from_leaderboard', False)

    if not is_private or current_user.is_admin() or current_user.id == str(media_user_id):
        days = request.args.get('days', 7, type=int)
        return jsonify(stats_manager.get_user_watch_details(media_user_id=media_user_id, days=days))
    else:
        logger.warning(f"Acesso negado para '{current_user.username}' ao tentar ver as estatísticas privadas do utilizador ID '{media_user_id}'.")
        return jsonify({"success": False, "message": _("Este usuário prefere manter suas estatísticas privadas.")}), 403

@stats_api_bp.route('/user/history')
@login_required
def get_user_watch_history_route():
    """Endpoint para obter o histórico de visualização paginado do utilizador logado."""
    try:
        page = request.args.get('page', 1, type=int)
        length = request.args.get('length', 15, type=int)
        
        raw_search = request.args.get('search', '', type=str)
        search = bleach.clean(raw_search, strip=True)

        # Pela fachada: o Plex vai ao Tautulli, o Jellyfin ao próprio servidor.
        history_data = media_server.get_watch_history(
            user_id=normalize_user_id(current_user.id),
            page=page,
            length=length,
            search=search
        )
        return jsonify(history_data)
    except Exception as e:
        logger.error(f"Erro ao obter o histórico de visualização para {current_user.username}: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao obter histórico de visualização."}), 500

@stats_api_bp.route('/recently-added')
@login_required
def get_recently_added_route():
    days = request.args.get('days', 7, type=int)
    return jsonify(stats_manager.get_recently_added(days=days))

@stats_api_bp.route('/recommendations')
@login_required
def get_recommendations_route():
    """
    Secções "Porque assistiu X, pode gostar de Y" do utilizador autenticado.

    As recomendações são sempre as do próprio: cruzar o histórico de terceiros
    para MOSTRAR a terceiros seria uma fuga de privacidade. O administrador pode
    pedir as de outro utilizador (para diagnosticar a funcionalidade) através do
    parâmetro 'user_id'.
    """
    target_user_id = current_user.id
    requested_id = request.args.get('user_id', type=int)
    if requested_id and current_user.is_admin():
        target_user_id = requested_id

    # Normalizado para texto: sem isto, o ID do utilizador autenticado (string)
    # e o pedido pelo administrador (int) criariam duas entradas de cache
    # distintas para exactamente o mesmo resultado.
    result = stats_manager.get_recommendations(str(target_user_id))
    if not result.get("success"):
        return jsonify(result), 502

    def _link(rating_key):
        # Quem sabe montar o endereço é o backend: no Plex é uma página do
        # plex.tv, no Jellyfin é a interface web do próprio servidor. Sem
        # ligação não há link, e o cartão aparece à mesma — só sem o botão.
        try:
            return media_server.link_para_item(rating_key)
        except Exception:
            logger.debug("Servidor indisponível: as recomendações seguem sem links profundos.")
            return None

    for section in result.get("sections", []):
        section["seed"]["item_url"] = _link(section["seed"].get("rating_key"))
        for item in section.get("items", []):
            item["item_url"] = _link(item.get("rating_key"))

    return jsonify(result)


@stats_api_bp.route('/wrapped/<media_user_id>')
@login_required
def get_wrapped_data_route(media_user_id):
    """
    Retrospectiva anual estilo 'Plex Wrapped' de um utilizador. Respeita a
    mesma configuração de privacidade (hide_from_leaderboard) do restante
    das estatísticas.
    """
    # 🐛 Não ter perfil local não é "utilizador não encontrado": o
    # administrador nunca tem um, e quem ainda não entrou no painel também não.
    # O Wrapped sai do histórico do servidor, que existe à mesma — e o perfil
    # aqui só serve para a preferência de privacidade.
    profile = data_manager.get_user_profile(media_user_id) or {}
    is_private = profile.get('hide_from_leaderboard', False)
    if is_private and not current_user.is_admin() and current_user.id != str(media_user_id):
        logger.warning(f"Acesso negado para '{current_user.username}' ao tentar ver o Plex Wrapped privado do utilizador ID '{media_user_id}'.")
        return jsonify({"success": False, "message": _("Este usuário prefere manter suas estatísticas privadas.")}), 403

    year = request.args.get('year', type=int)
    return jsonify(stats_manager.get_wrapped_data(media_user_id=media_user_id, year=year))

