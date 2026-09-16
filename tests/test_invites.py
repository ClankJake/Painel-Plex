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


class TestEnderecoDoConvite:
    """
    🐛 O `invite_url` era montado com `url_for(_external=True)`, que lê o
    endereço do PEDIDO. Um bot que corre na mesma rede de contentores chama o
    painel pelo nome interno, e o link que ele recebia — e mandava para o
    Telegram de quem ia entrar — só funcionava de dentro dessa rede.

    O resto do painel (o link de pagamento, o de reposição de palavra-passe) já
    resolvia isto com a `APP_BASE_URL`.
    """

    def test_o_link_respeita_a_app_base_url(self, admin, db_session, config_file):
        config_file(IS_CONFIGURED=True, APP_BASE_URL="https://painel.exemplo.com")
        resposta = admin.post("/api/invites/create", json={"libraries": ["Filmes"]})
        dados = resposta.get_json()
        assert dados["success"] is True
        assert dados["invite_url"].startswith("https://painel.exemplo.com/invite/")

    def test_uma_barra_a_mais_no_fim_nao_duplica(self, admin, db_session, config_file):
        config_file(IS_CONFIGURED=True, APP_BASE_URL="https://painel.exemplo.com/")
        resposta = admin.post("/api/invites/create", json={"libraries": ["Filmes"]})
        assert "//invite/" not in resposta.get_json()["invite_url"].replace("https://", "")

    def test_sem_app_base_url_continua_a_usar_o_pedido(self, admin, db_session, config_file):
        config_file(IS_CONFIGURED=True, APP_BASE_URL="")
        resposta = admin.post("/api/invites/create", json={"libraries": ["Filmes"]})
        assert resposta.get_json()["invite_url"].startswith("http://localhost/invite/")


class _ServidorFalso:
    """O mínimo da fachada que as rotas de criação tocam.

    `criar_a_serio` manda a criação para o ciclo de vida REAL: é o que permite
    testar os conflitos (código repetido, Telegram ID já com convite), que são
    decididos lá e não aqui.
    """

    def __init__(self, bibliotecas=('Filmes', 'Séries'), rebenta=False, criar_a_serio=False):
        self.bibliotecas = list(bibliotecas)
        self.rebenta = rebenta
        self.criar_a_serio = criar_a_serio
        self.criados = []

    def get_libraries(self):
        if self.rebenta:
            raise RuntimeError("servidor em baixo")
        # A forma REAL do contrato: uma lista, não um dicionário com 'success'.
        return [{'title': t, 'key': str(i)} for i, t in enumerate(self.bibliotecas)]

    def create_invitation(self, **kwargs):
        self.criados.append(kwargs)
        if self.criar_a_serio:
            from app.extensions import media_server
            return media_server.create_invitation(**kwargs)
        return {"success": True, "code": "CODIGO", "message": "ok"}

    def delete_invitation(self, code):
        from app.extensions import media_server
        return media_server.delete_invitation(code)

    def list_invitations(self):
        from app.extensions import media_server
        return media_server.list_invitations()


@pytest.fixture()
def servidor_falso(monkeypatch):
    duplo = _ServidorFalso()
    monkeypatch.setattr('app.blueprints.api.invites.media_server', duplo)
    return duplo


@pytest.fixture()
def servidor_que_cria_a_serio(monkeypatch):
    """As bibliotecas são do duplo; a criação do convite é a de verdade."""
    duplo = _ServidorFalso(criar_a_serio=True)
    monkeypatch.setattr('app.blueprints.api.invites.media_server', duplo)
    return duplo


class TestBibliotecasDoConvite:
    """
    🐛 Um convite era aceite com QUALQUER nome de biblioteca. A falha só
    aparecia no RESGATE, dentro do `send_invite` — quem pagava o engano do
    administrador era quem tinha acabado de clicar no link.
    """

    def test_uma_biblioteca_que_nao_existe_e_recusada_logo(self, admin, db_session, servidor_falso):
        resposta = admin.post("/api/invites/create", json={"libraries": ["Documentários"]})
        assert resposta.status_code == 400
        assert "Documentários" in resposta.get_json()["message"]
        assert servidor_falso.criados == []

    def test_a_grafia_do_servidor_e_a_que_fica_gravada(self, admin, db_session, servidor_falso):
        """
        O backend do Plex compara `s.title in library_titles` exatamente: um
        convite criado com "filmes" num servidor que tem "Filmes" nascia com
        uma biblioteca que nunca ia ser encontrada.
        """
        resposta = admin.post("/api/invites/create", json={"libraries": ["filmes", "SÉRIES"]})
        assert resposta.status_code == 200
        assert servidor_falso.criados[0]["library_titles"] == ["Filmes", "Séries"]

    def test_o_servidor_em_baixo_nao_impede_criar_um_convite(self, admin, db_session, monkeypatch):
        """Não saber que bibliotecas existem não é o mesmo que saber que não existem."""
        duplo = _ServidorFalso(rebenta=True)
        monkeypatch.setattr('app.blueprints.api.invites.media_server', duplo)

        resposta = admin.post("/api/invites/create", json={"libraries": ["Filmes"]})
        assert resposta.status_code == 200
        assert duplo.criados[0]["library_titles"] == ["Filmes"]


