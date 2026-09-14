"""A identidade do utilizador como texto, e não como inteiro do Plex.

O Plex identifica as contas por um inteiro, o Jellyfin por um GUID. Estes
testes garantem as duas coisas de que tudo o resto depende:

1. um GUID atravessa a persistência inteiro, incluindo as tabelas que apontam
   para o perfil;
2. o mesmo utilizador é encontrado venha o ID como inteiro ou como texto — no
   SQLite, uma consulta feita com 123 NÃO encontra a linha guardada como '123',
   e essa é a armadilha que o tipo `UserId` existe para fechar.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import PixPayment, UserId
from app.utils.identity import normalize_user_id, normalize_user_ids, same_user

GUID = "38c3a1f0e4b24d7f9c1a0b5e6d7f8a90"
OUTRO_GUID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


class TestNormalizacao:
    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            (123456, "123456"),
            ("123456", "123456"),
            ("  123456  ", "123456"),
            (GUID, GUID),
            (None, None),
            ("", None),
            ("   ", None),
        ],
    )
    def test_normalize_user_id(self, entrada, esperado):
        assert normalize_user_id(entrada) == esperado

    def test_normalize_user_ids_descarta_vazios_e_repetidos_mantendo_a_ordem(self):
        assert normalize_user_ids([2, "1", 1, None, "", "  ", 3]) == ["2", "1", "3"]

    def test_normalize_user_ids_aceita_none(self):
        assert normalize_user_ids(None) == []

    @pytest.mark.parametrize(
        "a,b,esperado",
        [
            (123, "123", True),
            ("123", 123, True),
            (GUID, GUID, True),
            (GUID, OUTRO_GUID, False),
            (None, None, False),  # sem identidade não há igualdade
            (None, 1, False),
        ],
    )
    def test_same_user(self, a, b, esperado):
        assert same_user(a, b) is esperado


class TestTipoDeColuna:
    def test_normaliza_o_que_e_gravado_e_o_que_e_comparado(self):
        tipo = UserId()

        assert tipo.process_bind_param(123, None) == "123"
        assert tipo.process_bind_param("  123  ", None) == "123"
        assert tipo.process_bind_param(None, None) is None

    def test_le_como_texto_mesmo_uma_linha_antiga_guardada_como_inteiro(self):
        # Defesa para linhas que tenham escapado ao CAST da migração.
        assert UserId().process_result_value(123, None) == "123"


@pytest.mark.integration
class TestPersistenciaComGuid:
    def test_perfil_com_guid_sobrevive_a_ida_e_volta(self, data_manager):
        data_manager.set_user_profile(GUID, {"username": "ana", "email": "ana@exemplo.com"})

        perfil = data_manager.get_user_profile(GUID)

        assert perfil["media_user_id"] == GUID
        assert perfil["username"] == "ana"

    def test_procura_por_username_devolve_o_guid_intacto(self, data_manager):
        data_manager.set_user_profile(GUID, {"username": "ana"})

        assert data_manager.get_user_profile_by_username("ana")["media_user_id"] == GUID

    def test_as_tabelas_que_apontam_para_o_perfil_aceitam_o_guid(self, data_manager):
        data_manager.set_user_profile(GUID, {"username": "ana"})

        data_manager.add_blocked_user(GUID, "ana", reason="expired")
        data_manager.create_notification(message="olá", media_user_id=GUID)
        data_manager.add_unlocked_achievements(GUID, "ana", [{"id": "primeiro"}])
        data_manager.log_stream_termination(GUID, "ana", "Filme", "Chrome", "limite")
        data_manager.create_pix_payment(
            txid="tx-guid", media_user_id=GUID, username="ana", value=30.0,
            provider="efi", screens=1, external_reference="ref-guid",
        )

        assert data_manager.get_blocked_user(GUID) is not None
        assert len(data_manager.get_notifications(media_user_id=GUID)) == 1
        assert data_manager.get_unlocked_achievements(GUID) == {"primeiro"}
        # A cobrança nasce 'ATIVA' e `get_payments_by_user` só lista as
        # concluídas — o que se verifica aqui é a chave estrangeira, por isso a
        # leitura é feita direta à tabela.
        assert PixPayment.query.filter_by(media_user_id=GUID).count() == 1

    def test_indicacoes_ligam_dois_guids(self, data_manager):
        data_manager.set_user_profile(GUID, {"username": "ana"})
        data_manager.set_user_profile(OUTRO_GUID, {"username": "bruno", "referred_by": GUID})

        indicados = data_manager.get_users_referred_by(GUID)

        assert [p["username"] for p in indicados] == ["bruno"]
        assert indicados[0]["referred_by"] == GUID

    def test_o_cupao_e_consumido_por_um_guid(self, data_manager):
        data_manager.set_user_profile(GUID, {"username": "ana"})
        data_manager.create_coupon({"code": "PROMO", "discount_type": "percentage", "value": 10.0})

        data_manager.record_coupon_usage("PROMO", GUID)

        assert data_manager.has_user_used_coupon(GUID, "PROMO") is True


@pytest.mark.integration
class TestInteiroETextoSaoOMesmoUtilizador:
    """A armadilha do SQLite: uma coluna de texto não casa com um inteiro.

    O ID do Plex chega do painel como inteiro (da API), da sessão como texto e
    de um URL como texto. Se estas três formas não encontrassem a mesma linha,
    o utilizador via o perfil às vezes sim e às vezes não.
    """

    def test_gravado_como_inteiro_e_lido_como_texto(self, data_manager):
        data_manager.set_user_profile(123456, {"username": "ana"})

        assert data_manager.get_user_profile("123456")["username"] == "ana"

    def test_gravado_como_texto_e_lido_como_inteiro(self, data_manager):
        data_manager.set_user_profile("123456", {"username": "ana"})

        assert data_manager.get_user_profile(123456)["username"] == "ana"

    def test_nao_cria_dois_perfis_para_o_mesmo_utilizador(self, data_manager):
        data_manager.set_user_profile(123456, {"username": "ana"})
        data_manager.set_user_profile("123456", {"screen_limit": 4})

        perfis = data_manager.get_all_user_profiles()

        assert len(perfis) == 1
        assert perfis[0]["screen_limit"] == 4
        assert perfis[0]["username"] == "ana"

    def test_a_procura_em_lote_aceita_as_duas_formas(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.set_user_profile(GUID, {"username": "bruno"})

        perfis = data_manager.get_user_profiles_by_id([1, GUID])

        assert {p["username"] for p in perfis.values()} == {"ana", "bruno"}

    def test_o_id_devolvido_e_sempre_texto(self, data_manager):
        data_manager.set_user_profile(123456, {"username": "ana", "expiration_date": (
            datetime.now(timezone.utc) + timedelta(days=5)
        ).isoformat()})

        assert list(data_manager.get_all_user_expirations()) == ["123456"]
        assert data_manager.get_user_profile(123456)["media_user_id"] == "123456"
