# tests/test_reativacao_por_servidor.py

"""Repor o acesso de quem pagou uma reativação — e porque não é a mesma coisa
nos dois servidores.

⚠️ **"Reativar" não é "enviar um convite".** Era o que estava escrito no
`PlexSubscriptionManager` — que os DOIS backends usam, porque o que lá vive é a
política de vencimentos, igual em todos. Um pagamento de reativação num painel
Jellyfin ia parar a `invites.send_invite()` e a notificação levava um endereço
`clients.plex.tv/.../accept` (ou, em falhando, `app.plex.tv/desktop`) a quem
nunca teve conta no Plex.

Pior do que o link errado: num servidor de contas locais, "enviar convite" quer
dizer **criar uma conta**. Com a conta a existir já, a tentativa seria uma conta
duplicada com o email por nome e uma palavra-passe que ninguém veria.

O que cada servidor faz é mesmo diferente, e por isso é do backend:

- no Plex o acesso foi RETIRADO (as partilhas) e é preciso convidar outra vez —
  fica um link por aceitar, e é ele que faz aparecer o botão de confirmação
  manual na página de pagamento;
- onde as contas são locais a conta foi SUSPENSA e basta reativá-la: não há
  nada para aceitar, e o link é o do próprio servidor.
"""

import json

import pytest

pytestmark = pytest.mark.integration

GUID = "38c3a1f0e4b24d7f9c1a0b5e6d7f8a90"


class _Duplo:
    def __getattr__(self, nome):
        return _Duplo()

    def __call__(self, *args, **kwargs):
        return None


# ---------------------------------------------------------------- Plex

class TestNoPlex:
    """Continua a ser um convite, e o link por aceitar continua a existir."""

    def _backend(self, monkeypatch, resultado_do_convite):
        from app.services.media_server.plex.backend import PlexManager

        backend = PlexManager(_Duplo(), _Duplo(), _Duplo(), _Duplo())
        enviados = []

        class ConvitesFalsos:
            def send_invite(self, identifier, library_titles, media_user_id=None, allow_sync=False):
                enviados.append({
                    'identifier': identifier, 'library_titles': library_titles,
                    'media_user_id': media_user_id, 'allow_sync': allow_sync,
                })
                return resultado_do_convite

        backend.invites = ConvitesFalsos()
        monkeypatch.setattr(backend, 'get_user_by_id', lambda _id: None)
        backend.notifier_manager = None
        return backend, enviados

    def test_um_convite_por_aceitar_da_link_e_link_pendente(self, app_context, monkeypatch):
        backend, enviados = self._backend(monkeypatch, {"success": True, "invite_token": "tok123"})

        resultado = backend.restaurar_acesso(
            '55', {'email': 'ana@exemplo.test', 'libraries': json.dumps(['Filmes'])}
        )

        assert enviados[0]['identifier'] == 'ana@exemplo.test'
        assert enviados[0]['library_titles'] == ['Filmes']
        assert 'clients.plex.tv' in resultado['link']
        # É o `link_pendente` que faz aparecer o botão de confirmação manual.
        assert resultado['link_pendente'] == resultado['link']

    def test_um_convite_ja_aceite_nao_deixa_nada_pendente(self, app_context, monkeypatch):
        backend, _ = self._backend(monkeypatch, {"success": True, "invite_token": "ACCEPTED"})

        resultado = backend.restaurar_acesso('55', {'email': 'ana@exemplo.test'})

        assert resultado['link_pendente'] is None
        assert resultado['link'] == 'https://app.plex.tv/desktop'

    def test_sem_email_nao_ha_a_quem_convidar(self, app_context, monkeypatch):
        backend, enviados = self._backend(monkeypatch, {"success": True})

        resultado = backend.restaurar_acesso('55', {'libraries': '[]'})

        assert resultado['success'] is False
        assert enviados == []


# ------------------------------------------------------------ Jellyfin

