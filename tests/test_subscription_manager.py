# tests/test_subscription_manager.py
"""Renovações: data base, âncora do dia de faturação e cálculo do vencimento."""

from datetime import datetime, timedelta, timezone

import pytest
from tzlocal import get_localzone

from app.services.plex import subscription_manager as subscription_module
from app.services.plex.subscription_manager import PlexSubscriptionManager
from tests.conftest import FakeDataManager

UTC = timezone.utc


class SchedulerEspiao:
    """Substituto do APScheduler que apenas regista o que lhe foi pedido."""

    def __init__(self):
        self.jobs = {}
        self.removidos = []

    def add_job(self, id, func, args, trigger, run_date, misfire_grace_time=None):
        self.jobs[id] = {"args": args, "run_date": run_date, "trigger": trigger}

    def remove_job(self, job_id):
        if job_id not in self.jobs:
            from apscheduler.jobstores.base import JobLookupError

            raise JobLookupError(job_id)
        del self.jobs[job_id]
        self.removidos.append(job_id)


@pytest.fixture()
def configurar(monkeypatch):
    def _configurar(**valores):
        config = {
            "UNIVERSAL_EXPIRATION_ENABLED": False,
            "UNIVERSAL_EXPIRATION_TIME": "23:59",
            "ENABLE_LINK_SHORTENER": False,
        }
        config.update(valores)
        monkeypatch.setattr(subscription_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


@pytest.fixture()
def manager(app_context, configurar):
    """Gestor de subscrições com dados em memória e agendador falso."""
    configurar()
    dados = FakeDataManager(profiles={
        1: {"plex_user_id": 1, "username": "ana", "screen_limit": 1},
    })
    return PlexSubscriptionManager(data_manager=dados, scheduler=SchedulerEspiao())


class TestCalculateNewExpirationDate:
    def test_soma_um_mes_simples(self, manager):
        base = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, None, billing_day=10)

        assert (nova.year, nova.month, nova.day) == (2026, 4, 10)

    def test_fevereiro_trunca_o_dia_31(self, manager):
        base = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, None, billing_day=31)

        assert (nova.month, nova.day) == (2, 28)

    def test_ano_bissexto(self, manager):
        base = datetime(2028, 1, 31, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, None, billing_day=31)

        assert (nova.month, nova.day) == (2, 29)

    def test_a_ancora_recupera_o_dia_apos_fevereiro(self, manager):
        # É esta a correção da "erosão de data": depois de 31/01 -> 28/02, a
        # renovação seguinte tem de voltar ao dia 31, e não ficar preso no 28.
        base = datetime(2026, 2, 28, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, None, billing_day=31)

        assert (nova.month, nova.day) == (3, 31)

    def test_doze_renovacoes_mensais_nao_perdem_dias(self, manager):
        # Sem a âncora, um vencimento a dia 31 acabava o ano preso no dia 28.
        data = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)
        for _ in range(12):
            data = manager._calculate_new_expiration_date(data, 1, None, billing_day=31)

        assert (data.year, data.month, data.day) == (2027, 1, 31)

    def test_sem_ancora_usa_o_dia_da_data_base(self, manager):
        base = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, None, billing_day=None)

        assert nova.day == 15

    def test_varios_meses_de_uma_vez(self, manager):
        base = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 3, None, billing_day=15)

        assert (nova.year, nova.month, nova.day) == (2027, 2, 15)

    def test_a_ancora_nunca_encurta_o_periodo_pago(self, manager):
        # Assinatura ancorada no dia 1 que venceu e é renovada a 28: aplicar a
        # âncora daria 01/05 (3 dias), em vez de um mês inteiro.
        base = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, None, billing_day=1)

        assert (nova.month, nova.day) == (5, 28)

    def test_hora_de_expiracao_personalizada(self, manager):
        base = datetime(2026, 3, 10, 8, 30, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, "23:59", billing_day=10)

        assert (nova.hour, nova.minute, nova.second) == (23, 59, 0)

    def test_hora_universal_sobrepoe_se_a_individual(self, app_context, configurar):
        configurar(UNIVERSAL_EXPIRATION_ENABLED=True, UNIVERSAL_EXPIRATION_TIME="06:00")
        gestor = PlexSubscriptionManager(FakeDataManager(), scheduler=SchedulerEspiao())
        base = datetime(2026, 3, 10, 8, 30, tzinfo=UTC)

        nova = gestor._calculate_new_expiration_date(base, 1, "23:59", billing_day=10)

        assert (nova.hour, nova.minute) == (6, 0)

    @pytest.mark.parametrize("hora", ["25h", "", "abc"])
    def test_hora_invalida_e_ignorada(self, manager, hora):
        base = datetime(2026, 3, 10, 8, 30, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, hora, billing_day=10)

        assert nova.hour == 8

    @pytest.mark.parametrize("meses", ["dois", None])
    def test_meses_invalidos_contam_como_zero(self, manager, meses):
        base = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, meses, None, billing_day=10)

        assert (nova.month, nova.day) == (3, 10)

    @pytest.mark.parametrize("ancora", [0, None, "abc"])
    def test_ancora_ausente_ou_ilegivel_cai_para_o_dia_da_base(self, manager, ancora):
        base = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, None, billing_day=ancora)

        assert nova.day == 15

    def test_ancora_fora_do_intervalo_e_limitada_ao_fim_do_mes(self, manager):
        base = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

        nova = manager._calculate_new_expiration_date(base, 1, None, billing_day=99)

        # 99 é limitado a 31 e, em abril, ao último dia real do mês.
        assert (nova.month, nova.day) == (4, 30)


