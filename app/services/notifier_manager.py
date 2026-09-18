# app/services/notifier_manager.py

import base64
import json
import logging
import uuid
import time
import html
import re
from datetime import datetime, timezone
from tzlocal import get_localzone

import requests
import telebot
from http.cookiejar import DefaultCookiePolicy
from requests.adapters import HTTPAdapter
from telebot import types
from urllib3.util.retry import Retry
from flask_babel import gettext as _

from ..config import load_or_create_config
from ..utils.log_sanitizer import mask_phone, mask_url_credentials

logger = logging.getLogger(__name__)

# --- LIMITES DAS APIS EXTERNAS ---
# Ultrapassar qualquer um destes limites faz a API rejeitar a mensagem INTEIRA,
# por isso preferimos dividir/cortar do nosso lado a perder a notificação.
TELEGRAM_MAX_MESSAGE_LEN = 4096
TELEGRAM_MAX_CAPTION_LEN = 1024
DISCORD_MAX_CONTENT_LEN = 2000
DISCORD_MAX_EMBED_DESCRIPTION_LEN = 4096

# (ligação, leitura). O timeout de ligação é curto porque um servidor fora do ar
# não deve segurar um envio em massa; o de leitura é generoso porque as APIs
# não-oficiais de WhatsApp costumam demorar a responder.
HTTP_TIMEOUT = (10, 30)

# Estados que valem a pena repetir: 429 (limite de ritmo) e erros de gateway,
# em que o pedido comprovadamente NÃO chegou a ser processado. O 500 fica de
# fora de propósito — pode ter sido processado, e repetir duplicaria a mensagem.
_RETRY_STATUS = (429, 502, 503, 504)

# Marcadores no formato {nome_do_campo}. Deliberadamente exige que o primeiro
# caractere seja uma letra/underscore, para nunca casar com a abertura de um
# objeto JSON (`{"content": ...}`) nos templates de Discord/Webhook.
_PLACEHOLDER_RE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')

# URLs completos, para os proteger da conversão de markdown (ver _convert_md_to_html).
_URL_RE = re.compile(r'https?://[^\s<>"\')]+')


def _build_http_session():
    """
    Cria a sessão HTTP partilhada por todos os canais baseados em HTTP
    (Webhook, Discord e WhatsApp).

    Porque isto importa: sem sessão, cada `requests.post` abre uma ligação TCP e
    faz um handshake TLS novo. Num envio em massa para mil utilizadores isso são
    mil handshakes — dezenas de segundos desperdiçados e uma carga desnecessária
    no servidor remoto. Com a sessão, as ligações são reutilizadas (keep-alive).

    A política de repetição respeita o cabeçalho `Retry-After`, que é como o
    Discord e a Evolution API pedem que abrandemos.
    """
    session = requests.Session()
    # A sessão é partilhada por todos os envios: sem isto, um servidor remoto que
    # devolvesse Set-Cookie escrevia num frasco de cookies comum a todas as
    # greenlets — estado partilhado que não traz qualquer benefício a webhooks.
    session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=_RETRY_STATUS,
        allowed_methods=frozenset(['POST']),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


def substitute_placeholders(template_str, values, transform=None):
    """
    Substitui os marcadores {chave} numa única passagem sobre o template.

    Fazer isto numa passagem (em vez de um `str.replace` por chave) é uma questão
    de correção, não de desempenho: com substituições sucessivas, um valor
    introduzido por um utilizador que contivesse o texto '{name}' seria ele
    próprio substituído na passagem seguinte — uma injeção de template a partir
    do conteúdo da mensagem.

    Marcadores desconhecidos ficam intactos, para que uma chave escrita com um
    erro de digitação não destrua o resto da mensagem.
    """
    def _replace(match):
        key = match.group(1)
        if key not in values:
            return match.group(0)
        return transform(values[key]) if transform else values[key]

    return _PLACEHOLDER_RE.sub(_replace, template_str)


def truncate(text, limit, suffix='…'):
    """Corta um texto ao limite da API, sinalizando visivelmente o corte."""
    if not text or len(text) <= limit:
        return text
    return text[:max(0, limit - len(suffix))] + suffix


class NotificationError(Exception):
    """Falha na entrega de uma notificação por um canal específico."""

