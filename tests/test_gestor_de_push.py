# tests/test_gestor_de_push.py
"""Quem recebe uma notificação push, e o que acontece quando ela não chega.

⚠️ O fio condutor destes testes é a convenção que o painel já tinha e que o push
herdou: **`media_user_id=None` é o ADMINISTRADOR**. Trocar isso não dá erro
nenhum — dá o aviso de um pagamento a chegar ao celular errado.
"""

import pytest

from app.services import web_push
from app.services.push_manager import PushManager

pytestmark = pytest.mark.integration


@pytest.fixture()
def perfil(db_session):
    """As subscrições de uma pessoa apontam para o perfil dela (chave estrangeira)."""
    from app.extensions import db
    from app.models import UserProfile

    linha = UserProfile(media_user_id="7", username="joana", status="active")
    db.session.add(linha)
    db.session.commit()
    return linha


@pytest.fixture()
def gestor(app_context, db_session, config_file):
    from app.services.data_manager import DataManager

    par = web_push.gerar_par_de_chaves()
    config_file(PUSH_ENABLED=True,
                PUSH_VAPID_PUBLIC_KEY=par['publica'],
                PUSH_VAPID_PRIVATE_KEY=par['privada'],
                PUSH_VAPID_SUBJECT="mailto:dono@exemplo.test")
    return PushManager(data_manager=DataManager())


def _subscricao(sufixo):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    chave = ec.generate_private_key(ec.SECP256R1())
    publica = chave.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {
        'endpoint': f'https://fcm.googleapis.com/wp/{sufixo}',
        'keys': {'p256dh': web_push.b64url(publica),
                 'auth': web_push.b64url(b'0123456789abcdef')},
    }


@pytest.fixture()
def entregas(monkeypatch):
    """Substitui a entrega real e guarda o que teria sido enviado."""
    from app.services import push_manager as modulo

    feitas = []

    def _falsa(subscricao, payload, privada, assunto, ttl=86400, urgencia='normal', sessao=None):
        feitas.append({'endpoint': subscricao['endpoint'], 'payload': payload,
                       'assunto': assunto, 'urgencia': urgencia})
        return 201

    monkeypatch.setattr(modulo.web_push, 'enviar', _falsa)
    return feitas


class TestRegisto:
    def test_o_mesmo_aparelho_nao_fica_duplicado(self, gestor, perfil):
        """O navegador devolve sempre o mesmo endereço; duas linhas entregavam a dobrar."""
        from app.models import PushSubscription

        gestor.registar("7", _subscricao("a"))
        gestor.registar("7", _subscricao("a"))

        assert PushSubscription.query.count() == 1

    def test_o_aparelho_muda_de_dono_com_a_sessao(self, gestor, perfil):
        """O mesmo computador, primeiro o administrador e depois a Joana."""
        subscricao = _subscricao("a")
        gestor.registar(None, subscricao)
        gestor.registar("7", subscricao)

        assert gestor.aparelhos_de(None) == []
        assert len(gestor.aparelhos_de("7")) == 1

    def test_uma_subscricao_incompleta_e_recusada(self, gestor):
        with pytest.raises(ValueError):
            gestor.registar(None, {'endpoint': 'https://fcm.googleapis.com/wp/x'})

    def test_o_administrador_subscreve_sem_perfil_local(self, gestor, db_session):
        """⚠️ O dono do painel pode ainda não ter perfil — e tem de poder ligar isto."""
        gestor.registar(None, _subscricao("dono"))
        assert len(gestor.aparelhos_de(None)) == 1