class TestBibliotecasNoEndpointDosBots:
    """
    🐛 `get_libraries()` devolve uma LISTA e o código pedia-lhe
    `.get('success')`. O `AttributeError` caía no `except` mesmo com o servidor
    a responder: o campo que a documentação anuncia como opcional dava sempre
    400, e nenhum bot podia deixar de conhecer os nomes das bibliotecas.
    """

    def _chave(self):
        from app.config import load_or_create_config
        return str(load_or_create_config().get('INTERNAL_TRIGGER_KEY') or '')

    def test_sem_bibliotecas_usa_todas_as_do_servidor(self, client, configurada, db_session, servidor_falso):
        resposta = client.post(
            "/api/invites/bot/create",
            json={"telegram_id": 123456789},
            headers={"X-API-Key": self._chave()},
        )
        assert resposta.status_code == 201
        assert servidor_falso.criados[0]["library_titles"] == ["Filmes", "Séries"]

    def test_uma_biblioteca_inventada_pelo_bot_e_recusada(self, client, configurada, db_session, servidor_falso):
        resposta = client.post(
            "/api/invites/bot/create",
            json={"telegram_id": 123456789, "libraries": ["Anime"]},
            headers={"X-API-Key": self._chave()},
        )
        assert resposta.status_code == 400
        assert servidor_falso.criados == []


class TestReativarNaoTornaOConviteEterno:
    def test_um_convite_expirado_ganha_a_mesma_janela(self, admin, data_manager):
        data_manager.add_invitation("PROMO-24H", detalhes(
            max_uses=1, created_at=iso(-3), expires_at=iso(-1),
        ))
        data_manager.increment_invitation_use("PROMO-24H", "ana")

        resposta = admin.post("/api/invites/reactivate", json={"code": "PROMO-24H"})

        assert resposta.get_json()["success"] is True
        assert data_manager.get_invitation("PROMO-24H")["expires_at"] is not None

    def test_um_convite_sem_prazo_continua_sem_prazo(self, admin, data_manager):
        """E a mensagem não pode prometer uma validade que não existe."""
        data_manager.add_invitation("SEM-PRAZO", detalhes(max_uses=1, expires_at=None))
        data_manager.increment_invitation_use("SEM-PRAZO", "ana")

        resposta = admin.post("/api/invites/reactivate", json={"code": "SEM-PRAZO"})
        dados = resposta.get_json()

        assert dados["success"] is True
        assert data_manager.get_invitation("SEM-PRAZO")["expires_at"] is None
        assert "não tem prazo" in dados["message"]

    def test_um_convite_ainda_valido_mantem_a_data_que_tinha(self, admin, data_manager):
        futuro = iso(5)
        data_manager.add_invitation("AINDA-VALE", detalhes(max_uses=1, expires_at=futuro))
        data_manager.increment_invitation_use("AINDA-VALE", "ana")

        admin.post("/api/invites/reactivate", json={"code": "AINDA-VALE"})

        assert data_manager.get_invitation("AINDA-VALE")["expires_at"] == futuro

    def test_uma_data_corrompida_nao_rebenta_a_reativacao(self, admin, data_manager):
        data_manager.add_invitation("DATA-MA", detalhes(max_uses=1, expires_at="nem-uma-data"))

        resposta = admin.post("/api/invites/reactivate", json={"code": "DATA-MA"})

        assert resposta.get_json()["success"] is True
        assert data_manager.get_invitation("DATA-MA")["expires_at"] is not None


class TestIdentidadeRegistadaNoConvite:
    """
    🐛 Onde as contas são LOCAIS, a vaga é reservada antes de a conta existir —
    e por isso sem ID. Isso era corrigido com um `release` seguido de um
    `reserve`, e entre os dois a vaga ficava LIVRE: com o worker gevent, outro
    resgate podia ficar com ela, o `reserve` seguinte devolvia False (que
    ninguém verificava) e o ID nunca chegava ao convite.
    """

    def test_o_id_entra_sem_gastar_outra_vaga(self, app_context, data_manager):
        data_manager.add_invitation("LOCAL", detalhes(max_uses=1))
        data_manager.reserve_invitation_use("LOCAL", "ana", None)

        assert data_manager.registar_identidade_no_convite("LOCAL", "ana", "guid-da-ana") is True

        convite = data_manager.get_invitation("LOCAL")
        assert convite["use_count"] == 1, "registar o ID não é gastar outra vaga"
        assert convite["claimed_by_ids"] == ["guid-da-ana"]
        assert convite["claimed_by_users"] == ["ana"]

    def test_um_convite_esgotado_continua_a_aceitar_o_id_de_quem_o_gastou(self, app_context, data_manager):
        """
        É este o caso que o `release`+`reserve` perdia: com as vagas esgotadas
        entretanto, o segundo `reserve` falhava e a pessoa ficava sem ID.
        """
        data_manager.add_invitation("LOCAL", detalhes(max_uses=1))
        data_manager.reserve_invitation_use("LOCAL", "ana", None)
        assert data_manager.reserve_invitation_use("LOCAL", "bruno", None) is False

        assert data_manager.registar_identidade_no_convite("LOCAL", "ana", "guid-da-ana") is True
        assert data_manager.get_invitation("LOCAL")["claimed_by_ids"] == ["guid-da-ana"]

    def test_repetir_nao_duplica(self, app_context, data_manager):
        data_manager.add_invitation("LOCAL", detalhes(max_uses=1))
        data_manager.reserve_invitation_use("LOCAL", "ana", None)

        data_manager.registar_identidade_no_convite("LOCAL", "ana", "guid-da-ana")
        data_manager.registar_identidade_no_convite("LOCAL", "ana", "guid-da-ana")

        convite = data_manager.get_invitation("LOCAL")
        assert convite["claimed_by_ids"] == ["guid-da-ana"]
        assert convite["claimed_by_users"] == ["ana"]

    def test_um_convite_que_nao_existe_diz_que_nao(self, app_context, data_manager):
        assert data_manager.registar_identidade_no_convite("NADA", "ana", "guid") is False