# --- CONSTANTES DE TEMPLATES PADRÃO ---
DEFAULT_TEMPLATES = {
    "TELEGRAM_EXPIRATION_MESSAGE_TEMPLATE": "Olá {name}, {greeting}!\n\nEste é um lembrete de que sua fatura está com o vencimento próximo.\nVencimento: *{date}*\nValor: *{price}*\nPlano: *{plan_name}*\nAcesso: `{email}`\n\nNa data do vencimento o sistema poderá bloquear o acesso. Para evitar a interrupção, realize o pagamento clicando no botão abaixo:",
    "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "✅ *Renovação Confirmada*\n\nOlá {name}!\nSua assinatura foi renovada com sucesso.\nNovo vencimento: *{new_date}*.",
    "TELEGRAM_REACTIVATION_MESSAGE_TEMPLATE": "✅ *Conta Reativada*\n\nOlá {name}!\nSua assinatura foi renovada e sua conta reativada com sucesso.\nNovo vencimento: *{new_date}*\n\nPara voltar a acessar, use o link abaixo:\n{invite_link}",
    "TELEGRAM_TRIAL_END_MESSAGE_TEMPLATE": "⌛ *Fim do Período de Teste*\n\n{name}, seu período de teste terminou.\nPara manter seu acesso, realize a renovação no botão abaixo:",
    "TELEGRAM_TRIAL_EXTENDED_MESSAGE_TEMPLATE": "⏳ *Período de Teste Estendido*\n\nOlá {name}!\nSeu período de teste no {server_name} foi estendido.\nAgora ele vai até *{new_date}*.\n\nBom entretenimento!",
    # A conta teve de ser criada de novo no servidor e a palavra-passe é nova.
    # ⚠️ Não há forma de recuperar a antiga, nem de pedir uma à pessoa: ela não
    # está a ver o painel, está a pagar. É por isto que este evento existe.
    "TELEGRAM_CREDENTIALS_MESSAGE_TEMPLATE": "🔑 *Acesso Restaurado*\n\nOlá {name}!\nSua conta no {server_name} foi criada novamente, por isso a senha mudou.\n\nUsuário: `{new_username}`\nSenha: `{new_password}`\n\nEntre em {invite_link} e troque a senha assim que puder.",
    "TELEGRAM_PASSWORD_RESET_MESSAGE_TEMPLATE": "🔑 *Redefinir a senha*\n\nOlá {name}!\nVocê pediu para redefinir a senha da sua conta no {server_name}.\n\nAbra este link e escolha uma nova:\n{reset_link}\n\nO link vale {reset_minutes} minutos e só pode ser usado uma vez. Se não foi você, ignore esta mensagem — nada muda até alguém abrir o link.",
    "DISCORD_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso de Vencimento", "description": "Olá **{username}**! 👋\\n\\nSeu acesso ao **{server_name}** está prestes a expirar em **{days} dia(s)**, no dia **{date}**.\\n\\nPara evitar a interrupção do serviço, por favor, [clique aqui para renovar]({payment_link}).", "color": 16776960}]}',
    "DISCORD_RENEWAL_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Renovação Confirmada!", "description": "Olá **{username}**! ✅\\n\\nSua assinatura foi renovada com sucesso. Seu novo vencimento é em **{new_date}**.\\n\\nObrigado e aproveite!", "color": 65280}]}',
    "DISCORD_REACTIVATION_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Conta Reativada!", "description": "Olá **{username}**! ✅\\n\\nSua assinatura foi reativada com sucesso. Seu novo vencimento é em **{new_date}**.\\n\\n[Clique aqui para voltar a acessar]({invite_link})", "color": 65280}]}',
    "DISCORD_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Período de Teste Terminou", "description": "Olá **{username}**! ⌛\\n\\nSeu período de teste gratuito terminou. Para continuar com acesso, por favor, [clique aqui para renovar]({payment_link}).", "color": 16711680}]}',
    "DISCORD_TRIAL_EXTENDED_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Período de Teste Estendido", "description": "Olá **{username}**! ⏳\\n\\nSeu período de teste no **{server_name}** foi estendido e agora vai até **{new_date}**.", "color": 16753920}]}',
    "DISCORD_CREDENTIALS_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Acesso Restaurado", "description": "Olá **{username}**! 🔑\\n\\nSua conta no **{server_name}** foi criada novamente, por isso a senha mudou.\\n\\nUsuário: `{new_username}`\\nSenha: `{new_password}`\\n\\nTroque a senha assim que puder.", "color": 3447003}]}',
    "DISCORD_PASSWORD_RESET_MESSAGE_TEMPLATE": "{\"content\": \"<@{discord_user_id}>\", \"embeds\": [{\"title\": \"Redefinir a senha\", \"description\": \"Olá **{username}**! 🔑\\n\\nVocê pediu para redefinir a senha da sua conta no **{server_name}**.\\n\\n[Clique aqui para escolher uma nova]({reset_link})\\n\\nO link vale {reset_minutes} minutos e só pode ser usado uma vez. Se não foi você, ignore esta mensagem.\", \"color\": 3447003}]}",
    "WEBHOOK_EXPIRATION_MESSAGE_TEMPLATE": '{"content": "Atenção: O acesso de {username} expira em {days} dias. Para renovar, acesse: {payment_link}"}',
    "WEBHOOK_RENEWAL_MESSAGE_TEMPLATE": '{"content": "✅ A assinatura de {username} foi renovada. Novo vencimento: {new_date}."}',
    "WEBHOOK_REACTIVATION_MESSAGE_TEMPLATE": '{"content": "✅ A assinatura de {username} foi reativada. Novo vencimento: {new_date}. Link de acesso: {invite_link}"}',
    "WEBHOOK_TRIAL_END_MESSAGE_TEMPLATE": '{"content": "O período de teste para {username} terminou. Para renovar, acesse: {payment_link}"}',
    "WEBHOOK_TRIAL_EXTENDED_MESSAGE_TEMPLATE": '{"content": "⏳ O período de teste de {username} foi estendido até {new_date}."}',
    "WEBHOOK_CREDENTIALS_MESSAGE_TEMPLATE": '{"content": "A conta de {username} foi criada novamente no servidor. Usuário: {new_username} — Senha: {new_password}"}',
    "WEBHOOK_PASSWORD_RESET_MESSAGE_TEMPLATE": "{\"content\": \"{username} pediu para redefinir a senha. Link válido {reset_minutes} min: {reset_link}\"}",
    "TELEGRAM_BULK_MESSAGE_TEMPLATE": "📢 *Aviso do Servidor*\n\nOlá {name},\n\n{message}",
    "DISCORD_BULK_MESSAGE_TEMPLATE": '{"content": "<@{discord_user_id}>", "embeds": [{"title": "Aviso do Servidor", "description": "{message}", "color": 3447003}]}',
    "WEBHOOK_BULK_MESSAGE_TEMPLATE": '{"phone": "{phone_number}@s.whatsapp.net", "message": "{message}"}',
    # --- WhatsApp (texto simples; sem markdown do Telegram nem JSON) ---
    "WHATSAPP_EXPIRATION_MESSAGE_TEMPLATE": "Olá {name}, {greeting}!\n\nSeu acesso vence em {days} dia(s), no dia {date}.\nPlano: {plan_name}\nValor: {price}\n\nRenove aqui para não perder o acesso:\n{payment_link}",
    "WHATSAPP_RENEWAL_MESSAGE_TEMPLATE": "✅ Renovação confirmada!\n\nOlá {name}, sua assinatura foi renovada com sucesso.\nNovo vencimento: {new_date}\n\nBom entretenimento!",
    "WHATSAPP_REACTIVATION_MESSAGE_TEMPLATE": "✅ Conta reativada!\n\nOlá {name}, sua conta foi reativada.\nNovo vencimento: {new_date}\n\nUse o link abaixo para voltar a acessar:\n{invite_link}",
    "WHATSAPP_TRIAL_END_MESSAGE_TEMPLATE": "⌛ Seu período de teste terminou\n\nOlá {name}, esperamos que tenha gostado!\nPara continuar com acesso, faça sua assinatura aqui:\n{payment_link}",
    "WHATSAPP_TRIAL_EXTENDED_MESSAGE_TEMPLATE": "⏳ Período de teste estendido\n\nOlá {name}, seu período de teste no {server_name} foi estendido.\nAgora ele vai até {new_date}.\n\nBom entretenimento!",
    "WHATSAPP_CREDENTIALS_MESSAGE_TEMPLATE": "🔑 Acesso restaurado\n\nOlá {name}, sua conta no {server_name} foi criada novamente, por isso a senha mudou.\n\nUsuário: {new_username}\nSenha: {new_password}\n\nEntre em {invite_link} e troque a senha assim que puder.",
    "WHATSAPP_PASSWORD_RESET_MESSAGE_TEMPLATE": "🔑 Redefinir a senha\n\nOlá {name}, você pediu para redefinir a senha da sua conta no {server_name}.\n\nAbra este link e escolha uma nova:\n{reset_link}\n\nVale {reset_minutes} minutos e só pode ser usado uma vez. Se não foi você, ignore esta mensagem.",
    "WHATSAPP_BULK_MESSAGE_TEMPLATE": "📢 Aviso do servidor\n\nOlá {name},\n\n{message}",
    # --- Pedidos no Overseerr/Jellyseerr ---
    "TELEGRAM_MEDIA_REQUEST_MESSAGE_TEMPLATE": "🍿 *Novo Conteúdo Solicitado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n🚀 *Acesse o pedido:*\n{media_url}",
    "WHATSAPP_MEDIA_REQUEST_MESSAGE_TEMPLATE": "🍿 *Novo Conteúdo Solicitado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n🚀 *Acesse o pedido:*\n{media_url}",
    "DISCORD_MEDIA_REQUEST_MESSAGE_TEMPLATE": '{"embeds": [{"title": "🍿 Novo Conteúdo Solicitado", "description": "**{title}**\\n\\n📝 {overview}", "color": 10181046, "fields": [{"name": "👤 Usuário", "value": "{username}", "inline": true}, {"name": "📊 Status", "value": "{status}", "inline": true}], "url": "{media_url}"}]}',
    # --- Templates por TIPO de evento do Seerr ---
    # Mensagens distintas para pendente / aprovado / disponível / recusado.
    # Se algum ficar em branco, recai no MEDIA_REQUEST genérico.
    "TELEGRAM_MEDIA_PENDING_MESSAGE_TEMPLATE": "🍿 *Novo Conteúdo Solicitado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n🚀 *Acesse o pedido:*\n{media_url}",
    "WHATSAPP_MEDIA_PENDING_MESSAGE_TEMPLATE": "🍿 *Novo Conteúdo Solicitado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n🚀 *Acesse o pedido:*\n{media_url}",
    "DISCORD_MEDIA_PENDING_MESSAGE_TEMPLATE": '{"embeds": [{"title": "🍿 Novo Conteúdo Solicitado", "description": "**{title}**\\n\\n📝 {overview}", "color": 10181046, "fields": [{"name": "👤 Usuário", "value": "{username}", "inline": true}, {"name": "📊 Status", "value": "{status}", "inline": true}], "url": "{media_url}"}]}',
    "TELEGRAM_MEDIA_APPROVED_MESSAGE_TEMPLATE": "✅ *Pedido Aprovado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n🚀 *Acompanhe aqui:*\n{media_url}",
    "WHATSAPP_MEDIA_APPROVED_MESSAGE_TEMPLATE": "✅ *Pedido Aprovado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n🚀 *Acompanhe aqui:*\n{media_url}",
    "DISCORD_MEDIA_APPROVED_MESSAGE_TEMPLATE": '{"embeds": [{"title": "✅ Pedido Aprovado", "description": "**{title}**\\n\\n📝 {overview}", "color": 10181046, "fields": [{"name": "👤 Usuário", "value": "{username}", "inline": true}, {"name": "📊 Status", "value": "{status}", "inline": true}], "url": "{media_url}"}]}',
    "TELEGRAM_MEDIA_AVAILABLE_MESSAGE_TEMPLATE": "🎉 *Já Está Disponível!*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n▶️ *Assista agora:*\n{media_url}",
    "WHATSAPP_MEDIA_AVAILABLE_MESSAGE_TEMPLATE": "🎉 *Já Está Disponível!*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\n▶️ *Assista agora:*\n{media_url}",
    "DISCORD_MEDIA_AVAILABLE_MESSAGE_TEMPLATE": '{"embeds": [{"title": "🎉 Já Está Disponível!", "description": "**{title}**\\n\\n📝 {overview}", "color": 10181046, "fields": [{"name": "👤 Usuário", "value": "{username}", "inline": true}, {"name": "📊 Status", "value": "{status}", "inline": true}], "url": "{media_url}"}]}',
    "TELEGRAM_MEDIA_DECLINED_MESSAGE_TEMPLATE": "❌ *Pedido Recusado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\nℹ️ *Ver detalhes:*\n{media_url}",
    "WHATSAPP_MEDIA_DECLINED_MESSAGE_TEMPLATE": "❌ *Pedido Recusado*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\nℹ️ *Ver detalhes:*\n{media_url}",
    "DISCORD_MEDIA_DECLINED_MESSAGE_TEMPLATE": '{"embeds": [{"title": "❌ Pedido Recusado", "description": "**{title}**\\n\\n📝 {overview}", "color": 10181046, "fields": [{"name": "👤 Usuário", "value": "{username}", "inline": true}, {"name": "📊 Status", "value": "{status}", "inline": true}], "url": "{media_url}"}]}',
    "TELEGRAM_MEDIA_FAILED_MESSAGE_TEMPLATE": "⚠️ *Falha no Pedido*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\nℹ️ *Ver detalhes:*\n{media_url}",
    "WHATSAPP_MEDIA_FAILED_MESSAGE_TEMPLATE": "⚠️ *Falha no Pedido*\n\n*{title}*\n\n📝 {overview}\n\n━━━━━━━━━━━━━━━\n👤 *Usuário:* {username}\n📊 *Status:* {status}\n━━━━━━━━━━━━━━━\n\nℹ️ *Ver detalhes:*\n{media_url}",
    "DISCORD_MEDIA_FAILED_MESSAGE_TEMPLATE": '{"embeds": [{"title": "⚠️ Falha no Pedido", "description": "**{title}**\\n\\n📝 {overview}", "color": 10181046, "fields": [{"name": "👤 Usuário", "value": "{username}", "inline": true}, {"name": "📊 Status", "value": "{status}", "inline": true}], "url": "{media_url}"}]}',

    # --- NOTIFICAÇÃO PUSH (navegador e celular) ---
    # ⚠️ Uma notificação push tem DUAS partes, e o sistema operacional trata-as
    # de forma diferente: o TÍTULO aparece em negrito e é o que se lê de
    # relance; o CORPO é cortado ao fim de duas linhas na tela de bloqueio. Por
    # isso os textos aqui são curtos — não é o mesmo tipo de mensagem que a do
    # Telegram, e reaproveitar aquela dava um aviso ilegível.
    "PUSH_EXPIRATION_TITLE_TEMPLATE": "Seu acesso vence em {days} dia(s)",
    "PUSH_EXPIRATION_MESSAGE_TEMPLATE": "Olá {name}! Vence em {date}, no valor de {price}. Toque para renovar.",
    "PUSH_RENEWAL_TITLE_TEMPLATE": "Renovação confirmada",
    "PUSH_RENEWAL_MESSAGE_TEMPLATE": "Tudo certo, {name}! Seu novo vencimento é {new_date}.",
    "PUSH_REACTIVATION_TITLE_TEMPLATE": "Conta reativada",
    "PUSH_REACTIVATION_MESSAGE_TEMPLATE": "Olá {name}, seu acesso ao {server_name} voltou. Novo vencimento: {new_date}.",
    "PUSH_TRIAL_END_TITLE_TEMPLATE": "Seu período de teste terminou",
    "PUSH_TRIAL_END_MESSAGE_TEMPLATE": "{name}, para continuar com acesso ao {server_name}, toque aqui e faça sua assinatura.",
    "PUSH_TRIAL_EXTENDED_TITLE_TEMPLATE": "Seu período de teste foi estendido",
    "PUSH_TRIAL_EXTENDED_MESSAGE_TEMPLATE": "{name}, agora seu teste no {server_name} vai até {new_date}.",
    "PUSH_BULK_TITLE_TEMPLATE": "Aviso do servidor",
    "PUSH_BULK_MESSAGE_TEMPLATE": "{message}",
    "PUSH_MEDIA_REQUEST_TITLE_TEMPLATE": "Novo conteúdo solicitado",
    "PUSH_MEDIA_REQUEST_MESSAGE_TEMPLATE": "{title} — {status}",
    "PUSH_MEDIA_PENDING_TITLE_TEMPLATE": "Pedido enviado",
    "PUSH_MEDIA_PENDING_MESSAGE_TEMPLATE": "{title} está aguardando aprovação.",
    "PUSH_MEDIA_APPROVED_TITLE_TEMPLATE": "Pedido aprovado",
    "PUSH_MEDIA_APPROVED_MESSAGE_TEMPLATE": "{title} foi aprovado e já entrou na fila de download.",
    "PUSH_MEDIA_AVAILABLE_TITLE_TEMPLATE": "Já está disponível",
    "PUSH_MEDIA_AVAILABLE_MESSAGE_TEMPLATE": "{title} já pode ser assistido no {server_name}.",
    "PUSH_MEDIA_DECLINED_TITLE_TEMPLATE": "Pedido recusado",
    "PUSH_MEDIA_DECLINED_MESSAGE_TEMPLATE": "{title} não foi aprovado.",
    "PUSH_MEDIA_FAILED_TITLE_TEMPLATE": "Falha no pedido",
    "PUSH_MEDIA_FAILED_MESSAGE_TEMPLATE": "Algo deu errado com {title}. Fale com o administrador.",
}


