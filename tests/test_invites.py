# tests/test_invites.py
"""
Testes do sistema de convites.

O foco está na janela entre VALIDAR um convite e CONTABILIZAR o seu uso — a
mesma classe de problema que os cupões já tinham fechado — e na validação do
código personalizado, que chega até ao URL público do convite.
"""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


def iso(dias=0):
    return (datetime.now(timezone.utc) + timedelta(days=dias)).isoformat()


def detalhes(**extra):
    base = {
        "libraries": ["Filmes"],
        "screen_limit": 1,
        "allow_downloads": False,
        "created_at": iso(),
        "expires_at": None,
        "max_uses": 1,
    }
    base.update(extra)
    return base


class TestReservaDeUtilizacao:
    """
    O resgate faz dezenas de chamadas à API do Plex entre validar o convite e
    registar o uso. Com o worker gevent, dois resgates simultâneos intercalam-se
    nessas esperas de rede: ambos liam use_count=0 e ambos passavam.
    """

    def test_reserva_respeita_o_limite(self, data_manager):
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))

        assert data_manager.reserve_invitation_use("UNICO", "ana") is True
        assert data_manager.reserve_invitation_use("UNICO", "bruno") is False

        convite = data_manager.get_invitation("UNICO")
        assert convite["use_count"] == 1
        assert convite["claimed_by_users"] == ["ana"]

    def test_reserva_permite_ate_ao_maximo(self, data_manager):
        data_manager.add_invitation("DUPLO", detalhes(max_uses=2))

        assert data_manager.reserve_invitation_use("DUPLO", "ana") is True
        assert data_manager.reserve_invitation_use("DUPLO", "bruno") is True
        assert data_manager.reserve_invitation_use("DUPLO", "carla") is False

        assert data_manager.get_invitation("DUPLO")["use_count"] == 2

    def test_reserva_de_convite_inexistente(self, data_manager):
        assert data_manager.reserve_invitation_use("NAO-EXISTE", "ana") is False

    def test_libertar_devolve_a_vaga(self, data_manager):
        """Se o Plex recusar o convite a meio, a vaga não pode ficar queimada."""
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))

        data_manager.reserve_invitation_use("UNICO", "ana")
        assert data_manager.release_invitation_use("UNICO", "ana") is True

        convite = data_manager.get_invitation("UNICO")
        assert convite["use_count"] == 0
        assert convite["claimed_by_users"] == []
        assert data_manager.reserve_invitation_use("UNICO", "bruno") is True

    def test_libertar_nunca_desce_abaixo_de_zero(self, data_manager):
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))

        data_manager.release_invitation_use("UNICO", "ana")

        assert data_manager.get_invitation("UNICO")["use_count"] == 0


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True)


@pytest.fixture()
def admin(client, configurada):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": 1, "username": "admin", "role": "admin"}
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True
    return client


class TestCodigoPersonalizado:
    """
    O código personalizado torna-se a chave do convite e um segmento do URL
    público. Sem validação, um código de um caractere era adivinhável e um
    código com '/' gerava um link permanentemente 404.
    """

    @pytest.mark.parametrize("codigo", ["a", "ab", "abc", "com espaco", "a/b", "x" * 65, "pro#mo"])
    def test_codigos_invalidos_sao_recusados(self, admin, db_session, codigo):
        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "custom_code": codigo,
        })
        assert resposta.status_code == 400

    @pytest.mark.parametrize("codigo", ["PROMO", "promo-2026", "a_b-C9", "x" * 64])
    def test_codigos_validos_passam_a_validacao(self, admin, db_session, codigo):
        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "custom_code": codigo,
        })
        # Sem Plex ligado a criação pode falhar mais à frente; o que importa
        # aqui é que não é o esquema a recusar o código.
        assert resposta.status_code != 400

    def test_codigo_vazio_continua_a_ser_opcional(self, admin, db_session):
        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "custom_code": "   ",
        })
        assert resposta.status_code != 400


