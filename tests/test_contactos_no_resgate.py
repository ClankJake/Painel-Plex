# tests/test_contactos_no_resgate.py
"""Quem resgata um convite passa a poder dizer por onde quer ser avisado.

🔔 **O buraco que isto fecha.** `resolver_contactos_do_convite` só grava um
contacto quando o convite foi gerado por um BOT para alguém em concreto. Quem
entra por um link público ficava com o perfil vazio — e aí `_prepare_and_send`
não tem por onde tentar e TODAS as notificações morrem em silêncio.

⚠️ E não é só o aviso de fim de teste: `trial_duration_minutes` é 0 por omissão,
por isso num convite normal não há teste nenhum (nem sequer `expiration_date`) —
mas é essa pessoa que vai ter vencimento e link de pagamento. Por isso a recolha
corre em TODO resgate, e o que muda com `is_trial` é só a frase.
"""

import pytest

from app import extensions

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _com_a_aplicacao_de_pe(app):
    """Os blueprints capturam os managers POR VALOR — ver o CLAUDE.md.

    Um import de `app.blueprints...` na recolha do pytest acontece antes de a
    fixture `app` correr o `create_app()`, e o `auth.py` fica com
    `data_manager = None` para o resto do processo.
    """
    return app


ALVO = '77001'
OUTRA = '77002'


def _perfil(media_user_id, username, **extra):
    extensions.data_manager.set_user_profile(
        media_user_id, {'media_user_id': media_user_id, 'username': username, **extra})


def _guardar(media_user_id, dados, **config):
    from app.services.contactos_do_resgate import guardar_contactos

    base = {'WHATSAPP_ENABLED': True, 'TELEGRAM_ENABLED': True,
            'DISCORD_ENABLED': True, 'DISCORD_WEBHOOK_URL': 'https://discord.test/x'}
    base.update(config)
    return guardar_contactos(extensions.data_manager, media_user_id, dados, base)


class TestOQueFicaGravado:

    def test_os_tres_canais_vao_para_as_colunas_certas(self, app, db_session):
        """⚠️ Os nomes DIVERGEM dos dois lados, e já houve código a ler uma
        coluna que não existe e a parecer funcionar por causa de um `or`."""
        with app.app_context():
            _perfil(ALVO, 'ana')
            gravados = _guardar(ALVO, {
                'name': 'Ana', 'whatsapp': '5521999998888',
                'telegram': '123456789', 'discord': '987654321098765432',
            })
            perfil = extensions.data_manager.get_user_profile(ALVO)

        assert sorted(gravados) == ['discord', 'telegram', 'whatsapp']
        assert perfil['name'] == 'Ana'
        assert perfil['phone_number'] == '5521999998888'
        assert perfil['telegram_user'] == '123456789'
        assert perfil['discord_user_id'] == '987654321098765432'

    def test_um_canal_ausente_nao_apaga_o_que_ja_estava(self, app, db_session):
        """Preencher só o WhatsApp e voltar depois à "Minha Conta" tem de dar."""
        with app.app_context():
            _perfil(ALVO, 'ana', telegram_user='555')
            _guardar(ALVO, {'whatsapp': '5521999998888'})
            perfil = extensions.data_manager.get_user_profile(ALVO)

        assert perfil['phone_number'] == '5521999998888'
        assert perfil['telegram_user'] == '555', "o que não veio no pedido não se apaga"

    def test_um_canal_DESLIGADO_nao_e_gravado(self, app, db_session):
        """Guardar um ID que ninguém vai ler é ocupar a verificação de
        duplicados contra toda a gente, por um dado morto."""
        with app.app_context():
            _perfil(ALVO, 'ana')
            gravados = _guardar(ALVO, {'telegram': '123'}, TELEGRAM_ENABLED=False)
            perfil = extensions.data_manager.get_user_profile(ALVO)

        assert gravados == []
        assert not perfil.get('telegram_user')

    def test_o_discord_sem_webhook_conta_como_desligado(self, app, db_session):
        """⚠️ Ligado não chega: `_prepare_and_send` exige o webhook do canal."""
        with app.app_context():
            _perfil(ALVO, 'ana')
            gravados = _guardar(ALVO, {'discord': '123'}, DISCORD_WEBHOOK_URL='')

        assert gravados == []

    def test_o_telefone_e_guardado_so_com_digitos(self, app, db_session):
        """O destinatário é `{phone_number}@s.whatsapp.net`."""
        with app.app_context():
            _perfil(ALVO, 'ana')
            _guardar(ALVO, {'whatsapp': '+55 (21) 99999-8888'})
            perfil = extensions.data_manager.get_user_profile(ALVO)

        assert perfil['phone_number'] == '5521999998888'


