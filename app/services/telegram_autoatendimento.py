"""Menu privado do bot de autoatendimento."""
import hashlib
import hmac
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from tzlocal import get_localzone

from flask import current_app, url_for
from sqlalchemy.exc import IntegrityError

from .. import extensions
from ..models import Invitation, TelegramVinculo, UserProfile, agora_utc
from .media_server.invitations import convite_expirado
from ..utils.periodo_de_teste import estado_do_teste, fim_do_teste

ACOES = {'Solicitar teste': '/teste', 'Renovar acesso': '/renovar',
         'Falar com suporte': '/suporte', 'Minha conta': '/conta'}
BOTOES = {'keyboard': [list(ACOES)[:2], list(ACOES)[2:]], 'resize_keyboard': True}


def segredo(config):
    return hmac.new(current_app.secret_key.encode(), config['AUTO_BOT_TOKEN'].encode(),
                    hashlib.sha256).hexdigest()


def endereco(config, endpoint, **kwargs):
    return config['APP_BASE_URL'].rstrip('/') + url_for(endpoint, **kwargs)


def validar(config):
    for key in ('AUTO_BOT_ENABLED', 'AUTO_BOT_TRIAL'):
        if not isinstance(config.get(key, False), bool):
            return 'Selecione uma opção válida de autoatendimento.'
    if not config.get('AUTO_BOT_ENABLED'):
        return None
    for key in ('AUTO_BOT_TOKEN', 'AUTO_BOT_SUPPORT', 'AUTO_BOT_LIBRARIES', 'APP_BASE_URL'):
        if not isinstance(config.get(key, ''), str):
            return 'Confira os campos de texto do autoatendimento.'
    try:
        base = urlsplit(config.get('APP_BASE_URL') or '')
        base.port  # Valida também uma porta explícita.
    except ValueError:
        return 'Confira a URL pública HTTPS do painel.'
    if base.scheme != 'https' or not base.hostname or base.query or base.fragment or base.username:
        return 'Configure a URL pública HTTPS do painel.'
    token = config.get('AUTO_BOT_TOKEN') or ''
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token) or token == config.get('TELEGRAM_BOT_TOKEN'):
        return 'Informe um token exclusivo para o bot de autoatendimento.'
    if not re.fullmatch(r'@?[A-Za-z][A-Za-z0-9_]{4,31}', config.get('AUTO_BOT_SUPPORT') or ''):
        return 'Informe o nome de usuário do suporte no Telegram.'
    if config.get('AUTO_BOT_TRIAL'):
        if not bibliotecas(config):
            return 'Escolha pelo menos uma biblioteca para o teste.'
        for key, maximum in [('AUTO_BOT_DURATION', 43200), ('AUTO_BOT_VALIDITY', 10080), ('AUTO_BOT_SCREENS', 6)]:
            value = config.get(key)
            if type(value) is not int or not 1 <= value <= maximum:
                return 'Confira a duração, a validade e o limite de telas do teste.'
    return None


def bibliotecas(config):
    return [name.strip() for name in config.get('AUTO_BOT_LIBRARIES', '').splitlines() if name.strip()]


def link(label, url):
    return {'inline_keyboard': [[{'text': label, 'url': url}]]}