class TestRotasPublicas:
    def test_detalhes_de_convite_inexistente(self, client, configurada, db_session):
        resposta = client.get("/api/invites/details/NAO-EXISTE")
        assert resposta.status_code == 404
        assert resposta.get_json()["success"] is False

    def test_detalhes_de_convite_valido(self, client, configurada, data_manager):
        data_manager.add_invitation("VALIDO-123", detalhes(max_uses=1))

        resposta = client.get("/api/invites/details/VALIDO-123")

        assert resposta.status_code == 200
        assert resposta.get_json()["success"] is True

    def test_convite_esgotado_nao_e_dado_como_valido(self, client, configurada, data_manager):
        data_manager.add_invitation("ESGOTADO", detalhes(max_uses=1))
        data_manager.reserve_invitation_use("ESGOTADO", "ana")

        assert client.get("/api/invites/details/ESGOTADO").status_code == 404

    def test_data_de_expiracao_corrompida_nao_rebenta(self, client, configurada, data_manager):
        """Uma data mal formada devolvia 500 a quem abrisse o link público."""
        data_manager.add_invitation("MALFORMADO", detalhes(expires_at="não é uma data"))

        resposta = client.get("/api/invites/details/MALFORMADO")

        assert resposta.status_code == 404
        assert resposta.get_json()["success"] is False

    def test_resgate_sem_corpo_json_devolve_400(self, client, configurada, db_session):
        """Antes, um POST sem cabeçalho JSON rebentava com 500."""
        resposta = client.post("/api/invites/claim", data="nada")
        assert resposta.status_code == 400


class TestRotasAdministrativas:
    def test_apagar_sem_corpo_json_devolve_400(self, admin, db_session):
        assert admin.post("/api/invites/delete", data="nada").status_code == 400

    def test_reativar_sem_corpo_json_devolve_400(self, admin, db_session):
        assert admin.post("/api/invites/reactivate", data="nada").status_code == 400


class _UserManagerFalso:
    def __init__(self, ids_no_plex=()):
        self.ids_no_plex = list(ids_no_plex)

    def invalidate_user_cache(self):
        pass

    def list_users(self):
        return [{"id": i} for i in self.ids_no_plex]


class _PlexManagerFalso:
    def __init__(self):
        self.limites = []

    def update_screen_limit(self, media_user_id, limite):
        self.limites.append((media_user_id, limite))


class _ContaPlex:
    def __init__(self, id_, username, email):
        self.id = id_
        self.username = username
        self.email = email


def _gestor(data_manager, envio, aceite=None):
    """
    Gestor de convites com as chamadas ao Plex substituídas, mas com o
    DataManager REAL — é a contabilização das vagas que está a ser testada.
    """
    from app.services.media_server.plex.invite_manager import PlexInviteManager

    gestor = PlexInviteManager(
        connection=None,
        user_manager=_UserManagerFalso(),
        data_manager=data_manager,
        plex_manager=_PlexManagerFalso(),
        overseerr_manager=None,
        notifier_manager=None,
    )
    gestor.send_invite = lambda **kwargs: envio
    gestor._accept_invite_v2 = lambda conta: aceite or {"success": True}
    gestor._apply_online_media_preferences = lambda conta: None
    gestor._setup_local_profile_and_integrations = lambda *a, **k: {"username": "ana"}
    return gestor


