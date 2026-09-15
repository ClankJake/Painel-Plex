# tests/test_recriacao_de_conta.py

"""Quando a conta já não existe no servidor e alguém paga a reativação.

Num servidor de contas locais, o `removal_job` apaga a conta ao fim de
`DAYS_TO_REMOVE_BLOCKED_USER` dias (`DELETE /Users`). Quem pagar depois disso
tem de voltar a ter acesso — e o painel é o único que o pode dar, porque foi ele
que criou a conta.

Três coisas que isso obriga, e que é o que estes testes guardam:

⚠️ **A identidade MUDA.** O servidor atribui um GUID ao criar a conta e não
aceita que se lhe imponha um: a conta recriada é, para ele, outra pessoa. Sem
migrar o perfil, quem pagou reaparecia como um estranho — sem pagamentos, sem
XP, sem conquistas e sem o vencimento que acabou de pagar.

⚠️ **A palavra-passe é NOVA.** Não há como recuperar a antiga nem como pedir uma
à pessoa: ela não está a ver o painel, está a pagar. Por isso só se recria
quando há por onde a entregar.

🛡️ **A palavra-passe nunca vai para o log.** Volta na resposta, é entregue pelos
canais de notificação, e mais nada.
"""

import json
import logging

import pytest

pytestmark = pytest.mark.integration

ANTIGO = "38c3a1f0e4b24d7f9c1a0b5e6d7f8a90"
NOVO = "aa11bb22cc33dd44ee55ff6677889900"


class _Duplo:
    def __getattr__(self, nome):
        return _Duplo()

    def __call__(self, *args, **kwargs):
        return None


def _backend(monkeypatch, data_manager, existe=False, criacao=None):
    from app.services.media_server.jellyfin.backend import JellyfinManager

    registo = {'criadas': [], 'bibliotecas': [], 'removidos': [], 'limites': []}

    class ContasFalsas:
        def create_account(self, username, password):
            registo['criadas'].append({'username': username, 'password': password})
            return criacao if criacao is not None else {
                "success": True, "user_id": NOVO, "username": username,
            }

    class UtilizadoresFalsos:
        def unblock_user(self, user_id):
            return {"success": True}

        def update_user_libraries(self, user_id, titulos, allow_sync=None):
            registo['bibliotecas'].append((user_id, titulos))
            return {"success": True}

        def remove_user(self, user_id):
            registo['removidos'].append(user_id)
            return {"success": True}

    backend = JellyfinManager(data_manager)
    backend.invites = ContasFalsas()
    backend.users = UtilizadoresFalsos()
    monkeypatch.setattr(backend, 'get_user_by_id',
                        lambda _id: {"id": _id, "username": "ana"} if existe else None)
    monkeypatch.setattr(backend, 'get_base_url', lambda: 'https://media.exemplo.test')
    monkeypatch.setattr(backend, 'update_screen_limit',
                        lambda user_id, telas: registo['limites'].append((user_id, telas)))
    return backend, registo


@pytest.fixture()
def perfil(data_manager):
    """Uma pessoa com histórico: é isso que a migração tem de preservar."""
    def criar(**extra):
        dados = {
            'username': 'ana', 'email': 'ana@exemplo.test', 'status': 'inactive',
            'xp': 1500, 'screen_limit': 2, 'telegram_user': '12345',
            'libraries': json.dumps(['Filmes']),
            'expiration_date': '2099-01-31T23:59:00+00:00',
        }
        dados.update(extra)
        return data_manager.set_user_profile(ANTIGO, dados)

    return criar


