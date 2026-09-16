# tests/test_chaves_de_api.py
"""Chaves de integração: nome, escopo e revogação.

⚠️ Havia UMA chave para tudo (`INTERNAL_TRIGGER_KEY`), partilhada pelo endpoint
de convites para bots e pelo webhook do Seerr. Duas consequências que só se
notam no pior dia: regenerá-la porque um bot foi comprometido derrubava também
o Seerr, e a chave dada a um bot de Telegram podia aceitar webhooks em nome do
painel.
"""

import pytest

pytestmark = pytest.mark.integration


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


def criar(nome="Bot do Telegram", escopos=("convites",)):
    from app.services import api_keys

    return api_keys.criar(nome, list(escopos))


class TestOQueFicaGravado:
    def test_a_chave_em_si_nunca_entra_na_tabela(self, app_context, db_session):
        """
        🛡️ Quem lesse a base de dados — ou um ZIP de backup, que é só um
        ficheiro — ficava com uma porta aberta por cada integração ligada.
        """
        from app.models import ApiKey

        linha, chave = criar()

        gravada = ApiKey.query.get(linha.id)
        assert chave not in gravada.resumo
        assert gravada.resumo != chave
        assert len(gravada.resumo) == 64  # sha256 em hexadecimal

    def test_o_prefixo_e_visivel_e_identifica_a_chave(self, app_context, db_session):
        linha, chave = criar()
        assert linha.prefixo in chave

    def test_duas_chaves_nunca_saem_iguais(self, app_context, db_session):
        _, primeira = criar("A")
        _, segunda = criar("B")
        assert primeira != segunda

    def test_a_listagem_nao_devolve_a_chave(self, app_context, db_session):
        from app.services import api_keys

        _, chave = criar()
        listadas = api_keys.listar()

        assert len(listadas) == 1
        assert chave not in str(listadas)


class TestOEscopo:
    """Um escopo é uma promessa: "esta chave só cria convites"."""

    def test_uma_chave_de_convites_nao_serve_para_webhooks(self, app_context, db_session):
        from app.services import api_keys

        _, chave = criar(escopos=("convites",))

        assert api_keys.verificar(chave, 'convites') is not None
        assert api_keys.verificar(chave, 'webhooks') is None

    def test_uma_chave_pode_ter_os_dois(self, app_context, db_session):
        from app.services import api_keys

        _, chave = criar(escopos=("convites", "webhooks"))

        assert api_keys.verificar(chave, 'convites') is not None
        assert api_keys.verificar(chave, 'webhooks') is not None

    def test_um_escopo_inventado_e_ignorado(self, app_context, db_session):
        from app.services import api_keys

        linha, _ = criar(escopos=("convites", "tomar-conta-do-servidor"))

        assert api_keys.listar()[0]['escopos'] == ['convites']

    def test_uma_chave_sem_escopo_nenhum_nao_e_criada(self, app_context, db_session):
        from app.services import api_keys

        with pytest.raises(api_keys.DadosInvalidos) as erro:
            api_keys.criar("Sem permissões", [])
        assert erro.value.motivo == api_keys.DadosInvalidos.SEM_ESCOPO

    def test_uma_chave_sem_nome_nao_e_criada(self, app_context, db_session):
        from app.services import api_keys

        with pytest.raises(api_keys.DadosInvalidos) as erro:
            api_keys.criar("   ", ["convites"])
        assert erro.value.motivo == api_keys.DadosInvalidos.SEM_NOME

    def test_a_recusa_nao_leva_o_texto_da_excecao(self, app_context, db_session):
        """
        🛡️ O motivo é uma CHAVE; o texto que a pessoa lê é escolhido pela rota.
        Devolver `str(e)` faz sair na resposta o que quer que tenha sido
        levantado dentro do `try` — e o CodeQL já marcou este padrão neste
        repositório.
        """
        from app.services import api_keys

        with pytest.raises(api_keys.DadosInvalidos) as erro:
            api_keys.criar("", ["convites"])

        # O que a exceção carrega é a chave, e mais nada.
        assert str(erro.value) == api_keys.DadosInvalidos.SEM_NOME


