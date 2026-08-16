# app/services/backup_manager.py

import os
import io
import re
import sqlite3
import logging
import zipfile
import tempfile
from datetime import datetime

logger = logging.getLogger(__name__)

# Nome dos ficheiros dentro do ZIP de backup — usados também para validar
# um ZIP na hora de restaurar.
CONFIG_ENTRY_NAME = "config.json"
APP_DB_ENTRY_NAME = "app_data.db"
SCHEDULER_DB_ENTRY_NAME = "scheduler_jobs.db"
MANIFEST_ENTRY_NAME = "backup_manifest.txt"

# Prefixo/sufixo usados para nomear ficheiros de backup gerados automaticamente,
# e para reconhecer com segurança quais ficheiros dentro da pasta de backups
# pertencem a este sistema (evita apagar ficheiros de terceiros por engano).
BACKUP_FILENAME_PATTERN = re.compile(r"^painel-plex-backup-\d{8}-\d{6}\.zip$")


class BackupManager:
    """
    Responsável por criar, listar, apagar e restaurar backups do
    config.json + bases de dados SQLite do painel.

    Usa a API de backup nativa do sqlite3 (Connection.backup()) em vez de
    uma simples cópia de ficheiro, porque copiar um ficheiro .db enquanto
    há escritas em curso (ex: webhook a processar um pagamento, scheduler
    a correr um job) pode gerar uma cópia corrompida ou inconsistente.
    A API de backup do SQLite lida com isto de forma segura e atómica,
    mesmo com a aplicação em produção e a base de dados em uso.
    """

    def __init__(self, config_dir, backups_dir=None):
        self.config_dir = config_dir
        self.config_file = os.path.join(config_dir, "config.json")
        self.app_db_path = os.path.join(config_dir, "app_data.db")
        self.scheduler_db_path = os.path.join(config_dir, "scheduler_jobs.db")
        self.backups_dir = backups_dir or os.path.join(config_dir, "backups")
        os.makedirs(self.backups_dir, exist_ok=True)

    # --------------------------------------------------------------------
    # CRIAÇÃO
    # --------------------------------------------------------------------

    def _backup_sqlite_to_bytes(self, source_path):
        """
        Usa a API de backup online do SQLite para copiar a base de dados de
        forma segura para um ficheiro temporário, e devolve os bytes resultantes.
        Se a base de dados de origem não existir (ex: scheduler_jobs.db pode não
        ter sido criado ainda), devolve None.
        """
        if not os.path.exists(source_path):
            logger.warning(f"[Backup] Ficheiro '{source_path}' não encontrado, a ignorar no backup.")
            return None

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            source_conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
            dest_conn = sqlite3.connect(tmp_path)
            try:
                source_conn.backup(dest_conn)
            finally:
                dest_conn.close()
                source_conn.close()

            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def create_backup_bytes(self):
        """
        Gera um ZIP em memória contendo o config.json e as bases de dados,
        e devolve os bytes prontos para serem enviados como download ou
        gravados em disco.
        """
        buffer = io.BytesIO()
        now = datetime.now()

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. config.json — cópia direta, é um ficheiro de texto pequeno,
            # sem risco de corrupção por concorrência (é reescrito por inteiro
            # a cada gravação, nunca editado in-place).
            if os.path.exists(self.config_file):
                zf.write(self.config_file, CONFIG_ENTRY_NAME)
            else:
                logger.warning("[Backup] config.json não encontrado — backup ficará incompleto.")

            # 2. Bases de dados — via API de backup segura do SQLite.
            app_db_bytes = self._backup_sqlite_to_bytes(self.app_db_path)
            if app_db_bytes:
                zf.writestr(APP_DB_ENTRY_NAME, app_db_bytes)

            scheduler_db_bytes = self._backup_sqlite_to_bytes(self.scheduler_db_path)
            if scheduler_db_bytes:
                zf.writestr(SCHEDULER_DB_ENTRY_NAME, scheduler_db_bytes)

            # 3. Manifesto simples, útil para diagnóstico e para confirmar
            # visualmente a origem/data de um backup ao restaurar.
            manifest = (
                f"Painel-Plex — Backup\n"
                f"Gerado em: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Contém: {CONFIG_ENTRY_NAME}"
                f"{', ' + APP_DB_ENTRY_NAME if app_db_bytes else ''}"
                f"{', ' + SCHEDULER_DB_ENTRY_NAME if scheduler_db_bytes else ''}\n"
            )
            zf.writestr(MANIFEST_ENTRY_NAME, manifest)

        buffer.seek(0)
        return buffer.read()

    def create_scheduled_backup(self, max_backups=7):
        """
        Cria um backup e grava-o na pasta de backups em disco, com nome
        baseado na data/hora, e remove os backups mais antigos que excedam
        o limite configurado.
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"painel-plex-backup-{timestamp}.zip"
        filepath = os.path.join(self.backups_dir, filename)

        try:
            backup_bytes = self.create_backup_bytes()
            with open(filepath, "wb") as f:
                f.write(backup_bytes)
            logger.info(f"[Backup] Backup automático criado com sucesso: {filename} ({len(backup_bytes) / 1024:.1f} KB)")
            self._prune_old_backups(max_backups)
            return filename
        except Exception as e:
            logger.error(f"[Backup] Falha ao criar backup automático: {e}", exc_info=True)
            return None

    def _prune_old_backups(self, max_backups):
        """Mantém apenas os N backups mais recentes gerados automaticamente."""
        backups = self.list_backups()
        if len(backups) <= max_backups:
            return
        for old_backup in backups[max_backups:]:
            try:
                os.remove(os.path.join(self.backups_dir, old_backup["filename"]))
                logger.info(f"[Backup] Backup antigo removido pela rotação: {old_backup['filename']}")
            except OSError as e:
                logger.warning(f"[Backup] Não foi possível remover o backup antigo '{old_backup['filename']}': {e}")

    # --------------------------------------------------------------------
    # LISTAGEM / GESTÃO
    # --------------------------------------------------------------------

    def list_backups(self):
        """Lista os backups automáticos guardados em disco, mais recentes primeiro."""
        if not os.path.isdir(self.backups_dir):
            return []

        results = []
        for filename in os.listdir(self.backups_dir):
            if not BACKUP_FILENAME_PATTERN.match(filename):
                continue
            filepath = os.path.join(self.backups_dir, filename)
            try:
                stat = os.stat(filepath)
                results.append({
                    "filename": filename,
                    "size_bytes": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
            except OSError:
                continue

        results.sort(key=lambda b: b["created_at"], reverse=True)
        return results

    def get_backup_path(self, filename):
        """
        Resolve o caminho de um backup pelo nome, com proteção contra
        path traversal (ex: '../../etc/passwd'). Devolve None se o nome
        não for um backup válido gerado por este sistema ou não existir.
        """
        if not BACKUP_FILENAME_PATTERN.match(filename or ""):
            return None
        filepath = os.path.normpath(os.path.join(self.backups_dir, filename))
        if not filepath.startswith(os.path.normpath(self.backups_dir) + os.sep):
            return None
        if not os.path.isfile(filepath):
            return None
        return filepath

    def delete_backup(self, filename):
        filepath = self.get_backup_path(filename)
        if not filepath:
            return False
        os.remove(filepath)
        logger.info(f"[Backup] Backup removido manualmente: {filename}")
        return True

    # --------------------------------------------------------------------
    # RESTAURO
    # --------------------------------------------------------------------

    def validate_backup_zip(self, file_stream):
        """
        Confirma que o ficheiro enviado é um ZIP de backup válido gerado por
        este sistema, contendo pelo menos o config.json. Devolve (True, None)
        ou (False, mensagem_de_erro).
        """
        try:
            with zipfile.ZipFile(file_stream) as zf:
                names = zf.namelist()
                if CONFIG_ENTRY_NAME not in names:
                    return False, "O ficheiro ZIP não contém um 'config.json' — não parece ser um backup válido deste sistema."
                # Confirma que o config.json de dentro do ZIP é um JSON válido.
                import json
                try:
                    json.loads(zf.read(CONFIG_ENTRY_NAME))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return False, "O 'config.json' dentro do ZIP está corrompido ou não é um JSON válido."
                return True, None
        except zipfile.BadZipFile:
            return False, "O ficheiro enviado não é um ZIP válido."

    def restore_from_zip(self, file_stream):
        """
        Restaura config.json e as bases de dados a partir de um ZIP de backup.

        ⚠️ Isto sobrescreve os ficheiros atuais. O chamador é responsável por
        garantir que a aplicação é reiniciada logo a seguir, para que as
        ligações à base de dados (SQLAlchemy engine, APScheduler jobstore)
        sejam recriadas a partir dos ficheiros novos — tentar continuar a
        usar as ligações antigas em memória após trocar os ficheiros por
        baixo pode causar comportamento inconsistente ou corrupção.
        """
        is_valid, error = self.validate_backup_zip(file_stream)
        if not is_valid:
            raise ValueError(error)

        file_stream.seek(0)
        with zipfile.ZipFile(file_stream) as zf:
            names = zf.namelist()

            # Grava cada ficheiro num temporário antes de substituir o original,
            # para minimizar a janela de risco caso a escrita falhe a meio.
            def _atomic_write(dest_path, data):
                tmp_path = dest_path + ".restoring.tmp"
                with open(tmp_path, "wb") as f:
                    f.write(data)
                os.replace(tmp_path, dest_path)

            _atomic_write(self.config_file, zf.read(CONFIG_ENTRY_NAME))
            logger.info("[Backup] config.json restaurado.")

            if APP_DB_ENTRY_NAME in names:
                _atomic_write(self.app_db_path, zf.read(APP_DB_ENTRY_NAME))
                logger.info("[Backup] app_data.db restaurado.")

            if SCHEDULER_DB_ENTRY_NAME in names:
                _atomic_write(self.scheduler_db_path, zf.read(SCHEDULER_DB_ENTRY_NAME))
                logger.info("[Backup] scheduler_jobs.db restaurado.")

        return True
