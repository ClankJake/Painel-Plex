"""Rotas para o bot exclusivo de autoatendimento."""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

import requests
from flask import Blueprint, abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required

from .auth import admin_required
from ..config import load_or_create_config
from ..extensions import db, limiter
from ..models import TelegramVinculo, UserProfile, agora_utc
from ..services.telegram_autoatendimento import endereco, responder, segredo

telegram_bp = Blueprint('telegram', __name__)


def _api(config, metodo, **dados):
    try:
        response = requests.post(f"https://api.telegram.org/bot{config['AUTO_BOT_TOKEN']}/{metodo}",
                                 json=dados, timeout=8, allow_redirects=False)
        body = response.json()
        if response.ok and body.get('ok'):
            return body['result']
    except (requests.RequestException, ValueError):
        pass
    return None


@telegram_bp.post('/conectar')
@login_required
@admin_required
def conectar():
    config = load_or_create_config()
    if not config.get('AUTO_BOT_ENABLED'):
        return jsonify(success=False, message='Ative o bot e salve as configurações primeiro.'), 400
    bot = _api(config, 'getMe')
    if not bot or not _api(config, 'setWebhook', url=endereco(config, 'telegram.receber'),
                           secret_token=segredo(config), allowed_updates=['message']):
        return jsonify(success=False, message='Confira o token e a URL pública HTTPS do painel.'), 502
    return jsonify(success=True, message=f"Bot conectado: @{bot['username']}.")


@telegram_bp.post('/vincular')
@login_required
def vincular():
    config = load_or_create_config()
    if not config.get('AUTO_BOT_ENABLED') or not db.session.get(UserProfile, current_user.id):
        abort(404)
    bot = _api(config, 'getMe')
    if not bot or not re.fullmatch(r'[A-Za-z0-9_]+', bot.get('username', '')):
        flash('Telegram indisponível. Tente novamente em alguns minutos.', 'error')
        return redirect(url_for('main.account_page'), 303)
    chave = secrets.token_urlsafe(24)
    vinculo = db.session.get(TelegramVinculo, current_user.id)
    if not vinculo:
        vinculo = TelegramVinculo(media_user_id=current_user.id)
        db.session.add(vinculo)
    vinculo.desafio = hashlib.sha256(chave.encode()).hexdigest()
    vinculo.validade = agora_utc() + timedelta(minutes=10)
    db.session.commit()
    response = redirect(f"https://t.me/{bot['username']}?start=vincular_{chave}", 303)
    response.headers['Cache-Control'] = 'no-store'
    return response


@telegram_bp.post('/desvincular')
@login_required
def desvincular():
    TelegramVinculo.query.filter_by(media_user_id=current_user.id).delete()
    db.session.commit()
    flash('Vínculo de autoatendimento removido.', 'success')
    return redirect(url_for('main.account_page'), 303)


@telegram_bp.post('/webhook')
@limiter.exempt
def receber():
    config = load_or_create_config()
    if not config.get('AUTO_BOT_ENABLED'):
        # Confirma e descarta mensagens recebidas enquanto o atendimento está fechado.
        return '', 204
    assinatura = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not hmac.compare_digest(assinatura.encode(), segredo(config).encode()):
        abort(403)
    update = request.get_json(silent=True)
    msg = update.get('message') if isinstance(update, dict) else None
    if not isinstance(msg, dict):
        return '', 204
    chat = msg.get('chat', {})
    texto = msg.get('text')
    if (chat.get('type') != 'private' or not isinstance(chat.get('id'), int)
            or chat['id'] <= 0 or chat['id'] != msg.get('from', {}).get('id') or not isinstance(texto, str)):
        return '', 204
    resposta, botoes = responder(config, str(chat['id']), texto.strip())
    return jsonify(method='sendMessage', chat_id=chat['id'], text=resposta, reply_markup=botoes)