def _data_local(valor, formato='%d/%m/%Y'):
    """Uma data — objeto ou texto ISO — no fuso de quem a vai ler.

    ⚠️ As datas do painel são guardadas em UTC e lidas por pessoas que vivem
    noutro fuso. Formatá-las sem converter é o mesmo engano que o campo de
    vencimento já custou: a hora certa, no fuso errado.
    """
    if not isinstance(valor, datetime):
        try:
            valor = datetime.fromisoformat(str(valor))
        except (ValueError, TypeError):
            return str(valor)[:10]
    if valor.tzinfo:
        valor = valor.astimezone(get_localzone())
    return valor.strftime(formato)


def get_greeting():
    current_hour = datetime.now(get_localzone()).hour
    if 5 <= current_hour < 12: return _("Bom dia")
    elif 12 <= current_hour < 18: return _("Boa tarde")
    else: return _("Boa noite")


# Mapa: tipo de notificação do Seerr -> sufixo do template no config.
# Os tipos vêm do enum 'Notification' do Seerr (server/lib/notifications/index.ts).
# Eventos não mapeados usam o template genérico MEDIA_REQUEST, para que uma
# notificação nova do Seerr nunca fique sem mensagem.
SEERR_EVENT_TEMPLATES = {
    "MEDIA_PENDING": "MEDIA_PENDING",
    "MEDIA_APPROVED": "MEDIA_APPROVED",
    "MEDIA_AUTO_APPROVED": "MEDIA_APPROVED",   # aprovação automática usa o mesmo texto
    "MEDIA_AVAILABLE": "MEDIA_AVAILABLE",
    "MEDIA_DECLINED": "MEDIA_DECLINED",
    "MEDIA_FAILED": "MEDIA_FAILED",
}


# 🛡️ **Estes dois eventos NUNCA vão por push**, e é uma decisão, não um
# esquecimento. A notificação push aparece na tela de bloqueio do celular, à
# vista de quem estiver por perto, e fica guardada pelo sistema operacional
# fora do painel. O `credentials` leva a senha nova de uma conta recriada — é o
# único sítio do painel por onde uma senha viaja — e o `password_reset` leva um
# link que É a credencial enquanto vive. Ambos continuam a ir pelos canais que
# a pessoa registou, onde há pelo menos uma conversa a proteger.
EVENTOS_SEM_PUSH = ('credentials', 'password_reset')


def resolve_seerr_template_key(canal, notification_type, parte='MESSAGE'):
    """
    Devolve a chave de configuração do template a usar para um dado canal e tipo
    de evento — por exemplo ('TELEGRAM', 'MEDIA_APPROVED') ->
    'TELEGRAM_MEDIA_APPROVED_MESSAGE_TEMPLATE'.

    Se o evento não tiver template próprio (ou o administrador o tiver deixado em
    branco), recai no template genérico de pedidos.

    `parte` é 'MESSAGE' em todos os canais menos no push, que tem também um
    'TITLE' — o texto em negrito que o sistema operacional mostra em primeiro.
    """
    sufixo = SEERR_EVENT_TEMPLATES.get(str(notification_type or "").upper())
    if not sufixo:
        return f"{canal}_MEDIA_REQUEST_{parte}_TEMPLATE"
    return f"{canal}_{sufixo}_{parte}_TEMPLATE"