class TestChaveDeApi:
    """
    A verificação da chave existia copiada em dois sítios e as cópias já tinham
    divergido: o webhook do Overseerr aceitava o `Authorization` sem o prefixo
    `Bearer` e o endpoint dos convites não. Quem configurasse os dois com o
    mesmo cliente levava 401 num deles sem perceber porquê.
    """

    def _chave(self):
        from app.config import load_or_create_config
        return str(load_or_create_config().get('INTERNAL_TRIGGER_KEY') or '')

    def _criar(self, client, headers):
        return client.post("/api/invites/bot/create", json={"telegram_id": 1}, headers=headers)

    def test_sem_chave_nenhuma(self, client, configurada, db_session, servidor_falso):
        assert self._criar(client, {}).status_code == 401

    def test_com_a_chave_errada(self, client, configurada, db_session, servidor_falso):
        assert self._criar(client, {"X-API-Key": "nao-e-esta"}).status_code == 401

    def test_x_api_key(self, client, configurada, db_session, servidor_falso):
        assert self._criar(client, {"X-API-Key": self._chave()}).status_code == 201

    def test_authorization_com_bearer(self, client, configurada, db_session, servidor_falso):
        cabecalhos = {"Authorization": f"Bearer {self._chave()}"}
        assert self._criar(client, cabecalhos).status_code == 201

    def test_authorization_sem_bearer(self, client, configurada, db_session, servidor_falso):
        """
        A interface do Overseerr chama ao campo "Authorization", e quem o
        preenche escreve lá a chave e mais nada. Os dois caminhos passam a
        aceitar as duas formas.
        """
        assert self._criar(client, {"Authorization": self._chave()}).status_code == 201

    def test_o_webhook_do_overseerr_usa_a_mesma_porta(self, client, configurada, db_session):
        recusado = client.post("/api/system/webhook/overseerr", json={"notification_type": "TEST"})
        assert recusado.status_code == 401

        aceite = client.post(
            "/api/system/webhook/overseerr",
            json={"notification_type": "TEST"},
            headers={"Authorization": self._chave()},
        )
        assert aceite.status_code == 200


class TestAuditoriaDosConvites:
    """
    🛡️ Criar um convite CONCEDE ACESSO ao servidor, e nada disso deixava rasto.
    Cupões, pagamentos e bloqueios eram todos auditados; os convites — a porta
    de entrada — não.
    """

    def _linhas(self, acao=None):
        from app.extensions import db
        from sqlalchemy import text

        with db.engine.begin() as ligacao:
            filas = ligacao.execute(text(
                'SELECT acao, alvo_id, detalhes, ator FROM audit_logs ORDER BY id'
            )).fetchall()
        return [f for f in filas if acao is None or f[0] == acao]

    def test_criar_fica_registrado_com_o_que_o_convite_da(self, admin, db_session, servidor_falso):
        admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "max_uses": 3, "screens": 2,
        })

        linhas = self._linhas('convite.criar')
        assert len(linhas) == 1
        assert linhas[0][1] == "CODIGO"
        assert '"usos": 3' in linhas[0][2]
        assert linhas[0][3] == "admin", "o administrador que criou tem de ficar nomeado"

    def test_apagar_guarda_o_que_o_convite_era(self, admin, data_manager):
        data_manager.add_invitation("PROMO", detalhes(max_uses=2))
        data_manager.increment_invitation_use("PROMO", "ana")

        admin.post("/api/invites/delete", json={"code": "PROMO"})

        linhas = self._linhas('convite.apagar')
        assert len(linhas) == 1
        # Depois de apagado não há a quem perguntar o que ele era.
        assert '"usos": "1/2"' in linhas[0][2]
        assert 'ana' in linhas[0][2]

    def test_apagar_um_convite_que_nao_existe_nao_regista_nada(self, admin, db_session):
        admin.post("/api/invites/delete", json={"code": "NUNCA-EXISTIU"})
        assert self._linhas('convite.apagar') == []

    def test_reativar_fica_registrado(self, admin, data_manager):
        data_manager.add_invitation("PROMO", detalhes(max_uses=1))
        admin.post("/api/invites/reactivate", json={"code": "PROMO"})

        assert len(self._linhas('convite.reativar')) == 1

    def test_um_convite_criado_por_um_bot_nao_inventa_um_ator(self, client, configurada, db_session, servidor_falso):
        from app.config import load_or_create_config

        client.post(
            "/api/invites/bot/create",
            json={"telegram_id": 42},
            headers={"X-API-Key": str(load_or_create_config().get('INTERNAL_TRIGGER_KEY'))},
        )

        linhas = self._linhas('convite.criar')
        assert len(linhas) == 1
        assert linhas[0][3] is None, "uma máquina não é uma pessoa; a coluna vazia diz a verdade"
        assert '"origem": "bot"' in linhas[0][2]

    def test_a_palavra_passe_do_resgate_nunca_entra_na_auditoria(self, app_context, monkeypatch):
        """
        🛡️ O corpo do resgate traz a senha que a pessoa acabou de escolher, e a
        auditoria vai dentro do ZIP de backup. Só os campos escolhidos à mão.
        """
        from app.blueprints.api import invites as rotas

        registados = []
        monkeypatch.setattr(rotas.audit, 'registar',
                            lambda *a, **k: registados.append((a, k)))

        rotas._registar_resgate("CODIGO", {"success": True}, "ana")

        assert len(registados) == 1
        corpo = str(registados[0])
        assert "senha" not in corpo.lower() and "password" not in corpo.lower()
        assert "ana" in corpo

    def test_um_resgate_falhado_nao_e_registrado_como_resgate(self, app_context, monkeypatch):
        from app.blueprints.api import invites as rotas

        registados = []
        monkeypatch.setattr(rotas.audit, 'registar', lambda *a, **k: registados.append(a))
        rotas._registar_resgate("CODIGO", {"success": False, "message": "expirou"}, "ana")

        assert registados == []