class TestARevogacao:
    def test_uma_chave_revogada_deixa_de_valer(self, app_context, db_session):
        from app.services import api_keys

        linha, chave = criar()
        api_keys.revogar(linha.id)

        assert api_keys.verificar(chave, 'convites') is None

    def test_a_linha_fica(self, app_context, db_session):
        """"Revogada em março" é diferente de "nunca existiu"."""
        from app.services import api_keys

        linha, _ = criar()
        api_keys.revogar(linha.id)

        listadas = api_keys.listar()
        assert len(listadas) == 1
        assert listadas[0]['revoked_at'] is not None

    def test_revogar_uma_nao_toca_nas_outras(self, app_context, db_session):
        """É esta a razão de elas existirem."""
        from app.services import api_keys

        do_bot, chave_do_bot = criar("Bot", ("convites",))
        _, chave_do_seerr = criar("Seerr", ("webhooks",))

        api_keys.revogar(do_bot.id)

        assert api_keys.verificar(chave_do_bot, 'convites') is None
        assert api_keys.verificar(chave_do_seerr, 'webhooks') is not None

    def test_revogar_duas_vezes_nao_rebenta(self, app_context, db_session):
        from app.services import api_keys

        linha, _ = criar()
        assert api_keys.revogar(linha.id) is not None
        assert api_keys.revogar(linha.id) is None


class TestOQueNaoEUmaChave:
    @pytest.mark.parametrize("entrada", ["", "   ", "nada", "pnl_", "pnl_abc",
                                         "outro_prefixo_x", None])
    def test_lixo_nao_passa(self, app_context, db_session, entrada):
        from app.services import api_keys

        assert api_keys.verificar(entrada, 'convites') is None

    def test_o_prefixo_certo_com_o_segredo_errado_nao_passa(self, app_context, db_session):
        from app.services import api_keys

        linha, _ = criar()
        assert api_keys.verificar(f"pnl_{linha.prefixo}_nao-e-o-segredo", 'convites') is None


class TestAsRotasProtegidas:
    def _com(self, chave):
        return {"X-API-Key": chave}

    def test_uma_chave_de_convites_cria_convites(self, client, configurada, db_session, monkeypatch):
        from app.blueprints.api import invites as rotas

        class Servidor:
            def get_libraries(self):
                return [{'title': 'Filmes', 'key': '1'}]

            def create_invitation(self, **kwargs):
                return {"success": True, "code": "OK", "message": "ok"}

        monkeypatch.setattr(rotas, 'media_server', Servidor())
        _, chave = criar(escopos=("convites",))

        resposta = client.post("/api/invites/bot/create", json={"telegram_id": 1},
                               headers=self._com(chave))
        assert resposta.status_code == 201

    def test_uma_chave_so_de_webhooks_nao_cria_convites(self, client, configurada, db_session):
        _, chave = criar(escopos=("webhooks",))

        resposta = client.post("/api/invites/bot/create", json={"telegram_id": 1},
                               headers=self._com(chave))
        assert resposta.status_code == 401

    def test_uma_chave_so_de_convites_nao_aceita_webhooks(self, client, configurada, db_session):
        _, chave = criar(escopos=("convites",))

        resposta = client.post("/api/system/webhook/overseerr",
                               json={"notification_type": "TEST"}, headers=self._com(chave))
        assert resposta.status_code == 401

    def test_uma_chave_de_webhooks_aceita_webhooks(self, client, configurada, db_session):
        _, chave = criar(escopos=("webhooks",))

        resposta = client.post("/api/system/webhook/overseerr",
                               json={"notification_type": "TEST"}, headers=self._com(chave))
        assert resposta.status_code == 200

    def test_uma_chave_revogada_e_recusada_pela_rota(self, client, configurada, db_session):
        from app.services import api_keys

        linha, chave = criar(escopos=("webhooks",))
        api_keys.revogar(linha.id)

        resposta = client.post("/api/system/webhook/overseerr",
                               json={"notification_type": "TEST"}, headers=self._com(chave))
        assert resposta.status_code == 401


class TestAChaveAntigaContinuaAValer:
    """
    ⚠️ Invalidá-la seria cortar, de uma vez e sem aviso, todas as integrações
    que já existem lá fora — onde este repositório não chega. Ela deixou de ser
    a única; não deixou de ser.
    """

    def _antiga(self):
        from app.config import load_or_create_config

        return {"X-API-Key": str(load_or_create_config().get('INTERNAL_TRIGGER_KEY') or '')}

    def test_serve_para_os_webhooks(self, client, configurada, db_session):
        resposta = client.post("/api/system/webhook/overseerr",
                               json={"notification_type": "TEST"}, headers=self._antiga())
        assert resposta.status_code == 200

    def test_serve_para_os_convites(self, client, configurada, data_manager):
        data_manager.add_invitation("QUALQUER", {
            "libraries": ["Filmes"], "screen_limit": 0, "allow_downloads": False,
            "created_at": "2026-01-01T00:00:00+00:00", "expires_at": None, "max_uses": 1,
        })
        resposta = client.get("/api/invites/bot/invite/QUALQUER", headers=self._antiga())
        assert resposta.status_code == 200