class NotifierManager:
    def __init__(self, link_shortener_service=None, socketio_instance=None):
        self.link_shortener = link_shortener_service
        self.socketio = socketio_instance
        self._bot = None
        # Uma única sessão HTTP para todo o processo: ver _build_http_session().
        self._http = _build_http_session()

    def _get_bot(self, config=None):
        config = config if config is not None else load_or_create_config()
        token = config.get("TELEGRAM_BOT_TOKEN")
        if not token: 
            return None
        if self._bot is None or self._bot.token != token:
            self._bot = telebot.TeleBot(token, threaded=False)
        return self._bot

    def _convert_md_to_html(self, text):
        """
        Converte o markdown simples dos templates para o HTML que o Telegram
        aceita.

        🐛 Os URLs são postos de lado antes da conversão e repostos no fim. Sem
        isso, um link legítimo com underscores — muito comum em capas do TMDb ou
        em links de pagamento — era destruído:
        'https://x.com/a_b_c' virava 'https://x.com/a<i>b</i>c', e o utilizador
        recebia um link partido (ou o Telegram recusava a mensagem inteira por
        HTML inválido).
        """
        if not text: return ""

        urls = []

        def _stash(match):
            urls.append(match.group(0))
            return f"\x00U{len(urls) - 1}\x00"

        text = _URL_RE.sub(_stash, text)

        # Uso do modificador flag in-line (?s) = re.DOTALL para que o .* cubra quebras de linha de forma contida
        text = re.sub(r'\*(.*?)\*', r'<b>\1</b>', text, flags=re.DOTALL)
        text = re.sub(r'_(.*?)_', r'<i>\1</i>', text, flags=re.DOTALL)
        text = re.sub(r'`(.*?)`', r'<code>\1</code>', text, flags=re.DOTALL)

        def _restore(match):
            indice = int(match.group(1))
            return urls[indice] if indice < len(urls) else match.group(0)

        return re.sub(r'\x00U(\d+)\x00', _restore, text)

    def _format_template(self, template_str, placeholders, is_json=False, use_html_escape=False):
        if not template_str: return None
        
        safe_placeholders = {}
        for k, v in placeholders.items():
            val = str(v) if v is not None else ''
            if use_html_escape and not any(sub in k.lower() for sub in ['link', 'url']):
                val = html.escape(val)
            safe_placeholders[k] = val
            
        if not is_json:
            return substitute_placeholders(template_str, safe_placeholders)

        # Nos templates JSON o valor tem de ser escapado como conteúdo de string
        # JSON (aspas, barras, quebras de linha), senão um nome com aspas parte
        # o payload enviado ao Discord.
        output = substitute_placeholders(
            template_str, safe_placeholders, transform=lambda v: json.dumps(v)[1:-1]
        )
        try:
            return json.loads(output)
        except Exception as e:
            logger.error(f"Falha ao processar template JSON: {e}")
            return None

    @staticmethod
    def _split_message(text, limit):
        """
        Parte uma mensagem longa em pedaços dentro do limite da API, cortando de
        preferência numa quebra de linha para não partir palavras a meio.

        O Telegram recusa (erro 400) qualquer mensagem acima do limite — sem esta
        divisão, uma mensagem em massa um pouco mais longa simplesmente não era
        entregue a ninguém.
        """
        if not text:
            return []
        if len(text) <= limit:
            return [text]

        pedacos = []
        restante = text
        while len(restante) > limit:
            corte = restante.rfind('\n', 0, limit)
            if corte <= limit // 2:  # sem quebra de linha útil: corta no limite
                corte = limit
            pedacos.append(restante[:corte].rstrip())
            restante = restante[corte:].lstrip('\n')
        if restante:
            pedacos.append(restante)
        # Um pedaco vazio (bloco so com espacos) seria recusado pela API.
        return [p for p in pedacos if p.strip()]

    def _telegram_call(self, action, request_id, media_user_id=None, max_retries=3):
        """
        Executa uma chamada à API do Telegram tratando o limite de ritmo (429) e
        o bloqueio do bot pelo utilizador (403).

        Levanta NotificationError quando a mensagem NÃO foi entregue — incluindo
        o caso de as tentativas por 429 se esgotarem, que antes terminava em
        silêncio e era contabilizado como sucesso.
        """
        for attempt in range(max_retries):
            try:
                action()
                return
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    retry_after = (e.result_json or {}).get('parameters', {}).get('retry_after', 5)
                    logger.warning(
                        f"[ID: {request_id}] Limite de ritmo do Telegram atingido. A aguardar {retry_after}s... "
                        f"(Tentativa {attempt + 1}/{max_retries})"
                    )
                    self._sleep(retry_after + 1)
                    continue

                if e.error_code == 403:
                    if media_user_id:
                        logger.warning(f"[ID: {request_id}] Bot bloqueado pelo utilizador {media_user_id}. A remover contacto.")
                        from .. import extensions
                        extensions.data_manager.update_user_profile(media_user_id, {'telegram_id': None, 'telegram_user': None})
                    raise NotificationError(_("O usuário bloqueou o bot no Telegram."))

                logger.error(f"[ID: {request_id}] Erro Telegram: {e.description}")
                raise NotificationError(f"Telegram: {e.description}")
            except NotificationError:
                raise
            except Exception as e:
                logger.error(f"[ID: {request_id}] Erro inesperado ao enviar Telegram: {e}")
                raise NotificationError(f"Telegram: {e}")

        raise NotificationError(
            _("O limite de ritmo do Telegram não permitiu a entrega após %(n)d tentativas.", n=max_retries)
        )

    def _send_telegram_notification(self, message, chat_id, request_id, reply_markup=None,
                                    media_user_id=None, photo_url=None, config=None):
        bot = self._get_bot(config)
        if not bot:
            # O canal está ligado mas sem token: comunicar isto como falha evita
            # que o envio em massa contabilize entregas que nunca aconteceram.
            raise NotificationError(_("O token do bot do Telegram não está configurado."))
        
        html_message = self._convert_md_to_html(message)

        # A legenda de uma foto tem um limite MUITO mais curto (1024) do que uma
        # mensagem de texto (4096). Em vez de deixar a API recusar tudo, enviamos
        # a imagem sozinha e o texto completo logo a seguir.
        if photo_url and len(html_message) > TELEGRAM_MAX_CAPTION_LEN:
            try:
                self._telegram_call(
                    lambda: bot.send_photo(chat_id=chat_id, photo=photo_url),
                    request_id, media_user_id
                )
            except NotificationError as e:
                # A capa é acessória: se falhar, a mensagem ainda tem de sair.
                logger.warning(f"[ID: {request_id}] Não foi possível enviar a imagem do Telegram: {e}")
            photo_url = None

        if photo_url:
            self._telegram_call(
                lambda: bot.send_photo(
                    chat_id=chat_id, photo=photo_url, caption=html_message,
                    parse_mode='HTML', reply_markup=reply_markup
                ),
                request_id, media_user_id
            )
            return

        pedacos = self._split_message(html_message, TELEGRAM_MAX_MESSAGE_LEN)
        for indice, pedaco in enumerate(pedacos):
            # O teclado (botão de pagamento) vai só no último pedaço, onde a
            # chamada para a ação faz sentido.
            markup = reply_markup if indice == len(pedacos) - 1 else None
            self._telegram_call(
                lambda p=pedaco, m=markup: bot.send_message(
                    chat_id=chat_id, text=p, parse_mode='HTML',
                    reply_markup=m, disable_web_page_preview=True
                ),
                request_id, media_user_id
            )

    def _sleep(self, seconds):
        """
        Pausa cooperativa: sob gevent/SocketIO um `time.sleep` bloquearia todo o
        worker (e com ele os eventos em tempo real da consola de envio).
        """
        if self.socketio:
            self.socketio.sleep(seconds)
        else:
            time.sleep(seconds)

    # ==========================================================================
    # WHATSAPP (APIs NÃO-OFICIAIS: Evolution API, GOWA, Baileys, etc.)
    # ==========================================================================

    @staticmethod
    def normalize_phone(phone, config=None):
        """
        Normaliza um número para o formato esperado pelas APIs de WhatsApp:
        apenas dígitos, com código de país.

        Os números são introduzidos por pessoas e chegam em todos os formatos
        possíveis — "+55 (11) 98888-7777", "011988887777", "5511988887777". Sem
        normalização, o mesmo contacto falharia ou duplicaria consoante como foi
        escrito.
        """
        if not phone:
            return None

        digits = re.sub(r'\D', '', str(phone))
        if not digits:
            return None

        # Remove o prefixo internacional "00" (ex: 005511... -> 5511...)
        if digits.startswith('00'):
            digits = digits[2:]

        # Heurística para números guardados SEM o código do país.
        #
        # 🐛 CUIDADO: esta heurística é deliberadamente conservadora. Uma versão
        # anterior aplicava-a a qualquer número de 10-11 dígitos, o que corrompia
        # números internacionais legítimos — um número dos EUA como +1 415 555 2671
        # (11 dígitos) tornava-se "5514155552671", um número brasileiro inexistente.
        #
        # Só acrescentamos o código do país quando o número tem MESMO o formato
        # nacional esperado. Para o Brasil (DDI 55): 10 dígitos (fixo com DDD) ou
        # 11 (telemóvel com DDD e o 9 inicial), e o DDD tem de ser válido (11-99).
        # O config é recebido já carregado nos envios em massa: reler o
        # config.json uma vez por número seria um acesso a disco por utilizador.
        config = config if config is not None else load_or_create_config()
        default_cc = str(config.get("WHATSAPP_DEFAULT_COUNTRY_CODE", "55") or "").strip()

        if default_cc and len(digits) in (10, 11):
            ddd = int(digits[:2])
            is_national_format = 11 <= ddd <= 99
            # Um telemóvel brasileiro com 11 dígitos tem sempre o 9 na 3ª posição.
            if default_cc == "55" and len(digits) == 11 and digits[2] != '9':
                is_national_format = False
            if is_national_format:
                digits = f"{default_cc}{digits}"

        return digits

    def _build_whatsapp_request(self, config, phone, message):
        """
        Monta o pedido HTTP conforme o provedor escolhido.

        Cada API não-oficial tem o seu próprio formato; centralizamos aqui as
        diferenças para que o resto do sistema não precise de saber qual está em uso.
        Devolve (url, headers, payload).
        """
        provider = (config.get("WHATSAPP_PROVIDER") or "evolution").strip().lower()
        base_url = (config.get("WHATSAPP_API_URL") or "").strip().rstrip('/')
        api_key = (config.get("WHATSAPP_API_KEY") or "").strip()
        instance = (config.get("WHATSAPP_INSTANCE") or "").strip()

        if not base_url:
            raise ValueError(_("O URL da API de WhatsApp não está configurado."))

        if provider == "evolution":
            # Evolution API v2: POST /message/sendText/{instance}
            if not instance:
                raise ValueError(_("O nome da instância é obrigatório para a Evolution API."))
            url = f"{base_url}/message/sendText/{instance}"
            headers = {"apikey": api_key, "Content-Type": "application/json"}
            payload = {"number": phone, "text": message}

        elif provider == "gowa":
            # GOWA / go-whatsapp-web-multidevice: POST /send/message
            #
            # 🔑 AUTENTICAÇÃO: ao contrário da Evolution API (que usa uma chave no
            # cabeçalho 'apikey'), o GOWA usa BASIC AUTH, configurado na instância
            # através de APP_BASIC_AUTH=utilizador:senha (ou --basic-auth).
            # Muitas instalações correm sem autenticação nenhuma — nesse caso, o
            # campo da chave fica simplesmente vazio.
            #
            # Aceitamos os dois formatos no mesmo campo:
            #   • "utilizador:senha" -> Basic Auth (o correto para o GOWA)
            #   • qualquer outro     -> Bearer (proxies/forks que exijam token)
            url = f"{base_url}/send/message"
            headers = {"Content-Type": "application/json"}
            if api_key:
                if ':' in api_key:
                    encoded = base64.b64encode(api_key.encode('utf-8')).decode('ascii')
                    headers["Authorization"] = f"Basic {encoded}"
                else:
                    headers["Authorization"] = f"Bearer {api_key}"
            payload = {"phone": f"{phone}@s.whatsapp.net", "message": message}

        elif provider == "waha":
            # WAHA (WhatsApp HTTP API): POST /api/sendText
            url = f"{base_url}/api/sendText"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["X-Api-Key"] = api_key
            payload = {
                "session": instance or "default",
                "chatId": f"{phone}@c.us",
                "text": message,
            }

        else:
            # 'custom': o administrador define o corpo através de um template JSON,
            # cobrindo qualquer API não listada acima (incl. Baileys caseiro).
            url = base_url
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            tpl = config.get("WHATSAPP_CUSTOM_PAYLOAD_TEMPLATE") or '{"phone": "{phone}", "message": "{message}"}'
            payload = self._format_template(tpl, {"phone": phone, "message": message}, is_json=True)
            if payload is None:
                raise ValueError(_("O template JSON personalizado do WhatsApp é inválido."))

        return url, headers, payload

    def _build_whatsapp_media_request(self, config, phone, caption, image_url):
        """
        Monta o pedido de envio de IMAGEM com legenda, conforme o provedor.
        Cada API não-oficial tem o seu próprio endpoint e formato para média.
        """
        provider = (config.get("WHATSAPP_PROVIDER") or "evolution").strip().lower()
        base_url = (config.get("WHATSAPP_API_URL") or "").strip().rstrip('/')
        api_key = (config.get("WHATSAPP_API_KEY") or "").strip()
        instance = (config.get("WHATSAPP_INSTANCE") or "").strip()

        if not base_url:
            raise ValueError(_("O URL da API de WhatsApp não está configurado."))

        if provider == "evolution":
            url = f"{base_url}/message/sendMedia/{instance}"
            headers = {"apikey": api_key, "Content-Type": "application/json"}
            payload = {"number": phone, "mediatype": "image", "media": image_url, "caption": caption}

        elif provider == "gowa":
            url = f"{base_url}/send/image"
            headers = {"Content-Type": "application/json"}
            if api_key:
                if ':' in api_key:
                    headers["Authorization"] = f"Basic {base64.b64encode(api_key.encode('utf-8')).decode('ascii')}"
                else:
                    headers["Authorization"] = f"Bearer {api_key}"
            payload = {"phone": f"{phone}@s.whatsapp.net", "caption": caption, "image_url": image_url}

        elif provider == "waha":
            url = f"{base_url}/api/sendImage"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["X-Api-Key"] = api_key
            payload = {
                "session": instance or "default",
                "chatId": f"{phone}@c.us",
                "file": {"url": image_url},
                "caption": caption,
            }

        else:
            # 'custom': sem forma fiável de saber o endpoint de média, por isso
            # deixamos o chamador fazer fallback para texto.
            raise ValueError("O provedor personalizado não tem envio de imagem definido.")

        return url, headers, payload

    def _send_whatsapp_notification(self, phone, message, request_id, config, image_url=None):
        """
        Envia uma mensagem de WhatsApp através da API não-oficial configurada.

        Se 'image_url' for indicado, tenta enviar como imagem com legenda. Nem
        todos os provedores suportam envio de média — se falhar, fazemos fallback
        para texto simples, porque é preferível o utilizador receber a mensagem
        sem capa do que não receber nada.
        """
        normalized = self.normalize_phone(phone, config)
        if not normalized:
            logger.warning(f"[ID: {request_id}] Número de WhatsApp inválido, envio ignorado: {mask_phone(phone)}")
            raise NotificationError(_("Número de WhatsApp inválido."))

        if image_url:
            try:
                url, headers, payload = self._build_whatsapp_media_request(config, normalized, message, image_url)
                response = self._http.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
                response.raise_for_status()
                logger.info(f"[ID: {request_id}] Mensagem de WhatsApp COM IMAGEM enviada para {mask_phone(normalized)}.")
                return
            except Exception as e:
                logger.warning(
                    f"[ID: {request_id}] Falha ao enviar imagem por WhatsApp ({e}). "
                    f"A reenviar apenas como texto."
                )
                # segue para o envio de texto abaixo

        url, headers, payload = self._build_whatsapp_request(config, normalized, message)

        try:
            response = self._http.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            logger.info(f"[ID: {request_id}] Mensagem de WhatsApp enviada para {mask_phone(normalized)}.")
        except requests.exceptions.RequestException as e:
            # Mesmo cuidado do webhook genérico: 'bool(response)' é False em
            # qualquer erro HTTP, por isso comparamos explicitamente com None para
            # não perder o corpo da resposta — que é onde estas APIs explicam o
            # motivo real da falha (sessão desligada, número inexistente, etc.).
            if hasattr(e, 'response') and e.response is not None:
                body = e.response.text.strip() if e.response.text else "(corpo vazio)"
                # 🔒 O corpo de erro destas APIs costuma ECOAR o número enviado
                # (ex: {"error":"5521985852539 is not on WhatsApp"}). Sem esta
                # substituição, o número completo voltaria a aparecer no log mesmo
                # com o mascaramento aplicado no resto da linha.
                body = body.replace(normalized, mask_phone(normalized))
                detail = f"HTTP {e.response.status_code} - {body[:300]}"
            else:
                detail = "Sem resposta do servidor (falha de conexão/timeout)"
            logger.error(f"[ID: {request_id}] Falha no envio de WhatsApp para {mask_phone(normalized)}: {detail}")
            raise NotificationError(f"WhatsApp: {detail}")

    def test_whatsapp_connection(self, phone=None):
        """
        Testa a ligação à API de WhatsApp. Se for indicado um número, envia uma
        mensagem real de teste; caso contrário, valida apenas a configuração.
        """
        config = load_or_create_config()
        if not config.get("WHATSAPP_ENABLED"):
            return {"success": False, "message": _("O canal de WhatsApp está desativado.")}

        try:
            if not phone:
                # Só valida se a configuração está completa e coerente.
                self._build_whatsapp_request(config, "5511999999999", "teste")
                return {"success": True, "message": _("Configuração válida. Informe um número para enviar uma mensagem de teste real.")}

            self._send_whatsapp_notification(
                phone,
                _("✅ Teste de conexão do %(app)s. Se recebeu esta mensagem, o WhatsApp está configurado corretamente!",
                  app=config.get("APP_TITLE", "Painel Plex")),
                str(uuid.uuid4()),
                config
            )
            return {"success": True, "message": _("Mensagem de teste enviada com sucesso!")}
        except Exception as e:
            return {"success": False, "message": str(e)}

    @staticmethod
    def _describe_http_error(e):
        """
        Resume um erro de requests numa linha legível.

        `bool(response)` do requests é False para qualquer status de erro
        (4xx/5xx), por isso comparamos explicitamente com None — de outro modo
        escondíamos o corpo da resposta justamente quando ele é mais necessário,
        que é onde as APIs explicam o motivo real da recusa.
        """
        response = getattr(e, 'response', None)
        if response is None:
            return _("Sem resposta do servidor (falha de conexão/timeout).")
        body = response.text.strip() if response.text else "(corpo de resposta vazio)"
        return f"HTTP {response.status_code} - {truncate(body, 300)}"

    @staticmethod
    def _webhook_headers(config):
        headers = {'Content-Type': 'application/json'}
        auth_header = config.get("WEBHOOK_AUTHORIZATION_HEADER")

        if auth_header:
            if ":" in auth_header:
                try:
                    key, value = auth_header.split(":", 1)
                    headers[key.strip()] = value.strip()
                except ValueError:
                    headers['Authorization'] = auth_header.strip()
            else:
                headers['Authorization'] = auth_header.strip()

        return headers

    def _send_webhook_notification(self, payload, request_id, config):
        webhook_url = config.get("WEBHOOK_URL")
        if not webhook_url: return

        try:
            response = self._http.post(
                webhook_url, json=payload, headers=self._webhook_headers(config), timeout=HTTP_TIMEOUT
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            detalhe = self._describe_http_error(e)
            # 🔒 O URL pode trazer credenciais embutidas (http://user:senha@host):
            # sem mascarar, ficavam em claro no ficheiro de log.
            logger.error(
                f"[ID: {request_id}] Falha no Webhook ({mask_url_credentials(webhook_url)}): {detalhe}"
            )
            raise NotificationError(f"Webhook: {detalhe}")

    def _send_discord_notification(self, payload, request_id, config):
        webhook_url = config.get("DISCORD_WEBHOOK_URL")
        if not webhook_url: return

        payload = self._enforce_discord_limits(payload)

        try:
            response = self._http.post(
                webhook_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=HTTP_TIMEOUT
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            # 🔒 O URL de um webhook do Discord CONTÉM o token que autoriza a
            # publicar no canal. A mensagem de erro do requests inclui o URL
            # completo, por isso registamos apenas o detalhe da resposta — quem
            # tiver acesso ao log não fica com a chave do canal.
            detalhe = self._describe_http_error(e)
            logger.error(f"[ID: {request_id}] Falha no Discord: {detalhe}")
            raise NotificationError(f"Discord: {detalhe}")

    @staticmethod
    def _enforce_discord_limits(payload):
        """
        Corta os campos que excedem os limites do Discord.

        O Discord recusa o pedido inteiro (400) se o `content` passar de 2000
        caracteres ou a descrição de um embed de 4096 — uma mensagem em massa um
        pouco mais longa deixava de ser entregue a toda a gente, sem aviso útil.
        """
        if not isinstance(payload, dict):
            return payload

        if isinstance(payload.get('content'), str):
            payload['content'] = truncate(payload['content'], DISCORD_MAX_CONTENT_LEN)

        for embed in payload.get('embeds') or []:
            if isinstance(embed, dict) and isinstance(embed.get('description'), str):
                embed['description'] = truncate(embed['description'], DISCORD_MAX_EMBED_DESCRIPTION_LEN)

        return payload

    def _get_price_and_plan(self, config, user_screen_limit):
        from flask_babel import ngettext, gettext as _
        
        screen_prices = config.get("SCREEN_PRICES", {})
        renewal_price_str = screen_prices.get(str(user_screen_limit), config.get("RENEWAL_PRICE", "0.00"))
        
        try:
            price_value = float(renewal_price_str.replace(',', '.'))
            formatted_price = f"R$ {price_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except ValueError: 
            formatted_price = "N/A"
            
        plan_name = ngettext('%(num)d Tela', '%(num)d Telas', user_screen_limit) % {'num': user_screen_limit} if user_screen_limit > 0 else _("Plano Padrão")
        return formatted_price, plan_name

    def _get_payment_link(self, config, event_type, user_profile):
        if event_type in ['renewal', 'reactivation'] or not user_profile.get('payment_token'):
            return None

        try:
            # 🛡️ Renovar a validade AQUI, no momento do envio, é o que torna a
            # expiração do token utilizável: o link que a pessoa acaba de
            # receber está sempre bom, e o que ela recebeu há três meses — e
            # que continua no histórico do Telegram para sempre — já não abre.
            # Se o perfil tiver desaparecido entretanto, fica-se pelo token que
            # já lá estava: um link sem validade é melhor do que uma
            # notificação de vencimento sem link nenhum.
            from ..extensions import data_manager as _dm
            token = (_dm.garantir_payment_token(user_profile.get('media_user_id'))
                     or user_profile['payment_token'])
            app_base_url = config.get("APP_BASE_URL", "").strip().rstrip('/')
            
            # 🚀 OTIMIZAÇÃO: Prioriza sempre a APP_BASE_URL.
            # O url_for via Scheduler em background costuma falhar pois não tem o SERVER_NAME injetado na thread.
            if app_base_url:
                long_url = f"{app_base_url}/pay/{token}"
            else:
                from flask import url_for
                # Fallback perigoso se executado fora de uma web request (ex: Tarefas CRON)
                long_url = url_for('main.payment_page', token=token, _external=True)

            if config.get("ENABLE_LINK_SHORTENER") and self.link_shortener:
                return self.link_shortener.create_short_link(long_url)
            return long_url
        except Exception as e:
            logger.warning(f"Erro ao gerar link de pagamento: {e}")
            return long_url if 'long_url' in locals() else None

    def _resolve_template(self, config, canal, event_type, sufixo='MESSAGE'):
        """Template configurado pelo administrador, com recurso ao padrão do sistema.

        `sufixo` existe por causa do push, que tem DUAS partes — o título e o
        corpo — em vez do texto único dos outros canais.
        """
        chave = f"{canal}_{event_type.upper()}_{sufixo}_TEMPLATE"
        return config.get(chave) or DEFAULT_TEMPLATES.get(chave, "")

    @staticmethod
    def _aparelhos_push(event_type, user_profile, config):
        """Os aparelhos desta pessoa que podem receber esta notificação.

        Perguntar ANTES de montar a mensagem é o que permite ao envio em massa
        contar como destinatário quem só tem push — e é o que evita contar como
        falha um envio a quem nunca ligou notificação nenhuma.
        """
        if not config.get("PUSH_ENABLED") or event_type in EVENTOS_SEM_PUSH:
            return []
        from .. import extensions
        gestor = extensions.push_manager
        if not (gestor and gestor.disponivel):
            return []
        try:
            return gestor.aparelhos_de(user_profile.get('media_user_id'))
        except Exception as e:
            logger.warning(f"Não foi possível ler os aparelhos subscritos: {e}")
            return []

    def _prepare_and_send(self, event_type, user, user_profile, context, config=None,
                          aparelhos_push=None):
        """
        Monta e envia a notificação de um evento por todos os canais ativos.

        Devolve um resumo da entrega — {'sent': [...], 'failed': [(canal, erro)]}
        — para que quem chama (em especial o envio em massa) saiba o que
        aconteceu de facto. Antes, todas as falhas eram engolidas aqui dentro e
        a consola de envio anunciava "✅ Sucesso" mesmo quando nenhum canal tinha
        conseguido entregar a mensagem.
        """
        # O config é passado já carregado nos envios em massa: cada
        # load_or_create_config() lê e valida o config.json inteiro do disco.
        config = config if config is not None else load_or_create_config()
        request_id = str(uuid.uuid4())
        resultado = {'sent': [], 'failed': []}

        telegram_chat_id = user_profile.get('telegram_id') or user_profile.get('telegram_user')
        can_notify_telegram = bool(config.get("TELEGRAM_ENABLED") and telegram_chat_id)
        can_notify_discord = bool(
            config.get("DISCORD_ENABLED") and config.get("DISCORD_WEBHOOK_URL")
            and user_profile.get('discord_user_id')
        )
        can_notify_whatsapp = bool(config.get("WHATSAPP_ENABLED") and user_profile.get('phone_number'))

        # 🐛 CORREÇÃO: o Webhook Genérico exigia que o utilizador tivesse um número de
        # telefone. Isso fazia sentido quando ele servia sobretudo de ponte para o
        # WhatsApp — mas o WhatsApp passou a ter canal próprio, e o webhook genérico
        # é justamente o canal para QUALQUER integração (n8n, Slack, Home Assistant,
        # sistemas internos...), muitas das quais não têm nada a ver com telefone.
        #
        # Continuar a exigir telefone significava que utilizadores sem número nunca
        # disparavam o webhook, mesmo com o canal ativo e configurado. Agora basta
        # o canal estar ativo; o template decide que dados usar.
        can_notify_webhook = bool(config.get("WEBHOOK_ENABLED") and config.get("WEBHOOK_URL"))

        # O push é o único canal cujo destinatário não está no perfil: está na
        # tabela de aparelhos subscritos. Uma pessoa sem aparelho nenhum não
        # tem push, mesmo com o canal ligado.
        if aparelhos_push is None:
            aparelhos_push = self._aparelhos_push(event_type, user_profile, config)
        can_notify_push = bool(aparelhos_push)

        if not (can_notify_telegram or can_notify_webhook or can_notify_discord
                or can_notify_whatsapp or can_notify_push):
            return resultado

        templates = {
            canal: self._resolve_template(config, canal, event_type)
            for canal in ("TELEGRAM", "WEBHOOK", "DISCORD", "WHATSAPP", "PUSH")
        }
        templates["PUSH_TITLE"] = self._resolve_template(config, "PUSH", event_type, sufixo='TITLE')
        bulk_msg = context.get('message', '')

        all_text = " ".join(list(templates.values()) + [bulk_msg])
        
        # Inteligência: Só gera link se a mensagem realmente pedir
        needs_payment_link = (
            "{payment_link}" in all_text or 
            "{paymentlink}" in all_text or 
            event_type in ['expiration', 'trial_end']
        )
        
        if needs_payment_link:
            payment_link = self._get_payment_link(config, event_type, user_profile)
        else:
            payment_link = None

        formatted_price, plan_name = self._get_price_and_plan(config, user_profile.get('screen_limit', 0))

        now = datetime.now(get_localzone())
        placeholders = {
            **self._build_placeholders(user, user_profile, context),
            'payment_link': payment_link or "#",
            'paymentlink': payment_link or "#",
            'price': formatted_price,
            'plan_name': plan_name,
            'planname': plan_name,
            'date_time': now.strftime('%d/%m/%Y %H:%M'),
            'days_left': context.get('days', 0),
            # `.get(chave, '')` devolve None quando a chave EXISTE com valor
            # None — e ela existe sempre nas reativações, podendo vir vazia
            # quando o servidor não sabe dizer um endereço. Quem o converte é o
            # `_format_template`; isto deixa-o explícito aqui, onde se lê.
            'invite_link': context.get('invite_link') or ''
        }

        def _entregar(canal, envio):
            """Isola a falha de um canal: um erro aqui não trava os restantes."""
            try:
                envio()
                resultado['sent'].append(canal)
            except Exception as e:
                resultado['failed'].append((canal, str(e)))
                logger.error(
                    f"[ID: {request_id}] Notificação via {canal} falhou para "
                    f"'{user.get('username')}': {e}"
                )

        if can_notify_telegram:
            message = self._format_template(templates["TELEGRAM"], placeholders, use_html_escape=True)
            photo_url = config.get(f"TELEGRAM_{event_type.upper()}_BANNER_URL")
            
            markup = None
            if payment_link and event_type in ['expiration', 'trial_end']:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(text=_("💳 Pagar Agora / Renovar"), url=payment_link))
                
            if message:
                _entregar('Telegram', lambda: self._send_telegram_notification(
                    message, telegram_chat_id, request_id,
                    reply_markup=markup, media_user_id=user_profile.get('media_user_id'),
                    photo_url=photo_url, config=config
                ))

        if can_notify_webhook:
            payload = self._format_template(templates["WEBHOOK"], placeholders, is_json=True)
            if payload:
                _entregar('Webhook', lambda: self._send_webhook_notification(payload, request_id, config))

        if can_notify_discord:
            payload = self._format_template(templates["DISCORD"], placeholders, is_json=True)
            if payload:
                _entregar('Discord', lambda: self._send_discord_notification(payload, request_id, config))

        if can_notify_whatsapp:
            # O WhatsApp recebe TEXTO SIMPLES: sem JSON e sem escape de HTML
            # (ao contrário do Telegram, que usa markup).
            message = self._format_template(templates["WHATSAPP"], placeholders, is_json=False)
            if message:
                _entregar('WhatsApp', lambda: self._send_whatsapp_notification(
                    user_profile.get('phone_number'), message, request_id, config
                ))
            else:
                logger.warning(
                    f"[ID: {request_id}] Template de WhatsApp para o evento '{event_type}' está vazio ou inválido. "
                    f"Nada foi enviado para '{user.get('username')}'."
                )

        if can_notify_push:
            titulo = self._format_template(templates["PUSH_TITLE"], placeholders)
            corpo = self._format_template(templates["PUSH"], placeholders)
            if titulo and corpo:
                # Onde a notificação abre ao ser tocada. Nos avisos de cobrança
                # é o link de pagamento (que é o que a pessoa vai fazer a
                # seguir); nos restantes é a página da conta dela, que é uma
                # rota do painel e por isso abre a aba já aberta, se houver.
                destino = (payment_link if (event_type in ('expiration', 'trial_end')
                                            and payment_link) else '/account')
                _entregar('Push', lambda: self._send_push_notification(
                    aparelhos_push, titulo, corpo, destino, event_type
                ))

        return resultado

    def _send_push_notification(self, aparelhos, titulo, corpo, destino, event_type):
        """Entrega a notificação push, falhando alto se nenhum aparelho a aceitou.

        Quem chama (o envio em massa) trata a exceção como uma falha de canal e
        mostra-a na consola: um aparelho que existe na tabela e recusa a entrega
        é exatamente o que o administrador precisa de ver.
        """
        from .. import extensions

        entregues = extensions.push_manager.entregar(
            aparelhos, titulo, corpo, url=destino, tag=event_type)
        if not entregues:
            raise NotificationError(
                f"Nenhum dos {len(aparelhos)} aparelho(s) subscrito(s) aceitou a notificação.")

    def send_expiration_notification(self, user, days_left, user_profile):
        expiration_date_str = user_profile.get('expiration_date')
        formatted_date = ""
        if expiration_date_str:
            try:
                exp_dt = datetime.fromisoformat(expiration_date_str)
                if exp_dt.tzinfo:
                    exp_dt = exp_dt.astimezone(get_localzone())
                formatted_date = exp_dt.strftime('%d/%m/%Y')
            except (ValueError, TypeError):
                formatted_date = expiration_date_str[:10]
        self._prepare_and_send('expiration', user, user_profile, {'days': days_left, 'date': formatted_date})

    def send_media_request_notification(self, user_profile, dados):
        """
        Notifica o utilizador sobre um pedido feito no Overseerr/Jellyseerr.

        'dados' vem do webhook do Overseerr, já normalizado, e contém:
            title, overview, status, username, media_url, image_url, event

        A imagem (capa do filme/série) é enviada quando o canal a suporta:
        Telegram e WhatsApp enviam como foto com legenda; o Discord usa embed.
        """
        request_id = str(uuid.uuid4())
        config = load_or_create_config()

        placeholders = {
            "title": dados.get("title") or _("Título desconhecido"),
            "overview": dados.get("overview") or "",
            "status": dados.get("status") or "",
            "username": dados.get("username") or user_profile.get("username") or "",
            "media_url": dados.get("media_url") or "",
            "event": dados.get("event") or "",
            "name": user_profile.get("name") or user_profile.get("username") or "",
            "discord_user_id": user_profile.get("discord_user_id") or "",
        }
        image_url = dados.get("image_url")

        can_telegram = config.get("TELEGRAM_ENABLED") and (user_profile.get('telegram_id') or user_profile.get('telegram_user'))
        can_whatsapp = config.get("WHATSAPP_ENABLED") and user_profile.get('phone_number')
        can_discord = config.get("DISCORD_ENABLED") and user_profile.get('discord_user_id')
        aparelhos_push = self._aparelhos_push('media_request', user_profile, config)

        if not (can_telegram or can_whatsapp or can_discord or aparelhos_push):
            logger.debug(f"[ID: {request_id}] Utilizador sem canais de contacto para notificar o pedido.")
            return

        # Cada canal é isolado: uma falha não impede os restantes.
        if can_telegram:
            try:
                chave = resolve_seerr_template_key("TELEGRAM", dados.get("notification_type"))
                # Recai no template genérico se o específico estiver em branco.
                tpl = (config.get(chave) or DEFAULT_TEMPLATES.get(chave)
                       or config.get("TELEGRAM_MEDIA_REQUEST_MESSAGE_TEMPLATE")
                       or DEFAULT_TEMPLATES.get("TELEGRAM_MEDIA_REQUEST_MESSAGE_TEMPLATE"))
                msg = self._format_template(tpl, placeholders, is_json=False, use_html_escape=True)
                if msg:
                    chat_id = user_profile.get('telegram_id') or user_profile.get('telegram_user')
                    self._send_telegram_notification(
                        msg, chat_id, request_id,
                        media_user_id=user_profile.get('media_user_id'),
                        photo_url=image_url, config=config
                    )
            except Exception as e:
                logger.error(f"[ID: {request_id}] Notificação de pedido via Telegram falhou: {e}")

        if can_whatsapp:
            try:
                chave = resolve_seerr_template_key("WHATSAPP", dados.get("notification_type"))
                # Recai no template genérico se o específico estiver em branco.
                tpl = (config.get(chave) or DEFAULT_TEMPLATES.get(chave)
                       or config.get("WHATSAPP_MEDIA_REQUEST_MESSAGE_TEMPLATE")
                       or DEFAULT_TEMPLATES.get("WHATSAPP_MEDIA_REQUEST_MESSAGE_TEMPLATE"))
                msg = self._format_template(tpl, placeholders, is_json=False)
                if msg:
                    self._send_whatsapp_notification(
                        user_profile.get('phone_number'), msg, request_id, config, image_url=image_url
                    )
            except Exception as e:
                logger.error(f"[ID: {request_id}] Notificação de pedido via WhatsApp falhou: {e}")

        if can_discord:
            try:
                chave = resolve_seerr_template_key("DISCORD", dados.get("notification_type"))
                # Recai no template genérico se o específico estiver em branco.
                tpl = (config.get(chave) or DEFAULT_TEMPLATES.get(chave)
                       or config.get("DISCORD_MEDIA_REQUEST_MESSAGE_TEMPLATE")
                       or DEFAULT_TEMPLATES.get("DISCORD_MEDIA_REQUEST_MESSAGE_TEMPLATE"))
                payload = self._format_template(tpl, placeholders, is_json=True)
                if payload:
                    # Anexa a capa ao embed, quando existe.
                    if image_url and isinstance(payload.get("embeds"), list) and payload["embeds"]:
                        payload["embeds"][0]["image"] = {"url": image_url}
                    self._send_discord_notification(payload, request_id, config)
            except Exception as e:
                logger.error(f"[ID: {request_id}] Notificação de pedido via Discord falhou: {e}")

        if aparelhos_push:
            try:
                titulo, corpo = self._textos_de_push_do_pedido(config, dados, placeholders)
                if titulo and corpo:
                    from .. import extensions
                    extensions.push_manager.entregar(
                        aparelhos_push, titulo, corpo,
                        url=dados.get("media_url") or '/account',
                        # A etiqueta inclui o título: dois pedidos diferentes
                        # são dois avisos, mas o mesmo pedido a mudar de estado
                        # substitui o anterior em vez de empilhar quatro linhas.
                        tag=f"pedido:{dados.get('title') or ''}"[:60],
                    )
            except Exception as e:
                logger.error(f"[ID: {request_id}] Notificação de pedido via Push falhou: {e}")

    def _textos_de_push_do_pedido(self, config, dados, placeholders):
        """O título e o corpo do push de um pedido, conforme o estado dele."""
        tipo = dados.get("notification_type")
        textos = []
        for parte in ('TITLE', 'MESSAGE'):
            chave = resolve_seerr_template_key("PUSH", tipo, parte=parte)
            generica = f"PUSH_MEDIA_REQUEST_{parte}_TEMPLATE"
            modelo = (config.get(chave) or DEFAULT_TEMPLATES.get(chave)
                      or config.get(generica) or DEFAULT_TEMPLATES.get(generica))
            textos.append(self._format_template(modelo, placeholders, is_json=False))
        return textos[0], textos[1]

    def send_media_request_admin_notification(self, dados):
        """Avisa o ADMINISTRADOR de que entrou um pedido novo.

        É ele quem aprova, e é o único aviso que existe sobre isso: o Seerr
        manda o webhook e mais nada acontece deste lado. Só os eventos de
        ENTRADA (pendente e aprovado automaticamente) o incomodam — os
        seguintes são o estado a andar, e quem quer saber é quem pediu.
        """
        from .. import extensions

        if str(dados.get("notification_type") or "").upper() not in (
                "MEDIA_PENDING", "MEDIA_AUTO_APPROVED"):
            return 0

        titulo = _("Novo pedido de conteúdo")
        corpo = _("%(username)s pediu %(title)s.",
                  username=dados.get("username") or _("Alguém"),
                  title=dados.get("title") or _("um título"))
        try:
            return extensions.push_manager.enviar_ao_administrador(
                'pedido', titulo, corpo, url=dados.get("media_url") or '/', tag='pedido-novo')
        except Exception as e:
            logger.warning(f"Não foi possível avisar o administrador do pedido novo: {e}")
            return 0

    def send_invite_claimed_admin_notification(self, username, code, note=None):
        """Avisa o ADMINISTRADOR de que alguém acabou de entrar por um convite.

        Até aqui, o único sinal era uma linha no sino do painel — que só avisa
        quem está com ele aberto. Quem gera convites e fecha o portátil ficava
        a saber no dia seguinte, e quem os gera por um bot não ficava a saber de
        todo: o link era mandado e o ciclo acabava ali.

        A `note` do convite vem no corpo porque é ela que diz PARA QUEM o
        convite era — e é isso que torna o aviso útil: "a ana resgatou o
        convite do João do grupo do WhatsApp" é uma informação; "alguém
        resgatou um convite" não é.

        Uma falha aqui nunca derruba o resgate: a pessoa já tem acesso, e o
        aviso é sobre isso ter acontecido.
        """
        from .. import extensions

        titulo = _("Convite resgatado")
        if note:
            corpo = _("%(username)s entrou pelo convite de %(note)s.",
                      username=username, note=note)
        else:
            corpo = _("%(username)s entrou por um convite.", username=username)

        try:
            return extensions.push_manager.enviar_ao_administrador(
                'convite', titulo, corpo, url='/users', tag=f'convite-{code}')
        except Exception as e:
            logger.warning(f"Não foi possível avisar o administrador do convite resgatado: {e}")
            return 0

    def send_renewal_notification(self, user, new_expiration_date, user_profile):
        # 🛡️ ANTI-DUPLICAÇÃO: Evita enviar a mensagem de Renovação logo após uma Reativação (Janela de 60 Segundos)
        # 🐛 NOTA: usar 'or 0' em vez de confiar no default do .get(). A coluna
        # 'last_reactivation_time' é nullable, por isso a chave EXISTE no perfil
        # com valor None para quem nunca foi reativado — e o default do .get()
        # só se aplica a chaves AUSENTES, não a chaves com valor None. Sem isto,
        # a subtração rebentava com "unsupported operand type(s) for -: 'float' and 'NoneType'".
        #
        # O float() envolvente protege ainda contra o valor vir como texto (o que
        # pode acontecer se o campo for escrito a partir de JSON ou de um formulário).
        try:
            last_reactivation = float(user_profile.get('last_reactivation_time') or 0)
        except (TypeError, ValueError):
            last_reactivation = 0.0

        if time.time() - last_reactivation < 60:
            logger.info(f"Notificação de Renovação ignorada para '{user.get('username')}' porque a notificação de Reativação já foi enviada.")
            return

        formatted_date = _data_local(new_expiration_date)
        self._prepare_and_send('renewal', user, user_profile, {'new_date': formatted_date, 'date': formatted_date})

    def send_reactivation_notification(self, user, new_expiration_date, user_profile, invite_link):
        formatted_date = _data_local(new_expiration_date)
        self._prepare_and_send('reactivation', user, user_profile, {'new_date': formatted_date, 'date': formatted_date, 'invite_link': invite_link})

    def send_trial_end_notification(self, user, user_profile):
        self._prepare_and_send('trial_end', user, user_profile, {})

    def send_trial_extended_notification(self, user, user_profile, novo_fim):
        """Avisa quem está em teste de que o período foi estendido.

        🔔 **Estender o teste era uma mudança silenciosa.** O administrador
        clicava em "Estender", o painel remarcava o `end_trial_job` e do outro
        lado ninguém ficava a saber: a pessoa continuava a contar com a data
        antiga — e o painel só falava com ela no fim, para dizer que o teste
        tinha acabado. Ou seja, o único aviso sobre o teste era o de má
        notícia. Este é o da boa.

        ⚠️ **A hora faz parte da data aqui**, ao contrário do vencimento de uma
        assinatura: um teste estende-se por horas ou minutos, e "vai até
        20/09/2026" sobre uma extensão de duas horas não diz nada a quem
        precisa de saber quando é que fica sem acesso.

        Devolve o resumo da entrega (`{'sent': [...], 'failed': [...]}`) para
        que a rota diga ao administrador se houve mesmo por onde avisar — um
        aviso que não saiu não pode parecer que saiu.
        """
        fim = _data_local(novo_fim, '%d/%m/%Y às %H:%M')
        return self._prepare_and_send('trial_extended', user, user_profile, {
            'new_date': fim, 'date': fim,
        })

    def send_password_reset_notification(self, user, user_profile, link, validade_minutos):
        """Entrega o link para repor a palavra-passe.

        🛡️ O link É a credencial enquanto vive: quem o tiver muda a
        palavra-passe da conta. Por isso vai pelos canais que a pessoa já
        registou no painel e por mais nenhum — e não fica no log.

        Devolve o resumo da entrega (`{'sent': [...], 'failed': [...]}`): quem
        chama precisa de saber se ALGUM canal aceitou, para não criar um pedido
        cujo link nunca chegou a ninguém.
        """
        if not link:
            return {'sent': [], 'failed': []}

        return self._prepare_and_send('password_reset', user, user_profile, {
            'reset_link': link,
            'reset_minutes': validade_minutos,
        })

    def send_credentials_notification(self, user, user_profile, credenciais, link=None):
        """Entrega as credenciais de uma conta que teve de ser criada de novo.

        🛡️ **É o único sítio do painel por onde uma palavra-passe viaja**, e é
        por desenho: num servidor de contas locais, uma conta apagada e recriada
        volta com uma palavra-passe nova que só o painel conhece. Não há como
        recuperar a antiga nem como pedir uma à pessoa — ela não está a ver o
        painel, está a pagar.

        Por isso: nada disto vai para o log (nem aqui, nem em quem chama), e a
        mensagem padrão pede à pessoa que a troque assim que puder.
        """
        if not (credenciais or {}).get('password'):
            return {'sent': [], 'failed': []}

        return self._prepare_and_send('credentials', user, user_profile, {
            'new_username': credenciais.get('username') or user.get('username'),
            'new_password': credenciais.get('password'),
            'invite_link': link,
        })

    def _nome_do_servidor(self):
        """Como se chama o servidor que esta pessoa usa.

        ⚠️ Os templates padrão diziam "Plex" no meio da frase ("O seu acesso ao
        Plex está prestes a expirar"). Num painel Jellyfin era a marca errada
        entregue ao utilizador — o mesmo que aconteceu com o `default.svg` e com
        o "Ver no Plex" das estatísticas. Quem sabe o nome é o servidor.
        """
        from .. import extensions

        return getattr(extensions.media_server, 'SHORT_NAME', None) or 'Plex'

    def _build_placeholders(self, user, user_profile, context):
        return {
            'server_name': self._nome_do_servidor(),
            'username': user.get('username'), 
            'name': user_profile.get('name') or user.get('username'),
            'email': user.get('email') or user_profile.get('email') or "",
            'greeting': get_greeting(),
            'telegram_user': user_profile.get('telegram_user', ''), 
            'telegram_id': user_profile.get('telegram_id', ''),
            'phone_number': user_profile.get('phone_number', ''),
            # 🐛 Estava EM FALTA: todos os templates padrão do Discord começam por
            # "<@{discord_user_id}>" para mencionar o utilizador. Sem este
            # marcador, a menção era publicada tal e qual — literalmente
            # "<@{discord_user_id}>" — em vez de notificar quem devia.
            'discord_user_id': user_profile.get('discord_user_id', ''),
            'media_user_id': user_profile.get('media_user_id') or user.get('id') or '',
            **context
        }

    # --- PROCESSAMENTO DE MENSAGENS EM MASSA (BULK) EM TEMPO REAL ---

    def process_bulk_notification_task(self, task):
        from .. import extensions
        from flask import current_app
        
        # Garante que extraímos o ID e o Payload quer seja um objeto SQLAlchemy ou um Dicionário nativo
        if isinstance(task, dict):
            task_id = task.get('id')
            raw_payload = task.get('payload', '{}')
        else:
            task_id = getattr(task, 'id', None)
            raw_payload = getattr(task, 'payload', '{}')

        if not task_id:
            logger.error("Tarefa sem ID não pode ser processada.")
            return

        app_obj = current_app._get_current_object() if current_app else None

        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            
            message = payload.get('message')
            if not message: 
                raise ValueError(_("Mensagem vazia."))

            # 🚀 O config é lido UMA vez para todo o lote. Antes, cada utilizador
            # provocava várias leituras do config.json do disco (uma por canal,
            # mais uma no bot do Telegram): num envio para mil pessoas eram
            # milhares de acessos a disco sem qualquer alteração entre eles.
            config = load_or_create_config()

            alvos = self._get_bulk_target_users(payload, extensions)
            all_profiles = {p['media_user_id']: p for p in extensions.data_manager.get_all_user_profiles()}

            # 🚀 Uma consulta para o lote inteiro, em vez de uma por pessoa:
            # o envio em massa pergunta duas vezes por cada uma (na contagem de
            # elegíveis e no envio), e são milhares num painel grande.
            aparelhos_por_pessoa = self._aparelhos_por_pessoa(extensions, config)

            elegiveis, ignorados = self._split_by_reachability(
                alvos, all_profiles, config, aparelhos_por_pessoa)
            total_users = len(elegiveis)

            extensions.data_manager.update_task(task_id, {'status': 'running', 'progress_total': total_users})

            self._run_bulk_worker(
                app_obj, extensions, task_id, message, config, elegiveis, all_profiles, ignorados,
                aparelhos_por_pessoa
            )
            
        except Exception as e:
            logger.error(f"Erro crítico no processamento Bulk: {e}", exc_info=True)
            
            if task_id:
                extensions.data_manager.update_task(task_id, {'status': 'failed', 'result': str(e)})
            
            try:
                if extensions.socketio:
                    extensions.socketio.emit('bulk_console_log', {'msg': f"💥 ERRO CRÍTICO NA TAREFA: {str(e)}"})
                    extensions.socketio.emit('bulk_notification_error', {'message': str(e)}, namespace='/dashboard')
                    extensions.socketio.emit('bulk_notification_error', {'message': str(e)}, namespace='/users')
            except Exception:
                pass

    @staticmethod
    def _split_by_reachability(users, all_profiles, config, aparelhos_por_pessoa=None):
        """
        Separa quem tem por onde ser notificado de quem não tem.

        A verificação é feita ANTES do envio para que o total anunciado na
        consola e a barra de progresso correspondam a envios reais — antes,
        utilizadores sem contacto entravam no total e a barra parecia saltar.

        A elegibilidade tem de olhar aos canais ATIVOS: o webhook genérico
        (n8n, Slack, sistemas internos) dispara para qualquer utilizador, mesmo
        sem telefone ou Telegram, por isso com ele ligado ninguém é ignorado.
        """
        webhook_ativo = bool(config.get("WEBHOOK_ENABLED") and config.get("WEBHOOK_URL"))
        telegram_ativo = bool(config.get("TELEGRAM_ENABLED"))
        discord_ativo = bool(config.get("DISCORD_ENABLED") and config.get("DISCORD_WEBHOOK_URL"))
        whatsapp_ativo = bool(config.get("WHATSAPP_ENABLED"))
        # ⚠️ O push não se vê no perfil: quem tem aparelho subscrito está na
        # tabela das subscrições. Sem isto, quem só ligou as notificações do
        # celular era contado como "sem contato" e nunca recebia um aviso em
        # massa — sem erro nenhum, apenas uma linha a dizer que foi ignorado.
        aparelhos_por_pessoa = aparelhos_por_pessoa or {}

        elegiveis, ignorados = [], []
        for user in users:
            profile = all_profiles.get(user['id'], {})
            tem_contacto = webhook_ativo or (
                (telegram_ativo and (profile.get('telegram_id') or profile.get('telegram_user')))
                or (whatsapp_ativo and profile.get('phone_number'))
                or (discord_ativo and profile.get('discord_user_id'))
                or bool(aparelhos_por_pessoa.get(user['id']))
            )
            (elegiveis if tem_contacto else ignorados).append(user)

        return elegiveis, ignorados

    @staticmethod
    def _aparelhos_por_pessoa(extensions, config):
        """Todos os aparelhos subscritos, agrupados por dono."""
        gestor = extensions.push_manager
        if not (config.get("PUSH_ENABLED") and gestor and gestor.disponivel):
            return {}
        try:
            por_pessoa = {}
            for aparelho in extensions.data_manager.get_all_push_subscriptions():
                por_pessoa.setdefault(aparelho.get('media_user_id'), []).append(aparelho)
            return por_pessoa
        except Exception as e:
            logger.warning(f"Não foi possível ler as subscrições push do lote: {e}")
            return {}

    def _run_bulk_worker(self, app_obj, extensions, task_id, message, config,
                         elegiveis, all_profiles, ignorados, aparelhos_por_pessoa=None):
        """Corre o lote em segundo plano, transmitindo o progresso em tempo real."""
        total_users = len(elegiveis)

        # Pausa entre envios. A proteção real contra o limite de ritmo já é feita
        # pelo tratamento do 429 (Telegram) e pelo Retry-After (Discord/WhatsApp);
        # esta pausa é cautela extra e é configurável para quem tem bases grandes.
        try:
            intervalo = max(0.0, float(config.get("BULK_SEND_INTERVAL_SECONDS", 0.2)))
        except (TypeError, ValueError):
            intervalo = 0.2

        def bulk_worker():
            ctx = None
            if app_obj:
                ctx = app_obj.test_request_context('/')
                ctx.push()

            def emit_ws(event, data):
                try:
                    if extensions.socketio:
                        extensions.socketio.emit(event, data, namespace='/dashboard')
                        extensions.socketio.emit(event, data, namespace='/users')
                except Exception as e:
                    logger.debug(f"Aviso ao tentar emitir evento WS: {e}")

            def log(msg):
                emit_ws('bulk_console_log', {'msg': msg})

            try:
                if extensions.socketio: extensions.socketio.sleep(1)

                emit_ws('bulk_notification_start', {'total': total_users})
                log("=========================================================")
                log(_("🚀 INÍCIO DO ENVIO EM MASSA (%(n)d usuários elegíveis)", n=total_users))
                if ignorados:
                    log(_("⏭️ %(n)d ignorados por não terem contato nos canais ativos.", n=len(ignorados)))
                log("=========================================================")

                entregues = 0
                falhados = 0

                for index, user in enumerate(elegiveis, 1):
                    username = user.get('username', f'ID {user.get("id")}')
                    profile = all_profiles.get(user['id'], {})

                    emit_ws('bulk_notification_progress', {'current': index, 'total': total_users})

                    # Atualiza a base de dados a cada 10 envios para poupar conexões
                    if index % 10 == 0 or index == total_users:
                        extensions.data_manager.update_task(task_id, {'progress_current': index})

                    try:
                        resultado = self._prepare_and_send(
                            'bulk', user, profile, {'message': message}, config=config,
                            aparelhos_push=(aparelhos_por_pessoa or {}).get(user['id'], [])
                        )
                    except Exception as user_err:
                        logger.error(f"Erro no envio em massa para {username}: {user_err}", exc_info=True)
                        resultado = {'sent': [], 'failed': [('geral', str(user_err))]}

                    # 🐛 O relatório era fictício: como _prepare_and_send engolia as
                    # falhas de cada canal, a consola escrevia "✅ Sucesso" para
                    # toda a gente — mesmo quando nada tinha sido entregue. Agora o
                    # resultado real de cada canal é que decide a linha e a contagem.
                    if resultado['sent']:
                        entregues += 1
                        canais = ", ".join(resultado['sent'])
                        aviso = ""
                        if resultado['failed']:
                            aviso = " | " + _("falhou em: %(canais)s", canais=", ".join(c for c, _erro in resultado['failed']))
                        log(f"[{index}/{total_users}] ✅ {username} — {canais}{aviso}")
                    elif resultado['failed']:
                        falhados += 1
                        motivos = "; ".join(f"{canal}: {erro}" for canal, erro in resultado['failed'])
                        log(f"[{index}/{total_users}] ❌ {username} — {motivos}")
                    else:
                        log(f"[{index}/{total_users}] ⏭️ {username} — " + _("nenhum canal aplicável."))

                    if intervalo and index < total_users:
                        self._sleep(intervalo)

                resumo = _("%(ok)d entregues, %(erro)d com falha, %(skip)d sem contato.",
                           ok=entregues, erro=falhados, skip=len(ignorados))

                extensions.data_manager.update_task(task_id, {
                    'status': 'completed',
                    'completed_at': datetime.now(timezone.utc),
                    'progress_current': total_users,
                    'result': resumo,
                })

                log(f"🎉 {resumo}")
                emit_ws('bulk_notification_end', {'message': resumo})

            except Exception as e:
                # 🐛 Sem este bloco, um erro dentro do worker deixava a tarefa
                # presa em 'running' para sempre: o try/except de quem chamou já
                # tinha terminado, porque o worker corre noutra greenlet.
                logger.error(f"Erro no worker de envio em massa: {e}", exc_info=True)
                try:
                    extensions.data_manager.update_task(task_id, {'status': 'failed', 'result': str(e)})
                except Exception:
                    logger.error("Não foi possível marcar a tarefa de envio em massa como falhada.", exc_info=True)
                log(f"💥 ERRO NO ENVIO EM MASSA: {e}")
                emit_ws('bulk_notification_error', {'message': str(e)})
            finally:
                if ctx is not None:
                    ctx.pop()

        if extensions.socketio:
            extensions.socketio.start_background_task(bulk_worker)
        else:
            bulk_worker()

    def _get_bulk_target_users(self, payload, extensions):
        target_audience = payload.get('target_audience', 'active')
        target_user_ids = payload.get('user_ids', [])
        
        all_plex_users = extensions.media_server.get_all_users()
        if not all_plex_users: 
            raise ValueError(_("Não foi possível obter a lista de usuários do servidor."))

        if target_audience == 'specific': 
            alvos = set(map(str, target_user_ids or []))
            return [u for u in all_plex_users if str(u['id']) in alvos]
        elif target_audience == 'all': 
            return all_plex_users
        else:
            blocked_ids = {str(u['media_user_id']) for u in extensions.data_manager.get_blocked_users_list()}
            if target_audience == 'blocked':
                return [u for u in all_plex_users if str(u['id']) in blocked_ids]
            else: 
                return [u for u in all_plex_users if str(u['id']) not in blocked_ids]