class TestResolveBillingDay:
    def test_data_base_explicita_define_a_nova_ancora(self, manager):
        base = datetime(2026, 5, 20, tzinfo=UTC)

        assert manager._resolve_billing_day({"billing_day": 31}, base, "2026-05-20") == 20

    def test_ancora_existente_e_preservada(self, manager):
        base = datetime(2026, 2, 28, tzinfo=UTC)

        assert manager._resolve_billing_day({"billing_day": 31}, base, None) == 31

    def test_primeira_renovacao_adota_o_dia_da_base(self, manager):
        base = datetime(2026, 2, 28, tzinfo=UTC)

        assert manager._resolve_billing_day({}, base, None) == 28

    @pytest.mark.parametrize("valor", [0, 32, "abc", None])
    def test_valores_invalidos_caem_para_o_dia_da_base(self, manager, valor):
        base = datetime(2026, 2, 10, tzinfo=UTC)

        assert manager._resolve_billing_day({"billing_day": valor}, base, None) == 10


class TestCalculateBaseDate:
    def test_modo_hoje(self, manager):
        agora = datetime.now(get_localzone())

        base = manager._calculate_base_date({"expiration_date": "2030-01-01T00:00:00"}, "today", None, agora)

        assert base == agora

    def test_modo_vencimento_acumula_meses(self, manager):
        agora = datetime.now(get_localzone())
        futuro = (agora + timedelta(days=20)).isoformat()

        base = manager._calculate_base_date({"expiration_date": futuro}, "expiry_date", None, agora)

        assert base.isoformat() == futuro

    def test_vencimento_ja_passado_conta_a_partir_de_hoje(self, manager):
        agora = datetime.now(get_localzone())
        passado = (agora - timedelta(days=5)).isoformat()

        base = manager._calculate_base_date({"expiration_date": passado}, "expiry_date", None, agora)

        assert base == agora

    def test_vencimento_invalido_conta_a_partir_de_hoje(self, manager):
        agora = datetime.now(get_localzone())

        base = manager._calculate_base_date({"expiration_date": "amanhã"}, "expiry_date", None, agora)

        assert base == agora

    def test_data_base_do_administrador_no_futuro(self, manager):
        agora = datetime.now(get_localzone())
        futuro = (agora + timedelta(days=30)).date().isoformat()

        base = manager._calculate_base_date({}, "today", futuro, agora)

        assert base.date().isoformat() == futuro

    def test_data_base_no_passado_e_ignorada(self, manager):
        agora = datetime.now(get_localzone())
        passado = (agora - timedelta(days=30)).date().isoformat()

        assert manager._calculate_base_date({}, "today", passado, agora) == agora

    def test_data_base_ilegivel_e_ignorada(self, manager):
        agora = datetime.now(get_localzone())

        assert manager._calculate_base_date({}, "today", "30/05/2026", agora) == agora


class TestAddDaysToSubscription:
    def test_soma_ao_vencimento_futuro(self, manager):
        agora = datetime.now(get_localzone())
        vencimento = agora + timedelta(days=10)
        manager.data_manager.profiles[1]["expiration_date"] = vencimento.isoformat()

        nova = manager.add_days_to_subscription(1, 7)

        assert (nova - vencimento).days == 7

    def test_vencimento_expirado_conta_a_partir_de_hoje(self, manager):
        agora = datetime.now(get_localzone())
        manager.data_manager.profiles[1]["expiration_date"] = (agora - timedelta(days=30)).isoformat()

        nova = manager.add_days_to_subscription(1, 7)

        # Quem está vencido tem de receber os 7 dias completos, não somados ao passado.
        assert (nova - agora).days >= 6

    def test_sem_vencimento_conta_a_partir_de_hoje(self, manager):
        nova = manager.add_days_to_subscription(1, 5)

        assert (nova - datetime.now(get_localzone())).days >= 4

    def test_vencimento_ilegivel_conta_a_partir_de_hoje(self, manager):
        manager.data_manager.profiles[1]["expiration_date"] = "não é uma data"

        assert manager.add_days_to_subscription(1, 5) is not None

    @pytest.mark.parametrize("dias", [0, -5, None])
    def test_dias_invalidos_nao_fazem_nada(self, manager, dias):
        assert manager.add_days_to_subscription(1, dias) is None
        assert "expiration_date" not in manager.data_manager.profiles[1]

    def test_perfil_inexistente(self, manager):
        with pytest.raises(ValueError):
            manager.add_days_to_subscription(999, 7)

    def test_agenda_a_nova_expiracao(self, manager):
        nova = manager.add_days_to_subscription(1, 7)

        assert len(manager.scheduler.jobs) == 1
        job = next(iter(manager.scheduler.jobs.values()))
        assert job["run_date"] == nova
        assert job["args"] == [1]