class TestEnvio:
    def test_entrega_a_todos_os_aparelhos_da_pessoa(self, gestor, perfil, entregas):
        gestor.registar("7", _subscricao("celular"))
        gestor.registar("7", _subscricao("computador"))

        assert gestor.enviar("7", "Oi", "tudo certo") == 2
        assert {e['payload']['title'] for e in entregas} == {"Oi"}

    def test_nao_entrega_ao_administrador_o_que_e_da_pessoa(self, gestor, perfil, entregas):
        gestor.registar("7", _subscricao("celular"))
        assert gestor.enviar(None, "Oi", "isto é do dono") == 0
        assert entregas == []

    def test_desligado_no_config_nao_envia_nada(self, app_context, db_session,
                                                config_file, perfil, entregas):
        from app.services.data_manager import DataManager

        par = web_push.gerar_par_de_chaves()
        config_file(PUSH_ENABLED=False, PUSH_VAPID_PUBLIC_KEY=par['publica'],
                    PUSH_VAPID_PRIVATE_KEY=par['privada'])
        gestor = PushManager(data_manager=DataManager())
        gestor.data_manager.registar_push_subscription(
            "7", "https://fcm.googleapis.com/wp/x", "p", "a")

        assert gestor.enviar("7", "Oi", "corpo") == 0
        assert entregas == []

    def test_sem_chaves_nao_envia_nada(self, app_context, db_session, config_file, entregas):
        from app.services.data_manager import DataManager

        config_file(PUSH_ENABLED=True, PUSH_VAPID_PUBLIC_KEY="", PUSH_VAPID_PRIVATE_KEY="")
        gestor = PushManager(data_manager=DataManager())
        assert gestor.disponivel is False
        assert gestor.enviar(None, "Oi", "corpo") == 0

    def test_o_titulo_e_o_corpo_sao_cortados(self, gestor, perfil, entregas):
        """Um corpo enorme estoura o limite do serviço de push, que responde 413."""
        gestor.registar("7", _subscricao("celular"))
        gestor.enviar("7", "T" * 300, "C" * 900)

        assert len(entregas[0]['payload']['title']) <= 80
        assert len(entregas[0]['payload']['body']) <= 300

    def test_uma_subscricao_morta_e_apagada(self, gestor, perfil, monkeypatch):
        """🐛 Sem isto, um 410 dava um erro no log por cada notificação, para sempre."""
        from app.models import PushSubscription
        from app.services import push_manager as modulo

        gestor.registar("7", _subscricao("celular"))

        def _morta(*args, **kwargs):
            raise web_push.PushExpirado("410")

        monkeypatch.setattr(modulo.web_push, 'enviar', _morta)
        assert gestor.enviar("7", "Oi", "corpo") == 0
        assert PushSubscription.query.count() == 0

    def test_uma_recusa_nao_apaga_a_subscricao(self, gestor, perfil, monkeypatch):
        """Um 429 ou um serviço em baixo são temporários: a linha fica."""
        from app.models import PushSubscription
        from app.services import push_manager as modulo

        gestor.registar("7", _subscricao("celular"))
        monkeypatch.setattr(modulo.web_push, 'enviar', lambda *a, **k: (_ for _ in ()).throw(
            web_push.PushRecusado("503")))

        assert gestor.enviar("7", "Oi", "corpo") == 0
        assert PushSubscription.query.count() == 1


class TestAvisosDoAdministrador:
    def test_respeita_o_interruptor_de_cada_assunto(self, app_context, db_session,
                                                   config_file, entregas):
        from app.services.data_manager import DataManager

        par = web_push.gerar_par_de_chaves()
        config_file(PUSH_ENABLED=True, PUSH_VAPID_PUBLIC_KEY=par['publica'],
                    PUSH_VAPID_PRIVATE_KEY=par['privada'],
                    PUSH_ADMIN_PAYMENTS=False, PUSH_ADMIN_MEDIA_REQUESTS=True)
        gestor = PushManager(data_manager=DataManager())
        gestor.registar(None, _subscricao("dono"))

        assert gestor.enviar_ao_administrador('pagamento', "Pago", "R$ 30") == 0
        assert gestor.enviar_ao_administrador('pedido', "Pedido", "um filme") == 1

    def test_um_assunto_desconhecido_passa(self, gestor, entregas):
        """Um aviso novo nunca deve ficar calado por esquecimento de configuração."""
        gestor.registar(None, _subscricao("dono"))
        assert gestor.enviar_ao_administrador('coisa-nova', "Título", "corpo") == 1


class TestChaves:
    def test_gera_o_par_na_primeira_vez(self, app_context, db_session, config_file):
        from app.config import load_or_create_config
        from app.services.data_manager import DataManager

        config_file(PUSH_ENABLED=True, PUSH_VAPID_PUBLIC_KEY="", PUSH_VAPID_PRIVATE_KEY="")
        gestor = PushManager(data_manager=DataManager())
        publica = gestor.garantir_chaves()

        assert publica
        assert load_or_create_config()['PUSH_VAPID_PUBLIC_KEY'] == publica

    def test_nao_substitui_um_par_que_ja_existe(self, gestor):
        """⚠️ Trocar a chave invalida TODAS as subscrições já feitas, de uma vez."""
        antes = gestor.chave_publica
        assert gestor.garantir_chaves() == antes
        assert gestor.chave_publica == antes

    def test_repara_um_par_desemparelhado(self, app_context, db_session, config_file):
        """Uma edição manual do config.json dava um 403 a cada envio e mais nada."""
        from app.services.data_manager import DataManager

        certo = web_push.gerar_par_de_chaves()
        outro = web_push.gerar_par_de_chaves()
        config_file(PUSH_ENABLED=True, PUSH_VAPID_PUBLIC_KEY=outro['publica'],
                    PUSH_VAPID_PRIVATE_KEY=certo['privada'])
        gestor = PushManager(data_manager=DataManager())

        assert gestor.garantir_chaves() == certo['publica']

    def test_o_assunto_recai_no_endereco_do_painel(self, app_context, db_session, config_file):
        from app.services.data_manager import DataManager

        par = web_push.gerar_par_de_chaves()
        config_file(PUSH_ENABLED=True, PUSH_VAPID_PUBLIC_KEY=par['publica'],
                    PUSH_VAPID_PRIVATE_KEY=par['privada'], PUSH_VAPID_SUBJECT="",
                    APP_BASE_URL="https://painel.exemplo.test/")
        gestor = PushManager(data_manager=DataManager())

        assert gestor._assunto_do_jwt() == "https://painel.exemplo.test"
