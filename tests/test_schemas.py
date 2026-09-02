# tests/test_schemas.py
"""Validação dos esquemas Pydantic usados nos endpoints da API."""

import pytest
from pydantic import ValidationError

from app.blueprints.api.schemas import (
    CreateInviteBotSchema,
    CreateInviteSchema,
    RenewSubscriptionSchema,
    UpdateAccountProfileSchema,
    UpdateProfileSchema,
)


class TestCreateInviteSchema:
    def test_valores_padrao(self):
        convite = CreateInviteSchema(libraries=["Filmes"])

        assert convite.screens == 0
        assert convite.allow_downloads is False
        assert convite.max_uses == 1
        assert convite.trial_duration_minutes == 0
        assert convite.telegram_id is None

    def test_pelo_menos_uma_biblioteca(self):
        with pytest.raises(ValidationError):
            CreateInviteSchema(libraries=[])

    @pytest.mark.parametrize("screens", [-1, 7])
    def test_numero_de_telas_fora_do_intervalo(self, screens):
        with pytest.raises(ValidationError):
            CreateInviteSchema(libraries=["Filmes"], screens=screens)

    def test_max_uses_tem_de_ser_positivo(self):
        with pytest.raises(ValidationError):
            CreateInviteSchema(libraries=["Filmes"], max_uses=0)

    def test_expiracao_negativa_e_recusada(self):
        with pytest.raises(ValidationError):
            CreateInviteSchema(libraries=["Filmes"], expires_in_minutes=-5)


class TestCreateInviteBotSchema:
    def test_telegram_id_numerico_e_convertido_para_texto(self):
        # A API do Telegram trata o chat_id como inteiro; o esquema tem de aceitar.
        convite = CreateInviteBotSchema(telegram_id=123456789)

        assert convite.telegram_id == "123456789"

    def test_telegram_id_com_espacos_e_normalizado(self):
        assert CreateInviteBotSchema(telegram_id="  123  ").telegram_id == "123"

    def test_telegram_id_obrigatorio(self):
        with pytest.raises(ValidationError):
            CreateInviteBotSchema()

    def test_telegram_id_em_branco_e_recusado(self):
        with pytest.raises(ValidationError):
            CreateInviteBotSchema(telegram_id="   ")

    def test_bibliotecas_sao_opcionais(self):
        # Um bot raramente conhece os nomes das bibliotecas: o servidor decide.
        assert CreateInviteBotSchema(telegram_id="1").libraries is None


class TestRenewSubscriptionSchema:
    def test_base_padrao_e_hoje(self):
        assert RenewSubscriptionSchema(months=1).base == "today"

    def test_meses_tem_de_ser_positivo(self):
        with pytest.raises(ValidationError):
            RenewSubscriptionSchema(months=0)

    def test_base_invalida(self):
        with pytest.raises(ValidationError):
            RenewSubscriptionSchema(months=1, base="amanha")

    def test_data_base_valida(self):
        assert RenewSubscriptionSchema(months=1, base_date="2026-01-31").base_date == "2026-01-31"

    @pytest.mark.parametrize("data", ["31/01/2026", "2026-13-01", "amanhã"])
    def test_data_base_invalida(self, data):
        with pytest.raises(ValidationError):
            RenewSubscriptionSchema(months=1, base_date=data)

    def test_hora_de_expiracao_valida(self):
        assert RenewSubscriptionSchema(months=1, expiration_time="23:59").expiration_time == "23:59"

    @pytest.mark.parametrize("hora", ["25:00", "23h59", "1159"])
    def test_hora_de_expiracao_invalida(self, hora):
        with pytest.raises(ValidationError):
            RenewSubscriptionSchema(months=1, expiration_time=hora)


class TestUpdateProfileSchema:
    def test_todos_os_campos_sao_opcionais(self):
        perfil = UpdateProfileSchema()

        assert perfil.name is None
        assert perfil.phone_number is None

    def test_data_hora_de_expiracao_valida(self):
        perfil = UpdateProfileSchema(expiration_datetime_local="2026-03-15T23:59")

        assert perfil.expiration_datetime_local == "2026-03-15T23:59"

    def test_data_hora_de_expiracao_invalida(self):
        with pytest.raises(ValidationError):
            UpdateProfileSchema(expiration_datetime_local="15/03/2026 23:59")


class TestUpdateAccountProfileSchema:
    def test_aceita_apenas_os_campos_do_proprio_utilizador(self):
        perfil = UpdateAccountProfileSchema(name="Ana", phone_number="5521999999999")

        assert perfil.name == "Ana"
        # A data de expiração NÃO faz parte deste esquema: só o admin a pode mudar.
        assert not hasattr(perfil, "expiration_datetime_local")
