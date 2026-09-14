# tests/test_reposicao_de_palavra_passe.py

"""O "esqueci-me da palavra-passe", para servidores de contas locais.

⚠️ **Só existe onde as contas são LOCAIS.** Num painel Plex a palavra-passe vive
no plex.tv — o painel nunca a vê (é para isso que o fluxo de PIN existe) e não a
pode repor. Oferecer o botão ali seria prometer o que não se pode cumprir.

O caminho é o de sempre neste painel: **o contacto que a pessoa já registou**.
Não sai email daqui — o painel nunca enviou emails — por isso o link vai por
Telegram, Discord, WhatsApp ou webhook, como os avisos de vencimento.

Três coisas que este ficheiro guarda, e que são a razão de o módulo existir:

🛡️ a resposta é SEMPRE a mesma, exista a conta ou não;
🛡️ o que fica na base de dados é o RESUMO do token, não o token;
🛡️ o link vale minutos e serve uma só vez.
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.services.media_server.base import MediaServerCapabilities

pytestmark = pytest.mark.integration

GUID = "38c3a1f0e4b24d7f9c1a0b5e6d7f8a90"


class BackendFalso:
    def __init__(self, cria_contas=True, aceita=True):
        self.SERVER_TYPE = 'jellyfin' if cria_contas else 'plex'
        self.DISPLAY_NAME = 'Jellyfin' if cria_contas else 'Plex Media Server'
        self.SHORT_NAME = 'Jellyfin' if cria_contas else 'Plex'
        self.CAPABILITIES = MediaServerCapabilities(
            convites_nativos=not cria_contas, cria_contas=cria_contas,
            fontes_media_online=not cria_contas, login_delegado=not cria_contas,
            desativa_conta=cria_contas, links_profundos=True,
            estatisticas=True, estatisticas_externas=not cria_contas,
            corte_forcado=cria_contas,
        )
        self._aceita = aceita
        self.definidas = []
        self.autenticacoes = []
        self.palavra_passe_atual = 'a-antiga'
        self.notifier_manager = self
        self.enviadas = []
        self.entrega = {'sent': ['Telegram'], 'failed': []}

    @property
    def capabilities(self):
        return self.CAPABILITIES

    def estatisticas_disponiveis(self):
        return False

    def authenticate(self, username, password):
        # ⚠️ É o que confirma a palavra-passe ATUAL: o duplo tem de o modelar,
        # ou o teste passa por cima da verificação que interessa.
        from app.services.media_server.base import OwnerAccount

        self.autenticacoes.append((username, password))
        if password != self.palavra_passe_atual:
            return None
        return OwnerAccount(id=GUID, username=username, email=None, thumb=None)

    def definir_palavra_passe(self, media_user_id, nova):
        self.definidas.append((media_user_id, nova))
        if not self._aceita:
            return {"success": False, "message": "o servidor recusou"}
        return {"success": True, "message": "ok"}

    # --- notificador ---
    def send_password_reset_notification(self, user, perfil, link, minutos):
        self.enviadas.append({'user': user, 'link': link, 'minutos': minutos})
        return self.entrega


@pytest.fixture()
def servidor(monkeypatch):
    def instalar(cria_contas=True, aceita=True):
        from app import extensions
        from app.blueprints import auth as auth_module
        from app.blueprints import main as main_module

        backend = BackendFalso(cria_contas, aceita)
        monkeypatch.setattr(extensions, 'media_server', backend)
        monkeypatch.setattr(auth_module, 'media_server', backend)
        monkeypatch.setattr(main_module, 'media_server', backend, raising=False)
        return backend

    return instalar


@pytest.fixture(autouse=True)
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, APP_BASE_URL="https://painel.exemplo.test")


@pytest.fixture(autouse=True)
def sem_travoes():
    from app.extensions import cache, limiter

    limiter.reset()
    cache.clear()
    yield
    limiter.reset()
    cache.clear()


@pytest.fixture()
def pessoa(data_manager):
    def criar(**extra):
        dados = {'username': 'ana', 'email': 'ana@exemplo.test', 'telegram_user': '12345'}
        dados.update(extra)
        return data_manager.set_user_profile(GUID, dados)

    return criar


# ---------------------------------------------------------------- o pedido

class TestPedirOLink:
    def test_envia_o_link_por_quem_a_pessoa_registou(self, client, db_session, servidor, pessoa):
        backend = servidor()
        pessoa()

        resposta = client.post('/auth/password/forgot', json={"identifier": "ana"})

        assert resposta.get_json()['success'] is True
        assert len(backend.enviadas) == 1
        assert backend.enviadas[0]['link'].startswith('https://painel.exemplo.test/password/reset/')

    def test_o_email_tambem_serve_para_o_encontrar(self, client, db_session, servidor, pessoa):
        backend = servidor()
        pessoa()

        client.post('/auth/password/forgot', json={"identifier": "ana@exemplo.test"})

        assert len(backend.enviadas) == 1

    def test_a_resposta_e_a_mesma_para_quem_nao_existe(self, client, db_session, servidor, pessoa):
        # 🛡️ Dizer "utilizador não encontrado" faria desta rota um oráculo sobre
        # quais as contas que existem neste servidor.
        backend = servidor()
        pessoa()

        existe = client.post('/auth/password/forgot', json={"identifier": "ana"})
        nao_existe = client.post('/auth/password/forgot', json={"identifier": "ninguem"})

        assert existe.status_code == nao_existe.status_code
        assert existe.get_json() == nao_existe.get_json()
        assert len(backend.enviadas) == 1

    def test_e_a_mesma_para_quem_nao_tem_contacto_nenhum(self, client, db_session, servidor, pessoa):
        backend = servidor()
        pessoa(telegram_user=None)

        resposta = client.post('/auth/password/forgot', json={"identifier": "ana"})

        assert resposta.get_json()['success'] is True
        assert backend.enviadas == []

    def test_pedir_duas_vezes_seguidas_so_envia_uma(self, client, db_session, servidor, pessoa):
        # 🛡️ Sem intervalo, esta rota era um botão para encher o Telegram de
        # outra pessoa com mensagens que o painel assina.
        backend = servidor()
        pessoa()

        client.post('/auth/password/forgot', json={"identifier": "ana"})
        client.post('/auth/password/forgot', json={"identifier": "ana"})

        assert len(backend.enviadas) == 1

    def test_num_painel_plex_a_rota_recusa(self, client, db_session, servidor, pessoa):
        servidor(cria_contas=False)
        pessoa()

        resposta = client.post('/auth/password/forgot', json={"identifier": "ana"})

        assert resposta.status_code == 400
        assert 'externa' in resposta.get_json()['message']


class TestOQueFicaGuardado:
    def test_a_base_de_dados_guarda_o_resumo_e_nao_o_token(self, app_context, db_session, data_manager, pessoa):
        # 🛡️ Quem lesse a base de dados — ou um ZIP de backup — ficava com uma
        # porta aberta por cada pedido válido.
        from app.models import PasswordReset

        pessoa()
        token = data_manager.criar_pedido_de_reposicao(GUID)

        guardado = PasswordReset.query.one()
        assert guardado.token_hash != token
        assert guardado.token_hash == hashlib.sha256(token.encode()).hexdigest()

    def test_pedir_de_novo_invalida_o_link_anterior(self, app_context, db_session, data_manager, pessoa):
        pessoa()
        primeiro = data_manager.criar_pedido_de_reposicao(GUID)
        segundo = data_manager.criar_pedido_de_reposicao(GUID)

        assert data_manager.ler_pedido_de_reposicao(primeiro) == (None, 'invalido')
        assert data_manager.ler_pedido_de_reposicao(segundo)[0] == GUID


class TestAValidadeDoLink:
    def test_serve_uma_vez_so(self, app_context, db_session, data_manager, pessoa):
        pessoa()
        token = data_manager.criar_pedido_de_reposicao(GUID)

        assert data_manager.consumir_pedido_de_reposicao(token)[0] == GUID
        assert data_manager.consumir_pedido_de_reposicao(token) == (None, 'usado')

    def test_um_link_expirado_diz_que_expirou(self, app_context, db_session, data_manager, pessoa):
        # São três coisas diferentes para quem está do outro lado — "inválido"
        # para todas seria mentira em duas delas.
        from app.extensions import db
        from app.models import PasswordReset

        pessoa()
        token = data_manager.criar_pedido_de_reposicao(GUID)
        pedido = PasswordReset.query.one()
        pedido.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        db.session.commit()

        assert data_manager.ler_pedido_de_reposicao(token) == (None, 'expirado')

    def test_um_token_inventado_nao_e_nada(self, app_context, db_session, data_manager):
        assert data_manager.ler_pedido_de_reposicao('inventado') == (None, 'invalido')
        assert data_manager.ler_pedido_de_reposicao('') == (None, 'invalido')


# ------------------------------------------------------------- a reposição

class TestGravarAPalavraPasseNova:
    def _token(self, client, backend, data_manager):
        client.post('/auth/password/forgot', json={"identifier": "ana"})
        return backend.enviadas[0]['link'].rsplit('/', 1)[-1]

    def test_o_caminho_completo(self, client, db_session, servidor, pessoa, data_manager):
        backend = servidor()
        pessoa()
        token = self._token(client, backend, data_manager)

        resposta = client.post('/auth/password/reset',
                               json={"token": token, "password": "uma-nova-boa"})

        assert resposta.get_json()['success'] is True
        assert backend.definidas == [(GUID, "uma-nova-boa")]

    def test_o_mesmo_link_nao_serve_duas_vezes(self, client, db_session, servidor, pessoa, data_manager):
        backend = servidor()
        pessoa()
        token = self._token(client, backend, data_manager)

        client.post('/auth/password/reset', json={"token": token, "password": "uma-nova-boa"})
        segunda = client.post('/auth/password/reset', json={"token": token, "password": "outra-ainda"})

        assert segunda.status_code == 400
        assert 'já foi usado' in segunda.get_json()['message']
        assert len(backend.definidas) == 1

    def test_uma_palavra_passe_curta_e_recusada_antes_do_servidor(self, client, db_session,
                                                                   servidor, pessoa, data_manager):
        backend = servidor()
        pessoa()
        token = self._token(client, backend, data_manager)

        resposta = client.post('/auth/password/reset', json={"token": token, "password": "123"})

        assert resposta.status_code == 400
        assert backend.definidas == []

    def test_uma_palavra_passe_enorme_tambem(self, client, db_session, servidor, pessoa, data_manager):
        backend = servidor()
        pessoa()
        token = self._token(client, backend, data_manager)

        resposta = client.post('/auth/password/reset',
                               json={"token": token, "password": "x" * 100000})

        assert resposta.status_code == 400
        assert backend.definidas == []

    def test_um_servidor_que_recusa_diz_porque(self, client, db_session, servidor, pessoa, data_manager):
        backend = servidor(aceita=False)
        pessoa()
        token = self._token(client, backend, data_manager)

        resposta = client.post('/auth/password/reset',
                               json={"token": token, "password": "uma-nova-boa"})

        assert resposta.status_code == 400
        assert 'recusou' in resposta.get_json()['message']


# ----------------------------------------------------------------- páginas

class TestAsPaginas:
    def test_a_pagina_do_pedido_existe_num_servidor_de_contas_locais(self, client, db_session, servidor):
        servidor()

        resposta = client.get('/password/forgot')

        assert resposta.status_code == 200
        assert 'Utilizador ou e-mail' in resposta.get_data(as_text=True)

    def test_num_painel_plex_redireciona_para_o_login(self, client, db_session, servidor):
        # Esconder a ligação não chega: um marcador nos favoritos dava uma
        # página vazia sem explicação.
        servidor(cria_contas=False)

        resposta = client.get('/password/forgot')

        assert resposta.status_code == 302
        assert '/auth/login' in resposta.headers['Location']

    def test_o_login_so_oferece_o_link_onde_ele_leva_a_algum_lado(self, client, db_session, servidor):
        servidor()
        com = client.get('/auth/login').get_data(as_text=True)
        servidor(cria_contas=False)
        sem = client.get('/auth/login').get_data(as_text=True)

        assert 'Esqueci-me da palavra-passe' in com
        assert 'Esqueci-me da palavra-passe' not in sem

    def test_o_formulario_so_aparece_com_um_link_valido(self, client, db_session, servidor,
                                                        pessoa, data_manager):
        # ⚠️ Escrever duas vezes uma palavra-passe para só depois ouvir "o link
        # expirou" é trabalho deitado fora.
        backend = servidor()
        pessoa()
        client.post('/auth/password/forgot', json={"identifier": "ana"})
        token = backend.enviadas[0]['link'].rsplit('/', 1)[-1]

        assert client.get(f'/password/reset/{token}').status_code == 200

    def test_um_link_invalido_cai_no_pedido_de_um_novo(self, client, db_session, servidor):
        servidor()

        resposta = client.get('/password/reset/inventado')

        assert resposta.status_code == 400
        pagina = resposta.get_data(as_text=True)
        # A pessoa que clicou num link velho quer é pedir outro.
        assert 'Peça um novo' in pagina
        assert 'forgot-form' in pagina


class TestOBackendQueGravaAPalavraPasse:
    """`definir_palavra_passe` — a única porta para mudar uma palavra-passe."""

    def test_o_plex_diz_que_nao_e_dele(self):
        # A conta vive no plex.tv, que é quem autentica. Não é difícil: é
        # impossível — e é por isso que o botão não aparece lá.
        from app.services.media_server.plex.backend import PlexManager

        class _Duplo:
            def __getattr__(self, nome):
                return _Duplo()

            def __call__(self, *a, **k):
                return None

        resultado = PlexManager(_Duplo(), _Duplo(), _Duplo(), _Duplo()).definir_palavra_passe('1', 'x')

        assert resultado['success'] is False

    def _jellyfin(self, monkeypatch, existe=True, falha_em=None):
        from app.services.media_server.jellyfin.api_client import JellyfinApiError
        from app.services.media_server.jellyfin.backend import JellyfinManager

        pedidos = []

        class ApiFalsa:
            base_url = 'https://media.exemplo.test'

            def post(self, endpoint, json=None, **kwargs):
                pedidos.append((endpoint, json))
                if falha_em is not None and len(pedidos) == falha_em:
                    raise JellyfinApiError("recusado", status_code=400)
                return None

        class _Duplo:
            def __getattr__(self, nome):
                return _Duplo()

            def __call__(self, *a, **k):
                return None

        backend = JellyfinManager(_Duplo())
        backend.conn.api = ApiFalsa()
        monkeypatch.setattr(type(backend.conn), 'connected', property(lambda self: True))
        monkeypatch.setattr(backend, 'get_user_by_id',
                            lambda _id: {"id": _id, "username": "ana"} if existe else None)
        return backend, pedidos

    def test_sao_dois_pedidos_apagar_e_gravar(self, app_context, monkeypatch):
        # ⚠️ O painel não conhece a palavra-passe antiga e o Jellyfin exige-a
        # para a trocar. A saída é a mesma da interface do próprio servidor:
        # `ResetPassword` apaga-a, e só depois se grava a nova.
        backend, pedidos = self._jellyfin(monkeypatch)

        assert backend.definir_palavra_passe(GUID, 'nova')['success'] is True
        assert [p[1] for p in pedidos] == [
            {'ResetPassword': True},
            {'CurrentPw': '', 'NewPw': 'nova'},
        ]
        assert all(p[0] == f'/Users/{GUID}/Password' for p in pedidos)

    def test_uma_conta_que_nao_existe_nao_leva_pedido_nenhum(self, app_context, monkeypatch):
        backend, pedidos = self._jellyfin(monkeypatch, existe=False)

        assert backend.definir_palavra_passe(GUID, 'nova')['success'] is False
        assert pedidos == []

    def test_se_o_segundo_pedido_falhar_diz_se_que_falhou(self, app_context, monkeypatch, caplog):
        # 🛡️ A conta fica SEM palavra-passe. Deixar a pessoa a pensar que está
        # tudo bem, com a conta aberta, seria muito pior.
        import logging

        backend, _ = self._jellyfin(monkeypatch, falha_em=2)

        with caplog.at_level(logging.ERROR):
            resultado = backend.definir_palavra_passe(GUID, 'nova')

        assert resultado['success'] is False
        assert 'SEM PALAVRA-PASSE' in caplog.text

    def test_a_palavra_passe_nunca_chega_ao_log(self, app_context, monkeypatch, caplog):
        import logging

        backend, _ = self._jellyfin(monkeypatch)

        with caplog.at_level(logging.DEBUG):
            backend.definir_palavra_passe(GUID, 'um-segredo-muito-proprio')

        assert 'um-segredo-muito-proprio' not in caplog.text


class TestAsNotificacoes:
    """A mensagem que leva o link, e o que ela não pode deixar de dizer."""

    def test_ha_template_padrao_para_os_quatro_canais(self):
        from app.services.notifier_manager import DEFAULT_TEMPLATES

        for canal in ('TELEGRAM', 'DISCORD', 'WEBHOOK', 'WHATSAPP'):
            texto = DEFAULT_TEMPLATES.get(f'{canal}_PASSWORD_RESET_MESSAGE_TEMPLATE')
            assert texto, canal
            assert '{reset_link}' in texto
            # Quem recebe tem de saber quanto tempo tem.
            assert '{reset_minutes}' in texto

    def test_os_templates_json_sao_validos(self):
        import json

        from app.services.notifier_manager import DEFAULT_TEMPLATES

        for canal in ('DISCORD', 'WEBHOOK'):
            texto = DEFAULT_TEMPLATES[f'{canal}_PASSWORD_RESET_MESSAGE_TEMPLATE']
            json.loads(texto.replace('{reset_link}', 'https://x').replace('{reset_minutes}', '30'))

    def test_a_mensagem_leva_o_link_inteiro(self, app_context, monkeypatch, config_file):
        # ⚠️ Um link é interpolado SEM escape de HTML (ver `_format_template`):
        # com escape, um '&' no token partia o endereço.
        from app import extensions
        from app.services.notifier_manager import NotifierManager

        class Marca:
            SHORT_NAME = 'Jellyfin'

        monkeypatch.setattr(extensions, 'media_server', Marca())
        config_file(IS_CONFIGURED=True, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="x",
                    DISCORD_ENABLED=False, WHATSAPP_ENABLED=False, WEBHOOK_ENABLED=False)

        notificador = NotifierManager()
        enviadas = []
        monkeypatch.setattr(notificador, '_send_telegram_notification',
                            lambda mensagem, *a, **k: enviadas.append(mensagem))

        notificador.send_password_reset_notification(
            {"username": "ana", "id": GUID}, {"media_user_id": GUID, "telegram_id": "1"},
            "https://painel.exemplo.test/password/reset/abc-123_XYZ", 30,
        )

        assert "https://painel.exemplo.test/password/reset/abc-123_XYZ" in enviadas[0]
        assert "30" in enviadas[0]

    def test_sem_link_nao_se_envia_nada(self, app_context, monkeypatch, config_file):
        from app.services.notifier_manager import NotifierManager

        assert NotifierManager().send_password_reset_notification(
            {"username": "ana"}, {}, None, 30) == {'sent': [], 'failed': []}


class TestQuandoNenhumCanalAceita:
    def test_o_pedido_e_descartado(self, client, db_session, servidor, pessoa, data_manager):
        # ⚠️ Um token válido à solta, cujo link não chegou a ninguém, é uma
        # porta aberta sem dono.
        from app.models import PasswordReset

        backend = servidor()
        backend.entrega = {'sent': [], 'failed': [('Telegram', 'bot bloqueado')]}
        pessoa()

        client.post('/auth/password/forgot', json={"identifier": "ana"})

        assert PasswordReset.query.filter_by(used_at=None).count() == 0


# --------------------------------------------------- alterar na "Minha Conta"

class TestAlterarNaMinhaConta:
    """Mudar a palavra-passe já autenticado, sem passar por link nenhum.

    ⚠️ **É a MESMA palavra-passe do servidor de média** — a que abre a aplicação
    dele e este painel, porque o painel autentica contra ele. O painel não
    guarda nenhuma, em sítio nenhum: o que aqui se grava vai direto para o
    servidor.
    """

    def _autenticar(self, client, username='ana', role='user'):
        with client.session_transaction() as sessao:
            sessao["user_details"] = {"id": GUID, "username": username,
                                      "email": "ana@exemplo.test", "role": role}
            sessao["_user_id"] = GUID
            sessao["_fresh"] = True

    def test_grava_no_servidor_de_media(self, client, db_session, servidor, pessoa):
        backend = servidor()
        pessoa()
        self._autenticar(client)

        resposta = client.post('/api/users/account/password',
                               json={"current_password": "a-antiga", "new_password": "a-nova-boa"})

        assert resposta.get_json()['success'] is True
        assert backend.definidas == [(GUID, "a-nova-boa")]

    def test_a_palavra_passe_atual_e_confirmada_contra_o_servidor(self, client, db_session,
                                                                   servidor, pessoa):
        # 🛡️ Uma sessão do painel esquecida aberta num computador partilhado não
        # pode bastar para tomar a conta.
        backend = servidor()
        pessoa()
        self._autenticar(client)

        resposta = client.post('/api/users/account/password',
                               json={"current_password": "errada", "new_password": "a-nova-boa"})

        assert resposta.status_code == 403
        assert backend.definidas == []

    def test_sem_sessao_nao_se_muda_nada(self, client, db_session, servidor, pessoa):
        backend = servidor()
        pessoa()

        resposta = client.post('/api/users/account/password',
                               json={"current_password": "a", "new_password": "a-nova-boa"})

        assert resposta.status_code in (302, 401, 403)
        assert backend.definidas == []

    def test_uma_palavra_passe_curta_e_recusada(self, client, db_session, servidor, pessoa):
        backend = servidor()
        pessoa()
        self._autenticar(client)

        resposta = client.post('/api/users/account/password',
                               json={"current_password": "a-antiga", "new_password": "123"})

        assert resposta.status_code == 400
        assert backend.definidas == []

    def test_num_painel_plex_a_rota_recusa(self, client, db_session, servidor, pessoa):
        backend = servidor(cria_contas=False)
        pessoa()
        self._autenticar(client)

        resposta = client.post('/api/users/account/password',
                               json={"current_password": "a", "new_password": "a-nova-boa"})

        assert resposta.status_code == 400
        assert backend.definidas == []

    def test_o_administrador_tambem_pode_mudar_a_dele(self, client, db_session, servidor, pessoa):
        # Ele é um utilizador do Jellyfin como os outros — a conta dele tem
        # palavra-passe e é a mesma que abre o painel.
        backend = servidor()
        pessoa()
        self._autenticar(client, role='admin')

        resposta = client.post('/api/users/account/password',
                               json={"current_password": "a-antiga", "new_password": "a-nova-boa"})

        assert resposta.get_json()['success'] is True

    def test_a_sessao_do_painel_sobrevive(self, client, db_session, servidor, pessoa):
        # ⚠️ A sessão é um cookie assinado pelo painel e não guarda a
        # palavra-passe: quem acabou de a mudar continua a poder navegar. Dizer
        # o contrário na mensagem seria mentira.
        servidor()
        pessoa()
        self._autenticar(client)

        client.post('/api/users/account/password',
                    json={"current_password": "a-antiga", "new_password": "a-nova-boa"})

        # A página da conta continua a abrir: a sessão não caiu.
        assert client.get('/account').status_code == 200


class TestOCartaoNaPagina:
    def _autenticar(self, client):
        with client.session_transaction() as sessao:
            sessao["user_details"] = {"id": GUID, "username": "ana",
                                      "email": "ana@exemplo.test", "role": "user"}
            sessao["_user_id"] = GUID
            sessao["_fresh"] = True

    def test_aparece_num_servidor_de_contas_locais(self, client, db_session, servidor, pessoa):
        servidor()
        pessoa()
        self._autenticar(client)

        pagina = client.get('/account').get_data(as_text=True)

        assert 'change-password-form' in pagina
        # Não há duas palavras-passe, e a página tem de o dizer.
        assert 'muda-a nos dois' in pagina

    def test_nao_aparece_num_painel_plex(self, client, db_session, servidor, pessoa):
        servidor(cria_contas=False)
        pessoa()
        self._autenticar(client)

        pagina = client.get('/account').get_data(as_text=True)

        assert 'change-password-form' not in pagina