class TestResgateContabilizaUmaSoVez:
    def test_resgate_com_sucesso_gasta_exatamente_uma_vaga(self, app_context, data_manager):
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))
        gestor = _gestor(data_manager, envio={"success": True})

        resultado = gestor.claim_invitation("UNICO", _ContaPlex(10, "ana", "ana@exemplo.pt"))

        assert resultado["success"] is True
        convite = data_manager.get_invitation("UNICO")
        # Antes a reserva e o increment_invitation_use somavam-se: dava 2.
        assert convite["use_count"] == 1
        assert convite["claimed_by_users"] == ["ana"]

    def test_segundo_resgate_e_recusado(self, app_context, data_manager):
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))
        gestor = _gestor(data_manager, envio={"success": True})

        gestor.claim_invitation("UNICO", _ContaPlex(10, "ana", "ana@exemplo.pt"))
        segundo = gestor.claim_invitation("UNICO", _ContaPlex(11, "bruno", "bruno@exemplo.pt"))

        assert segundo["success"] is False
        assert data_manager.get_invitation("UNICO")["use_count"] == 1

    def test_falha_do_plex_devolve_a_vaga(self, app_context, data_manager):
        """Uma recusa do Plex não pode queimar a única utilização do convite."""
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))
        gestor = _gestor(data_manager, envio={"success": False, "message": "Plex fora do ar"})

        resultado = gestor.claim_invitation("UNICO", _ContaPlex(10, "ana", "ana@exemplo.pt"))

        assert resultado["success"] is False
        convite = data_manager.get_invitation("UNICO")
        assert convite["use_count"] == 0
        assert convite["claimed_by_users"] == []

    def test_utilizador_ja_amigo_devolve_a_vaga(self, app_context, data_manager):
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))
        gestor = _gestor(data_manager, envio={"success": True, "already_exists": True})

        resultado = gestor.claim_invitation("UNICO", _ContaPlex(10, "ana", "ana@exemplo.pt"))

        assert resultado["success"] is False
        assert data_manager.get_invitation("UNICO")["use_count"] == 0

    def test_excecao_a_meio_devolve_a_vaga(self, app_context, data_manager):
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))
        gestor = _gestor(data_manager, envio={"success": True})

        def rebenta(conta):
            raise RuntimeError("a rede caiu a meio do aceite")

        gestor._accept_invite_v2 = rebenta

        with pytest.raises(RuntimeError):
            gestor.claim_invitation("UNICO", _ContaPlex(10, "ana", "ana@exemplo.pt"))

        assert data_manager.get_invitation("UNICO")["use_count"] == 0

    def test_dois_resgates_intercalados_nao_ultrapassam_o_limite(self, app_context, data_manager):
        """
        Reprodução determinística da corrida.

        O segundo resgate começa ENQUANTO o primeiro está parado à espera do
        Plex — exatamente o que os greenlets fazem em cada chamada de rede sob o
        worker gevent. Antes da reserva atómica, ambos liam use_count=0, ambos
        passavam na validação e ambos recebiam acesso a um convite de uso único.
        """
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))

        segundo_gestor = _gestor(data_manager, envio={"success": True})
        resultados = {}

        def envio_que_intercala(**kwargs):
            if "segundo" not in resultados:
                resultados["segundo"] = segundo_gestor.claim_invitation(
                    "UNICO", _ContaPlex(11, "bruno", "bruno@exemplo.pt")
                )
            return {"success": True}

        primeiro_gestor = _gestor(data_manager, envio={"success": True})
        primeiro_gestor.send_invite = envio_que_intercala

        resultados["primeiro"] = primeiro_gestor.claim_invitation(
            "UNICO", _ContaPlex(10, "ana", "ana@exemplo.pt")
        )

        aceites = [r for r in resultados.values() if r.get("success")]
        assert len(aceites) == 1, "só um dos dois resgates simultâneos pode ser aceite"

        convite = data_manager.get_invitation("UNICO")
        assert convite["use_count"] == 1
        assert convite["claimed_by_users"] == ["ana"]


