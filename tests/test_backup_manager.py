# tests/test_backup_manager.py
"""Backups: criação do ZIP, rotação, proteção de caminhos e restauro."""

import io
import json
import sqlite3
import zipfile

import pytest

from app.services.backup_manager import (
    APP_DB_ENTRY_NAME,
    CONFIG_ENTRY_NAME,
    MANIFEST_ENTRY_NAME,
    SCHEDULER_DB_ENTRY_NAME,
    BackupManager,
)


def criar_sqlite(caminho, valor="original"):
    ligacao = sqlite3.connect(caminho)
    ligacao.execute("CREATE TABLE IF NOT EXISTS teste (valor TEXT)")
    ligacao.execute("INSERT INTO teste VALUES (?)", (valor,))
    ligacao.commit()
    ligacao.close()


def ler_sqlite(caminho):
    ligacao = sqlite3.connect(caminho)
    try:
        return [linha[0] for linha in ligacao.execute("SELECT valor FROM teste")]
    finally:
        ligacao.close()


@pytest.fixture()
def instalacao(tmp_path):
    """Simula uma pasta 'config' completa de uma instalação real."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"IS_CONFIGURED": True, "APP_TITLE": "Painel Plex"}), encoding="utf-8"
    )
    criar_sqlite(config_dir / "app_data.db")
    criar_sqlite(config_dir / "scheduler_jobs.db")
    return config_dir


@pytest.fixture()
def manager(instalacao):
    return BackupManager(config_dir=str(instalacao))


class TestCreateBackupBytes:
    def test_o_zip_contem_tudo(self, manager):
        with zipfile.ZipFile(io.BytesIO(manager.create_backup_bytes())) as zf:
            nomes = zf.namelist()

        assert CONFIG_ENTRY_NAME in nomes
        assert APP_DB_ENTRY_NAME in nomes
        assert SCHEDULER_DB_ENTRY_NAME in nomes
        assert MANIFEST_ENTRY_NAME in nomes

    def test_o_config_guardado_e_o_verdadeiro(self, manager):
        with zipfile.ZipFile(io.BytesIO(manager.create_backup_bytes())) as zf:
            guardado = json.loads(zf.read(CONFIG_ENTRY_NAME))

        assert guardado["APP_TITLE"] == "Painel Plex"

    def test_a_base_de_dados_copiada_e_legivel(self, manager, tmp_path):
        # A cópia é feita pela API de backup do SQLite, e não por cópia de
        # ficheiro, para não gravar uma base de dados a meio de uma escrita.
        with zipfile.ZipFile(io.BytesIO(manager.create_backup_bytes())) as zf:
            copia = tmp_path / "copia.db"
            copia.write_bytes(zf.read(APP_DB_ENTRY_NAME))

        assert ler_sqlite(copia) == ["original"]

    def test_bases_de_dados_em_falta_sao_ignoradas(self, instalacao):
        (instalacao / "scheduler_jobs.db").unlink()
        gestor = BackupManager(config_dir=str(instalacao))

        with zipfile.ZipFile(io.BytesIO(gestor.create_backup_bytes())) as zf:
            nomes = zf.namelist()

        assert SCHEDULER_DB_ENTRY_NAME not in nomes
        assert CONFIG_ENTRY_NAME in nomes

    def test_o_manifesto_lista_o_conteudo(self, manager):
        with zipfile.ZipFile(io.BytesIO(manager.create_backup_bytes())) as zf:
            manifesto = zf.read(MANIFEST_ENTRY_NAME).decode("utf-8")

        assert "Painel-Plex" in manifesto
        assert APP_DB_ENTRY_NAME in manifesto


class TestBackupsAgendados:
    def test_grava_o_ficheiro_na_pasta_de_backups(self, manager):
        nome = manager.create_scheduled_backup()

        assert nome.startswith("painel-plex-backup-")
        assert nome.endswith(".zip")
        assert manager.get_backup_path(nome)

    def test_a_rotacao_mantem_apenas_os_mais_recentes(self, manager):
        for i in range(5):
            caminho = manager.backups_dir + f"/painel-plex-backup-2026010{i}-120000.zip"
            with open(caminho, "wb") as f:
                f.write(b"antigo")

        manager.create_scheduled_backup(max_backups=3)

        assert len(manager.list_backups()) == 3

    def test_a_rotacao_apaga_sempre_os_mais_antigos(self, manager, tmp_path):
        import os
        import time

        antigo = os.path.join(manager.backups_dir, "painel-plex-backup-20200101-120000.zip")
        with open(antigo, "wb") as f:
            f.write(b"antigo")
        os.utime(antigo, (time.time() - 86400, time.time() - 86400))

        manager.create_scheduled_backup(max_backups=1)

        assert not os.path.exists(antigo)

    def test_ficheiros_de_terceiros_nao_sao_tocados(self, manager):
        import os

        estranho = os.path.join(manager.backups_dir, "as-minhas-fotos.zip")
        with open(estranho, "wb") as f:
            f.write(b"dados importantes")

        manager.create_scheduled_backup(max_backups=1)

        assert os.path.exists(estranho)
        assert all(b["filename"] != "as-minhas-fotos.zip" for b in manager.list_backups())

    def test_falha_na_criacao_devolve_none(self, manager, monkeypatch):
        monkeypatch.setattr(
            BackupManager, "create_backup_bytes",
            lambda self: (_ for _ in ()).throw(RuntimeError("disco cheio")),
        )

        assert manager.create_scheduled_backup() is None


class TestListagem:
    def test_lista_vazia_numa_instalacao_nova(self, manager):
        assert manager.list_backups() == []

    def test_ordena_do_mais_recente_para_o_mais_antigo(self, manager):
        import os
        import time

        for i, nome in enumerate(["painel-plex-backup-20260101-120000.zip",
                                  "painel-plex-backup-20260202-120000.zip"]):
            caminho = os.path.join(manager.backups_dir, nome)
            with open(caminho, "wb") as f:
                f.write(b"x")
            os.utime(caminho, (time.time() - (10 - i) * 60, time.time() - (10 - i) * 60))

        nomes = [b["filename"] for b in manager.list_backups()]

        assert nomes[0] == "painel-plex-backup-20260202-120000.zip"

    def test_inclui_tamanho_e_data(self, manager):
        nome = manager.create_scheduled_backup()

        registo = next(b for b in manager.list_backups() if b["filename"] == nome)
        assert registo["size_bytes"] > 0
        assert registo["created_at"]


class TestGetBackupPath:
    @pytest.mark.parametrize("nome", [
        "../../etc/passwd",
        "../config.json",
        "/etc/passwd",
        "backup.zip",
        "painel-plex-backup-2026.zip",
        "",
        None,
    ])
    def test_nomes_invalidos_ou_maliciosos_sao_recusados(self, manager, nome):
        assert manager.get_backup_path(nome) is None

    def test_backup_inexistente(self, manager):
        assert manager.get_backup_path("painel-plex-backup-20260101-120000.zip") is None

    def test_backup_valido(self, manager):
        nome = manager.create_scheduled_backup()

        assert manager.get_backup_path(nome).endswith(nome)


class TestDeleteBackup:
    def test_apaga_um_backup_existente(self, manager):
        nome = manager.create_scheduled_backup()

        assert manager.delete_backup(nome) is True
        assert manager.list_backups() == []

    def test_nome_invalido_nao_apaga_nada(self, manager):
        assert manager.delete_backup("../../config.json") is False


class TestValidateBackupZip:
    def test_zip_valido(self, manager):
        assert manager.validate_backup_zip(io.BytesIO(manager.create_backup_bytes())) == (True, None)

    def test_ficheiro_que_nao_e_zip(self, manager):
        valido, mensagem = manager.validate_backup_zip(io.BytesIO(b"isto nao e um zip"))

        assert valido is False
        assert "ZIP" in mensagem

    def test_zip_sem_config_json(self, manager):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("outra-coisa.txt", "conteúdo")
        buffer.seek(0)

        valido, mensagem = manager.validate_backup_zip(buffer)

        assert valido is False
        assert "config.json" in mensagem

    def test_config_json_corrompido(self, manager):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(CONFIG_ENTRY_NAME, "{ isto nao e json")
        buffer.seek(0)

        valido, mensagem = manager.validate_backup_zip(buffer)

        assert valido is False
        assert "corrompido" in mensagem


class TestRestoreFromZip:
    def test_restaura_o_config_e_as_bases_de_dados(self, manager, instalacao):
        backup = io.BytesIO(manager.create_backup_bytes())

        # Estraga a instalação depois de o backup ter sido feito.
        (instalacao / "config.json").write_text('{"APP_TITLE": "estragado"}', encoding="utf-8")
        (instalacao / "app_data.db").unlink()

        assert manager.restore_from_zip(backup) is True

        config = json.loads((instalacao / "config.json").read_text(encoding="utf-8"))
        assert config["APP_TITLE"] == "Painel Plex"
        assert ler_sqlite(instalacao / "app_data.db") == ["original"]

    def test_zip_invalido_e_recusado_antes_de_tocar_nos_ficheiros(self, manager, instalacao):
        original = (instalacao / "config.json").read_text(encoding="utf-8")

        with pytest.raises(ValueError):
            manager.restore_from_zip(io.BytesIO(b"nao e um zip"))

        assert (instalacao / "config.json").read_text(encoding="utf-8") == original

    def test_zip_apenas_com_config(self, manager, instalacao):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(CONFIG_ENTRY_NAME, json.dumps({"APP_TITLE": "restaurado"}))
        buffer.seek(0)

        assert manager.restore_from_zip(buffer) is True

        config = json.loads((instalacao / "config.json").read_text(encoding="utf-8"))
        assert config["APP_TITLE"] == "restaurado"
        # A base de dados que não vinha no ZIP tem de ficar como estava.
        assert ler_sqlite(instalacao / "app_data.db") == ["original"]

    def test_nao_deixa_ficheiros_temporarios_para_tras(self, manager, instalacao):
        manager.restore_from_zip(io.BytesIO(manager.create_backup_bytes()))

        assert not list(instalacao.glob("*.restoring.tmp"))


class TestPastaDeBackups:
    def test_e_criada_automaticamente(self, tmp_path):
        import os

        destino = tmp_path / "config"
        destino.mkdir()

        gestor = BackupManager(config_dir=str(destino))

        assert os.path.isdir(gestor.backups_dir)

    def test_pode_ser_personalizada(self, tmp_path):
        destino = tmp_path / "config"
        destino.mkdir()
        alternativa = tmp_path / "outros-backups"

        gestor = BackupManager(config_dir=str(destino), backups_dir=str(alternativa))

        assert gestor.backups_dir == str(alternativa)