class TestNumServidorDeContasLocais:
    def _backend(self, monkeypatch, existe=True, desbloqueio=None):
        from app.services.media_server.jellyfin.backend import JellyfinManager

        backend = JellyfinManager(_Duplo())
        registo = {'desbloqueados': [], 'bibliotecas': [], 'convites': []}

        class UtilizadoresFalsos:
            def unblock_user(self, user_id):
                registo['desbloqueados'].append(user_id)
                return desbloqueio or {"success": True}

            def update_user_libraries(self, user_id, titulos, allow_sync=None):
                registo['bibliotecas'].append((user_id, titulos))
                return {"success": True}

        class ConvitesFalsos:
            def send_invite(self, *args, **kwargs):
                registo['convites'].append((args, kwargs))
                return {"success": True}

        backend.users = UtilizadoresFalsos()
        backend.invites = ConvitesFalsos()
        monkeypatch.setattr(backend, 'get_user_by_id',
                            lambda _id: {"id": GUID, "username": "ana"} if existe else None)
        monkeypatch.setattr(backend, 'get_base_url', lambda: 'https://media.exemplo.test')
        return backend, registo

    def test_reativa_a_conta_em_vez_de_a_convidar(self, app_context, monkeypatch):
        # 🐛 Era aqui que nascia uma conta duplicada com o email por nome.
        backend, registo = self._backend(monkeypatch)

        resultado = backend.restaurar_acesso(GUID, {'libraries': json.dumps(['Filmes'])})

        assert resultado['success'] is True
        assert registo['convites'] == []
        assert registo['desbloqueados'] == [GUID]

    def test_repoe_as_bibliotecas_que_o_perfil_guardou(self, app_context, monkeypatch):
        backend, registo = self._backend(monkeypatch)

        backend.restaurar_acesso(GUID, {'libraries': json.dumps(['Filmes', 'Séries'])})

        assert registo['bibliotecas'] == [(GUID, ['Filmes', 'Séries'])]

    def test_o_link_e_o_do_proprio_servidor(self, app_context, monkeypatch):
        # ⚠️ Não um endereço de plex.tv para quem nunca teve conta no Plex.
        backend, _ = self._backend(monkeypatch)

        resultado = backend.restaurar_acesso(GUID, {'libraries': '[]'})

        assert resultado['link'] == 'https://media.exemplo.test'
        assert 'plex' not in (resultado['link'] or '')

    def test_nao_ha_nada_para_aceitar(self, app_context, monkeypatch):
        # O botão de confirmação manual na página de pagamento pediria à pessoa
        # um passo que não existe neste servidor.
        backend, _ = self._backend(monkeypatch)

        assert backend.restaurar_acesso(GUID, {'libraries': '[]'})['link_pendente'] is None

    def test_uma_conta_ja_removida_diz_que_nao_deu(self, app_context, monkeypatch):
        # ⚠️ Depois de `DAYS_TO_REMOVE_BLOCKED_USER` dias, o `removal_job` apaga
        # a conta mesmo. Recriá-la seria inventar uma palavra-passe nova que
        # ninguém entregaria — dar por reposto um acesso que não existe é pior.
        backend, registo = self._backend(monkeypatch, existe=False)

        resultado = backend.restaurar_acesso(GUID, {'username': 'ana'})

        assert resultado['success'] is False
        assert registo['desbloqueados'] == []


# ------------------------------------------ O caminho partilhado da renovação