class TestRenewSubscription:
    def test_renovacao_mensal_completa(self, manager):
        nova = manager.renew_subscription(1, months_to_add=1)

        perfil = manager.data_manager.profiles[1]
        assert perfil["expiration_date"] == nova.isoformat()
        assert perfil["billing_day"] == nova.day
        assert perfil["expiration_job_id"] in manager.scheduler.jobs

    def test_atualiza_o_limite_de_telas(self, manager):
        manager.renew_subscription(1, months_to_add=1, screens=3)

        assert manager.data_manager.profiles[1]["screen_limit"] == 3

    def test_telas_nao_indicadas_ficam_como_estavam(self, manager):
        manager.renew_subscription(1, months_to_add=1)

        assert manager.data_manager.profiles[1]["screen_limit"] == 1

    def test_perfil_inativo_e_reativado(self, manager):
        manager.data_manager.profiles[1]["status"] = "inactive"

        manager.renew_subscription(1, months_to_add=1)

        perfil = manager.data_manager.profiles[1]
        assert perfil["status"] == "active"
        assert perfil["last_reactivation_time"] > 0

    def test_limpa_os_dados_do_periodo_de_teste(self, manager):
        manager.scheduler.jobs["trial_1"] = {}
        manager.data_manager.profiles[1].update({
            "trial_end_date": "2026-01-01T00:00:00", "trial_job_id": "trial_1",
        })

        manager.renew_subscription(1, months_to_add=1)

        perfil = manager.data_manager.profiles[1]
        assert perfil["trial_end_date"] is None
        assert perfil["trial_job_id"] is None
        assert "trial_1" in manager.scheduler.removidos

    def test_a_tarefa_de_expiracao_antiga_e_substituida(self, manager):
        manager.scheduler.jobs["sub_end_antigo"] = {}
        manager.data_manager.profiles[1]["expiration_job_id"] = "sub_end_antigo"

        manager.renew_subscription(1, months_to_add=1)

        assert "sub_end_antigo" in manager.scheduler.removidos
        assert len(manager.scheduler.jobs) == 1

    def test_renovacoes_sucessivas_mantem_a_ancora(self, manager):
        manager.data_manager.profiles[1]["billing_day"] = 31
        manager.data_manager.profiles[1]["expiration_date"] = datetime(
            2026, 1, 31, 12, 0, tzinfo=get_localzone()
        ).isoformat()

        # Renova sempre a partir do vencimento anterior, como acontece em produção.
        for _ in range(2):
            manager.renew_subscription(1, months_to_add=1, base_mode="expiry_date")

        assert manager.data_manager.profiles[1]["billing_day"] == 31

    def test_perfil_inexistente(self, manager):
        with pytest.raises(ValueError):
            manager.renew_subscription(999, months_to_add=1)

    def test_sem_agendador_a_renovacao_nao_rebenta(self, app_context, configurar):
        configurar()
        gestor = PlexSubscriptionManager(
            FakeDataManager(profiles={1: {"plex_user_id": 1, "username": "ana"}}), scheduler=None
        )

        assert gestor.renew_subscription(1, months_to_add=1) is not None


class TestUnblockUserIfNeeded:
    class PlexManagerEspiao:
        def __init__(self):
            self.desbloqueados = []

        def unblock_user(self, plex_user_id):
            self.desbloqueados.append(plex_user_id)

    @pytest.mark.parametrize("motivo", ["expired", "trial_expired"])
    def test_desbloqueia_quem_foi_bloqueado_por_vencimento(self, manager, motivo):
        manager.data_manager.blocked[1] = {"user_plex_id": 1, "block_reason": motivo}
        manager.plex_manager = self.PlexManagerEspiao()

        manager._unblock_user_if_needed(1)

        assert manager.plex_manager.desbloqueados == [1]

    def test_bloqueio_manual_do_administrador_e_mantido(self, manager):
        # Pagar não deve anular um bloqueio aplicado à mão pelo administrador.
        manager.data_manager.blocked[1] = {"user_plex_id": 1, "block_reason": "manual"}
        manager.plex_manager = self.PlexManagerEspiao()

        manager._unblock_user_if_needed(1)

        assert manager.plex_manager.desbloqueados == []

    def test_utilizador_nao_bloqueado(self, manager):
        manager.plex_manager = self.PlexManagerEspiao()

        manager._unblock_user_if_needed(1)

        assert manager.plex_manager.desbloqueados == []