class TestAsRotasDeAdministracao:
    def test_criar_devolve_a_chave_uma_vez(self, admin, db_session):
        resposta = admin.post("/api/system/api-keys",
                              json={"nome": "Bot", "escopos": ["convites"]})

        assert resposta.status_code == 201
        assert resposta.get_json()["key"].startswith("pnl_")

    def test_a_listagem_seguinte_ja_nao_a_traz(self, admin, db_session):
        chave = admin.post("/api/system/api-keys",
                           json={"nome": "Bot", "escopos": ["convites"]}).get_json()["key"]

        listagem = admin.get("/api/system/api-keys").get_json()

        assert chave not in str(listagem)
        assert listagem["keys"][0]["nome"] == "Bot"

    def test_criar_sem_nome_e_um_erro_do_pedido(self, admin, db_session):
        resposta = admin.post("/api/system/api-keys", json={"escopos": ["convites"]})

        assert resposta.status_code == 400
        # A mensagem é a da ROTA, escolhida a partir do motivo.
        assert "nome" in resposta.get_json()["message"].lower()

    def test_criar_sem_permissao_nenhuma_diz_o_que_falta(self, admin, db_session):
        resposta = admin.post("/api/system/api-keys", json={"nome": "Bot", "escopos": []})

        assert resposta.status_code == 400
        assert "permiss" in resposta.get_json()["message"].lower()

    def test_revogar_pela_rota(self, admin, db_session):
        id_da_chave = admin.post("/api/system/api-keys",
                                 json={"nome": "Bot", "escopos": ["convites"]}).get_json()["id"]

        assert admin.delete(f"/api/system/api-keys/{id_da_chave}").status_code == 200
        assert admin.delete(f"/api/system/api-keys/{id_da_chave}").status_code == 404

    def test_so_administradores(self, client, configurada, db_session):
        assert client.get("/api/system/api-keys").status_code in (302, 401, 403)

    def test_criar_e_revogar_ficam_na_auditoria(self, admin, db_session):
        from sqlalchemy import text

        from app.extensions import db

        id_da_chave = admin.post("/api/system/api-keys",
                                 json={"nome": "Bot", "escopos": ["convites"]}).get_json()["id"]
        admin.delete(f"/api/system/api-keys/{id_da_chave}")

        with db.engine.begin() as ligacao:
            linhas = ligacao.execute(text(
                "SELECT acao, detalhes FROM audit_logs ORDER BY id"
            )).fetchall()

        acoes = [l[0] for l in linhas]
        assert acoes == ['chave_api.criar', 'chave_api.revogar']
        # 🛡️ A chave NÃO entra na auditoria — só o facto de ter sido criada.
        assert 'pnl_' not in str(linhas)


class TestOSeparadorNaoColideComOAlfabeto:
    """
    🐛 O prefixo saía do `token_urlsafe`, cujo alfabeto inclui o '_' — que é o
    separador da chave. Um prefixo como `-v_wYCdF` partia a chave em quatro
    pedaços e a leitura ficava com `-v` no lugar do prefixo: a chave era criada
    com sucesso e depois nunca mais era reconhecida. Aparecia em cerca de um
    terço das chaves, o que é pior do que aparecer sempre.
    """

    def test_nenhum_prefixo_traz_o_separador(self, app_context, db_session):
        from app.services import api_keys

        for i in range(40):
            linha, chave = criar(f"Chave {i}")
            assert '_' not in linha.prefixo
            # O que interessa mesmo: a chave recém-criada é reconhecida.
            assert api_keys.verificar(chave, 'convites') is not None

    def test_o_segredo_pode_ter_underscores_a_vontade(self, app_context, db_session):
        from app.services import api_keys

        linha, chave = criar()
        forjada = f"pnl_{linha.prefixo}_a_b_c_d"

        # Não é a chave certa, mas o PREFIXO tem de ser lido corretamente —
        # senão nem chega a comparar o resumo.
        assert api_keys._prefixo_de(forjada) == linha.prefixo
        assert api_keys.verificar(forjada, 'convites') is None