class TestAuditoriaDeUmConviteJaExpirado:
    """
    ⚠️ Um convite expirado ou esgotado é o que mais se apaga, e
    `get_invitation_by_code` recusa-se a devolvê-lo (é a porta do resgate, não
    a de leitura). A auditoria tem de ler a linha CRUA, ou ficava vazia
    precisamente no caso comum.
    """

    def test_o_que_ele_era_fica_registrado_mesmo_expirado(self, admin, data_manager):
        from app.extensions import db
        from sqlalchemy import text

        data_manager.add_invitation("VENCIDO", detalhes(max_uses=2, expires_at=iso(-5)))
        data_manager.increment_invitation_use("VENCIDO", "ana")

        admin.post("/api/invites/delete", json={"code": "VENCIDO"})

        with db.engine.begin() as ligacao:
            detalhe = ligacao.execute(text(
                "SELECT detalhes FROM audit_logs WHERE acao = 'convite.apagar'"
            )).scalar()

        assert '"usos": "1/2"' in detalhe
        assert 'ana' in detalhe


class TestAApiDeBotsSabeResponderSobreUmConvite:
    """
    A API de bots só sabia CRIAR. Um bot que gerava um convite ficava sem saber
    o que lhe tinha acontecido — a única alternativa era perguntar à pessoa — e
    um link mandado para o chat errado não tinha como ser travado sem entrar no
    painel, que é o que uma automação, por definição, não faz.
    """

    def _chave(self):
        from app.config import load_or_create_config
        return {"X-API-Key": str(load_or_create_config().get('INTERNAL_TRIGGER_KEY') or '')}

    def test_um_convite_por_usar_diz_que_esta_ativo(self, client, configurada, data_manager):
        data_manager.add_invitation("ABERTO", detalhes(max_uses=2))

        dados = client.get("/api/invites/bot/invite/ABERTO", headers=self._chave()).get_json()

        assert dados["invite"]["active"] is True
        assert dados["invite"]["uses_left"] == 2
        assert dados["invite"]["invite_url"].endswith("/invite/ABERTO")

    def test_um_convite_gasto_diz_quem_o_gastou(self, client, configurada, data_manager):
        data_manager.add_invitation("GASTO", detalhes(max_uses=1))
        data_manager.increment_invitation_use("GASTO", "ana")

        dados = client.get("/api/invites/bot/invite/GASTO", headers=self._chave()).get_json()

        assert dados["invite"]["active"] is False
        assert dados["invite"]["exhausted"] is True
        assert dados["invite"]["claimed_by"] == ["ana"]
        assert dados["invite"]["uses_left"] == 0

    def test_um_convite_expirado_continua_a_ser_encontrado(self, client, configurada, data_manager):
        """
        ⚠️ Com a porta do RESGATE (`get_invitation_by_code`), um convite gasto
        seria indistinguível de um que nunca existiu — e "expirado" é
        precisamente a resposta que se veio buscar.
        """
        data_manager.add_invitation("VENCIDO", detalhes(expires_at=iso(-1)))

        resposta = client.get("/api/invites/bot/invite/VENCIDO", headers=self._chave())

        assert resposta.status_code == 200
        assert resposta.get_json()["invite"]["expired"] is True

    def test_um_convite_que_nao_existe_da_404(self, client, configurada, db_session):
        resposta = client.get("/api/invites/bot/invite/NADA", headers=self._chave())
        assert resposta.status_code == 404

    def test_sem_chave_nao_se_pergunta_nada(self, client, configurada, data_manager):
        data_manager.add_invitation("ABERTO", detalhes())
        assert client.get("/api/invites/bot/invite/ABERTO").status_code == 401

    def test_a_vista_publica_nao_leva_as_bibliotecas(self, client, configurada, data_manager):
        """🔒 Os nomes das bibliotecas são infraestrutura do servidor."""
        data_manager.add_invitation("ABERTO", detalhes())

        dados = client.get("/api/invites/bot/invite/ABERTO", headers=self._chave()).get_json()

        assert "libraries" not in dados["invite"]

    def test_revogar_trava_o_link(self, client, configurada, data_manager):
        data_manager.add_invitation("ENGANO", detalhes())

        resposta = client.delete("/api/invites/bot/invite/ENGANO", headers=self._chave())

        assert resposta.get_json()["success"] is True
        assert data_manager.get_invitation("ENGANO") is None

    def test_revogar_o_que_nao_existe_da_404(self, client, configurada, db_session):
        assert client.delete("/api/invites/bot/invite/NADA", headers=self._chave()).status_code == 404

    def test_revogar_deixa_rasto_na_auditoria(self, client, configurada, data_manager):
        from app.extensions import db
        from sqlalchemy import text

        data_manager.add_invitation("ENGANO", detalhes())
        client.delete("/api/invites/bot/invite/ENGANO", headers=self._chave())

        with db.engine.begin() as ligacao:
            detalhe = ligacao.execute(text(
                "SELECT detalhes FROM audit_logs WHERE acao = 'convite.apagar'"
            )).scalar()
        assert '"origem": "bot"' in detalhe