class TestARenovacaoNaoSabeDeMarcas:
    """O `PlexSubscriptionManager` é dos dois: só pode chamar o contrato."""

    def test_a_reativacao_passa_pelo_backend(self, app_context, db_session, data_manager):
        from app.services.media_server.plex.subscription_manager import PlexSubscriptionManager

        data_manager.set_user_profile(GUID, {'username': 'ana', 'status': 'inactive'})
        chamadas = []

        class BackendFalso:
            def __init__(self):
                self.notifier_manager = self
                self.invites = self

            def get_user_by_id(self, _id):
                return {"id": GUID, "username": "ana"}

            def restaurar_acesso(self, media_user_id, profile, libraries=None):
                chamadas.append(media_user_id)
                return {"success": True, "message": "", "link": "https://media.exemplo.test",
                        "link_pendente": None}

            def update_screen_limit(self, *a, **k):
                pass

            def send_invite(self, *a, **k):  # pragma: no cover - não deve ser chamado
                raise AssertionError("a renovação não pode convidar por sua conta")

            def send_reactivation_notification(self, user, data, perfil, link):
                chamadas.append(link)

        gestor = PlexSubscriptionManager(data_manager, None, scheduler=None)
        gestor.plex_manager = BackendFalso()

        gestor.renew_subscription(GUID, 1, is_reactivation=True)

        assert chamadas == [GUID, "https://media.exemplo.test"]

    def test_sem_nada_por_aceitar_o_perfil_nao_guarda_link_pendente(self, app_context, db_session, data_manager):
        from app.services.media_server.plex.subscription_manager import PlexSubscriptionManager

        data_manager.set_user_profile(GUID, {
            'username': 'ana', 'status': 'inactive',
            'pending_invite_link': 'https://clients.plex.tv/antigo',
        })

        class BackendFalso:
            notifier_manager = True

            def get_user_by_id(self, _id):
                return {"id": GUID, "username": "ana"}

            def restaurar_acesso(self, media_user_id, profile, libraries=None):
                return {"success": True, "message": "", "link": "https://media.exemplo.test",
                        "link_pendente": None}

            def update_screen_limit(self, *a, **k):
                pass

            def send_reactivation_notification(self, *a, **k):
                pass

        gestor = PlexSubscriptionManager(data_manager, None, scheduler=None)
        backend = BackendFalso()
        backend.notifier_manager = backend
        gestor.plex_manager = backend

        gestor.renew_subscription(GUID, 1, is_reactivation=True)

        assert data_manager.get_user_profile(GUID)['pending_invite_link'] is None


class TestAPaginaDePagamento:
    """O último passo da reativação, para quem paga.

    ⚠️ O bloco "Entrar no Plex" é do fluxo de CONVITE: pede um PIN ao plex.tv e
    aceita a partilha. Num servidor de contas locais não há convite nenhum para
    aceitar — e o que aparecia era o símbolo do Plex e um botão que abria um
    fluxo que não leva a lado nenhum.
    """

    def _pagina(self, client, monkeypatch, convites_nativos):
        from app import extensions
        from app.blueprints import main as main_module
        from app.services.media_server.base import MediaServerCapabilities

        class BackendFalso:
            SERVER_TYPE = 'plex' if convites_nativos else 'jellyfin'
            DISPLAY_NAME = SHORT_NAME = 'Plex' if convites_nativos else 'Jellyfin'
            CAPABILITIES = MediaServerCapabilities(
                convites_nativos=convites_nativos, cria_contas=not convites_nativos,
                fontes_media_online=convites_nativos, login_delegado=convites_nativos,
                desativa_conta=not convites_nativos, links_profundos=True,
                estatisticas=True, estatisticas_externas=convites_nativos,
            )

            @property
            def capabilities(self):
                return self.CAPABILITIES

            def estatisticas_disponiveis(self):
                return False

        backend = BackendFalso()
        monkeypatch.setattr(extensions, 'media_server', backend)
        monkeypatch.setattr(main_module, 'media_server', backend, raising=False)
        return client.get('/pay/um-token-qualquer').get_data(as_text=True)

    def test_num_painel_plex_o_botao_do_convite_existe(self, client, config_file,
                                                       db_session, data_manager, monkeypatch):
        config_file(IS_CONFIGURED=True)
        data_manager.set_user_profile(GUID, {'username': 'ana', 'payment_token': 'um-token-qualquer'})

        pagina = self._pagina(client, monkeypatch, convites_nativos=True)

        assert 'reactivate-plex-login-btn' in pagina

    def test_num_servidor_de_contas_locais_nao(self, client, config_file,
                                               db_session, data_manager, monkeypatch):
        config_file(IS_CONFIGURED=True)
        data_manager.set_user_profile(GUID, {'username': 'ana', 'payment_token': 'um-token-qualquer'})

        pagina = self._pagina(client, monkeypatch, convites_nativos=False)

        assert 'reactivate-plex-login-btn' not in pagina
        assert 'Entrar no Plex' not in pagina
        # No lugar dele, o que é verdade: o pagamento entrou, o acesso é com o
        # administrador.
        assert 'administrador' in pagina