class TestAIdentidadeMuda:
    def test_o_perfil_passa_a_ser_o_da_conta_nova(self, app_context, db_session, data_manager,
                                                  perfil, monkeypatch):
        perfil()
        backend, _ = _backend(monkeypatch, data_manager)

        resultado = backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))

        assert resultado['success'] is True
        assert resultado['media_user_id'] == NOVO
        assert data_manager.get_user_profile(ANTIGO) is None

    def test_o_historico_vem_com_ela(self, app_context, db_session, data_manager, perfil, monkeypatch):
        # 🐛 Sem isto, quem pagou reaparecia como um estranho.
        perfil()
        backend, _ = _backend(monkeypatch, data_manager)

        backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))

        novo = data_manager.get_user_profile(NOVO)
        assert novo['xp'] == 1500
        assert novo['expiration_date'] == '2099-01-31T23:59:00+00:00'
        assert novo['username'] == 'ana'

    def test_as_bibliotecas_e_o_limite_de_telas_seguem(self, app_context, db_session, data_manager,
                                                        perfil, monkeypatch):
        perfil()
        backend, registo = _backend(monkeypatch, data_manager)

        backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))

        assert registo['bibliotecas'] == [(NOVO, ['Filmes'])]
        # O limite vive no perfil e, havendo plugin, também no servidor — que
        # não conhece a conta nova.
        assert registo['limites'] == [(NOVO, 2)]


class TestAPalavraPasse:
    def test_volta_na_resposta_para_ser_entregue(self, app_context, db_session, data_manager,
                                                 perfil, monkeypatch):
        perfil()
        backend, registo = _backend(monkeypatch, data_manager)

        resultado = backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))

        assert resultado['credenciais']['username'] == 'ana'
        assert resultado['credenciais']['password'] == registo['criadas'][0]['password']
        assert len(resultado['credenciais']['password']) >= 12

    def test_nunca_chega_ao_log(self, app_context, db_session, data_manager, perfil,
                                monkeypatch, caplog):
        # 🛡️ Um log com palavras-passe é um log que não se pode partilhar.
        perfil()
        backend, registo = _backend(monkeypatch, data_manager)

        with caplog.at_level(logging.DEBUG):
            backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))

        assert registo['criadas'][0]['password'] not in caplog.text

    def test_sem_contacto_nao_se_cria_conta_nenhuma(self, app_context, db_session, data_manager,
                                                    perfil, monkeypatch):
        # ⚠️ Uma conta com uma palavra-passe que ninguém vai receber é pior do
        # que conta nenhuma: o administrador tem de saber que ficou por fazer.
        perfil(telegram_user=None)
        backend, registo = _backend(monkeypatch, data_manager)

        resultado = backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))

        assert resultado['success'] is False
        assert registo['criadas'] == []
        assert data_manager.get_user_profile(ANTIGO) is not None

    # ⚠️ O valor de cada contacto tem de ser plausível: `phone_number` é
    # normalizado para só dígitos ao ser gravado (ver `UserProfile`), por isso
    # um 'x' de faz-de-conta chegava ao perfil como NULL e o teste passava a
    # medir o caso oposto ao que diz medir — "sem contacto nenhum".
    @pytest.mark.parametrize('contacto, valor', [
        ('telegram_user', 'ana'),
        ('discord_user_id', '123456789012345678'),
        ('phone_number', '5511999999999'),
    ])
    def test_qualquer_canal_serve(self, app_context, db_session, data_manager, perfil,
                                  monkeypatch, contacto, valor):
        contactos = {'telegram_user': None, 'discord_user_id': None, 'phone_number': None}
        contactos[contacto] = valor
        perfil(**contactos)
        backend, _ = _backend(monkeypatch, data_manager)

        assert backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))['success'] is True


class TestQuandoCorreMal:
    def test_um_servidor_que_recusa_criar_nao_migra_nada(self, app_context, db_session,
                                                          data_manager, perfil, monkeypatch):
        perfil()
        backend, _ = _backend(monkeypatch, data_manager,
                              criacao={"success": False, "message": "já existe"})

        resultado = backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))

        assert resultado['success'] is False
        assert data_manager.get_user_profile(ANTIGO) is not None

    def test_uma_migracao_falhada_desfaz_a_conta_criada(self, app_context, db_session,
                                                         data_manager, perfil, monkeypatch):
        # As duas metades ficariam a falar de gente diferente: a conta no
        # servidor com um id, o painel com outro.
        perfil()
        backend, registo = _backend(monkeypatch, data_manager)
        monkeypatch.setattr(data_manager, 'migrar_identidade',
                            lambda antigo, novo: (_ for _ in ()).throw(ValueError("não deu")))

        resultado = backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))

        assert resultado['success'] is False
        assert registo['removidos'] == [NOVO]

    def test_sem_nome_de_utilizador_nao_ha_conta_a_criar(self, app_context, db_session,
                                                          data_manager, monkeypatch):
        backend, registo = _backend(monkeypatch, data_manager)

        resultado = backend.restaurar_acesso(ANTIGO, {'telegram_user': '1'})

        assert resultado['success'] is False
        assert registo['criadas'] == []