class TestOsConvitesDeUmTelegramId:
    def _chave(self):
        from app.config import load_or_create_config
        return {"X-API-Key": str(load_or_create_config().get('INTERNAL_TRIGGER_KEY') or '')}

    def test_so_os_daquela_pessoa(self, client, configurada, data_manager):
        data_manager.add_invitation("DELE", detalhes(telegram_id="123"))
        data_manager.add_invitation("DOUTRO", detalhes(telegram_id="999"))
        data_manager.add_invitation("DE-NINGUEM", detalhes())

        dados = client.get("/api/invites/bot/invites?telegram_id=123",
                           headers=self._chave()).get_json()

        assert [c["code"] for c in dados["invites"]] == ["DELE"]

    def test_o_id_com_espacos_encontra_o_mesmo(self, client, configurada, data_manager):
        """A mesma normalização da criação: '123' tem de encontrar ' 123 '."""
        data_manager.add_invitation("DELE", detalhes(telegram_id=" 123 "))

        dados = client.get("/api/invites/bot/invites?telegram_id=123",
                           headers=self._chave()).get_json()

        assert len(dados["invites"]) == 1

    def test_sem_telegram_id_e_um_erro_do_pedido(self, client, configurada, db_session):
        resposta = client.get("/api/invites/bot/invites", headers=self._chave())
        assert resposta.status_code == 400

    def test_ninguem_com_convites_devolve_uma_lista_vazia(self, client, configurada, db_session):
        dados = client.get("/api/invites/bot/invites?telegram_id=555",
                           headers=self._chave()).get_json()
        assert dados["success"] is True and dados["invites"] == []


class TestOsCodigosHttpDaCriacao:
    """
    🐛 A rota respondia 409 a TUDO o que falhasse — inclusive a "informe pelo
    menos uma biblioteca", que é um erro do PEDIDO. Do outro lado não havia
    como saber se valia a pena tentar outra vez com outro código (409: o estado
    é que não deixa) ou se o pedido estava errado (400: tentar de novo dá o
    mesmo).
    """

    def _chave(self):
        from app.config import load_or_create_config
        return {"X-API-Key": str(load_or_create_config().get('INTERNAL_TRIGGER_KEY') or '')}

    def test_um_codigo_ja_em_uso_e_409(self, client, configurada, data_manager, servidor_que_cria_a_serio):
        data_manager.add_invitation("REPETIDO", detalhes())

        resposta = client.post("/api/invites/bot/create",
                               json={"telegram_id": 1, "custom_code": "REPETIDO"},
                               headers=self._chave())
        assert resposta.status_code == 409
        assert resposta.get_json()["erro"] == "conflito"

    def test_um_telegram_id_ja_com_convite_ativo_e_409(self, client, configurada, data_manager, servidor_que_cria_a_serio):
        data_manager.add_invitation("JA-TEM", detalhes(telegram_id="777"))

        resposta = client.post("/api/invites/bot/create", json={"telegram_id": 777},
                               headers=self._chave())
        assert resposta.status_code == 409

    def test_uma_biblioteca_inventada_e_400(self, client, configurada, db_session, servidor_falso):
        resposta = client.post("/api/invites/bot/create",
                               json={"telegram_id": 1, "libraries": ["Nao-Existe"]},
                               headers=self._chave())
        assert resposta.status_code == 400


