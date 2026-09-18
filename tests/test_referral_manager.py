# tests/test_referral_manager.py
"""Programa "Indique e Ganhe": códigos, registo da indicação e recompensas."""

import pytest

from app.services import referral_manager as referral_module
from app.services.referral_manager import CODE_ALPHABET, CODE_LENGTH, ReferralManager
from tests.conftest import FakeDataManager


class SubscriptionManagerEspiao:
    def __init__(self):
        self.chamadas = []

    def add_days_to_subscription(self, media_user_id, days):
        self.chamadas.append((media_user_id, days))
        return True


@pytest.fixture()
def configurar(monkeypatch):
    def _configurar(**valores):
        config = {
            "REFERRAL_ENABLED": True,
            "REFERRAL_REWARD_TYPE": "days",
            "REFERRAL_REWARD_DAYS": 7,
            "REFERRAL_REWARD_CREDIT": 5.0,
        }
        config.update(valores)
        monkeypatch.setattr(referral_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


@pytest.fixture()
def cenario(app_context, configurar):
    """Dois utilizadores: o 1 indicou o 2."""
    configurar()
    dm = FakeDataManager(
        profiles={
            1: {"media_user_id": 1, "username": "ana", "referral_code": "ABCD2345"},
            2: {"media_user_id": 2, "username": "bruno"},
        },
        # ⚠️ Quem indica já pagou: sem isto a recompensa fica RETIDA (ver o
        # `TestRecompensaRetida`) e todos os testes de entrega falhariam por um
        # motivo que não é o deles.
        paid_users=[1],
    )
    subs = SubscriptionManagerEspiao()
    return ReferralManager(data_manager=dm, subscription_manager=subs), dm, subs


class TestGeracaoDeCodigos:
    def test_codigo_criado_na_primeira_utilizacao(self, cenario):
        gestor, dm, _subs = cenario

        codigo = gestor.get_or_create_code(2)

        assert len(codigo) == CODE_LENGTH
        assert set(codigo) <= set(CODE_ALPHABET)
        assert dm.profiles["2"]["referral_code"] == codigo

    def test_codigo_existente_e_reutilizado(self, cenario):
        gestor, _dm, _subs = cenario

        assert gestor.get_or_create_code(1) == "ABCD2345"

    def test_perfil_inexistente(self, cenario):
        gestor, _dm, _subs = cenario

        assert gestor.get_or_create_code(999) is None

    def test_alfabeto_sem_caracteres_ambiguos(self):
        # Os códigos são ditados ao telefone: 0/O e 1/I/L causam confusão.
        assert not set("01OIL") & set(CODE_ALPHABET)


class TestRegisterReferral:
    def test_associa_o_indicado_a_quem_indicou(self, cenario):
        gestor, dm, _subs = cenario

        resultado = gestor.register_referral(2, "ABCD2345")

        assert resultado["success"] is True
        assert resultado["referrer_username"] == "ana"
        assert dm.profiles["2"]["referred_by"] == 1
        assert dm.profiles["2"]["referral_rewarded"] is False

    def test_codigo_e_insensivel_a_maiusculas_e_espacos(self, cenario):
        gestor, dm, _subs = cenario

        assert gestor.register_referral(2, "  abcd2345 ")["success"] is True
        assert dm.profiles["2"]["referred_by"] == 1

    def test_auto_indicacao_e_bloqueada(self, cenario):
        gestor, dm, _subs = cenario

        resultado = gestor.register_referral(1, "ABCD2345")

        assert resultado["success"] is False
        assert "referred_by" not in dm.profiles["1"]

    def test_codigo_invalido(self, cenario):
        gestor, _dm, _subs = cenario

        assert gestor.register_referral(2, "ZZZZZZZZ")["success"] is False

    def test_codigo_em_falta(self, cenario):
        gestor, _dm, _subs = cenario

        assert gestor.register_referral(2, "")["success"] is False

    def test_apenas_uma_indicacao_por_utilizador(self, cenario):
        gestor, dm, _subs = cenario
        dm.profiles["2"]["referred_by"] = 99

        resultado = gestor.register_referral(2, "ABCD2345")

        assert resultado["success"] is False
        assert dm.profiles["2"]["referred_by"] == 99

    def test_indicacao_circular_e_bloqueada(self, cenario):
        """Dois amigos a trocarem códigos entre si não podem premiar-se um ao outro."""
        gestor, dm, _subs = cenario
        dm.profiles["1"]["referred_by"] = 2
        dm.profiles["2"]["referral_code"] = "WXYZ6789"

        resultado = gestor.register_referral(2, "ABCD2345")

        assert resultado["success"] is False
        assert dm.profiles["2"].get("referred_by") is None

    def test_quem_ja_pagou_nao_pode_usar_um_codigo(self, app_context, configurar):
        """O programa premeia assinaturas NOVAS, não a renovação de um cliente antigo."""
        configurar()
        dm = FakeDataManager(
            profiles={
                1: {"media_user_id": 1, "username": "ana", "referral_code": "ABCD2345"},
                2: {"media_user_id": 2, "username": "bruno"},
            },
            paid_users=[2],
        )

        resultado = ReferralManager(dm).register_referral(2, "ABCD2345")

        assert resultado["success"] is False
        assert dm.profiles["2"].get("referred_by") is None

    def test_sistema_desativado(self, app_context, configurar):
        configurar(REFERRAL_ENABLED=False)
        gestor = ReferralManager(FakeDataManager(profiles={1: {"media_user_id": 1, "referral_code": "ABCD2345"}}))

        assert gestor.register_referral(2, "ABCD2345")["success"] is False


class TestRecompensa:
    def _com_indicacao(self, dm):
        dm.profiles["2"]["referred_by"] = 1
        dm.profiles["2"]["referral_rewarded"] = False

    def test_dias_gratis_sao_somados_a_quem_indicou(self, cenario):
        gestor, dm, subs = cenario
        self._com_indicacao(dm)

        resultado = gestor.reward_referrer_on_payment(2)

        assert resultado["rewarded"] is True
        assert subs.chamadas == [(1, 7)]
        assert dm.profiles["2"]["referral_rewarded"] is True

    def test_credito_e_somado_ao_saldo(self, app_context, configurar):
        configurar(REFERRAL_REWARD_TYPE="credit", REFERRAL_REWARD_CREDIT=5.0)
        dm = FakeDataManager(
            profiles={
                1: {"media_user_id": 1, "username": "ana", "referral_credit": 2.5},
                2: {"media_user_id": 2, "username": "bruno", "referred_by": 1, "referral_rewarded": False},
            },
            paid_users=[1],
        )

        resultado = ReferralManager(dm).reward_referrer_on_payment(2)

        assert resultado["rewarded"] is True
        assert dm.profiles["1"]["referral_credit"] == 7.5

    def test_a_recompensa_e_paga_apenas_uma_vez(self, cenario):
        gestor, dm, subs = cenario
        self._com_indicacao(dm)

        gestor.reward_referrer_on_payment(2)
        segunda = gestor.reward_referrer_on_payment(2)

        assert segunda["rewarded"] is False
        assert len(subs.chamadas) == 1

    def test_utilizador_sem_indicacao(self, cenario):
        gestor, _dm, subs = cenario

        resultado = gestor.reward_referrer_on_payment(2)

        assert resultado == {"success": True, "rewarded": False}
        assert subs.chamadas == []

    def test_quem_indicou_e_notificado(self, cenario):
        gestor, dm, _subs = cenario
        self._com_indicacao(dm)

        gestor.reward_referrer_on_payment(2)

        assert len(dm.notifications) == 1
        assert dm.notifications[0]["media_user_id"] == 1

    def test_sem_subscription_manager_nao_rebenta(self, app_context, configurar):
        configurar()
        dm = FakeDataManager(
            profiles={
                1: {"media_user_id": 1, "username": "ana"},
                2: {"media_user_id": 2, "username": "bruno", "referred_by": 1, "referral_rewarded": False},
            },
            paid_users=[1],
        )

        resultado = ReferralManager(dm, subscription_manager=None).reward_referrer_on_payment(2)

        assert resultado == {"success": False, "rewarded": False}

    def test_recompensa_de_zero_dias_nao_faz_nada(self, app_context, configurar):
        configurar(REFERRAL_REWARD_DAYS=0)
        dm = FakeDataManager(
            profiles={
                1: {"media_user_id": 1, "username": "ana"},
                2: {"media_user_id": 2, "username": "bruno", "referred_by": 1, "referral_rewarded": False},
            },
            paid_users=[1],
        )
        subs = SubscriptionManagerEspiao()

        assert ReferralManager(dm, subs).reward_referrer_on_payment(2)["rewarded"] is False
        assert subs.chamadas == []

    def test_limite_de_recompensas_por_utilizador(self, app_context, configurar):
        """Com o teto atingido, quem indica deixa de acumular novas recompensas."""
        configurar(REFERRAL_MAX_REWARDS_PER_USER=1)
        dm = FakeDataManager(
            profiles={
                1: {"media_user_id": 1, "username": "ana"},
                2: {"media_user_id": 2, "username": "bruno", "referred_by": 1, "referral_rewarded": True},
                3: {"media_user_id": 3, "username": "carla", "referred_by": 1, "referral_rewarded": False},
            },
            paid_users=[1],
        )
        subs = SubscriptionManagerEspiao()

        resultado = ReferralManager(dm, subs).reward_referrer_on_payment(3)

        assert resultado["rewarded"] is False
        assert subs.chamadas == []
        # A oportunidade fica intacta: se o limite subir, a recompensa ainda pode ser paga.
        assert dm.profiles["3"]["referral_rewarded"] is False

    def test_uma_entrega_falhada_nao_queima_a_recompensa(self, app_context, configurar):
        """
        Se a entrega rebentar a meio, o indicado não pode ficar marcado como
        'já recompensado' — senão quem o indicou nunca chegaria a receber nada.
        """
        configurar()

        class SubscriptionManagerQueRebenta:
            def add_days_to_subscription(self, media_user_id, days):
                raise RuntimeError("Plex indisponível")

        dm = FakeDataManager(
            profiles={
                1: {"media_user_id": 1, "username": "ana"},
                2: {"media_user_id": 2, "username": "bruno", "referred_by": 1, "referral_rewarded": False},
            },
            paid_users=[1],
        )

        resultado = ReferralManager(dm, SubscriptionManagerQueRebenta()).reward_referrer_on_payment(2)

        assert resultado == {"success": False, "rewarded": False}
        assert dm.profiles["2"]["referral_rewarded"] is False

    def test_falha_no_indicador_nunca_quebra_o_pagamento(self, app_context, configurar):
        configurar()

        class DataManagerQueRebenta(FakeDataManager):
            def get_user_profile(self, media_user_id):
                raise RuntimeError("base de dados indisponível")

        resultado = ReferralManager(DataManagerQueRebenta()).reward_referrer_on_payment(2)

        assert resultado == {"success": False, "rewarded": False}


class TestGetReferralStats:
    def test_resumo_do_programa(self, app_context, configurar):
        configurar()
        dm = FakeDataManager(profiles={
            1: {"media_user_id": 1, "username": "ana", "referral_code": "ABCD2345", "referral_credit": 10.0},
            2: {"media_user_id": 2, "username": "bruno", "referred_by": 1, "referral_rewarded": True},
            3: {"media_user_id": 3, "username": "carla", "referred_by": 1, "referral_rewarded": False},
        })

        stats = ReferralManager(dm).get_referral_stats(1)

        assert stats["enabled"] is True
        assert stats["code"] == "ABCD2345"
        assert stats["total_referred"] == 2
        assert stats["total_confirmed"] == 1
        assert stats["pending"] == 1
        assert stats["current_credit"] == 10.0
        assert {u["username"] for u in stats["referred_users"]} == {"bruno", "carla"}

    def test_utilizador_sem_indicacoes(self, cenario):
        gestor, _dm, _subs = cenario

        stats = gestor.get_referral_stats(2)

        assert stats["total_referred"] == 0
        assert stats["current_credit"] == 0.0


class TestConsumeCredit:
    def test_abate_o_valor_pedido(self, app_context, configurar):
        configurar()
        dm = FakeDataManager(profiles={1: {"media_user_id": 1, "referral_credit": 10.0}})

        usado = ReferralManager(dm).consume_credit(1, 4.0)

        assert usado == 4.0
        assert dm.profiles["1"]["referral_credit"] == 6.0

    def test_nunca_consome_mais_do_que_o_saldo(self, app_context, configurar):
        configurar()
        dm = FakeDataManager(profiles={1: {"media_user_id": 1, "referral_credit": 3.0}})

        usado = ReferralManager(dm).consume_credit(1, 10.0)

        assert usado == 3.0
        assert dm.profiles["1"]["referral_credit"] == 0.0

    def test_valor_negativo_e_ignorado(self, app_context, configurar):
        configurar()
        dm = FakeDataManager(profiles={1: {"media_user_id": 1, "referral_credit": 3.0}})

        assert ReferralManager(dm).consume_credit(1, -5.0) == 0.0
        assert dm.profiles["1"]["referral_credit"] == 3.0

    def test_perfil_inexistente(self, app_context, configurar):
        configurar()

        assert ReferralManager(FakeDataManager()).consume_credit(999, 5.0) == 0.0


class TestRecompensaRetida:
    """🎁 A recompensa só é ENTREGUE a quem já é assinante.

    A indicação conta e fica registada — o mérito de ter trazido alguém é de
    quem indicou. O que espera é a entrega, até ele próprio pagar. Sem isto,
    uma conta de TESTE (que num servidor de contas locais não custa nada de
    criar e nada liga à mesma pessoa) acumulava dias e crédito sem nunca pagar.
    """

    def _cenario(self, configurar, quem_pagou):
        configurar()
        dm = FakeDataManager(
            profiles={
                1: {"media_user_id": 1, "username": "ana", "referral_code": "ABCD2345"},
                2: {"media_user_id": 2, "username": "bruno", "referred_by": 1,
                    "referral_rewarded": False},
            },
            paid_users=quem_pagou,
        )
        subs = SubscriptionManagerEspiao()
        return ReferralManager(dm, subs), dm, subs

    def test_quem_ainda_nao_pagou_nao_recebe_agora(self, app_context, configurar):
        gestor, _dm, subs = self._cenario(configurar, quem_pagou=[2])

        resultado = gestor.reward_referrer_on_payment(2)

        assert resultado["rewarded"] is False
        assert resultado["retida"] is True
        assert subs.chamadas == []

    def test_a_oportunidade_fica_intacta(self, app_context, configurar):
        """O `referral_rewarded` a FALSO É o estado "por entregar".

        Marcá-lo ao reter queimava a única oportunidade que este indicado tem de
        gerar prémio, e a recompensa nunca mais sairia.
        """
        gestor, dm, _subs = self._cenario(configurar, quem_pagou=[2])

        gestor.reward_referrer_on_payment(2)

        assert dm.profiles["2"]["referral_rewarded"] is False

    def test_o_pagamento_de_quem_indicou_liberta_a_recompensa(self, app_context, configurar):
        gestor, dm, subs = self._cenario(configurar, quem_pagou=[2])
        gestor.reward_referrer_on_payment(2)

        # Ana paga: é agora que o que ela já tinha ganho lhe é entregue.
        dm.paid_users.add("1")
        resultado = gestor.liberar_recompensas_retidas(1)

        assert resultado["entregues"] == ["bruno"]
        assert subs.chamadas == [(1, 7)]
        assert dm.profiles["2"]["referral_rewarded"] is True

    def test_nada_e_libertado_antes_de_quem_indicou_pagar(self, app_context, configurar):
        gestor, _dm, subs = self._cenario(configurar, quem_pagou=[2])

        assert gestor.liberar_recompensas_retidas(1)["entregues"] == []
        assert subs.chamadas == []

    def test_um_indicado_que_nao_pagou_nao_e_uma_recompensa_retida(self, app_context, configurar):
        """"Por confirmar" e "por entregar" são coisas diferentes.

        Bruno nunca pagou: não há prémio nenhum à espera, há uma indicação que
        ainda não converteu — e essa já está contada nos `pending`.
        """
        gestor, _dm, subs = self._cenario(configurar, quem_pagou=[1])

        assert gestor.liberar_recompensas_retidas(1)["entregues"] == []
        assert subs.chamadas == []

    def test_libertar_duas_vezes_nao_paga_a_dobrar(self, app_context, configurar):
        gestor, dm, subs = self._cenario(configurar, quem_pagou=[2])
        gestor.reward_referrer_on_payment(2)
        dm.paid_users.add("1")

        gestor.liberar_recompensas_retidas(1)
        segunda = gestor.liberar_recompensas_retidas(1)

        assert segunda["entregues"] == []
        assert subs.chamadas == [(1, 7)]

    def test_o_teto_de_recompensas_continua_a_valer_ao_libertar(self, app_context, configurar):
        """A libertação passa pelo caminho normal — e por todas as regras dele."""
        configurar(REFERRAL_MAX_REWARDS_PER_USER=1)
        dm = FakeDataManager(
            profiles={
                1: {"media_user_id": 1, "username": "ana"},
                2: {"media_user_id": 2, "username": "bruno", "referred_by": 1,
                    "referral_rewarded": True},
                3: {"media_user_id": 3, "username": "carla", "referred_by": 1,
                    "referral_rewarded": False},
            },
            paid_users=[1, 3],
        )
        subs = SubscriptionManagerEspiao()

        resultado = ReferralManager(dm, subs).liberar_recompensas_retidas(1)

        assert resultado["entregues"] == []
        assert subs.chamadas == []
        assert dm.profiles["3"]["referral_rewarded"] is False

    def test_uma_falha_ao_libertar_nunca_derruba_o_pagamento(self, app_context, configurar):
        configurar()

        class DataManagerQueRebenta(FakeDataManager):
            def get_users_referred_by(self, media_user_id):
                raise RuntimeError("base de dados indisponível")

        dm = DataManagerQueRebenta(
            profiles={1: {"media_user_id": 1, "username": "ana"}},
            paid_users=[1],
        )

        assert ReferralManager(dm).liberar_recompensas_retidas(1) == {
            "success": False, "entregues": []
        }

    def test_o_resumo_diz_que_a_recompensa_esta_retida(self, app_context, configurar):
        """É daqui que sai o aviso da página da conta."""
        gestor, dm, _subs = self._cenario(configurar, quem_pagou=[2])
        gestor.reward_referrer_on_payment(2)

        stats = gestor.get_referral_stats(1)

        assert stats["pode_receber"] is False
        assert stats["retidas"] == 1

    def test_quem_ja_pagou_nao_ve_o_aviso(self, app_context, configurar):
        gestor, _dm, _subs = self._cenario(configurar, quem_pagou=[1, 2])

        stats = gestor.get_referral_stats(1)

        assert stats["pode_receber"] is True
        assert stats["retidas"] == 0
