# tests/test_rotas_de_push.py
"""As rotas que ligam e desligam as notificações de um aparelho.

🛡️ A decisão que estes testes prendem: **quem é o dono do aparelho é o
SERVIDOR que decide**, a partir da sessão. O corpo do pedido só traz o endereço
e as chaves. Aceitar um dono vindo do navegador deixava qualquer pessoa com
sessão receber os avisos de pagamento do administrador.
"""

import pytest

from app.services import web_push

pytestmark = pytest.mark.integration


@pytest.fixture()
def push_ligado(app_context, db_session, config_file):
    """Painel configurado, com as notificações push ligadas e com chaves."""
    from app import extensions

    par = web_push.gerar_par_de_chaves()
    config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1",
                PUSH_ENABLED=True,
                PUSH_VAPID_PUBLIC_KEY=par['publica'],
                PUSH_VAPID_PRIVATE_KEY=par['privada'])
    extensions.push_manager.reload_credentials()
    yield par
    # O gestor é um singleton do processo: sem isto, ficava ligado para os
    # testes seguintes com uma configuração que já não existe.
    extensions.push_manager.reload_credentials()


def _perfil(media_user_id):
    from app.extensions import db
    from app.models import UserProfile

    db.session.add(UserProfile(media_user_id=media_user_id,
                               username=f"pessoa-{media_user_id}", status="active"))
    db.session.commit()


def _autenticar(client, media_user_id, role):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": str(media_user_id),
                                  "username": f"pessoa-{media_user_id}",
                                  "email": f"p{media_user_id}@exemplo.test", "role": role}
        sessao["_user_id"] = str(media_user_id)
        sessao["_fresh"] = True


def _corpo(sufixo="a"):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    chave = ec.generate_private_key(ec.SECP256R1())
    publica = chave.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {
        'endpoint': f'https://push.exemplo.test/aparelho/{sufixo}',
        'keys': {'p256dh': web_push.b64url(publica),
                 'auth': web_push.b64url(b'0123456789abcdef')},
    }


class TestQuemFicaDonoDoAparelho:
    def test_o_administrador_subscreve_como_administrador(self, client, push_ligado):
        from app.models import PushSubscription

        _autenticar(client, "1", "admin")
        resposta = client.post('/api/notifications/push/subscribe', json=_corpo())

        assert resposta.status_code == 200
        assert PushSubscription.query.one().media_user_id is None

    def test_um_usuario_comum_subscreve_em_nome_proprio(self, client, push_ligado):
        from app.models import PushSubscription

        _perfil("42")
        _autenticar(client, "42", "user")
        resposta = client.post('/api/notifications/push/subscribe', json=_corpo())

        assert resposta.status_code == 200
        assert PushSubscription.query.one().media_user_id == "42"

    def test_um_dono_vindo_do_pedido_e_ignorado(self, client, push_ligado):
        """Mandar `media_user_id` no corpo não muda nada: quem manda é a sessão."""
        from app.models import PushSubscription

        _perfil("42")
        _autenticar(client, "42", "user")
        corpo = _corpo()
        corpo['media_user_id'] = None
        client.post('/api/notifications/push/subscribe', json=corpo)

        assert PushSubscription.query.one().media_user_id == "42"

    def test_sem_sessao_nao_se_subscreve(self, client, push_ligado):
        resposta = client.post('/api/notifications/push/subscribe', json=_corpo())
        assert resposta.status_code in (302, 401)


class TestValidacao:
    def test_recusa_um_endereco_que_nao_seja_https(self, client, push_ligado):
        """🛡️ O painel faz POST para este endereço a partir do servidor."""
        _autenticar(client, "1", "admin")
        corpo = _corpo()
        corpo['endpoint'] = 'http://interno.exemplo.test/aparelho'
        resposta = client.post('/api/notifications/push/subscribe', json=corpo)

        assert resposta.status_code == 400

    def test_recusa_uma_chave_com_o_tamanho_errado(self, client, push_ligado):
        """Gravada, ela só daria erro semanas depois, a cada notificação."""
        _autenticar(client, "1", "admin")
        corpo = _corpo()
        corpo['keys']['auth'] = web_push.b64url(b'curta-demais-aqui')
        resposta = client.post('/api/notifications/push/subscribe', json=corpo)

        assert resposta.status_code == 400

    def test_com_o_push_desligado_responde_409(self, client, app_context, db_session,
                                               config_file):
        from app import extensions

        config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1",
                    PUSH_ENABLED=False)
        extensions.push_manager.reload_credentials()
        _autenticar(client, "1", "admin")

        resposta = client.post('/api/notifications/push/subscribe', json=_corpo())
        assert resposta.status_code == 409


class TestRemocao:
    def test_desligar_apaga_a_linha(self, client, push_ligado):
        from app.models import PushSubscription

        _autenticar(client, "1", "admin")
        corpo = _corpo()
        client.post('/api/notifications/push/subscribe', json=corpo)
        resposta = client.post('/api/notifications/push/unsubscribe',
                               json={'endpoint': corpo['endpoint']})

        assert resposta.status_code == 200
        assert PushSubscription.query.count() == 0


class TestTeste:
    def test_sem_aparelho_nenhum_diz_o_que_falta(self, client, push_ligado):
        _autenticar(client, "1", "admin")
        resposta = client.post('/api/notifications/push/test')

        assert resposta.status_code == 200
        assert resposta.get_json()['success'] is False

    def test_entrega_aos_aparelhos_de_quem_pediu(self, client, push_ligado, monkeypatch):
        from app.services import push_manager as modulo

        monkeypatch.setattr(modulo.web_push, 'enviar', lambda *a, **k: 201)
        _autenticar(client, "1", "admin")
        client.post('/api/notifications/push/subscribe', json=_corpo())

        resposta = client.post('/api/notifications/push/test')
        assert resposta.get_json() == {'success': True, 'sent': 1,
                                       'message': resposta.get_json()['message']}
