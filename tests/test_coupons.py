# tests/test_coupons.py
"""
Testes do sistema de cupões: criação, validação e contabilização de usos.

O foco está nas regras que antes deixavam passar cupões impossíveis (valores
negativos, tipos desconhecidos) e nas duas janelas em que o limite de
utilizações podia ser contornado — entre gerar a cobrança e pagá-la.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import FakeDataManager

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


def _criar(admin, **campos):
    corpo = {"code": "PROMO", "discount_type": "percentage", "value": 25, "max_uses": 1}
    corpo.update(campos)
    return admin.post("/api/coupons/create", json=corpo)


class TestCriacaoPelaAPI:
    def test_cria_cupao_valido(self, admin, db_session):
        resposta = _criar(admin)

        assert resposta.status_code == 200
        assert resposta.get_json()["coupon"]["code"] == "PROMO"

    def test_o_codigo_e_normalizado(self, admin, db_session):
        # A interface já enviava em maiúsculas, mas só no JavaScript.
        resposta = _criar(admin, code="  promo25  ")

        assert resposta.get_json()["coupon"]["code"] == "PROMO25"

    def test_codigo_duplicado_ignora_maiusculas(self, admin, db_session):
        _criar(admin, code="VERAO")

        assert _criar(admin, code="verao").status_code == 409

    @pytest.mark.parametrize("codigo_de_origem", ["DIASOFF", "diasoff"])
    def test_recria_cupao_apagado_preservando_os_usos(
        self, admin, data_manager, db_session, codigo_de_origem
    ):
        from app.models import Coupon, UserProfile

        cupao = Coupon(code=codigo_de_origem, discount_type="percentage", value=10,
                       max_uses=5, expires_at=datetime(2027, 1, 1))
        db_session.add_all([cupao, UserProfile(media_user_id=51, username="bruno")])
        db_session.commit()
        cupao_id = cupao.id
        assert data_manager.record_coupon_usage("DIASOFF", 51) is True
        assert admin.post(f"/api/coupons/delete/{cupao_id}").status_code == 200
        assert data_manager.get_coupon_by_code("DIASOFF") is None

        resposta = _criar(admin, code=" diasoff ", discount_type="fixed", value=20, max_uses=30)

        assert resposta.status_code == 200
        recriado = resposta.get_json()["coupon"]
        assert recriado["id"] == cupao_id
        assert recriado["code"] == "DIASOFF"
        assert recriado["discount_type"] == "fixed"
        assert recriado["value"] == 20
        assert recriado["max_uses"] == 30
        assert recriado["expires_at"] is None
        assert recriado["is_active"] is True
        assert recriado["use_count"] == 1
        assert data_manager.has_user_used_coupon(51, "DIASOFF") is True
        assert data_manager.record_coupon_usage("DIASOFF", 51) is False
        assert [cupao["id"] for cupao in data_manager.get_all_coupons()] == [cupao_id]
        assert _criar(admin, code="DIASOFF").status_code == 409

    def test_editar_restaurar_e_excluir_na_lixeira(self, admin, db_session):
        cupom = _criar(admin).get_json()["coupon"]
        base = "/api/coupons"
        ident = cupom["id"]
        campos = {"code": "PROMO", "discount_type": "fixed", "value": 7,
                  "expires_at": "2027-02-28", "max_uses": 30}
        assert admin.post(f"{base}/edit/{ident}", json=campos).status_code == 200
        atualizado, = admin.get(f"{base}/list").get_json()["coupons"]
        assert atualizado["value"] == 7
        assert atualizado["data_validade"] == "2027-02-28"
        assert admin.post(f"{base}/purge/{ident}").status_code == 409
        assert admin.post(f"{base}/delete/{ident}").status_code == 200
        assert admin.get(f"{base}/list").get_json()["coupons"] == []
        apagado, = admin.get(f"{base}/list?apagados=1").get_json()["coupons"]
        assert apagado["id"] == ident
        assert admin.post(f"{base}/edit/{ident}", json=campos).status_code == 409
        assert admin.post(f"{base}/restore/{ident}").status_code == 200
        restaurado, = admin.get(f"{base}/list").get_json()["coupons"]
        assert restaurado["is_active"] is True
        assert restaurado["max_uses"] == 30
        assert admin.post(f"{base}/delete/{ident}").status_code == 200
        assert admin.post(f"{base}/purge/{ident}").status_code == 200
        assert admin.get(f"{base}/list?apagados=1").get_json()["coupons"] == []

    def test_exclusao_permanente_preserva_pagamento_e_resgate(self, admin, db_session, data_manager):
        from app.models import Coupon, CouponUsage, PixPayment, UserProfile
        ident = _criar(admin).get_json()["coupon"]["id"]
        db_session.add(UserProfile(media_user_id=1, username="ana"))
        db_session.commit()
        assert data_manager.record_coupon_usage("PROMO", 1)
        db_session.add(PixPayment(txid="vinculado", media_user_id=1, username="ana", value=10,
                                  provider="EFI", status="ATIVA", created_at="2026-10-15", coupon_code="promo"))
        db_session.commit()
        assert admin.post(f"/api/coupons/delete/{ident}").status_code == 200
        assert admin.post(f"/api/coupons/purge/{ident}").status_code == 200
        assert db_session.get(Coupon, ident) is None
        assert db_session.get(PixPayment, "vinculado").coupon_code == "promo"
        uso = CouponUsage.query.one()
        assert uso.coupon_id is None
        assert uso.archived_coupon_code == "PROMO"
        assert _criar(admin).status_code == 200
        assert data_manager.has_user_used_coupon(1, "promo")
        assert not data_manager.record_coupon_usage("PROMO", 1)

    @pytest.mark.parametrize("campos", [
        {"value": -50},                       # aumentava o preço a pagar
        {"value": 0},                         # desconto que não desconta
        {"value": 150},                       # mais de 100%
        {"discount_type": "banana"},          # nunca chegava a descontar nada
        {"code": "COM ESPACO"},
        {"code": ""},
        {"max_uses": -1},
        {"expires_at": "31-12-2026"},
    ])
    def test_dados_invalidos_sao_recusados(self, admin, db_session, campos):
        assert _criar(admin, **campos).status_code == 400

    def test_percentagem_de_100_e_permitida(self, admin, db_session):
        assert _criar(admin, value=100).status_code == 200

    def test_valor_fixo_acima_de_100_e_permitido(self, admin, db_session):
        # O limite de 100 é só para percentagens: R$ 150 fixos são legítimos.
        assert _criar(admin, code="FIXO", discount_type="fixed", value=150).status_code == 200

    def test_expiracao_e_no_fim_do_dia_local(self, admin, db_session, monkeypatch):
        import pytz
        import app.blueprints.api.coupons as coupons_api

        # Painel em UTC-3: "válido até 30/09" tem de durar até às 23:59 locais,
        # ou seja, 02:59 UTC do dia seguinte. Antes guardava-se a meia-noite UTC
        # do próprio dia 30 e o cupão morria um dia antes do previsto.
        monkeypatch.setattr(coupons_api, "get_app_timezone", lambda: pytz.timezone("America/Sao_Paulo"))

        _criar(admin, code="ATE30", expires_at="2026-09-30")

        from app.models import Coupon
        guardado = Coupon.query.filter_by(code="ATE30").first()
        assert guardado.expires_at == datetime(2026, 10, 1, 2, 59, 59)

    def test_exige_administrador(self, client, configurada):
        assert _criar(client).status_code in (302, 401, 403)


class TestRegistoDeUsos:
    def test_registar_duas_vezes_nao_rebenta(self, data_manager, db_session):
        # ⚠️ Esta é a raiz do bug mais grave: a segunda inserção violava a
        # restrição de unicidade, a exceção subia até ao processamento do
        # pagamento e marcava como 'FALHOU' uma renovação já concretizada.
        from app.models import UserProfile

        db_session.add(UserProfile(media_user_id=7, username="ana"))
        db_session.commit()
        data_manager.create_coupon({"code": "UNICO", "discount_type": "percentage",
                                    "value": 10, "max_uses": 5})

        assert data_manager.record_coupon_usage("UNICO", 7) is True
        assert data_manager.record_coupon_usage("UNICO", 7) is False  # idempotente
        assert data_manager.get_coupon_by_code("UNICO")["use_count"] == 1

    def test_registo_aceita_o_codigo_em_minusculas(self, data_manager, db_session):
        from app.models import UserProfile

        db_session.add(UserProfile(media_user_id=8, username="bea"))
        db_session.commit()
        data_manager.create_coupon({"code": "CAIXA", "discount_type": "fixed",
                                    "value": 5, "max_uses": 5})

        assert data_manager.record_coupon_usage("caixa", 8) is True

    def test_cupao_inexistente_nao_rebenta(self, data_manager, db_session):
        assert data_manager.record_coupon_usage("NAO-EXISTE", 9) is False


class TestReservasEmCobrancasAbertas:
    def _cobranca(self, db_session, txid, codigo, media_user_id=1, status="ATIVA", idade_horas=0):
        from app.models import PixPayment, UserProfile

        if not UserProfile.query.get(media_user_id):
            db_session.add(UserProfile(media_user_id=media_user_id, username=f"u{media_user_id}"))
            db_session.flush()
        db_session.add(PixPayment(
            txid=txid, media_user_id=media_user_id, username=f"u{media_user_id}", value=10.0,
            status=status, provider="EFI", coupon_code=codigo,
            created_at=(datetime.now(timezone.utc) - timedelta(hours=idade_horas)).isoformat(),
        ))
        db_session.commit()

    def test_conta_cobrancas_por_pagar(self, data_manager, db_session):
        self._cobranca(db_session, "t1", "PROMO", media_user_id=1)
        self._cobranca(db_session, "t2", "PROMO", media_user_id=2)

        assert data_manager.get_reserved_coupon_uses("PROMO") == 2

    def test_ignora_cobrancas_ja_concluidas(self, data_manager, db_session):
        # Uma cobrança concluída já foi contada no 'use_count' — contá-la aqui
        # seria descontar o mesmo uso duas vezes.
        self._cobranca(db_session, "t-ok", "PROMO", status="CONCLUIDA")

        assert data_manager.get_reserved_coupon_uses("PROMO") == 0

    def test_ignora_cobrancas_ja_expiradas(self, data_manager, db_session):
        # Os provedores geram PIX com 20 minutos de validade: uma cobrança de há
        # uma hora já não é pagável e não pode continuar a segurar o cupão.
        self._cobranca(db_session, "t-velho", "PROMO", idade_horas=1)

        assert data_manager.get_reserved_coupon_uses("PROMO") == 0
        assert data_manager.has_user_pending_coupon_charge(1, "PROMO") is False

    def test_deteta_cobranca_aberta_do_proprio_utilizador(self, data_manager, db_session):
        self._cobranca(db_session, "t-ana", "PROMO", media_user_id=3)

        assert data_manager.has_user_pending_coupon_charge(3, "promo") is True
        assert data_manager.has_user_pending_coupon_charge(4, "PROMO") is False


def _cupao(**campos):
    base = {"code": "PROMO", "discount_type": "percentage", "value": 50,
            "max_uses": 1, "use_count": 0, "is_active": True, "expires_at": None}
    base.update(campos)
    return base


@pytest.fixture()
def precos(config_file):
    config_file(SCREEN_PRICES={"1": "30.00"}, RENEWAL_PRICE="30.00")


class TestLimiteDeUsosNaValidacao:
    def _gestor(self, **kwargs):
        from app.services.pricing_manager import PricingManager

        return PricingManager(FakeDataManager(coupons={"PROMO": _cupao(**kwargs.pop("cupao", {}))}, **kwargs))

    def test_reservas_contam_para_o_limite(self, precos, app_context):
        # Com um cupão de uso único e uma cobrança já aberta, o cupão está
        # esgotado — mesmo que o 'use_count' ainda esteja a zero.
        gestor = self._gestor(reserved_coupons={"PROMO": 1})

        resultado = gestor.calculate_price("1", coupon_code="PROMO", media_user_id=5)

        assert resultado["success"] is False

    def test_cobranca_aberta_do_proprio_utilizador_bloqueia(self, precos, app_context):
        gestor = self._gestor(cupao={"max_uses": 100}, pending_coupon_charges=[(5, "PROMO")])

        resultado = gestor.calculate_price("1", coupon_code="PROMO", media_user_id=5)

        assert resultado["success"] is False

    def test_max_uses_zero_significa_ilimitado(self, precos, app_context):
        # A lista de cupões sempre mostrou '∞' para 0; o backend é que lia
        # "limite zero" e nascia esgotado.
        gestor = self._gestor(cupao={"max_uses": 0, "use_count": 999})

        assert gestor.calculate_price("1", coupon_code="PROMO")["success"] is True

    def test_tipo_de_desconto_desconhecido_e_recusado(self, precos, app_context):
        gestor = self._gestor(cupao={"discount_type": "misterio"})

        assert gestor.calculate_price("1", coupon_code="PROMO")["success"] is False

    @pytest.mark.parametrize("valor", [-50, 0])
    def test_valor_nao_positivo_e_recusado(self, precos, app_context, valor):
        gestor = self._gestor(cupao={"value": valor})

        assert gestor.calculate_price("1", coupon_code="PROMO")["success"] is False

    def test_percentagem_invalida_nunca_aumenta_o_preco(self, precos, app_context):
        # Barreira final: um cupão gravado antes da validação da API não pode
        # transformar um desconto em acréscimo.
        gestor = self._gestor(cupao={"value": 150})

        resultado = gestor.calculate_price("1", coupon_code="PROMO")

        assert resultado["discounted_price"] <= resultado["original_price"]
