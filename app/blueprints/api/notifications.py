# app/blueprints/api/notifications.py

import logging
from datetime import datetime
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from flask_babel import gettext as _

from ... import extensions
from ...extensions import data_manager, limiter
from .decorators import validate_json
from .schemas import RemocaoDeSubscricaoPushSchema, SubscricaoPushSchema

logger = logging.getLogger(__name__)
notifications_api_bp = Blueprint('notifications_api', __name__)

@notifications_api_bp.route('/')
@login_required
@limiter.exempt
def get_notifications_route():
    try:
        # Admin vê notificações do sistema (media_user_id=None)
        # Utilizadores vêm as suas próprias (media_user_id=current_user.id)
        target_plex_id = None if current_user.is_admin() else current_user.id
        
        notifications_data = data_manager.get_notifications(media_user_id=target_plex_id, limit=15, include_read=True)
        unread_count = data_manager.get_unread_notification_count(media_user_id=target_plex_id)

        for n in notifications_data:
            if isinstance(n.get('timestamp'), datetime):
                n['timestamp'] = n['timestamp'].strftime('%Y-%m-%dT%H:%M:%S')
        return jsonify({"success": True, "notifications": notifications_data, "unread_count": unread_count})
    except Exception as e:
        logger.error(f"Erro ao buscar notificações: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao buscar notificações."}), 500

@notifications_api_bp.route('/read-all', methods=['POST'])
@login_required
def mark_all_notifications_as_read_route():
    try:
        target_plex_id = None if current_user.is_admin() else current_user.id
        updated_count = data_manager.mark_all_as_read(media_user_id=target_plex_id)
        return jsonify({"success": True, "message": f"{updated_count} notificações marcadas como lidas."})
    except Exception as e:
        logger.error(f"Erro ao marcar notificações como lidas: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao marcar notificações como lidas."}), 500

@notifications_api_bp.route('/clear-all', methods=['POST'])
@login_required
def clear_all_notifications_route():
    try:
        target_plex_id = None if current_user.is_admin() else current_user.id
        deleted_count = data_manager.delete_all_notifications(media_user_id=target_plex_id)
        return jsonify({"success": True, "message": f"{deleted_count} notificações foram limpas com sucesso."})
    except Exception as e:
        logger.error(f"Erro ao limpar todas as notificações: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Falha ao limpar as notificações."}), 500


# ==========================================
# NOTIFICAÇÕES PUSH (navegador e celular)
# ==========================================
#
# ⚠️ **Quem subscreve é o DONO DA SESSÃO, e é o servidor que decide quem ele é.**
# O aparelho de um administrador fica com `media_user_id` a NULL (recebe os
# avisos do painel); o de um usuário comum fica com o id dele (recebe os avisos
# da conta dele). O navegador nunca escolhe: aceitar um dono vindo do pedido
# deixava qualquer pessoa com sessão receber as notificações do administrador.

def _dono_da_sessao():
    return None if current_user.is_admin() else current_user.id


def _etiqueta_do_aparelho(pedido_label):
    """Um nome curto para a pessoa reconhecer o aparelho na lista."""
    if pedido_label:
        return str(pedido_label)[:120]
    # O `User-Agent` é escolhido por quem faz o pedido e vai ser mostrado numa
    # página: corta-se, e quem o desenha escapa-o.
    return (request.headers.get('User-Agent') or '')[:120] or None


@notifications_api_bp.route('/push/subscribe', methods=['POST'])
@login_required
@limiter.limit("30 per hour")
@validate_json(SubscricaoPushSchema)
def subscribe_push_route(validated_data):
    """Grava o aparelho que acabou de aceitar receber notificações.

    É chamada outra vez a cada carregamento de página em que já haja permissão
    — de propósito. A subscrição vive no navegador e a linha vive na base de
    dados do painel: um restauro de backup, ou uma linha apagada à mão, deixava
    o navegador convencido de que estava tudo bem e o painel sem para onde
    entregar. Regravar é barato e conserta isso sozinho.
    """
    gestor = extensions.push_manager
    if not (gestor and gestor.disponivel):
        return jsonify({"success": False, "message": _("As notificações push não estão ativas neste painel.")}), 409

    try:
        gestor.registar(
            _dono_da_sessao(),
            {'endpoint': validated_data.endpoint,
             'keys': {'p256dh': validated_data.keys.p256dh, 'auth': validated_data.keys.auth}},
            device_label=_etiqueta_do_aparelho(validated_data.device_label),
        )
    except Exception as e:
        logger.error(f"Erro ao registar a subscrição push: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Não foi possível ativar as notificações neste dispositivo.")}), 500

    return jsonify({"success": True, "message": _("Notificações ativadas neste dispositivo.")})


@notifications_api_bp.route('/push/unsubscribe', methods=['POST'])
@login_required
@validate_json(RemocaoDeSubscricaoPushSchema)
def unsubscribe_push_route(validated_data):
    """Esquece este aparelho.

    Não se verifica de quem era a linha: para a apagar é preciso saber o
    endereço, que só o próprio navegador tem — e recusar a remoção de uma
    subscrição que mudou de dono (o mesmo computador, outra sessão) deixava
    notificações a chegar a quem já não as queria.
    """
    try:
        removido = extensions.push_manager.remover(validated_data.endpoint)
    except Exception as e:
        logger.error(f"Erro ao remover a subscrição push: {e}", exc_info=True)
        return jsonify({"success": False, "message": _("Não foi possível desativar as notificações.")}), 500

    return jsonify({"success": True, "removed": bool(removido),
                    "message": _("Notificações desativadas neste dispositivo.")})


@notifications_api_bp.route('/push/test', methods=['POST'])
@login_required
@limiter.limit("10 per hour")
def test_push_route():
    """Manda uma notificação de teste para os aparelhos de quem pediu.

    É o único jeito de confirmar que o caminho inteiro funciona — as chaves, o
    serviço de push do navegador e a permissão do sistema operacional — sem
    esperar por um pagamento de verdade.
    """
    gestor = extensions.push_manager
    if not (gestor and gestor.disponivel):
        return jsonify({"success": False, "message": _("As notificações push não estão ativas neste painel.")}), 409

    entregues = gestor.enviar(
        _dono_da_sessao(),
        _("Notificação de teste"),
        _("Se você está lendo isto, as notificações do painel estão funcionando."),
        url='/', tag='teste',
    )
    if not entregues:
        return jsonify({"success": False, "message": _("Nenhum dispositivo recebeu a notificação. Ative as notificações neste dispositivo e tente de novo.")}), 200
    return jsonify({"success": True, "sent": entregues,
                    "message": _("Enviada para %(n)d dispositivo(s).", n=entregues)})