def teste(config, chat, confirmar):
    if not config.get('AUTO_BOT_TRIAL'):
        return 'Os testes estão fechados no momento.', BOTOES
    if not confirmar:
        return (f"Teste de {config['AUTO_BOT_DURATION']} min, com {config['AUTO_BOT_SCREENS']} tela(s). "
                f"Bibliotecas: {', '.join(bibliotecas(config))}. "
                f"Resgate em até {config['AUTO_BOT_VALIDITY']} min.",
                {'keyboard': [['Confirmar teste'], ['Voltar']], 'resize_keyboard': True})
    historico = Invitation.query.filter_by(telegram_id=chat).order_by(Invitation.created_at.desc())
    if historico.filter(Invitation.use_count > 0, Invitation.trial_duration_minutes > 0).first():
        return 'Este Telegram já utilizou um teste.', BOTOES
    if TelegramVinculo.query.filter_by(chat_id=chat).first() or extensions.data_manager.get_user_profile_by_telegram(chat):
        return 'Você já tem uma conta. Use Minha conta ou Renovar acesso.', BOTOES
    anterior = historico.filter_by(note='Autoatendimento no Telegram').first()
    if anterior and anterior.deleted_at:
        return 'Seu convite foi revogado. Entre em contato com o suporte.', BOTOES
    if anterior and not convite_expirado(anterior.expires_at):
        code = anterior.code
    else:
        # Duas entregas simultâneas da confirmação disputam a mesma chave primária.
        origem = f"auto-teste:{chat}:{anterior.code if anterior else ''}"
        code = hmac.new(current_app.secret_key.encode(), origem.encode(), hashlib.sha256).hexdigest()[:32]
        disponiveis = {lib['title'].casefold(): lib['title']
                       for lib in extensions.media_server.get_libraries() or [] if lib.get('title')}
        libs = [disponiveis.get(nome.casefold()) for nome in bibliotecas(config)]
        if not libs or not all(libs):
            return 'As bibliotecas do teste estão indisponíveis. Fale com o suporte.', BOTOES
        try:
            result = extensions.media_server.create_invitation(
                library_titles=libs, screens=config['AUTO_BOT_SCREENS'], allow_downloads=False,
                expires_in_minutes=config['AUTO_BOT_VALIDITY'], trial_duration_minutes=config['AUTO_BOT_DURATION'],
                custom_code=code, max_uses=1, telegram_id=chat, note='Autoatendimento no Telegram')
        except IntegrityError:
            extensions.db.session.rollback()
            result = {'success': False}
        if not result.get('success'):
            existente = Invitation.query.filter_by(code=code, telegram_id=chat, deleted_at=None, use_count=0).first()
            if not existente or convite_expirado(existente.expires_at):
                return 'Não foi possível emitir o convite. Fale com o suporte.', BOTOES
    return 'Seu convite está pronto.', link('Ativar meu teste', endereco(config, 'main.claim_invite_page', code=code))


def confirmar_vinculo(chave, chat):
    digest = hashlib.sha256(chave.encode()).hexdigest()
    try:
        atualizados = TelegramVinculo.query.filter(
            TelegramVinculo.desafio == digest, TelegramVinculo.validade > agora_utc()
        ).update({'chat_id': chat, 'desafio': None, 'validade': None}, synchronize_session=False)
        extensions.db.session.commit()
    except IntegrityError:
        extensions.db.session.rollback()
        return 'Este Telegram já está vinculado a outra conta.', BOTOES
    if atualizados:
        return 'Conta vinculada. Escolha uma opção.', BOTOES
    return 'Link inválido ou expirado. Gere outro em Minha conta no painel.', BOTOES


def responder(config, chat, texto):
    if texto.startswith('/start vincular_'):
        return confirmar_vinculo(texto.removeprefix('/start vincular_'), chat)
    comando = ACOES.get(texto, texto)
    if comando in ('/teste', 'Confirmar teste'):
        return teste(config, chat, comando == 'Confirmar teste')
    if comando == '/suporte':
        return 'Converse com a equipe de suporte.', link('Abrir suporte', 'https://t.me/' + config['AUTO_BOT_SUPPORT'].lstrip('@'))
    if comando not in ('/conta', '/renovar'):
        return 'Olá! Como podemos ajudar?', BOTOES
    vinculo = TelegramVinculo.query.filter_by(chat_id=chat).first()
    perfil = extensions.db.session.get(UserProfile, vinculo.media_user_id) if vinculo else None
    if not perfil:
        return ('Acesse Minha conta no painel e toque em Vincular Telegram para confirmar sua identidade.',
                link('Abrir Minha conta', endereco(config, 'main.account_page')))
    if comando == '/renovar':
        token = extensions.data_manager.garantir_payment_token(perfil.media_user_id)
        return 'Consulte as opções de renovação e pague pelo painel.', link(
            'Renovar acesso', endereco(config, 'main.payment_page', token=token))
    situacao = 'Ativa' if perfil.status == 'active' else 'Inativa'
    teste_atual = estado_do_teste(perfil)
    if teste_atual:
        situacao = 'Em teste' if teste_atual == 'a_decorrer' else 'Teste encerrado'
    if extensions.data_manager.get_blocked_user(perfil.media_user_id):
        situacao = 'Bloqueada'
    vencimento = None
    if teste_atual:
        vencimento = fim_do_teste(perfil).astimezone(get_localzone()).strftime('%d/%m/%Y às %H:%M')
    elif perfil.expiration_date:
        try:
            fim = datetime.fromisoformat(perfil.expiration_date)
            if not fim.tzinfo:
                fim = fim.replace(tzinfo=timezone.utc)
            vencimento = fim.astimezone(get_localzone()).strftime('%d/%m/%Y')
        except ValueError:
            vencimento = None
    return (f"Conta: {perfil.username}\nSituação: {situacao}\nVencimento: {vencimento or 'Não definido'}\n"
            f"Telas: {perfil.screen_limit or 'Sem limite'}", BOTOES)