class TestApagarUmConviteNaoApagaOMembroDesde:
    """
    🛡️ `get_user_claim_date` — a única resposta do painel ao "desde quando é
    que esta pessoa está aqui" — procura o username dentro de
    `claimed_by_users`. Não há outra fonte: a data não está no perfil. O botão
    que existe para arrumar a lista de convites gastos destruía em silêncio o
    histórico de entrada de cada pessoa que os tinha resgatado.
    """

    def _perfil(self, data_manager, nome="ana", id_="10"):
        data_manager.set_user_profile(id_, {"username": nome})
        return id_

    def test_a_data_de_entrada_sobrevive_ao_convite(self, admin, data_manager):
        media_user_id = self._perfil(data_manager)
        data_manager.add_invitation("PROMO", detalhes(max_uses=1))
        data_manager.increment_invitation_use("PROMO", "ana", media_user_id)
        antes = data_manager.get_user_claim_date(media_user_id)
        assert antes is not None

        admin.post("/api/invites/delete", json={"code": "PROMO"})

        assert data_manager.get_user_claim_date(media_user_id) == antes

    def test_o_link_deixa_de_funcionar_mesmo_assim(self, admin, client, configurada, data_manager):
        data_manager.add_invitation("PROMO", detalhes(max_uses=3))
        admin.post("/api/invites/delete", json={"code": "PROMO"})

        assert data_manager.get_invitation("PROMO") is None
        assert client.get("/api/invites/details/PROMO").status_code == 404

    def test_sai_da_lista_do_painel(self, admin, data_manager):
        data_manager.add_invitation("PROMO", detalhes())
        admin.post("/api/invites/delete", json={"code": "PROMO"})

        assert [c["code"] for c in data_manager.get_all_invitations()] == []

    def test_apagar_duas_vezes_diz_que_nao_ha_nada(self, admin, data_manager):
        data_manager.add_invitation("PROMO", detalhes())
        admin.post("/api/invites/delete", json={"code": "PROMO"})

        segunda = admin.post("/api/invites/delete", json={"code": "PROMO"})
        assert segunda.get_json()["success"] is False


class TestReutilizarUmCodigoPersonalizado:
    def test_um_codigo_removido_e_nunca_usado_volta_a_estar_livre(self, admin, data_manager, servidor_que_cria_a_serio):
        data_manager.add_invitation("VERAO", detalhes())
        admin.post("/api/invites/delete", json={"code": "VERAO"})

        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "custom_code": "VERAO",
        })

        assert resposta.get_json()["success"] is True
        assert data_manager.get_invitation("VERAO") is not None

    def test_um_codigo_que_alguem_resgatou_nao_volta(self, admin, data_manager, servidor_que_cria_a_serio):
        """O registro de quem entrou por ele é o que se está a proteger."""
        data_manager.add_invitation("VERAO", detalhes(max_uses=1))
        data_manager.increment_invitation_use("VERAO", "ana")
        admin.post("/api/invites/delete", json={"code": "VERAO"})

        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "custom_code": "VERAO",
        })
        dados = resposta.get_json()

        assert dados["success"] is False
        assert "escolha outro código" in dados["message"]

    def test_um_codigo_vivo_continua_a_ser_recusado(self, admin, data_manager, servidor_que_cria_a_serio):
        data_manager.add_invitation("VERAO", detalhes())

        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "custom_code": "VERAO",
        })
        assert resposta.get_json()["success"] is False


class TestLimpezaDeConvitesAntigos:
    def test_um_convite_expirado_e_nunca_usado_sai(self, app_context, data_manager):
        data_manager.add_invitation("LIXO", detalhes(created_at=iso(-200), expires_at=iso(-190)))

        assert data_manager.limpar_convites_antigos(90) == 1
        assert data_manager.get_invitation("LIXO", incluir_apagados=True) is None

    def test_um_convite_resgatado_fica_para_sempre(self, app_context, data_manager):
        """É ele que responde ao "membro desde" de quem entrou por ele."""
        data_manager.add_invitation("HISTORIA", detalhes(created_at=iso(-500), expires_at=iso(-490)))
        data_manager.increment_invitation_use("HISTORIA", "ana")

        assert data_manager.limpar_convites_antigos(90) == 0
        assert data_manager.get_invitation("HISTORIA") is not None

    def test_um_convite_removido_e_nunca_usado_sai(self, app_context, data_manager):
        data_manager.add_invitation("REMOVIDO", detalhes(created_at=iso(-200)))
        data_manager.delete_invitation("REMOVIDO")

        assert data_manager.limpar_convites_antigos(90) == 1

    def test_um_convite_recente_fica(self, app_context, data_manager):
        data_manager.add_invitation("NOVO", detalhes(created_at=iso(-2), expires_at=iso(-1)))
        assert data_manager.limpar_convites_antigos(90) == 0

    def test_um_convite_sem_prazo_e_por_usar_fica(self, app_context, data_manager):
        """Antigo mas ainda válido: não é lixo, é um convite aberto."""
        data_manager.add_invitation("ABERTO", detalhes(created_at=iso(-500), expires_at=None))
        assert data_manager.limpar_convites_antigos(90) == 0

    def test_zero_dias_desliga_a_limpeza(self, app_context, data_manager):
        data_manager.add_invitation("LIXO", detalhes(created_at=iso(-900), expires_at=iso(-890)))
        assert data_manager.limpar_convites_antigos(0) == 0
        assert data_manager.get_invitation("LIXO") is not None