class TestAContaQueAindaExiste:
    def test_nao_se_recria_o_que_nao_foi_apagado(self, app_context, db_session, data_manager,
                                                 perfil, monkeypatch):
        perfil()
        backend, registo = _backend(monkeypatch, data_manager, existe=True)

        resultado = backend.restaurar_acesso(ANTIGO, data_manager.get_user_profile(ANTIGO))

        assert registo['criadas'] == []
        assert resultado['media_user_id'] == ANTIGO
        assert 'credenciais' not in resultado


class TestAMigracaoDeIdentidade:
    """O que `migrar_identidade` tem de arrastar consigo, e o que tem de recusar."""

    def test_os_pagamentos_seguem_a_pessoa(self, app_context, db_session, data_manager, perfil):
        perfil()
        data_manager.create_pix_payment(
            txid='tx1', media_user_id=ANTIGO, username='ana', value=25.0,
            provider='efi', screens=2, external_reference='ref1',
        )
        data_manager.update_pix_payment_status('tx1', 'CONCLUIDA')

        data_manager.migrar_identidade(ANTIGO, NOVO)

        assert [p['txid'] for p in data_manager.get_payments_by_user(NOVO)] == ['tx1']
        assert data_manager.get_payments_by_user(ANTIGO) == []

    def test_as_conquistas_seguem(self, app_context, db_session, data_manager, perfil):
        perfil()
        data_manager.add_unlocked_achievements(ANTIGO, 'ana', [{'id': 'maratonista'}])

        data_manager.migrar_identidade(ANTIGO, NOVO)

        assert set(data_manager.get_unlocked_achievements(NOVO)) == {'maratonista'}

    def test_quem_indicou_continua_a_apontar_para_a_pessoa_certa(self, app_context, db_session,
                                                                  data_manager, perfil):
        # `referred_by` guarda uma identidade sem ser chave estrangeira: é
        # exatamente o tipo de coluna que fica para trás numa migração.
        perfil()
        data_manager.set_user_profile('outra-pessoa', {'username': 'rita', 'referred_by': ANTIGO})

        data_manager.migrar_identidade(ANTIGO, NOVO)

        assert data_manager.get_user_profile('outra-pessoa')['referred_by'] == NOVO

    def test_um_pedido_de_reposicao_em_curso_segue_a_pessoa(self, app_context, db_session,
                                                             data_manager, perfil):
        # 🐛 `password_resets` é uma chave estrangeira NOVA para
        # `user_profiles.media_user_id` e ficou de fora de
        # `_TABELAS_COM_IDENTIDADE`: a linha ficava a apontar para um perfil que
        # já não existe, e o link que a pessoa tinha acabado de receber deixava
        # de funcionar sem explicação nenhuma.
        perfil()
        token = data_manager.criar_pedido_de_reposicao(ANTIGO)

        data_manager.migrar_identidade(ANTIGO, NOVO)

        dono, _motivo = data_manager.consumir_pedido_de_reposicao(token)
        assert dono == NOVO

    def test_o_convite_ja_resgatado_continua_resgatado(self, app_context, db_session,
                                                        data_manager, perfil):
        # 🛡️ `claimed_by_ids` é uma lista JSON, não uma coluna de ID: deixá-la
        # para trás abria a porta a resgatar de novo um convite já usado.
        perfil()
        from app.extensions import db
        from app.models import Invitation

        convite = Invitation(
            code='ABC', libraries='[]', screen_limit=1, allow_downloads=False,
            created_at='2026-01-01T00:00:00+00:00', claimed_by_ids=json.dumps([ANTIGO]),
        )
        db.session.add(convite)
        db.session.commit()

        data_manager.migrar_identidade(ANTIGO, NOVO)

        assert json.loads(Invitation.query.get('ABC').claimed_by_ids) == [NOVO]

    def test_recusa_juntar_dois_historicos(self, app_context, db_session, data_manager, perfil):
        # Fundir duas pessoas é uma decisão de quem administra, não de uma
        # rotina automática que corre sozinha a meio de um pagamento.
        perfil()
        data_manager.set_user_profile(NOVO, {'username': 'outra'})

        with pytest.raises(ValueError, match="dois históricos"):
            data_manager.migrar_identidade(ANTIGO, NOVO)

    def test_recusa_migrar_um_perfil_que_nao_existe(self, app_context, db_session, data_manager):
        with pytest.raises(ValueError):
            data_manager.migrar_identidade(ANTIGO, NOVO)

    def test_migrar_para_o_mesmo_id_nao_faz_nada(self, app_context, db_session, data_manager, perfil):
        perfil()

        assert data_manager.migrar_identidade(ANTIGO, ANTIGO) is False