class TestAbusoDeTestesPorIdDoPlex:
    """
    O username do Plex pode ser alterado pelo próprio utilizador — o painel tem
    sincronização (`_sync_local_user_data`) precisamente porque isso acontece.
    A verificação anti-abuso comparava só o username, por isso bastava mudar de
    nome para ganhar um segundo período de teste, quantas vezes se quisesse.
    """

    def _gestor_trial(self, data_manager):
        return _gestor(data_manager, envio={"success": True})

    def test_id_registado_no_resgate(self, app_context, data_manager):
        data_manager.add_invitation("TESTE-1", detalhes(trial_duration_minutes=60))
        gestor = self._gestor_trial(data_manager)

        gestor.claim_invitation("TESTE-1", _ContaPlex(77, "ana", "ana@exemplo.pt"))

        assert data_manager.get_invitation("TESTE-1")["claimed_by_ids"] == ["77"]

    def test_mudar_de_username_ja_nao_dá_um_segundo_teste(self, app_context, data_manager):
        data_manager.add_invitation("TESTE-1", detalhes(trial_duration_minutes=60))
        data_manager.add_invitation("TESTE-2", detalhes(trial_duration_minutes=60))
        gestor = self._gestor_trial(data_manager)

        gestor.claim_invitation("TESTE-1", _ContaPlex(77, "ana", "ana@exemplo.pt"))

        # A MESMA conta do Plex (ID 77) volta, agora com outro username.
        segundo = gestor.claim_invitation("TESTE-2", _ContaPlex(77, "ana-nova", "ana@exemplo.pt"))

        assert segundo["success"] is False
        assert "teste" in segundo["message"].lower()

    def test_utilizador_diferente_continua_a_poder_usar_um_teste(self, app_context, data_manager):
        data_manager.add_invitation("TESTE-1", detalhes(trial_duration_minutes=60))
        data_manager.add_invitation("TESTE-2", detalhes(trial_duration_minutes=60))
        gestor = self._gestor_trial(data_manager)

        gestor.claim_invitation("TESTE-1", _ContaPlex(77, "ana", "ana@exemplo.pt"))
        outro = gestor.claim_invitation("TESTE-2", _ContaPlex(88, "bruno", "bruno@exemplo.pt"))

        assert outro["success"] is True

    def test_historico_antigo_sem_ids_continua_a_bloquear(self, app_context, data_manager):
        """
        Convites resgatados antes desta alteração não têm IDs guardados.
        Ignorá-los reabriria a mesma brecha para quem já está no histórico.
        """
        data_manager.add_invitation("ANTIGO", detalhes(trial_duration_minutes=60))
        data_manager.increment_invitation_use("ANTIGO", "ana")  # sem media_user_id
        data_manager.add_invitation("TESTE-2", detalhes(trial_duration_minutes=60))

        resultado = self._gestor_trial(data_manager).claim_invitation(
            "TESTE-2", _ContaPlex(77, "ana", "ana@exemplo.pt")
        )

        assert resultado["success"] is False

    def test_resgate_duplicado_reconhecido_pelo_id(self, app_context, data_manager):
        data_manager.add_invitation("MULTI", detalhes(max_uses=5))
        gestor = _gestor(data_manager, envio={"success": True})

        gestor.claim_invitation("MULTI", _ContaPlex(77, "ana", "ana@exemplo.pt"))
        repetido = gestor.claim_invitation("MULTI", _ContaPlex(77, "ana-nova", "ana@exemplo.pt"))

        assert repetido["success"] is False
        assert data_manager.get_invitation("MULTI")["use_count"] == 1

    def test_libertar_a_vaga_retira_tambem_o_id(self, app_context, data_manager):
        data_manager.add_invitation("UNICO", detalhes(max_uses=1))
        gestor = _gestor(data_manager, envio={"success": False, "message": "Plex fora do ar"})

        gestor.claim_invitation("UNICO", _ContaPlex(77, "ana", "ana@exemplo.pt"))

        convite = data_manager.get_invitation("UNICO")
        assert convite["claimed_by_ids"] == []
        assert convite["claimed_by_users"] == []


class TestAcessoAosPedidosNoResgate:
    """🐛 O acesso ao Seerr era dado por garantido ao resgatar um convite.

    O perfil ficava com `overseerr_access` a True mesmo quando a importação
    falhava (Seerr em baixo, chave errada): o painel mostrava o acesso ligado,
    a pessoa não conseguia pedir nada, e desligar-e-ligar era a única forma de
    o repor.
    """

    class SeerrFalso:
        def __init__(self, resultado=None, erro=None):
            self.importados = []
            self.resultado = resultado or {"success": True}
            self.erro = erro

        def import_user(self, user_info, tipo_servidor='plex'):
            self.importados.append((user_info, tipo_servidor))
            if self.erro:
                raise self.erro
            return self.resultado

    def _gestor_com(self, seerr, data_manager):
        gestor = _gestor(data_manager, envio={"success": True})
        gestor.overseerr_manager = seerr
        return gestor

    def test_entra_pela_porta_do_plex(self, app_context, data_manager):
        seerr = self.SeerrFalso()
        gestor = self._gestor_com(seerr, data_manager)

        assert gestor._dar_acesso_aos_pedidos(_ContaPlex(10, "ana", "ana@exemplo.pt")) is True
        user_info, tipo = seerr.importados[0]
        assert tipo == 'plex'
        assert user_info == {"id": 10, "email": "ana@exemplo.pt", "username": "ana"}

    def test_uma_recusa_do_seerr_nao_liga_o_acesso(self, app_context, data_manager):
        seerr = self.SeerrFalso({"success": False, "message": "recusado"})

        assert self._gestor_com(seerr, data_manager)._dar_acesso_aos_pedidos(
            _ContaPlex(10, "ana", "ana@exemplo.pt")
        ) is False

    def test_o_seerr_em_baixo_nao_derruba_o_resgate(self, app_context, data_manager):
        seerr = self.SeerrFalso(erro=RuntimeError("sem rede"))

        assert self._gestor_com(seerr, data_manager)._dar_acesso_aos_pedidos(
            _ContaPlex(10, "ana", "ana@exemplo.pt")
        ) is False

    def test_sem_seerr_configurado_nao_rebenta(self, app_context, data_manager):
        gestor = _gestor(data_manager, envio={"success": True})

        assert gestor._dar_acesso_aos_pedidos(_ContaPlex(10, "ana", "ana@exemplo.pt")) is False


