# tests/test_data_manager.py
"""Persistência: perfis, cupões, convites, bloqueios, pagamentos e notificações."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import PixPayment, ShortLink

pytestmark = pytest.mark.integration


def iso(dias=0):
    return (datetime.now(timezone.utc) + timedelta(days=dias)).isoformat()


class TestPerfis:
    def test_cria_e_le_um_perfil(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana", "email": "ana@exemplo.com"})

        perfil = data_manager.get_user_profile(1)

        assert perfil["username"] == "ana"
        assert perfil["email"] == "ana@exemplo.com"
        # Cada perfil recebe um token de pagamento próprio na criação.
        assert perfil["payment_token"]

    def test_atualizar_preserva_o_token_de_pagamento(self, data_manager):
        token = data_manager.set_user_profile(1, {"username": "ana"})["payment_token"]

        data_manager.set_user_profile(1, {"screen_limit": 3})

        perfil = data_manager.get_user_profile(1)
        assert perfil["payment_token"] == token
        assert perfil["screen_limit"] == 3
        assert perfil["username"] == "ana"

    def test_campos_desconhecidos_sao_ignorados(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana", "campo_inexistente": "x"})

        assert "campo_inexistente" not in data_manager.get_user_profile(1)

    def test_perfil_inexistente(self, data_manager):
        assert data_manager.get_user_profile(999) is None

    def test_procura_por_username_ignora_maiusculas(self, data_manager):
        data_manager.set_user_profile(1, {"username": "Ana"})

        assert data_manager.get_user_profile_by_username("ana")["plex_user_id"] == 1

    def test_procura_por_email_ignora_maiusculas_e_espacos(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana", "email": "Ana@Exemplo.com"})

        assert data_manager.get_user_profile_by_email("  ana@exemplo.com ")["plex_user_id"] == 1

    def test_procura_por_email_vazio(self, data_manager):
        assert data_manager.get_user_profile_by_email(None) is None

    def test_procura_por_telegram(self, data_manager):
        # A coluna do perfil chama-se 'telegram_user' (não 'telegram_id').
        data_manager.set_user_profile(1, {"username": "ana", "telegram_user": "123456"})

        assert data_manager.get_user_profile_by_telegram(123456)["plex_user_id"] == 1
        assert data_manager.get_user_profile_by_telegram(" 123456 ")["plex_user_id"] == 1

    @pytest.mark.parametrize("valor", [None, "", "   "])
    def test_procura_por_telegram_vazio(self, data_manager, valor):
        assert data_manager.get_user_profile_by_telegram(valor) is None

    def test_procura_por_codigo_de_indicacao(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana", "referral_code": "ABCD2345"})

        assert data_manager.get_user_profile_by_referral_code("abcd2345")["plex_user_id"] == 1
        assert data_manager.get_user_profile_by_referral_code("ZZZZ") is None

    def test_lista_de_indicados(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.set_user_profile(2, {"username": "bruno", "referred_by": 1})
        data_manager.set_user_profile(3, {"username": "carla", "referred_by": 1})

        assert len(data_manager.get_users_referred_by(1)) == 2
        assert data_manager.get_users_referred_by(2) == []

    def test_apagar_perfil(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})

        assert data_manager.delete_user_profile(1) is True
        assert data_manager.get_user_profile(1) is None
        assert data_manager.delete_user_profile(1) is False

    def test_reset_de_xp_preserva_o_lifetime(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana", "xp": 500, "lifetime_xp": 500})
        data_manager.set_user_profile(2, {"username": "bruno", "xp": 100, "lifetime_xp": 100})

        assert data_manager.reset_all_users_xp() == 2

        perfil = data_manager.get_user_profile(1)
        assert perfil["xp"] == 0
        assert perfil["lifetime_xp"] == 500

    def test_listas_por_vencimento_e_teste(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana", "expiration_date": iso(10)})
        data_manager.set_user_profile(2, {"username": "bruno", "trial_end_date": iso(1)})

        assert list(data_manager.get_all_user_expirations()) == [1]
        assert list(data_manager.get_all_trial_users()) == [2]


class TestCupoes:
    def test_criar_e_procurar(self, data_manager):
        data_manager.create_coupon({"code": "PROMO", "discount_type": "percentage", "value": 10.0})

        cupao = data_manager.get_coupon_by_code("PROMO")
        assert cupao["value"] == 10.0
        assert cupao["is_active"] is True

    def test_alternar_ativacao(self, data_manager):
        cupao = data_manager.create_coupon({"code": "PROMO", "discount_type": "fixed", "value": 5.0})

        assert data_manager.toggle_coupon_active(cupao["id"])["is_active"] is False
        assert data_manager.toggle_coupon_active(cupao["id"])["is_active"] is True

    def test_apagar(self, data_manager):
        cupao = data_manager.create_coupon({"code": "PROMO", "discount_type": "fixed", "value": 5.0})

        assert data_manager.delete_coupon(cupao["id"]) is True
        assert data_manager.get_coupon_by_code("PROMO") is None

    def test_registar_utilizacao(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_coupon({"code": "PROMO", "discount_type": "fixed", "value": 5.0})

        assert data_manager.has_user_used_coupon(1, "PROMO") is False
        assert data_manager.record_coupon_usage("PROMO", 1) is True
        assert data_manager.has_user_used_coupon(1, "PROMO") is True
        assert data_manager.get_coupon_by_code("PROMO")["use_count"] == 1

    def test_utilizacao_de_cupao_inexistente(self, data_manager):
        assert data_manager.record_coupon_usage("NAO-EXISTE", 1) is False

    def test_utilizacao_por_outro_utilizador_nao_conta(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_coupon({"code": "PROMO", "discount_type": "fixed", "value": 5.0})
        data_manager.record_coupon_usage("PROMO", 1)

        assert data_manager.has_user_used_coupon(2, "PROMO") is False


class TestConvites:
    def _detalhes(self, **extra):
        detalhes = {
            "libraries": ["Filmes", "Séries"],
            "screen_limit": 2,
            "allow_downloads": True,
            "created_at": iso(),
            "expires_at": iso(1),
            "max_uses": 2,
        }
        detalhes.update(extra)
        return detalhes

    def test_bibliotecas_voltam_como_lista(self, data_manager):
        data_manager.add_invitation("ABC123", self._detalhes())

        convite = data_manager.get_invitation("ABC123")
        assert convite["libraries"] == ["Filmes", "Séries"]
        assert convite["screen_limit"] == 2

    def test_convite_inexistente(self, data_manager):
        assert data_manager.get_invitation("NAO-EXISTE") is None

    def test_incrementar_utilizacao(self, data_manager):
        data_manager.add_invitation("ABC123", self._detalhes())

        assert data_manager.increment_invitation_use("ABC123", "ana") is True

        convite = data_manager.get_invitation("ABC123")
        assert convite["use_count"] == 1
        assert "ana" in convite["claimed_by_users"]
        assert convite["claimed_at"]

    def test_o_mesmo_utilizador_nao_e_registado_duas_vezes(self, data_manager):
        data_manager.add_invitation("ABC123", self._detalhes())
        data_manager.increment_invitation_use("ABC123", "ana")
        data_manager.increment_invitation_use("ABC123", "ana")

        convite = data_manager.get_invitation("ABC123")
        assert convite["use_count"] == 2
        assert convite["claimed_by_users"] == ["ana"]

    def test_convites_esgotados_saem_dos_pendentes(self, data_manager):
        data_manager.add_invitation("ABC123", self._detalhes(max_uses=1))

        assert len(data_manager.get_all_pending_invitations()) == 1

        data_manager.increment_invitation_use("ABC123", "ana")

        assert data_manager.get_all_pending_invitations() == []
        assert len(data_manager.get_all_invitations()) == 1

    def test_reset_reativa_e_limpa_a_expiracao_passada(self, data_manager):
        data_manager.add_invitation("ABC123", self._detalhes(max_uses=1, expires_at=iso(-1)))
        data_manager.increment_invitation_use("ABC123", "ana")

        assert data_manager.reset_invitation_usage("ABC123") is True

        convite = data_manager.get_invitation("ABC123")
        assert convite["use_count"] == 0
        assert convite["expires_at"] is None

    def test_apagar_convite(self, data_manager):
        data_manager.add_invitation("ABC123", self._detalhes())

        assert data_manager.delete_invitation("ABC123") is True
        assert data_manager.delete_invitation("ABC123") is False

    def test_telegram_id_com_convite_ativo(self, data_manager):
        data_manager.add_invitation("ABC123", self._detalhes(telegram_id="555", max_uses=1))

        assert data_manager.check_telegram_id_exists_in_invites("555") is True
        assert data_manager.check_telegram_id_exists_in_invites("999") is False
        assert data_manager.check_telegram_id_exists_in_invites(None) is False

    def test_telegram_id_com_convite_expirado(self, data_manager):
        data_manager.add_invitation("ABC123", self._detalhes(telegram_id="555", expires_at=iso(-1)))

        assert data_manager.check_telegram_id_exists_in_invites("555") is False

    def test_telegram_id_com_convite_ja_usado(self, data_manager):
        data_manager.add_invitation("ABC123", self._detalhes(telegram_id="555", max_uses=1))
        data_manager.increment_invitation_use("ABC123", "ana")

        assert data_manager.check_telegram_id_exists_in_invites("555") is False


class TestBloqueados:
    def test_adicionar_e_remover(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})

        bloqueado = data_manager.add_blocked_user(1, "ana", reason="expired")

        assert bloqueado["block_reason"] == "expired"
        assert data_manager.get_blocked_user(1) is not None
        assert list(data_manager.get_blocked_users_dict()) == [1]

        assert data_manager.remove_blocked_user(1) is True
        assert data_manager.get_blocked_user(1) is None

    def test_bloquear_duas_vezes_atualiza_o_motivo(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.add_blocked_user(1, "ana", reason="manual")

        data_manager.add_blocked_user(1, "ana", reason="expired")

        assert len(data_manager.get_blocked_users_list()) == 1
        assert data_manager.get_blocked_user(1)["block_reason"] == "expired"

    def test_remover_quem_nao_esta_bloqueado(self, data_manager):
        assert data_manager.remove_blocked_user(999) is False


class TestPagamentos:
    def test_criar_e_atualizar_estado(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_pix_payment("tx1", 1, "ana", 25.0, "efi", 2, "ref-1")

        assert data_manager.get_pix_payment("tx1")["status"] == "ATIVA"

        assert data_manager.update_pix_payment_status("tx1", "CONCLUIDA") is True
        assert data_manager.get_pix_payment("tx1")["status"] == "CONCLUIDA"

    def test_estado_de_pagamento_inexistente(self, data_manager):
        assert data_manager.update_pix_payment_status("nao-existe", "CONCLUIDA") is False

    def test_reserva_de_credito_de_indicacoes(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_pix_payment("tx1", 1, "ana", 25.0, "efi", 2, "ref-1")

        assert data_manager.set_payment_referral_credit("tx1", 5.0) is True
        assert data_manager.get_pix_payment("tx1")["referral_credit_used"] == 5.0

    def test_marcar_como_pro_rata(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_pix_payment("tx1", 1, "ana", 4.0, "efi", 3, "ref-1")

        assert data_manager.get_pix_payment("tx1")["is_proration"] is False
        assert data_manager.mark_payment_as_proration("tx1") is True
        assert data_manager.get_pix_payment("tx1")["is_proration"] is True

    def test_pagamento_manual_fica_concluido(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})

        pagamento = data_manager.add_manual_payment(1, "ana", 30.0, "Pago em dinheiro", iso())

        assert pagamento["status"] == "CONCLUIDA"
        assert pagamento["provider"] == "Manual"
        assert len(data_manager.get_payments_by_user(1)) == 1

    def test_historico_ignora_cobrancas_pendentes(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_pix_payment("tx1", 1, "ana", 25.0, "efi", 2, "ref-1")

        assert data_manager.get_payments_by_user(1) == []

    def test_limpeza_apaga_apenas_pendentes_antigas(self, db_session, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        db_session.add_all([
            PixPayment(txid="antiga", user_plex_id=1, username="ana", value=10.0,
                       status="ATIVA", created_at=iso(-10)),
            PixPayment(txid="recente", user_plex_id=1, username="ana", value=10.0,
                       status="ATIVA", created_at=iso(-1)),
            PixPayment(txid="paga", user_plex_id=1, username="ana", value=10.0,
                       status="CONCLUIDA", created_at=iso(-10)),
        ])
        db_session.commit()

        assert data_manager.delete_old_pending_payments(3) == 1
        assert data_manager.get_pix_payment("antiga") is None
        assert data_manager.get_pix_payment("recente") is not None
        assert data_manager.get_pix_payment("paga") is not None

    @pytest.mark.parametrize("dias", [0, -1, "3", None])
    def test_limpeza_ignora_parametros_invalidos(self, data_manager, dias):
        assert data_manager.delete_old_pending_payments(dias) == 0

    def test_apagar_pagamento(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_pix_payment("tx1", 1, "ana", 25.0, "efi", 2, "ref-1")

        assert data_manager.delete_pix_payment("tx1") is True
        assert data_manager.delete_pix_payment("tx1") is False


class TestNotificacoes:
    def test_criar_e_contar_por_utilizador(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_notification("Bem-vindo", user_plex_id=1)
        data_manager.create_notification("Aviso global")

        assert data_manager.get_unread_notification_count(1) == 1
        # Sem user_plex_id ficam as notificações do administrador.
        assert data_manager.get_unread_notification_count() == 1
        assert data_manager.get_notifications(1)[0]["message"] == "Bem-vindo"

    def test_marcar_todas_como_lidas(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_notification("A", user_plex_id=1)
        data_manager.create_notification("B", user_plex_id=1)

        assert data_manager.mark_all_as_read(1) == 2
        assert data_manager.get_unread_notification_count(1) == 0
        assert data_manager.get_notifications(1) == []
        assert len(data_manager.get_notifications(1, include_read=True)) == 2

    def test_apagar_todas(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.create_notification("A", user_plex_id=1)

        assert data_manager.delete_all_notifications(1) == 1
        assert data_manager.get_notifications(1, include_read=True) == []

    def test_limite_de_resultados(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        for i in range(5):
            data_manager.create_notification(f"Aviso {i}", user_plex_id=1)

        assert len(data_manager.get_notifications(1, limit=3)) == 3


class TestAuditoriaDeCortes:
    def test_registar_e_listar(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.log_stream_termination(1, "ana", "Matrix", "Android", "screen_limit")

        registos = data_manager.get_stream_termination_logs()
        assert len(registos) == 1
        assert registos[0]["media_title"] == "Matrix"

    def test_apagar_um_registo(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.log_stream_termination(1, "ana", "Matrix", "Android", "screen_limit")
        registo_id = data_manager.get_stream_termination_logs()[0]["id"]

        assert data_manager.delete_stream_termination_log(registo_id) is True
        assert data_manager.delete_stream_termination_log(registo_id) is False

    def test_limpar_tudo(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        for titulo in ("Matrix", "Duna"):
            data_manager.log_stream_termination(1, "ana", titulo, "Android", "screen_limit")

        assert data_manager.clear_all_stream_termination_logs() == 2
        assert data_manager.get_stream_termination_logs() == []


class TestConquistas:
    def test_desbloquear_e_ler(self, data_manager):
        data_manager.set_user_profile(1, {"username": "ana"})
        data_manager.add_unlocked_achievements(1, "ana", [{"id": "maratonista"}, {"id": "coruja"}])

        assert data_manager.get_unlocked_achievements(1) == {"maratonista", "coruja"}

    def test_utilizador_sem_conquistas(self, data_manager):
        assert data_manager.get_unlocked_achievements(999) == set()


class TestLimpezaDeLinksCurtos:
    def test_apaga_apenas_os_antigos(self, db_session, data_manager):
        db_session.add_all([
            ShortLink(short_code="antigo", original_url="http://a",
                      created_at=datetime.now(timezone.utc) - timedelta(days=40)),
            ShortLink(short_code="novo", original_url="http://b",
                      created_at=datetime.now(timezone.utc)),
        ])
        db_session.commit()

        assert data_manager.delete_old_short_links(30) == 1
        assert ShortLink.query.count() == 1

    def test_ignora_parametros_invalidos(self, data_manager):
        assert data_manager.delete_old_short_links(0) == 0
