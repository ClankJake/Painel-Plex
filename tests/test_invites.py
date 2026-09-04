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

    def get_all_plex_users(self):
        return [{"id": i} for i in self.ids_no_plex]


class _PlexManagerFalso:
    def __init__(self):
        self.limites = []

    def update_screen_limit(self, plex_user_id, limite):
        self.limites.append((plex_user_id, limite))


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
    from app.services.plex.invite_manager import PlexInviteManager

    gestor = PlexInviteManager(
        connection=None,
        user_manager=_UserManagerFalso(),
        data_manager=data_manager,
        plex_manager=_PlexManagerFalso(),
        overseerr_manager=None,
        notifier_manager=None,
    )
    gestor.send_plex_invite = lambda **kwargs: envio
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
        primeiro_gestor.send_plex_invite = envio_que_intercala

        resultados["primeiro"] = primeiro_gestor.claim_invitation(
            "UNICO", _ContaPlex(10, "ana", "ana@exemplo.pt")
        )

        aceites = [r for r in resultados.values() if r.get("success")]
        assert len(aceites) == 1, "só um dos dois resgates simultâneos pode ser aceite"

        convite = data_manager.get_invitation("UNICO")
        assert convite["use_count"] == 1
        assert convite["claimed_by_users"] == ["ana"]