class TestANotaDoConvite:
    """
    O painel já guardava para QUEM um convite era, mas só quando havia
    Telegram. Todos os outros ficavam a ser um código aleatório e mais nada, e
    um convite gasto só dizia o nome de quem o usou — não o de quem o devia ter
    usado.
    """

    def test_a_nota_e_gravada(self, admin, data_manager, servidor_que_cria_a_serio):
        admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "custom_code": "COM-NOTA",
            "note": "João do grupo do WhatsApp",
        })
        assert data_manager.get_invitation("COM-NOTA")["note"] == "João do grupo do WhatsApp"

    def test_uma_nota_em_branco_e_o_mesmo_que_nota_nenhuma(self, admin, data_manager, servidor_que_cria_a_serio):
        admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "custom_code": "SEM-NOTA", "note": "   ",
        })
        assert data_manager.get_invitation("SEM-NOTA")["note"] is None

    def test_uma_nota_enorme_e_recusada(self, admin, db_session, servidor_falso):
        resposta = admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "note": "x" * 201,
        })
        assert resposta.status_code == 400

    def test_a_nota_chega_ao_bot(self, client, configurada, data_manager):
        from app.config import load_or_create_config

        data_manager.add_invitation("COM-NOTA", detalhes(note="Para a Ana"))
        dados = client.get("/api/invites/bot/invite/COM-NOTA", headers={
            "X-API-Key": str(load_or_create_config().get('INTERNAL_TRIGGER_KEY') or '')
        }).get_json()

        assert dados["invite"]["note"] == "Para a Ana"

    def test_a_nota_fica_na_auditoria_da_criacao(self, admin, db_session, servidor_falso):
        from app.extensions import db
        from sqlalchemy import text

        admin.post("/api/invites/create", json={
            "libraries": ["Filmes"], "note": "Para a Ana",
        })
        with db.engine.begin() as ligacao:
            detalhe = ligacao.execute(text(
                "SELECT detalhes FROM audit_logs WHERE acao = 'convite.criar'"
            )).scalar()
        assert "Para a Ana" in detalhe


class TestAListaPaginada:
    """
    ⚡ `/list` devolvia a tabela INTEIRA, com o histórico de resgates de cada
    convite, e a página pedia-a de dez em dez segundos — para contar quantos
    estavam abertos e para filtrar as duas abas do lado do navegador.
    """

    def _semear(self, data_manager, quantos, **extra):
        for i in range(quantos):
            data_manager.add_invitation(f"C{i:03d}", detalhes(created_at=iso(-i), **extra))

    def test_a_pagina_tem_o_tamanho_pedido(self, admin, data_manager):
        self._semear(data_manager, 25)

        dados = admin.get("/api/invites/list?estado=ativos&pagina=1&por_pagina=10").get_json()

        assert len(dados["invites"]) == 10
        assert dados["total"] == 25
        assert dados["paginas"] == 3

    def test_a_ultima_pagina_traz_o_resto(self, admin, data_manager):
        self._semear(data_manager, 25)

        dados = admin.get("/api/invites/list?estado=ativos&pagina=3&por_pagina=10").get_json()

        assert len(dados["invites"]) == 5

    def test_vem_do_mais_recente_para_o_mais_antigo(self, admin, data_manager):
        self._semear(data_manager, 5)

        dados = admin.get("/api/invites/list?estado=ativos&por_pagina=5").get_json()

        assert [c["code"] for c in dados["invites"]] == ["C000", "C001", "C002", "C003", "C004"]

    def test_a_aba_dos_ativos_nao_traz_os_esgotados(self, admin, data_manager):
        data_manager.add_invitation("ABERTO", detalhes(max_uses=2))
        data_manager.add_invitation("GASTO", detalhes(max_uses=1))
        data_manager.increment_invitation_use("GASTO", "ana")

        ativos = admin.get("/api/invites/list?estado=ativos").get_json()
        historico = admin.get("/api/invites/list?estado=historico").get_json()

        assert [c["code"] for c in ativos["invites"]] == ["ABERTO"]
        assert [c["code"] for c in historico["invites"]] == ["GASTO"]

    def test_a_aba_dos_ativos_nao_traz_os_expirados(self, admin, data_manager):
        data_manager.add_invitation("ABERTO", detalhes(expires_at=iso(5)))
        data_manager.add_invitation("VENCIDO", detalhes(expires_at=iso(-5)))

        ativos = admin.get("/api/invites/list?estado=ativos").get_json()
        historico = admin.get("/api/invites/list?estado=historico").get_json()

        assert [c["code"] for c in ativos["invites"]] == ["ABERTO"]
        assert [c["code"] for c in historico["invites"]] == ["VENCIDO"]

    def test_um_convite_sem_prazo_conta_como_ativo(self, admin, data_manager):
        data_manager.add_invitation("SEM-PRAZO", detalhes(expires_at=None))
        dados = admin.get("/api/invites/list?estado=ativos").get_json()
        assert [c["code"] for c in dados["invites"]] == ["SEM-PRAZO"]

    def test_os_removidos_nao_aparecem_em_aba_nenhuma(self, admin, data_manager):
        data_manager.add_invitation("REMOVIDO", detalhes())
        data_manager.delete_invitation("REMOVIDO")

        for aba in ("ativos", "historico"):
            assert admin.get(f"/api/invites/list?estado={aba}").get_json()["invites"] == []

    def test_uma_pagina_que_nao_e_um_numero_e_um_erro_do_pedido(self, admin, db_session):
        assert admin.get("/api/invites/list?pagina=abc").status_code == 400

    def test_o_tamanho_da_pagina_tem_teto(self, admin, data_manager):
        """Pedir 100000 por página era pedir a tabela inteira por outro caminho."""
        self._semear(data_manager, 5)

        dados = admin.get("/api/invites/list?por_pagina=100000").get_json()

        # ⚠️ A resposta ecoa o valor EFETIVO. Ecoar o pedido fazia a interface
        # calcular o número de páginas sobre um tamanho que não foi o usado.
        assert dados["por_pagina"] == 100
        assert len(dados["invites"]) == 5

    def test_uma_pagina_de_zero_nao_e_uma_pagina(self, admin, data_manager):
        self._semear(data_manager, 3)
        dados = admin.get("/api/invites/list?por_pagina=0&pagina=0").get_json()
        assert dados["por_pagina"] == 20 and dados["pagina"] == 1