class TestDuasPessoasNoMesmoContacto:
    """🛡️ A regra vale MAIS aqui do que na criação do convite.

    Ali quem escreve o ID é o administrador; aqui é quem acabou de entrar, num
    formulário público. Apontar o contacto de outra pessoa redirecionava-lhe as
    notificações — e entre elas vai o `/pay/<token>`, que funciona para quem o
    tiver.
    """

    @pytest.mark.parametrize('canal, coluna, valor', [
        ('whatsapp', 'phone_number', '5521999998888'),
        ('telegram', 'telegram_user', '123456789'),
        ('discord', 'discord_user_id', '987654321098765432'),
    ])
    def test_o_contacto_de_outra_pessoa_e_recusado(self, app, db_session, canal, coluna, valor):
        from app.services.contactos_do_resgate import ContactoEmUso

        with app.app_context():
            _perfil(OUTRA, 'joana', **{coluna: valor})
            _perfil(ALVO, 'ana')

            with pytest.raises(ContactoEmUso):
                _guardar(ALVO, {canal: valor})

            perfil = extensions.data_manager.get_user_profile(ALVO)

        assert not perfil.get(coluna), "nada pode ficar gravado quando a recusa acontece"

    def test_regravar_o_proprio_contacto_nao_e_conflito(self, app, db_session):
        with app.app_context():
            _perfil(ALVO, 'ana', phone_number='5521999998888')
            gravados = _guardar(ALVO, {'whatsapp': '5521999998888'})

        assert gravados == ['whatsapp']

    def test_a_recusa_nao_diz_de_quem_e(self, app, db_session):
        """🛡️ Quem preenche não tem de ficar a saber quem mais está no painel."""
        from app.services.contactos_do_resgate import ContactoEmUso

        with app.app_context():
            _perfil(OUTRA, 'joana', telegram_user='123')
            _perfil(ALVO, 'ana')
            with pytest.raises(ContactoEmUso) as erro:
                _guardar(ALVO, {'telegram': '123'})

        assert 'joana' not in str(erro.value)
        assert OUTRA not in str(erro.value)


class TestOsCanaisOferecidos:

    def test_so_se_pede_o_que_o_painel_consegue_usar(self, app):
        from app.services.media_server.invitations import canais_ativos_no_resgate

        ativos = canais_ativos_no_resgate({
            'WHATSAPP_ENABLED': True,
            'TELEGRAM_ENABLED': False,
            'DISCORD_ENABLED': True, 'DISCORD_WEBHOOK_URL': '',
        })
        assert [c.canal for c in ativos] == ['whatsapp']

    def test_o_mapa_dos_canais_nao_e_o_dos_convites(self):
        """⚠️ O WhatsApp está num e não no outro, e de propósito: não há
        `invitations.phone_number` para o pré-atribuir. Juntá-los faria
        `resolver_contactos_do_convite` ler uma coluna que não existe."""
        from app.services.media_server.invitations import CANAIS_DO_RESGATE, CONTACTOS

        assert 'whatsapp' in {c.canal for c in CANAIS_DO_RESGATE}
        assert 'whatsapp' not in {c.canal for c in CONTACTOS}