class TestAEntregaDasCredenciais:
    """A palavra-passe nova tem de CHEGAR à pessoa — é só para isso que existe."""

    def _notificador(self, monkeypatch, config_file):
        from app import extensions
        from app.services.notifier_manager import NotifierManager

        class BackendFalso:
            SHORT_NAME = 'Jellyfin'

        monkeypatch.setattr(extensions, 'media_server', BackendFalso())
        config_file(IS_CONFIGURED=True, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="x",
                    DISCORD_ENABLED=False, WHATSAPP_ENABLED=False, WEBHOOK_ENABLED=False)

        notificador = NotifierManager()
        enviadas = []
        monkeypatch.setattr(notificador, '_send_telegram_notification',
                            lambda mensagem, *a, **k: enviadas.append(mensagem))
        return notificador, enviadas

    def test_a_mensagem_leva_o_utilizador_e_a_palavra_passe(self, app_context, monkeypatch, config_file):
        notificador, enviadas = self._notificador(monkeypatch, config_file)

        notificador.send_credentials_notification(
            {"username": "ana", "id": ANTIGO}, {"media_user_id": ANTIGO, "telegram_id": "1"},
            {"username": "ana", "password": "segredo-novo"}, link="https://media.exemplo.test",
        )

        assert 'segredo-novo' in enviadas[0]
        assert 'ana' in enviadas[0]
        assert 'https://media.exemplo.test' in enviadas[0]

    def test_sem_palavra_passe_nao_se_envia_nada(self, app_context, monkeypatch, config_file):
        # Só se manda a mensagem quando há mesmo uma credencial nova: uma
        # reativação normal não muda palavra-passe nenhuma.
        notificador, enviadas = self._notificador(monkeypatch, config_file)

        notificador.send_credentials_notification(
            {"username": "ana", "id": ANTIGO}, {"media_user_id": ANTIGO, "telegram_id": "1"}, {},
        )

        assert enviadas == []

    def test_o_template_padrao_existe_para_todos_os_canais(self):
        from app.services.notifier_manager import DEFAULT_TEMPLATES

        for canal in ('TELEGRAM', 'DISCORD', 'WEBHOOK', 'WHATSAPP'):
            chave = f'{canal}_CREDENTIALS_MESSAGE_TEMPLATE'
            assert DEFAULT_TEMPLATES.get(chave), chave
            assert '{new_password}' in DEFAULT_TEMPLATES[chave]

    def test_os_templates_json_continuam_validos(self):
        from app.services.notifier_manager import DEFAULT_TEMPLATES

        for canal in ('DISCORD', 'WEBHOOK'):
            texto = DEFAULT_TEMPLATES[f'{canal}_CREDENTIALS_MESSAGE_TEMPLATE']
            json.loads(texto.replace('{new_password}', 'p').replace('{new_username}', 'u'))