class TestORegistoDeUso:
    def test_a_primeira_utilizacao_fica_marcada(self, app_context, db_session):
        from app.services import api_keys

        linha, chave = criar()
        assert api_keys.listar()[0]['last_used_at'] is None

        api_keys.verificar(chave, 'convites')

        assert api_keys.listar()[0]['last_used_at'] is not None

    def test_nao_escreve_a_cada_pedido(self, app_context, db_session):
        """
        ⚡ Um webhook bate na rota dezenas de vezes por minuto, e o que
        interessa saber é "esta chave ainda está a ser usada?".
        """
        from app.services import api_keys

        _, chave = criar()
        api_keys.verificar(chave, 'convites')
        primeira = api_keys.listar()[0]['last_used_at']

        for _ in range(5):
            api_keys.verificar(chave, 'convites')

        assert api_keys.listar()[0]['last_used_at'] == primeira

    def test_registar_o_uso_nao_espera_por_um_lock(self, app_context, db_session):
        """
        🐛 A primeira versão escrevia por uma ligação PRÓPRIA, como o
        `audit.registar` faz. Parece a correção óbvia — não arrastar o que
        estiver pendente na sessão — e é pior neste sítio, porque a auditoria
        corre DEPOIS do commit do chamador e isto corre a meio: com o ficheiro
        trancado, a segunda ligação espera o `busy_timeout` inteiro. Este teste
        demorava trinta segundos.
        """
        import time

        from app.services import api_keys

        _, chave = criar()

        comeco = time.monotonic()
        assert api_keys.verificar(chave, 'convites') is not None
        assert time.monotonic() - comeco < 2, "ficou à espera de um lock do SQLite"


class TestOResumoNaoSeVerificaSoComABaseDeDados:
    """
    🛡️ O resumo é um HMAC com um segredo do painel. Quem leia só a base de
    dados — um `.db` copiado, uma injeção de SQL — fica com resumos que não
    consegue verificar: para testar um palpite precisa também do
    `API_KEYS_PEPPER`, que vive no config.json.
    """

    def test_o_resumo_nao_e_o_sha256_da_chave(self, app_context, db_session):
        import hashlib

        from app.models import ApiKey

        linha, chave = criar()

        simples = hashlib.sha256(chave.encode('utf-8')).hexdigest()
        assert ApiKey.query.get(linha.id).resumo != simples

    def test_o_segredo_nasce_com_a_primeira_chave(self, app_context, db_session, config_file):
        from app.config import load_or_create_config
        from app.services import api_keys

        config_file(IS_CONFIGURED=True, API_KEYS_PEPPER="")
        assert not load_or_create_config().get('API_KEYS_PEPPER')

        api_keys.criar("Bot", ["convites"])

        assert load_or_create_config().get('API_KEYS_PEPPER')

    def test_o_segredo_nunca_e_substituido(self, app_context, db_session):
        """Trocá-lo invalidaria todas as chaves de uma vez, e em silêncio."""
        from app.config import load_or_create_config
        from app.services import api_keys

        _, chave = criar("Primeira")
        segredo = load_or_create_config().get('API_KEYS_PEPPER')

        criar("Segunda")

        assert load_or_create_config().get('API_KEYS_PEPPER') == segredo
        assert api_keys.verificar(chave, 'convites') is not None

    def test_com_outro_segredo_a_chave_deixa_de_valer(self, app_context, db_session, config_file):
        import secrets as _secrets

        from app.config import load_or_create_config, save_app_config
        from app.services import api_keys

        _, chave = criar()
        assert api_keys.verificar(chave, 'convites') is not None

        config = load_or_create_config()
        config['API_KEYS_PEPPER'] = _secrets.token_hex(32)
        save_app_config(config)

        assert api_keys.verificar(chave, 'convites') is None

    def test_o_segredo_nao_desce_para_o_navegador(self, admin, db_session):
        """Ele mais a tabela dão exatamente o que o HMAC existe para impedir."""
        from app.config import load_or_create_config

        criar()  # o segredo passa a existir
        real = load_or_create_config()['API_KEYS_PEPPER']
        assert real, "o segredo devia ter nascido com a chave"

        resposta = admin.get("/api/system/settings").get_json()
        enviado = resposta.get('config', resposta)

        # A convenção do painel para uma credencial: diz que está posta e o
        # tamanho, nunca o valor.
        assert real not in str(resposta)
        assert enviado['API_KEYS_PEPPER'] == {'is_set': True, 'length': len(real)}
