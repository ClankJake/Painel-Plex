# tests/test_push_nas_notificacoes.py
"""O push como CANAL: onde entra nas notificações que já existiam.

Duas coisas que estes testes existem para prender:

- 🛡️ **a senha e o link de redefinição NUNCA vão por push.** Uma notificação
  push aparece na tela de bloqueio, à vista de quem estiver perto, e fica
  guardada pelo sistema operacional fora do painel;
- ⚠️ **quem só ligou as notificações do celular conta como tendo contato.** O
  push não se vê no perfil (está na tabela dos aparelhos), e sem isso o envio em
  massa ignorava essas pessoas em silêncio.
"""

import pytest

from app.services import web_push
from app.services.notifier_manager import NotifierManager

pytestmark = pytest.mark.integration


@pytest.fixture()
def config_com_push(app_context, db_session, config_file):
    from app import extensions

    par = web_push.gerar_par_de_chaves()
    valores = config_file(IS_CONFIGURED=True, PUSH_ENABLED=True,
                          PUSH_VAPID_PUBLIC_KEY=par['publica'],
                          PUSH_VAPID_PRIVATE_KEY=par['privada'],
                          TELEGRAM_ENABLED=False, DISCORD_ENABLED=False,
                          WHATSAPP_ENABLED=False, WEBHOOK_ENABLED=False)
    extensions.push_manager.reload_credentials()
    yield valores
    extensions.push_manager.reload_credentials()


@pytest.fixture()
def entregas(monkeypatch):
    from app.services import push_manager as modulo

    feitas = []

    def _falsa(subscricao, payload, privada, assunto, ttl=86400, urgencia='normal', sessao=None):
        feitas.append(payload)
        return 201

    monkeypatch.setattr(modulo.web_push, 'enviar', _falsa)
    return feitas


@pytest.fixture()
def pessoa_com_aparelho(db_session):
    from app import extensions
    from app.extensions import db
    from app.models import UserProfile

    db.session.add(UserProfile(media_user_id="7", username="joana", status="active",
                               payment_token="tok", screen_limit=1))
    db.session.commit()
    extensions.data_manager.registar_push_subscription(
        "7", "https://fcm.googleapis.com/wp/joana",
        "p256dh-de-teste", "auth-de-teste")
    return {'media_user_id': "7", 'username': "joana", 'name': "Joana",
            'screen_limit': 1, 'payment_token': "tok", 'expiration_date': None}


class TestOCanalPush:
    def test_a_renovacao_chega_ao_aparelho(self, config_com_push, entregas,
                                           pessoa_com_aparelho):
        notificador = NotifierManager()
        resultado = notificador._prepare_and_send(
            'renewal', {'username': 'joana'}, pessoa_com_aparelho,
            {'new_date': '01/10/2026'}, config=config_com_push)

        assert resultado['sent'] == ['Push']
        assert entregas[0]['title']
        assert '01/10/2026' in entregas[0]['body']
        # O destino é uma rota do painel: abre a aba que já estiver aberta.
        assert entregas[0]['url'] == '/account'

    def test_o_aviso_de_vencimento_leva_ao_pagamento(self, config_com_push, entregas,
                                                     pessoa_com_aparelho):
        config_com_push['APP_BASE_URL'] = 'https://painel.exemplo.test'
        notificador = NotifierManager()
        notificador._prepare_and_send(
            'expiration', {'username': 'joana'}, pessoa_com_aparelho,
            {'days': 2, 'date': '03/10/2026'}, config=config_com_push)

        assert entregas[0]['url'].startswith('https://painel.exemplo.test/pay/')

    @pytest.mark.parametrize("evento, contexto", [
        ('credentials', {'new_username': 'joana', 'new_password': 'sPzX9-segredo'}),
        ('password_reset', {'reset_link': 'https://painel.test/password/reset/abc',
                            'reset_minutes': 15}),
    ])
    def test_a_senha_e_o_link_de_redefinicao_nunca_vao_por_push(
            self, config_com_push, entregas, pessoa_com_aparelho, evento, contexto):
        notificador = NotifierManager()
        resultado = notificador._prepare_and_send(
            evento, {'username': 'joana'}, pessoa_com_aparelho, contexto,
            config=config_com_push)

        assert entregas == []
        assert 'Push' not in resultado['sent']

    def test_sem_aparelho_nenhum_o_canal_nao_conta(self, config_com_push, entregas,
                                                   db_session):
        from app.extensions import db
        from app.models import UserProfile

        db.session.add(UserProfile(media_user_id="9", username="rui", status="active"))
        db.session.commit()
        notificador = NotifierManager()
        resultado = notificador._prepare_and_send(
            'renewal', {'username': 'rui'},
            {'media_user_id': "9", 'username': 'rui', 'screen_limit': 0},
            {'new_date': '01/10/2026'}, config=config_com_push)

        assert resultado == {'sent': [], 'failed': []}


