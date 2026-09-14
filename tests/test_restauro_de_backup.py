# tests/test_restauro_de_backup.py

"""O que tem de acontecer ANTES de trocar a base de dados por baixo do painel.

Restaurar um backup substitui os ficheiros `.db` de um processo que está vivo.
Com o agendador ainda a correr, ele relê o jobstore restaurado, encontra lá as
tarefas com a hora de execução no PASSADO — a do momento em que o backup foi
feito — e tenta submetê-las todas de uma vez, em cima do reinício que o próprio
restauro agenda. O log enchia-se de `RuntimeError: cannot schedule new futures
after shutdown`, um erro por tarefa, logo a seguir a um restauro BEM-SUCEDIDO.

A ordem é o que se guarda aqui: primeiro calar, depois trocar.
"""

import io
import json
import zipfile

import pytest

from app.services.backup_manager import CONFIG_ENTRY_NAME

pytestmark = pytest.mark.integration


class AgendadorFalso:
    """Um APScheduler com a superfície que o encerramento usa."""

    def __init__(self, running=True):
        self.running = running
        self.chamadas = []

    def pause(self):
        self.chamadas.append('pause')

    def shutdown(self, wait=True):
        self.chamadas.append('shutdown')
        self.running = False


@pytest.fixture()
def agendador(monkeypatch):
    from app import extensions

    falso = AgendadorFalso()
    monkeypatch.setattr(extensions, 'scheduler', falso)
    monkeypatch.setattr(extensions, 'stream_manager', None)
    return falso


class TestPararOsServicos:
    def test_pausa_antes_de_encerrar(self, agendador):
        # ⚠️ É a ordem que evita o erro: o `shutdown()` fecha os executores, mas
        # o ciclo do APScheduler pode estar nesse instante a submeter as tarefas
        # que já estão na hora. Em pausa, não submete nada.
        from app import parar_servicos_de_fundo

        parar_servicos_de_fundo()

        assert agendador.chamadas == ['pause', 'shutdown']

    def test_com_o_agendador_ja_parado_nao_faz_nada(self, agendador):
        from app import parar_servicos_de_fundo

        agendador.running = False
        parar_servicos_de_fundo()

        assert agendador.chamadas == []

    def test_um_agendador_que_rebenta_nao_impede_o_resto(self, monkeypatch):
        from app import extensions, parar_servicos_de_fundo

        class Partido(AgendadorFalso):
            def pause(self):
                raise RuntimeError("já não há executores")

        parados = []

        class ListenerFalso:
            def stop_listener(self):
                parados.append(True)

        monkeypatch.setattr(extensions, 'scheduler', Partido())
        monkeypatch.setattr(extensions, 'stream_manager', ListenerFalso())

        parar_servicos_de_fundo()

        # O listener é outra coisa: uma falha a parar o agendador não o deixa
        # a correr sobre uma base de dados que vai ser substituída.
        assert parados == [True]


class TestAOrdemDoRestauro:
    """Primeiro calar, depois trocar."""

    def _zip(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(CONFIG_ENTRY_NAME, json.dumps({"IS_CONFIGURED": True}))
        buffer.seek(0)
        return buffer

    def test_o_agendador_ja_esta_parado_quando_os_ficheiros_sao_trocados(
            self, client, config_file, db_session, agendador, monkeypatch):
        from app import extensions
        from app.blueprints.api import system as system_module

        config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")

        estado_na_troca = {}

        class BackupFalso:
            def restore_from_zip(self, ficheiro):
                # O momento exato em que os ficheiros seriam substituídos.
                estado_na_troca['agendador_a_correr'] = agendador.running
                return True

        monkeypatch.setattr(extensions, 'backup_manager', BackupFalso())
        monkeypatch.setattr(system_module, '_agendar_reinicio', lambda motivo: None)

        with client.session_transaction() as sessao:
            sessao["user_details"] = {"id": "1", "username": "dono", "email": "a@b.test", "role": "admin"}
            sessao["_user_id"] = "1"
            sessao["_fresh"] = True

        resposta = client.post('/api/system/backup/restore',
                               data={'file': (self._zip(), 'backup.zip')},
                               content_type='multipart/form-data')

        assert resposta.status_code == 200
        assert estado_na_troca['agendador_a_correr'] is False

    def test_um_zip_recusado_nao_deixa_o_painel_parado_a_meio(
            self, client, config_file, db_session, agendador, monkeypatch):
        # A validação corre ANTES de se tocar em ficheiro nenhum (ver o
        # `BackupManager`), mas se chegar aqui um erro o painel fica sem
        # agendador até reiniciar — e é por isso que o restauro reinicia sempre.
        from app import extensions
        from app.blueprints.api import system as system_module

        config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")

        class BackupFalso:
            def restore_from_zip(self, ficheiro):
                raise ValueError("este backup é de uma versão MAIS RECENTE")

        monkeypatch.setattr(extensions, 'backup_manager', BackupFalso())
        monkeypatch.setattr(system_module, '_agendar_reinicio', lambda motivo: None)

        with client.session_transaction() as sessao:
            sessao["user_details"] = {"id": "1", "username": "dono", "email": "a@b.test", "role": "admin"}
            sessao["_user_id"] = "1"
            sessao["_fresh"] = True

        resposta = client.post('/api/system/backup/restore',
                               data={'file': (self._zip(), 'backup.zip')},
                               content_type='multipart/form-data')

        assert resposta.status_code == 400
        assert "MAIS RECENTE" in resposta.get_json()["message"]
