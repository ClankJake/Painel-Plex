# tests/test_fim_do_periodo_de_teste.py
"""O fim de um período de teste: a tarefa datada e a varredura que a apanha.

🐛 O fim do teste dependia por inteiro de UMA tarefa datada no APScheduler, e
uma tarefa datada que não corra na hora marcada **desaparece**. Duas coisas
falhavam ao mesmo tempo:

- `agendar_fim_do_teste` era a única `add_job` do painel sem
  `misfire_grace_time`, e o valor por omissão do APScheduler é UM SEGUNDO;
- não havia varredura nenhuma sobre `trial_end_date` — ao contrário do que o
  comentário do índice parcial, em `app/models.py`, dá por existente.

Juntas, davam acesso gratuito permanente sem uma linha de erro.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import FakeDataManager

UTC = timezone.utc


class TestToleranciaDaTarefaDatada:
    def test_a_tarefa_de_fim_de_teste_tem_tolerancia(self, app_context, monkeypatch):
        """Um painel a reiniciar não pode fazer o teste durar para sempre.

        Um reinício demora os 30 segundos do `--graceful-timeout` só a largar
        as ligações abertas. Com o segundo de tolerância que o APScheduler dá
        por omissão, a tarefa era descartada e a conta ficava aberta.
        """
        from app.services.media_server.invitations import InvitationLifecycle

        agendadas = []

        class AgendadorEspiao:
            timezone = UTC

            def add_job(self, **kwargs):
                agendadas.append(kwargs)

        monkeypatch.setattr('app.extensions.scheduler', AgendadorEspiao())

        fim, id_da_tarefa = InvitationLifecycle().agendar_fim_do_teste("42", 60)

        assert len(agendadas) == 1
        assert agendadas[0]['misfire_grace_time'] == 3600
        assert agendadas[0]['id'] == id_da_tarefa
        assert fim > datetime.now(UTC)

    def test_tem_a_mesma_tolerancia_do_fim_de_assinatura(self):
        """As duas são a mesma coisa vista de dois lados; divergirem foi o bug.

        Este teste lê o código-fonte de propósito: o que falhou não foi um
        valor errado, foi um argumento ESQUECIDO — e um argumento esquecido não
        aparece em lado nenhum senão na ausência dele.
        """
        import inspect

        from app.services.media_server import invitations

        fonte = inspect.getsource(invitations.InvitationLifecycle.agendar_fim_do_teste)
        assert 'misfire_grace_time' in fonte


@pytest.fixture()
def correr(app_context, monkeypatch):
    """Corre o `trial_sweep_job` sobre perfis em memória.

    Devolve `(bloqueados, dm)` — quem foi bloqueado, e o duplo, para o
    teste poder olhar para o que ficou gravado.
    """
    def _correr(profiles, blocked=None, no_servidor=None):
        from app import scheduler as scheduler_module
        from app.utils.identity import same_user

        dm = FakeDataManager(profiles=profiles, blocked=blocked)
        bloqueados = []

        # `no_servidor` é quem o servidor de média diz ter. `None` quer
        # dizer "toda a gente lá está", que é o caso normal; uma lista
        # VAZIA é o servidor a não responder, e as duas coisas têm de dar
        # resultados diferentes.
        class ServidorEspiao:
            def get_user_by_id(self, media_user_id):
                if no_servidor is not None and not any(
                    same_user(i, media_user_id) for i in no_servidor
                ):
                    return None
                return {'id': media_user_id, 'username': f'u{media_user_id}'}

            def get_all_users(self, force_refresh=False):
                presentes = dm.profiles if no_servidor is None else no_servidor
                return [{'id': i, 'username': f'u{i}'} for i in presentes]

            def block_user(self, media_user_id, reason=None):
                bloqueados.append((media_user_id, reason))
                dm.blocked[media_user_id] = {'block_reason': reason}
                return True

        class NotificadorEspiao:
            def send_trial_end_notification(self, user_info, profile):
                return True

        from app import extensions

        monkeypatch.setattr(extensions, 'data_manager', dm, raising=False)
        monkeypatch.setattr(extensions, 'media_server', ServidorEspiao(), raising=False)
        monkeypatch.setattr(extensions, 'notifier_manager', NotificadorEspiao(), raising=False)

        scheduler_module.trial_sweep_job()
        return bloqueados, dm

    return _correr


class TestVarreduraDosTestesVencidos:
    """A rede por baixo: fecha o que a tarefa datada não fechou."""

    def _perfil(self, minutos_atras, **extra):
        fim = datetime.now(UTC) - timedelta(minutes=minutos_atras)
        perfil = {"media_user_id": "1", "username": "ana", "trial_end_date": fim.isoformat()}
        perfil.update(extra)
        return {1: perfil}

    def test_fecha_um_teste_vencido_que_ficou_aberto(self, correr):
        bloqueados, _dm = correr(self._perfil(minutos_atras=30))

        assert bloqueados == [("1", 'trial_expired')]

    def test_nao_toca_num_teste_que_ainda_corre(self, correr):
        bloqueados, _dm = correr(self._perfil(minutos_atras=-30))

        assert bloqueados == []

    def test_quem_ja_tem_vencimento_nao_e_bloqueado(self, correr):
        """Passou a assinante: o `trial_end_date` que ficou é história.

        É o mesmo "dar e tirar" que o `add_days_to_subscription` fazia ao
        deixar a tarefa datada de pé — aqui visto do outro lado.
        """
        futuro = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        bloqueados, _dm = correr(self._perfil(minutos_atras=30, expiration_date=futuro))

        assert bloqueados == []

    def test_nao_bloqueia_nem_avisa_duas_vezes(self, correr):
        """Sem esta trava, era um bloqueio e um aviso de 15 em 15 minutos."""
        bloqueados, _dm = correr(
            self._perfil(minutos_atras=30),
            blocked={1: {'block_reason': 'trial_expired'}},
        )

        assert bloqueados == []

    def test_uma_data_sem_fuso_e_lida_como_utc(self, correr):
        """A mesma leitura que os dois backends já fazem nestas colunas.

        Com `astimezone` sobre uma data ingénua, o fuso é o do SISTEMA e o
        teste fechava horas antes ou depois do que devia.
        """
        fim = (datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None)
        bloqueados, _dm = correr({1: {
            "media_user_id": "1", "username": "ana", "trial_end_date": fim.isoformat(),
        }})

        assert bloqueados == [("1", 'trial_expired')]

    def test_uma_data_ilegivel_nao_derruba_a_varredura(self, correr):
        bloqueados, _dm = correr({
            1: {"media_user_id": "1", "username": "ana", "trial_end_date": "não é uma data"},
            2: {"media_user_id": "2", "username": "bruno",
                "trial_end_date": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()},
        })

        assert bloqueados == [("2", 'trial_expired')]

    def test_a_varredura_esta_registada_no_agendador(self):
        """Uma tarefa que ninguém regista é a mesma coisa que não existir — que
        é exatamente o que acontecia ao `get_all_trial_users`, escrito e nunca
        chamado."""
        import inspect

        from app import scheduler as scheduler_module

        fonte = inspect.getsource(scheduler_module.setup_scheduler)
        assert "id='trial_sweep_job'" in fonte


class TestContaQueJaNaoExisteNoServidor:
    """🐛 A varredura reencontrava para sempre quem já tinha sido removido.

    `end_trial_job` não encontrava a conta, escrevia um WARNING e não tocava em
    nada — e quem remove uma conta (`removal_job`, a remoção manual) APAGA a
    linha de `blocked_users`, por isso a trava do "já bloqueado" não apanhava
    estes perfis. Ficavam com o teste vencido, sem vencimento e sem bloqueio,
    que é exatamente o que a varredura procura: dois WARNING por perfil de 15
    em 15 minutos, para sempre, sobre contas removidas há meses.
    """

    def _perfil_vencido(self, **extra):
        fim = datetime.now(UTC) - timedelta(days=90)
        perfil = {
            "media_user_id": "1", "username": "ana", "status": "active",
            "trial_end_date": fim.isoformat(), "trial_job_id": "trial_1",
        }
        perfil.update(extra)
        return {1: perfil}

    def test_a_varredura_fecha_o_teste_e_nao_volta_a_encontra_lo(self, correr):
        perfis = self._perfil_vencido()

        bloqueados, dm = correr(perfis, no_servidor=["9"])

        assert bloqueados == []
        assert dm.profiles["1"]["status"] == "inactive"
        assert dm.profiles["1"]["trial_job_id"] is None
        # O `trial_end_date` fica: é história, e é por ele que se percebe,
        # meses depois, que aquela pessoa esteve em teste.
        assert dm.profiles["1"]["trial_end_date"]

        # A volta seguinte, quinze minutos depois, já não tem nada a dizer.
        bloqueados, dm = correr(perfis, no_servidor=["9"])

        assert bloqueados == []
        assert dm.profiles["1"]["status"] == "inactive"

    def test_um_perfil_ja_inativo_nao_entra_na_varredura(self, correr):
        """`inactive` é como o painel escreve "esta conta não tem acesso".

        Quem a removeu apagou também a linha de `blocked_users`, por isso a
        trava do "já bloqueado" não chega: sem esta, era um bloqueio e um aviso
        de fim de teste de quinze em quinze minutos sobre quem já saiu.
        """
        bloqueados, _dm = correr(self._perfil_vencido(status="inactive"))

        assert bloqueados == []

    def test_o_servidor_em_baixo_nao_fecha_o_teste_de_ninguem(self, correr):
        """⚠️ Não saber não é saber que não existe.

        Com a ligação em baixo a lista de utilizadores vem vazia e toda a gente
        parece ter desaparecido. Fechar o teste aqui tirava o acesso a quem
        ainda o estava a usar; o perfil fica como está e a varredura volta a
        passar daqui a quinze minutos.
        """
        perfis = self._perfil_vencido()

        bloqueados, dm = correr(perfis, no_servidor=[])

        assert bloqueados == []
        assert dm.profiles["1"]["status"] == "active"
        assert dm.profiles["1"]["trial_job_id"] == "trial_1"