class TestEnvioEmMassa:
    def test_quem_so_tem_push_conta_como_tendo_contato(self, config_com_push,
                                                      pessoa_com_aparelho):
        """⚠️ Sem isto, a pessoa era ignorada em silêncio — com o canal ligado."""
        aparelhos = {"7": [{'endpoint': 'https://fcm.googleapis.com/wp/joana'}]}
        elegiveis, ignorados = NotifierManager._split_by_reachability(
            [{'id': "7", 'username': 'joana'}], {"7": pessoa_com_aparelho},
            config_com_push, aparelhos)

        assert [u['id'] for u in elegiveis] == ["7"]
        assert ignorados == []

    def test_sem_canal_nenhum_continua_a_ser_ignorado(self, config_com_push,
                                                     pessoa_com_aparelho):
        elegiveis, ignorados = NotifierManager._split_by_reachability(
            [{'id': "7", 'username': 'joana'}], {"7": pessoa_com_aparelho},
            config_com_push, {})

        assert elegiveis == []
        assert [u['id'] for u in ignorados] == ["7"]


class TestPedidosDeConteudo:
    """O webhook do Seerr: quem pediu recebe o estado, o dono recebe o pedido novo."""

    def _webhook(self, tipo="MEDIA_PENDING", email="ninguem@exemplo.test"):
        return {
            'notification_type': tipo,
            'subject': 'Duna (2021)',
            'message': 'Um filme de areia.',
            'media': {'media_type': 'movie', 'tmdbId': 438631, 'status': 'PENDING'},
            'request': {'request_id': 1, 'requestedBy_email': email,
                        'requestedBy_username': 'joana'},
        }

    def test_o_dono_e_avisado_mesmo_sem_perfil_local(self, config_com_push, entregas):
        """⚠️ O webhook morria aqui em silêncio — e quem aprova é o administrador."""
        from app import extensions
        from app.models import Notification

        extensions.push_manager.registar(
            None, {'endpoint': 'https://web.push.apple.com/dono',
                   'keys': {'p256dh': 'p', 'auth': 'a'}})

        resposta = extensions.overseerr_manager.handle_notification_webhook(self._webhook())

        assert resposta['success'] is True
        assert len(entregas) == 1
        assert 'Duna' in entregas[0]['body']
        # E fica também no sino do painel, para quem não usa push.
        assert Notification.query.filter_by(media_user_id=None).count() == 1

    def test_um_pedido_sem_nome_de_quem_pediu_nao_rebenta(self, config_com_push, entregas):
        """⚠️ `perfil` é None e o Seerr mandou só o email: era um AttributeError."""
        from app import extensions

        pedido = self._webhook()
        pedido['request']['requestedBy_username'] = ''

        resposta = extensions.overseerr_manager.handle_notification_webhook(pedido)
        assert resposta['success'] is True

    def test_um_pedido_que_so_mudou_de_estado_nao_incomoda_o_dono(self, config_com_push,
                                                                 entregas):
        """O dono quer saber do que ENTRA; o resto é para quem pediu."""
        from app import extensions
        from app.models import Notification

        extensions.push_manager.registar(
            None, {'endpoint': 'https://web.push.apple.com/dono',
                   'keys': {'p256dh': 'p', 'auth': 'a'}})

        extensions.overseerr_manager.handle_notification_webhook(
            self._webhook(tipo="MEDIA_AVAILABLE"))

        assert entregas == []
        assert Notification.query.count() == 0

    def test_quem_pediu_recebe_no_aparelho(self, config_com_push, entregas,
                                           pessoa_com_aparelho):
        from app import extensions
        from app.extensions import db
        from app.models import UserProfile

        perfil = UserProfile.query.get("7")
        perfil.email = "joana@exemplo.test"
        db.session.commit()

        extensions.overseerr_manager.handle_notification_webhook(
            self._webhook(tipo="MEDIA_AVAILABLE", email="joana@exemplo.test"))

        assert len(entregas) == 1
        assert 'Duna' in entregas[0]['body']