class TestAAutorizacaoDaRota:
    """🛡️ Quem diz de quem são estes contactos é a SESSÃO do navegador que
    resgatou — nunca o `payment_token`.

    A pessoa ainda não tem sessão de utilizador (no Plex nem sempre passa a
    ter), por isso alguma coisa tem de identificar o perfil. Não pode ser o
    `payment_token` que a resposta do resgate também leva: ele viaja por
    Telegram e WhatsApp e fica no histórico dessas conversas para sempre, por
    isso quem apanhasse um link antigo passaria a poder apontar as notificações
    de outra pessoa — e com elas o link de pagamento — para si.
    """

    def _marcar(self, client, media_user_id=ALVO, minutos_atras=0):
        from datetime import datetime, timedelta, timezone

        quando = datetime.now(timezone.utc) - timedelta(minutes=minutos_atras)
        with client.session_transaction() as sessao:
            sessao['resgate_recente'] = {
                'media_user_id': media_user_id,
                'codigo_telegram': 'abc123',
                'em': quando.isoformat(),
            }

    def test_sem_marca_na_sessao_a_rota_recusa(self, app, client, db_session, config_file):
        config_file(IS_CONFIGURED=True, WHATSAPP_ENABLED=True)
        resposta = client.post('/api/invites/claim/contacts', json={'whatsapp': '5521999998888'})
        assert resposta.status_code == 403

    def test_com_a_marca_grava(self, app, client, db_session, config_file):
        config_file(IS_CONFIGURED=True, WHATSAPP_ENABLED=True)
        with app.app_context():
            _perfil(ALVO, 'ana')
        self._marcar(client)

        resposta = client.post('/api/invites/claim/contacts', json={'whatsapp': '5521999998888'})
        assert resposta.status_code == 200, resposta.get_data(as_text=True)

        with app.app_context():
            assert extensions.data_manager.get_user_profile(ALVO)['phone_number'] == '5521999998888'

    def test_uma_marca_velha_deixa_de_servir(self, app, client, db_session, config_file):
        """⚠️ E sai do cookie: uma chave de escrita a envelhecer num navegador
        partilhado é pior do que não haver atalho nenhum."""
        config_file(IS_CONFIGURED=True, WHATSAPP_ENABLED=True)
        with app.app_context():
            _perfil(ALVO, 'ana')
        self._marcar(client, minutos_atras=60 * 5)

        resposta = client.post('/api/invites/claim/contacts', json={'whatsapp': '5521999998888'})
        assert resposta.status_code == 403

        with client.session_transaction() as sessao:
            assert 'resgate_recente' not in sessao

    def test_o_contacto_de_outra_pessoa_responde_409(self, app, client, db_session, config_file):
        """409 e não 400: o pedido está certo, é o estado que não deixa — a
        mesma distinção que o `ESTADO_HTTP` faz no resto do ficheiro."""
        config_file(IS_CONFIGURED=True, WHATSAPP_ENABLED=True)
        with app.app_context():
            _perfil(OUTRA, 'joana', phone_number='5521999998888')
            _perfil(ALVO, 'ana')
        self._marcar(client)

        resposta = client.post('/api/invites/claim/contacts', json={'whatsapp': '5521999998888'})
        assert resposta.status_code == 409

    def test_um_telefone_mal_formado_responde_400(self, app, client, db_session, config_file):
        config_file(IS_CONFIGURED=True, WHATSAPP_ENABLED=True)
        with app.app_context():
            _perfil(ALVO, 'ana')
        self._marcar(client)

        resposta = client.post('/api/invites/claim/contacts', json={'whatsapp': '123'})
        assert resposta.status_code == 400


class TestOResgateNuncaCai:
    """⚠️ A conta já existe quando isto corre: a recolha de contactos é um
    extra, e um extra não derruba o que já aconteceu.

    O link do Telegram monta-se com uma chamada de rede (`getMe`). Sem este
    guarda, um bot fora do ar fazia a pessoa ver um erro DEPOIS de a conta estar
    criada — e tentar de novo dava "você já resgatou este convite". É a mesma
    decisão do `_dar_acesso_aos_pedidos` e do aviso ao administrador.
    """

    def test_uma_falha_a_preparar_os_contactos_nao_estraga_o_resgate(
            self, app, client, config_file, monkeypatch):
        """A rota tem de responder 200: a conta foi criada e o acesso foi dado."""
        from app.blueprints.api import invites as invites_module

        config_file(IS_CONFIGURED=True)

        class ServidorFalso:
            def conta_a_partir_de_credenciais(self, dados):
                return type('Conta', (), {'username': 'ana', 'id': ALVO})(), None

            def claim_invitation(self, code, conta):
                return {'success': True, 'user_data': {'media_user_id': ALVO}}

        monkeypatch.setattr(invites_module, 'media_server', ServidorFalso())
        monkeypatch.setattr(invites_module, '_registar_resgate', lambda *a, **k: None)
        monkeypatch.setattr(
            invites_module, '_preparar_recolha_de_contactos',
            lambda _r: (_ for _ in ()).throw(RuntimeError('o Telegram não respondeu')))

        resposta = client.post('/api/invites/claim', json={'code': 'abc'})

        assert resposta.status_code == 200, resposta.get_data(as_text=True)
        assert resposta.get_json()['success'] is True

    def test_sem_media_user_id_o_passo_e_saltado_em_vez_de_rebentar(self, app, config_file):
        from app.blueprints.api import invites as invites_module

        config_file(IS_CONFIGURED=True)
        resultado = {'success': True, 'user_data': {}}
        with app.test_request_context('/'):
            invites_module._preparar_recolha_de_contactos(resultado)

        assert 'contact_channels' not in resultado['user_data']

    def test_um_resgate_falhado_nao_marca_a_sessao(self, app, config_file):
        from flask import session

        from app.blueprints.api import invites as invites_module

        config_file(IS_CONFIGURED=True)
        with app.test_request_context('/'):
            invites_module._preparar_recolha_de_contactos({'success': False})
            assert 'resgate_recente' not in session