class TestLimitesNumericos:
    """
    🐛 Os campos de tempo tinham `ge=0` e mais nada, e `create_invitation`
    soma-os a `datetime.now()`. Um número grande o suficiente levantava
    `OverflowError: date value out of range` — um 500 com traceback numa rota
    que tinha acabado de validar a entrada.

    O `trial_duration_minutes` era o pior dos dois: a CRIAÇÃO passava e só o
    RESGATE rebentava, na cara de quem estava a entrar.
    """

    @pytest.mark.parametrize("campo", ["expires_in_minutes", "trial_duration_minutes"])
    def test_um_tempo_impossivel_e_recusado_e_nao_rebenta(self, admin, db_session, campo):
        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], campo: 10 ** 12,
        })
        assert resposta.status_code == 400

    def test_um_numero_de_usos_absurdo_e_recusado(self, admin, db_session):
        """Mil milhões de vagas é um convite público e eterno criado por engano."""
        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "max_uses": 10 ** 9,
        })
        assert resposta.status_code == 400

    def test_os_valores_normais_continuam_a_passar(self, admin, db_session):
        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "expires_in_minutes": 1440,
            "trial_duration_minutes": 60, "max_uses": 5,
        })
        assert resposta.status_code != 400

    def test_um_convite_ja_gravado_com_um_tempo_impossivel_nao_rebenta(self, app_context, data_manager):
        """
        O esquema defende a ENTRADA; isto defende o que já está na base de
        dados. Um convite criado antes deste limite existir continua lá com o
        valor absurdo, e quem o resgatasse levava com o `OverflowError`.
        """
        from app.services.media_server.invitations import _minutos_seguros, TETO_DE_MINUTOS

        assert _minutos_seguros(10 ** 12) == TETO_DE_MINUTOS
        assert _minutos_seguros(10 ** 20) == TETO_DE_MINUTOS
        assert _minutos_seguros(60) == 60
        assert _minutos_seguros(0) == 0
        assert _minutos_seguros(None) == 0
        assert _minutos_seguros("nem um número") == 0

    def test_o_teto_continua_a_dar_uma_data_valida(self, app_context, data_manager):
        from app.services.media_server.invitations import TETO_DE_MINUTOS

        data_manager.add_invitation("TETO", detalhes())
        # O que interessa é não levantar: a data tem de ser representável.
        from datetime import datetime as dt
        assert dt.now(timezone.utc) + timedelta(minutes=TETO_DE_MINUTOS)


class TestApagarUmConviteQueNaoExiste:
    """
    🐛 O retorno do `data_manager` era deitado fora e a resposta era sempre
    "Convite removido com sucesso" — mesmo para um código que nunca existiu.
    Quem apagasse pelo código errado ficava convencido de que tinha apagado.
    """

    def test_apagar_um_codigo_inexistente_diz_que_nao_existe(self, admin, db_session):
        resposta = admin.post("/api/invites/delete", json={"code": "NUNCA-EXISTIU"})
        assert resposta.get_json()["success"] is False

    def test_apagar_um_convite_a_serio_continua_a_funcionar(self, admin, data_manager):
        data_manager.add_invitation("PARA-APAGAR", detalhes())
        resposta = admin.post("/api/invites/delete", json={"code": "PARA-APAGAR"})
        assert resposta.get_json()["success"] is True
        assert data_manager.get_invitation("PARA-APAGAR") is None