class TestOResumoDosConvites:
    """É esta a pergunta que o polling faz: "já foi usado algum?"."""

    def test_conta_os_abertos_e_o_total(self, admin, data_manager):
        data_manager.add_invitation("ABERTO", detalhes(max_uses=2))
        data_manager.add_invitation("GASTO", detalhes(max_uses=1))
        data_manager.increment_invitation_use("GASTO", "ana")
        data_manager.add_invitation("VENCIDO", detalhes(expires_at=iso(-1)))

        dados = admin.get("/api/invites/summary").get_json()

        assert dados["ativos"] == 1
        assert dados["total"] == 3

    def test_os_removidos_nao_contam(self, admin, data_manager):
        data_manager.add_invitation("REMOVIDO", detalhes())
        data_manager.delete_invitation("REMOVIDO")

        dados = admin.get("/api/invites/summary").get_json()
        assert dados["ativos"] == 0 and dados["total"] == 0

    def test_e_so_para_administradores(self, client, configurada, db_session):
        assert client.get("/api/invites/summary").status_code in (302, 401, 403)


class TestOAvisoDeConviteResgatado:
    """
    O sino do painel só avisa quem está com ele aberto. Quem gera convites e
    fecha o portátil ficava a saber no dia seguinte, e quem os gera por um bot
    não ficava a saber de todo: o link era mandado e o ciclo acabava ali.
    """

    def _espiar(self, monkeypatch):
        from app.blueprints.api import invites as rotas

        avisos = []
        monkeypatch.setattr(rotas.notifier_manager,
                            'send_invite_claimed_admin_notification',
                            lambda *a, **k: avisos.append((a, k)))
        return avisos

    def test_um_resgate_avisa_o_administrador(self, app_context, data_manager, monkeypatch):
        from app.blueprints.api import invites as rotas

        avisos = self._espiar(monkeypatch)
        data_manager.add_invitation("PROMO", detalhes(note="João do grupo"))

        rotas._registar_resgate("PROMO", {"success": True}, "ana")

        assert len(avisos) == 1
        # A nota diz PARA QUEM o convite era, e é isso que torna o aviso útil.
        assert avisos[0][0] == ("ana", "PROMO", "João do grupo")

    def test_um_resgate_falhado_nao_avisa_ninguem(self, app_context, data_manager, monkeypatch):
        from app.blueprints.api import invites as rotas

        avisos = self._espiar(monkeypatch)
        data_manager.add_invitation("PROMO", detalhes())

        rotas._registar_resgate("PROMO", {"success": False, "message": "expirou"}, "ana")

        assert avisos == []

    def test_uma_falha_a_avisar_nao_derruba_o_resgate(self, app_context, data_manager, monkeypatch):
        """A pessoa já tem acesso; o aviso é sobre isso ter acontecido."""
        from app.blueprints.api import invites as rotas

        def rebenta(*a, **k):
            raise RuntimeError("sem rede")

        monkeypatch.setattr(rotas.notifier_manager,
                            'send_invite_claimed_admin_notification', rebenta)
        data_manager.add_invitation("PROMO", detalhes())

        rotas._registar_resgate("PROMO", {"success": True}, "ana")  # não levanta

    def test_o_administrador_pode_desligar_so_este_aviso(self, app_context, config_file):
        from app.services.push_manager import PushManager

        config_file(IS_CONFIGURED=True, PUSH_ADMIN_INVITES=False, PUSH_ADMIN_PAYMENTS=True)
        gestor = PushManager()
        gestor.reload_credentials()

        assert gestor.avisar_administrador['convite'] is False
        assert gestor.avisar_administrador['pagamento'] is True
