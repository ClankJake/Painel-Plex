from datetime import timedelta

import pytest
from flask import g

from app.models import Invitation, TelegramVinculo, UserProfile, agora_utc
from app.services.telegram_autoatendimento import segredo


@pytest.fixture()
def config_bot(config_file, db_session):
    return config_file(IS_CONFIGURED=True, ADMIN_USER_ID='1', ADMIN_USER='dono',
                       APP_BASE_URL='https://painel.example', AUTO_BOT_ENABLED=True,
                       TELEGRAM_BOT_TOKEN='123:' + 'x' * 32, AUTO_BOT_SUPPORT='@suporte',
                       AUTO_BOT_TRIAL=True, AUTO_BOT_LIBRARIES='Filmes', AUTO_BOT_VALIDITY=30,
                       AUTO_BOT_DURATION=120, AUTO_BOT_SCREENS=1)


@pytest.fixture()
def enviar(client, config_bot):
    def post(texto, chat=44, tipo='private'):
        return client.post('/api/telegram/webhook', json={
            'message': {'from': {'id': chat}, 'chat': {'id': chat, 'type': tipo}, 'text': texto}},
            headers={'X-Telegram-Bot-Api-Secret-Token': segredo(config_bot)})
    return post


def entrar(client, uid, role='user'):
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['user_details'] = dict(id=uid, username='dono' if role == 'admin' else 'assinante',
                                       role=role, email='user@example.test')
        session['_user_id'] = uid
        session['_fresh'] = True


def url(resposta):
    return resposta.json['reply_markup']['inline_keyboard'][0][0]['url']


def test_menu_privado(client, enviar):
    assert client.post('/api/telegram/webhook', json={}).status_code == 403
    assert enviar('/conta', tipo='group').status_code == 204
    botoes = enviar('/start').json['reply_markup']['keyboard']
    assert {b for row in botoes for b in row} == {'Minha conta', 'Renovar acesso', 'Solicitar teste', 'Falar com suporte'}
    assert url(enviar('Falar com suporte')) == 'https://t.me/suporte'
    assert url(enviar('Renovar acesso')).endswith('/account')


def test_teste_repetido_expirado_e_resgatado(enviar, db_session, monkeypatch):
    monkeypatch.setattr('app.extensions.media_server.get_libraries', lambda: [{'title': 'Filmes'}])
    assert '120 min' in enviar('Solicitar teste').json['text']
    primeiro = url(enviar('Confirmar teste'))
    assert primeiro == url(enviar('Confirmar teste'))
    convite = Invitation.query.one()
    assert convite.telegram_id == '44' and convite.max_uses == 1 and convite.screen_limit == 1
    convite.expires_at = '2000-01-01T00:00:00+00:00'
    db_session.commit()
    segundo = url(enviar('Confirmar teste'))
    assert segundo != primeiro
    novo = db_session.get(Invitation, segundo.split('/')[-1])
    novo.use_count = 1
    db_session.commit()
    assert 'já utilizou' in enviar('Confirmar teste').json['text']


def test_vinculo_exige_prova_e_renovacao_usa_o_perfil(enviar, client, db_session, monkeypatch):
    perfil = UserProfile(media_user_id='22', username='assinante', telegram_user='44', screen_limit=2,
                         expiration_date='2026-12-15')
    db_session.add(perfil)
    db_session.commit()
    assert 'assinante' not in enviar('/conta').json['text']
    entrar(client, '22')
    monkeypatch.setattr('app.blueprints.telegram._api', lambda *args, **kw: {'username': 'TesteBot'})
    def novo_desafio():
        resposta = client.post('/api/telegram/vincular')
        assert resposta.status_code == 303
        return '/start ' + resposta.location.split('?start=')[1]
    pagina = client.get('/account')
    assert pagina.status_code == 200 and b'Vincular Telegram' in pagina.data
    expirado = novo_desafio()
    TelegramVinculo.query.one().validade = agora_utc() - timedelta(seconds=1)
    db_session.commit()
    assert 'expirado' in enviar(expirado).json['text']
    desafio = novo_desafio()
    assert 'Conta vinculada' in enviar(desafio).json['text']
    assert 'expirado' in enviar(desafio, chat=45).json['text']
    assert '15/12/2026' in enviar('Minha conta').json['text']
    assert url(enviar('/renovar')) == 'https://painel.example/pay/' + perfil.payment_token
    assert url(enviar('/renovar', chat=45)).endswith('/account')
    client.post('/api/telegram/desvincular')
    assert url(enviar('/conta')).endswith('/account')


def test_registro_do_webhook_e_token_mascarado(config_bot, client, monkeypatch):
    assert client.post('/api/telegram/conectar').status_code != 200
    entrar(client, '1', 'admin')
    chamadas = {}
    def api(config, metodo, **dados):
        chamadas[metodo] = dados
        return {'username': 'TesteBot'} if metodo == 'getMe' else True
    monkeypatch.setattr('app.blueprints.telegram._api', api)
    assert client.post('/api/telegram/conectar').json['success']
    pagina = client.get('/settings')
    assert pagina.status_code == 200 and b'Salvar altera' in pagina.data
    assert chamadas['setWebhook']['secret_token'] == segredo(config_bot)
    assert chamadas['setWebhook']['url'] == 'https://painel.example/api/telegram/webhook'
    assert client.get('/api/system/settings').json['TELEGRAM_BOT_TOKEN']['is_set']
    assert client.post('/api/system/settings', json={'TELEGRAM_BOT_TOKEN': '  ' + config_bot['TELEGRAM_BOT_TOKEN'] + '  '}).status_code == 200


@pytest.mark.parametrize('auto_ativo', [True, False])
def test_resgate_recebe_start_pelo_webhook(config_bot, config_file, enviar, monkeypatch, auto_ativo):
    from app.services import telegram_vinculo
    config = config_file(TELEGRAM_ENABLED=True, AUTO_BOT_ENABLED=auto_ativo)
    monkeypatch.setattr(telegram_vinculo, 'nome_do_bot', lambda cfg: 'TesteBot')
    codigo = 'abc123def456'
    assert telegram_vinculo.link_de_vinculo(config, codigo).endswith(codigo)
    assert enviar('/start ' + codigo).json['text'].startswith('Volte ao painel')
    assert telegram_vinculo.procurar_chat(config, codigo).chat_id == '44'


@pytest.mark.parametrize('principal,esperado', [('principal', 'principal'), ('', 'anterior')])
def test_migracao_do_token_antigo(config_file, principal, esperado):
    from app.config import load_or_create_config
    config_file(TELEGRAM_BOT_TOKEN=principal, AUTO_BOT_TOKEN='anterior')
    atualizado = load_or_create_config()
    assert atualizado['TELEGRAM_BOT_TOKEN'] == esperado
    assert 'AUTO_BOT_TOKEN' not in atualizado