class TestOCaminhoCompletoDaRenovacao:
    """Do pagamento à notificação, com a identidade a mudar pelo meio."""

    def _gestor(self, data_manager, restauro):
        from app.services.media_server.plex.subscription_manager import PlexSubscriptionManager

        registo = {'credenciais': [], 'reativacao': [], 'limites': []}

        class BackendFalso:
            def __init__(self):
                self.notifier_manager = self

            def get_user_by_id(self, _id):
                return {"id": _id, "username": "ana"}

            def restaurar_acesso(self, media_user_id, profile, libraries=None):
                # O de verdade migra o perfil antes de devolver o id novo; um
                # duplo que não o faça não está a testar o caminho real.
                novo = restauro.get('media_user_id')
                if novo and novo != media_user_id:
                    data_manager.migrar_identidade(media_user_id, novo)
                return restauro

            def update_screen_limit(self, user_id, telas):
                registo['limites'].append((user_id, telas))

            def send_reactivation_notification(self, user, data, perfil, link):
                registo['reativacao'].append(user['id'])

            def send_credentials_notification(self, user, perfil, credenciais, link=None):
                registo['credenciais'].append(credenciais)

        gestor = PlexSubscriptionManager(data_manager, None, scheduler=None)
        gestor.plex_manager = BackendFalso()
        return gestor, registo

    def test_o_vencimento_fica_gravado_na_identidade_NOVA(self, app_context, db_session,
                                                          data_manager, perfil):
        # 🐛 Se a migração acontecesse DEPOIS do resto, a data de vencimento, a
        # tarefa de expiração e o limite de telas ficavam gravados num
        # identificador que a essa altura já não existe.
        perfil()
        gestor, _ = self._gestor(data_manager, {
            "success": True, "media_user_id": NOVO, "link": "https://media.exemplo.test",
            "link_pendente": None, "credenciais": {"username": "ana", "password": "nova"},
        })

        gestor.renew_subscription(ANTIGO, 1, is_reactivation=True)

        assert data_manager.get_user_profile(ANTIGO) is None
        assert data_manager.get_user_profile(NOVO)['expiration_date']
        assert data_manager.get_user_profile(NOVO)['status'] == 'active'

    def test_as_credenciais_novas_sao_enviadas(self, app_context, db_session, data_manager, perfil):
        perfil()
        gestor, registo = self._gestor(data_manager, {
            "success": True, "media_user_id": NOVO, "link": "https://media.exemplo.test",
            "link_pendente": None, "credenciais": {"username": "ana", "password": "nova"},
        })

        gestor.renew_subscription(ANTIGO, 1, is_reactivation=True)

        assert registo['credenciais'] == [{"username": "ana", "password": "nova"}]
        assert registo['reativacao'] == [NOVO]

    def test_numa_reativacao_normal_nao_se_envia_palavra_passe_nenhuma(self, app_context, db_session,
                                                                       data_manager, perfil):
        perfil()
        gestor, registo = self._gestor(data_manager, {
            "success": True, "media_user_id": ANTIGO,
            "link": "https://media.exemplo.test", "link_pendente": None,
        })

        gestor.renew_subscription(ANTIGO, 1, is_reactivation=True)

        assert registo['credenciais'] == []
        assert registo['reativacao'] == [ANTIGO]

    def test_um_id_novo_sem_perfil_migrado_nao_duplica_ninguem(self, app_context, db_session,
                                                                data_manager, perfil):
        # 🛡️ Se o backend disser que a identidade mudou mas não tiver migrado
        # nada, gravar o perfil antigo sob a chave nova criava uma linha com o
        # mesmo nome de utilizador (que é único): erro de integridade DEPOIS de
        # o pagamento já ter sido aceite.
        from app.services.media_server.plex.subscription_manager import PlexSubscriptionManager

        perfil()

        class BackendMentiroso:
            def __init__(self):
                self.notifier_manager = self

            def get_user_by_id(self, _id):
                return {"id": _id, "username": "ana"}

            def restaurar_acesso(self, media_user_id, profile, libraries=None):
                return {"success": True, "media_user_id": NOVO, "link": None, "link_pendente": None}

            def update_screen_limit(self, *a, **k):
                pass

            def send_reactivation_notification(self, *a, **k):
                pass

        gestor = PlexSubscriptionManager(data_manager, None, scheduler=None)
        gestor.plex_manager = BackendMentiroso()

        gestor.renew_subscription(ANTIGO, 1, is_reactivation=True)

        assert data_manager.get_user_profile(NOVO) is None
        assert data_manager.get_user_profile(ANTIGO)['status'] == 'active'
