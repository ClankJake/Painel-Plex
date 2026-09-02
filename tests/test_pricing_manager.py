# tests/test_pricing_manager.py
"""Cálculo de preços, cupões, crédito de indicações e upgrade pró-rata."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services import pricing_manager as pricing_module
from app.services.pricing_manager import PricingManager
from tests.conftest import FakeDataManager

PRECOS_BASE = {"1": "10.00", "2": "18.00", "3": "25.00", "4": "30.00"}


def em_dias(dias):
    """Data ISO (UTC) daqui a N dias — usado como vencimento nos testes."""
    return (datetime.now(timezone.utc) + timedelta(days=dias)).isoformat()


@pytest.fixture()
def configurar(monkeypatch):
    """Substitui a configuração lida pelo PricingManager por valores de teste."""
    def _configurar(**valores):
        config = {
            "SCREEN_PRICES": dict(PRECOS_BASE),
            "RENEWAL_PRICE": "10.00",
            "DAYS_TO_NOTIFY_EXPIRATION": 7,
            "PRORATION_ENABLED": False,
            "PRORATION_MIN_CHARGE": 2.0,
            "PRORATION_MIN_DAYS": 3,
            "PRORATION_FREE_BELOW_MINIMUM": True,
            "REFERRAL_ENABLED": False,
            "REFERRAL_REWARD_TYPE": "days",
        }
        config.update(valores)
        monkeypatch.setattr(pricing_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


@pytest.fixture()
def manager(app_context, configurar):
    """PricingManager com configuração padrão e um DataManager em memória."""
    configurar()
    return PricingManager(data_manager=FakeDataManager())


def cupao(**valores):
    base = {
        "code": "PROMO",
        "discount_type": "percentage",
        "value": 50,
        "is_active": True,
        "use_count": 0,
        "max_uses": 10,
        "expires_at": None,
    }
    base.update(valores)
    return base


class TestCalculatePrice:
    def test_preco_do_plano_selecionado(self, manager):
        resultado = manager.calculate_price("2")

        assert resultado["success"] is True
        assert resultado["original_price"] == 18.0
        assert resultado["discounted_price"] == 18.0
        assert resultado["coupon_applied"] is False

    def test_plano_desconhecido_usa_o_preco_de_renovacao(self, manager):
        assert manager.calculate_price("99")["original_price"] == 10.0

    def test_sem_precos_configurados_falha(self, app_context, configurar):
        configurar(SCREEN_PRICES={}, RENEWAL_PRICE="")
        gestor = PricingManager(data_manager=FakeDataManager())

        assert gestor.calculate_price("1")["success"] is False

    def test_preco_invalido_na_configuracao(self, app_context, configurar):
        configurar(SCREEN_PRICES={"1": "grátis"}, RENEWAL_PRICE="")
        gestor = PricingManager(data_manager=FakeDataManager())

        assert gestor.calculate_price("1")["success"] is False


class TestCupoes:
    def test_desconto_percentual(self, app_context, configurar):
        configurar()
        gestor = PricingManager(FakeDataManager(coupons={"PROMO": cupao(value=50)}))

        resultado = gestor.calculate_price("3", coupon_code="PROMO")

        assert resultado["discounted_price"] == 12.5
        assert resultado["coupon_applied"] is True

    def test_desconto_fixo(self, app_context, configurar):
        configurar()
        gestor = PricingManager(FakeDataManager(coupons={"PROMO": cupao(discount_type="fixed", value=5)}))

        assert gestor.calculate_price("3", coupon_code="PROMO")["discounted_price"] == 20.0

    def test_preco_nunca_fica_negativo(self, app_context, configurar):
        configurar()
        gestor = PricingManager(FakeDataManager(coupons={"PROMO": cupao(discount_type="fixed", value=999)}))

        assert gestor.calculate_price("1", coupon_code="PROMO")["discounted_price"] == 0.0

    def test_cupao_inexistente(self, manager):
        assert manager.calculate_price("1", coupon_code="NAO-EXISTE")["success"] is False

    def test_cupao_inativo(self, app_context, configurar):
        configurar()
        gestor = PricingManager(FakeDataManager(coupons={"PROMO": cupao(is_active=False)}))

        assert gestor.calculate_price("1", coupon_code="PROMO")["success"] is False

    def test_cupao_expirado(self, app_context, configurar):
        configurar()
        gestor = PricingManager(FakeDataManager(coupons={"PROMO": cupao(expires_at=em_dias(-1))}))

        assert gestor.calculate_price("1", coupon_code="PROMO")["success"] is False

    def test_cupao_ainda_dentro_da_validade(self, app_context, configurar):
        configurar()
        gestor = PricingManager(FakeDataManager(coupons={"PROMO": cupao(expires_at=em_dias(1))}))

        assert gestor.calculate_price("1", coupon_code="PROMO")["success"] is True

    def test_cupao_esgotado(self, app_context, configurar):
        configurar()
        gestor = PricingManager(FakeDataManager(coupons={"PROMO": cupao(use_count=10, max_uses=10)}))

        assert gestor.calculate_price("1", coupon_code="PROMO")["success"] is False

    def test_cupao_ja_usado_pelo_utilizador(self, app_context, configurar):
        configurar()
        gestor = PricingManager(FakeDataManager(
            coupons={"PROMO": cupao()},
            used_coupons=[(1, "PROMO")],
        ))

        assert gestor.calculate_price("1", coupon_code="PROMO", plex_user_id=1)["success"] is False


class TestCreditoDeIndicacoes:
    def _gestor(self, credito):
        return PricingManager(FakeDataManager(profiles={1: {"plex_user_id": 1, "referral_credit": credito}}))

    def test_nao_e_aplicado_sem_opt_in(self, app_context, configurar):
        configurar(REFERRAL_ENABLED=True, REFERRAL_REWARD_TYPE="credit")

        resultado = self._gestor(5.0).calculate_price("2", plex_user_id=1)

        assert resultado["discounted_price"] == 18.0
        assert resultado["referral_credit_applied"] == 0.0

    def test_abate_o_credito_disponivel(self, app_context, configurar):
        configurar(REFERRAL_ENABLED=True, REFERRAL_REWARD_TYPE="credit")

        resultado = self._gestor(5.0).calculate_price("2", plex_user_id=1, apply_referral_credit=True)

        assert resultado["referral_credit_available"] == 5.0
        assert resultado["referral_credit_applied"] == 5.0
        assert resultado["discounted_price"] == 13.0

    def test_nunca_gera_troco(self, app_context, configurar):
        configurar(REFERRAL_ENABLED=True, REFERRAL_REWARD_TYPE="credit")

        resultado = self._gestor(100.0).calculate_price("1", plex_user_id=1, apply_referral_credit=True)

        assert resultado["referral_credit_applied"] == 10.0
        assert resultado["discounted_price"] == 0.0

    def test_aplicado_depois_do_cupao(self, app_context, configurar):
        configurar(REFERRAL_ENABLED=True, REFERRAL_REWARD_TYPE="credit")
        gestor = PricingManager(FakeDataManager(
            profiles={1: {"plex_user_id": 1, "referral_credit": 5.0}},
            coupons={"PROMO": cupao(value=50)},
        ))

        resultado = gestor.calculate_price("3", coupon_code="PROMO", plex_user_id=1, apply_referral_credit=True)

        # 25.00 -50% = 12.50, menos 5.00 de crédito = 7.50
        assert resultado["discounted_price"] == 7.5

    def test_saldo_continua_a_ser_gasto_se_a_recompensa_passar_a_dias(self, app_context, configurar):
        """
        O crédito só se acumula no modo 'credit', mas o que já foi ganho não pode
        ficar preso quando o administrador muda a recompensa para dias grátis.
        """
        configurar(REFERRAL_ENABLED=True, REFERRAL_REWARD_TYPE="days")

        resultado = self._gestor(5.0).calculate_price("2", plex_user_id=1, apply_referral_credit=True)

        assert resultado["referral_credit_applied"] == 5.0
        assert resultado["discounted_price"] == 13.0

    def test_credito_preso_noutra_cobranca_nao_e_oferecido_de_novo(self, app_context, configurar):
        """
        Duas cobranças abertas ao mesmo tempo não podem descontar o mesmo saldo:
        o que já está reservado numa deixa de estar disponível para a seguinte.
        """
        configurar(REFERRAL_ENABLED=True, REFERRAL_REWARD_TYPE="credit")
        gestor = PricingManager(FakeDataManager(
            profiles={1: {"plex_user_id": 1, "referral_credit": 5.0}},
            reserved_credit={1: 4.0},
        ))

        resultado = gestor.calculate_price("2", plex_user_id=1, apply_referral_credit=True)

        assert resultado["referral_credit_available"] == 1.0
        assert resultado["referral_credit_applied"] == 1.0
        assert resultado["discounted_price"] == 17.0

    def test_ignorado_com_o_sistema_desativado(self, app_context, configurar):
        configurar(REFERRAL_ENABLED=False, REFERRAL_REWARD_TYPE="credit")

        resultado = self._gestor(5.0).calculate_price("2", plex_user_id=1, apply_referral_credit=True)

        assert resultado["discounted_price"] == 18.0

    def test_o_calculo_nunca_debita_o_saldo(self, app_context, configurar):
        configurar(REFERRAL_ENABLED=True, REFERRAL_REWARD_TYPE="credit")
        dm = FakeDataManager(profiles={1: {"plex_user_id": 1, "referral_credit": 5.0}})

        PricingManager(dm).calculate_price("2", plex_user_id=1, apply_referral_credit=True)

        # Um PIX gerado e nunca pago não pode consumir o crédito de ninguém.
        assert dm.profiles[1]["referral_credit"] == 5.0


class TestRequiresProrationForUpgrade:
    def test_falso_com_o_pro_rata_desativado(self, app_context, configurar):
        configurar(PRORATION_ENABLED=False)
        gestor = PricingManager(FakeDataManager())

        assert gestor.requires_proration_for_upgrade({"expiration_date": em_dias(20)}) is False

    def test_verdadeiro_longe_do_vencimento(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True)
        gestor = PricingManager(FakeDataManager())

        assert gestor.requires_proration_for_upgrade({"expiration_date": em_dias(20)}) is True

    def test_falso_dentro_da_janela_de_renovacao(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True, DAYS_TO_NOTIFY_EXPIRATION=7)
        gestor = PricingManager(FakeDataManager())

        assert gestor.requires_proration_for_upgrade({"expiration_date": em_dias(3)}) is False

    @pytest.mark.parametrize("perfil", [{}, {"expiration_date": ""}, {"expiration_date": "amanhã"}])
    def test_falso_sem_vencimento_valido(self, app_context, configurar, perfil):
        configurar(PRORATION_ENABLED=True)
        gestor = PricingManager(FakeDataManager())

        assert gestor.requires_proration_for_upgrade(perfil) is False


class TestCalculateUpgradeProration:
    def _gestor(self, screens=1, dias=30):
        return PricingManager(FakeDataManager(profiles={
            1: {"plex_user_id": 1, "screen_limit": screens, "expiration_date": em_dias(dias)}
        }))

    def test_desativado(self, app_context, configurar):
        configurar(PRORATION_ENABLED=False)

        resultado = self._gestor().calculate_upgrade_proration(1, 3)

        assert resultado["eligible"] is False
        assert resultado["reason"]

    def test_cobra_apenas_a_diferenca_pelos_dias_restantes(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True)

        resultado = self._gestor(screens=1, dias=30).calculate_upgrade_proration(1, 3)

        # (25.00 - 10.00) / 30 dias * 30 dias restantes = 15.00
        assert resultado["eligible"] is True
        assert resultado["amount"] == 15.0
        assert resultado["current_screens"] == 1
        assert resultado["new_screens"] == 3
        assert resultado["days_remaining"] == 30

    def test_metade_do_ciclo(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True)

        resultado = self._gestor(screens=1, dias=15).calculate_upgrade_proration(1, 3)

        assert resultado["amount"] == 7.5

    def test_downgrade_e_bloqueado(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True)

        resultado = self._gestor(screens=3, dias=20).calculate_upgrade_proration(1, 1)

        assert resultado["eligible"] is False

    def test_mesmo_plano_e_bloqueado(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True)

        assert self._gestor(screens=2, dias=20).calculate_upgrade_proration(1, 2)["eligible"] is False

    def test_perfil_inexistente(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True)
        gestor = PricingManager(FakeDataManager())

        assert gestor.calculate_upgrade_proration(999, 3)["eligible"] is False

    def test_subscricao_ja_expirada(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True)

        resultado = self._gestor(screens=1, dias=-2).calculate_upgrade_proration(1, 3)

        assert resultado["eligible"] is False
        assert resultado["days_remaining"] == 0

    def test_horas_restantes_contam_como_um_dia(self, app_context, configurar):
        # Sem o arredondamento para cima, quem tem 20 horas restantes seria
        # tratado como já expirado.
        configurar(PRORATION_ENABLED=True, PRORATION_MIN_DAYS=0, PRORATION_MIN_CHARGE=0)
        gestor = PricingManager(FakeDataManager(profiles={
            1: {
                "plex_user_id": 1,
                "screen_limit": 1,
                "expiration_date": (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat(),
            }
        }))

        assert gestor.calculate_upgrade_proration(1, 3)["days_remaining"] == 1

    def test_poucos_dias_para_o_vencimento(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True, PRORATION_MIN_DAYS=5)

        resultado = self._gestor(screens=1, dias=2).calculate_upgrade_proration(1, 3)

        assert resultado["eligible"] is False

    def test_valor_abaixo_do_minimo_pode_ser_gratuito(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True, PRORATION_MIN_CHARGE=10.0, PRORATION_FREE_BELOW_MINIMUM=True)

        resultado = self._gestor(screens=1, dias=10).calculate_upgrade_proration(1, 3)

        assert resultado["eligible"] is True
        assert resultado["is_free"] is True
        assert resultado["amount"] == 0.0

    def test_valor_abaixo_do_minimo_pode_ser_recusado(self, app_context, configurar):
        configurar(PRORATION_ENABLED=True, PRORATION_MIN_CHARGE=10.0, PRORATION_FREE_BELOW_MINIMUM=False)

        resultado = self._gestor(screens=1, dias=10).calculate_upgrade_proration(1, 3)

        assert resultado["eligible"] is False
        assert resultado["is_free"] is False


class TestGetAvailablePlans:
    def test_publico_devolve_o_plano_atual(self, manager):
        planos = manager.get_available_plans({"screen_limit": 2}, is_public_request=True)

        assert planos == {"2": "18.00"}

    def test_publico_usa_o_preco_de_renovacao_quando_nao_ha_plano(self, app_context, configurar):
        configurar(SCREEN_PRICES={"1": "10.00"}, RENEWAL_PRICE="9.90")
        gestor = PricingManager(FakeDataManager())

        assert gestor.get_available_plans({"screen_limit": 5}, is_public_request=True) == {"5": "9.90"}

    def test_publico_cai_para_o_plano_mais_barato(self, app_context, configurar):
        configurar(SCREEN_PRICES={"2": "18.00", "3": "25.00"}, RENEWAL_PRICE="")
        gestor = PricingManager(FakeDataManager())

        assert gestor.get_available_plans({"screen_limit": 0}, is_public_request=True) == {"2": "18.0"}

    def test_publico_sem_qualquer_preco_configurado(self, app_context, configurar):
        configurar(SCREEN_PRICES={}, RENEWAL_PRICE="")
        gestor = PricingManager(FakeDataManager())

        assert gestor.get_available_plans({"screen_limit": 0}, is_public_request=True) == {}

    def test_privado_esconde_downgrades_longe_do_vencimento(self, manager):
        planos = manager.get_available_plans({"screen_limit": 3, "expiration_date": em_dias(25)})

        assert "1" not in planos
        assert "2" not in planos
        assert "3" in planos
        assert "4" in planos

    def test_privado_permite_downgrade_perto_do_vencimento(self, app_context, configurar):
        configurar(DAYS_TO_NOTIFY_EXPIRATION=7)
        gestor = PricingManager(FakeDataManager())

        planos = gestor.get_available_plans({"screen_limit": 3, "expiration_date": em_dias(2)})

        assert "1" in planos
        assert "4" in planos

    def test_privado_mantem_planos_superiores_com_pro_rata_ativo(self, app_context, configurar):
        # Os planos superiores continuam listados para que o utilizador consiga
        # chegar ao pró-rata; o bloqueio acontece ao gerar a cobrança.
        configurar(PRORATION_ENABLED=True)
        gestor = PricingManager(FakeDataManager())

        planos = gestor.get_available_plans({"screen_limit": 2, "expiration_date": em_dias(25)})

        assert "3" in planos
        assert "4" in planos

    def test_privado_cai_para_o_preco_unico(self, app_context, configurar):
        configurar(SCREEN_PRICES={}, RENEWAL_PRICE="10.00")
        gestor = PricingManager(FakeDataManager())

        assert gestor.get_available_plans({"screen_limit": 0}) == {"0": "10.00"}
