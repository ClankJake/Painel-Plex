# app/services/backup_manager.py

import os
import io
import re
import sqlite3
import logging
import zipfile
import tempfile
from datetime import datetime

from ..utils.ficheiros import proteger_base_de_dados, proteger_ficheiro

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

# Onde estão as migrações deste painel, para saber que versões da base de dados
# ele sabe ler. Daqui (`app/services/`) até à raiz do projeto são dois níveis.
PASTA_DAS_MIGRACOES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "migrations", "versions",
)

# `revision = 'abc123'` no topo de cada ficheiro de migração.
REVISAO_NO_FICHEIRO = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.M)

# Uma tabela que só existe depois de o painel ter corrido: serve para distinguir
# "base de dados vazia" de "base de dados com dados mas sem versão".
TABELA_DE_CONTROLO = "user_profiles"


def _revisoes_conhecidas():
    """As versões da base de dados que ESTE painel sabe aplicar.

    Lê os ficheiros de migração em vez de perguntar ao Alembic: isto corre
    dentro de um pedido HTTP, sem contexto de aplicação garantido, e uma
    verificação de segurança não pode depender de conseguir montar a
    configuração do Alembic. Se a pasta não existir, devolve um conjunto vazio
    e a validação deixa passar — nunca se bloqueia um restauro por não se ter
    conseguido ler o próprio código.
    """
    revisoes = set()
    try:
        for nome in os.listdir(PASTA_DAS_MIGRACOES):
            if not nome.endswith(".py"):
                continue
            caminho = os.path.join(PASTA_DAS_MIGRACOES, nome)
            with open(caminho, "r", encoding="utf-8", errors="replace") as ficheiro:
                encontrada = REVISAO_NO_FICHEIRO.search(ficheiro.read())
            if encontrada:
                revisoes.add(encontrada.group(1))
    except OSError as e:
        logger.debug(f"[Backup] Não foi possível listar as migrações conhecidas: {e}")
        return set()
    return revisoes


def _ler_esquema(dados_da_bd):
    """A versão e o estado de uma base de dados que vem dentro de um ZIP.

    Devolve `(revisao, tem_dados)`: a revisão do Alembic (None se a tabela de
    versões não existir) e se a base de dados já tem tabelas do painel.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(dados_da_bd)
        caminho = tmp.name

    try:
        ligacao = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True, timeout=10)
        try:
            tabelas = {
                linha[0] for linha in
                ligacao.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            revisao = None
            if "alembic_version" in tabelas:
                linha = ligacao.execute("SELECT version_num FROM alembic_version").fetchone()
                revisao = linha[0] if linha else None
            return revisao, TABELA_DE_CONTROLO in tabelas
        finally:
            ligacao.close()
    except sqlite3.DatabaseError as e:
        raise ValueError(
            f"O 'app_data.db' dentro do ZIP não é uma base de dados SQLite utilizável ({e})."
        )
    finally:
        try:
            os.remove(caminho)
        except OSError:
            pass


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
            # A versão da base de dados e o servidor de média ficam escritos:
            # é o que permite perceber, meses depois, se um ZIP dá para
            # restaurar num painel — e é o que a validação lê ao restaurar.
            revisao = None
            if app_db_bytes:
                try:
                    revisao, _tem_dados = _ler_esquema(app_db_bytes)
                except ValueError as e:
                    logger.warning(f"[Backup] Não foi possível ler a versão da base de dados: {e}")

            manifest = (
                f"Painel-Plex — Backup\n"
                f"Gerado em: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Versão da base de dados: {revisao or 'desconhecida'}\n"
                f"Servidor de média: {self._tipo_de_servidor()}\n"
                f"Contém: {CONFIG_ENTRY_NAME}"
                f"{', ' + APP_DB_ENTRY_NAME if app_db_bytes else ''}"
                f"{', ' + SCHEDULER_DB_ENTRY_NAME if scheduler_db_bytes else ''}\n"
            )
            zf.writestr(MANIFEST_ENTRY_NAME, manifest)

        buffer.seek(0)
        return buffer.read()

    def _tipo_de_servidor(self):
        """O servidor de média deste painel, para o manifesto.

        Lê-se do config.json em vez do manager: o backup pode ser gerado por um
        job de fundo, e isto é informação para quem lê o ZIP.
        """
        try:
            import json

            with open(self.config_file, "r", encoding="utf-8") as ficheiro:
                return json.load(ficheiro).get("MEDIA_SERVER_TYPE") or "plex"
        except Exception:
            return "desconhecido"

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
            # 🛡️ O ZIP leva o config.json INTEIRO — SECRET_KEY, token do Plex,
            # chave de administrador do Jellyfin e as credenciais dos gateways —
            # mais as bases de dados. Um backup automático é o ficheiro mais
            # sensível que o painel escreve, e ficava com as permissões do umask.
            proteger_ficheiro(filepath)
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

                if APP_DB_ENTRY_NAME in names:
                    return self._validar_esquema(zf.read(APP_DB_ENTRY_NAME))
                return True, None
        except zipfile.BadZipFile:
            return False, "O ficheiro enviado não é um ZIP válido."

    def _validar_esquema(self, dados_da_bd):
        """A base de dados do backup é legível por ESTE painel?

        🛡️ Restaurar é substituir a base de dados por baixo da aplicação; o
        esquema só é acertado no arranque seguinte, por `flask db upgrade`. Há
        dois casos em que esse arranque falharia — e um painel que não arranca
        é pior do que um restauro recusado:

        - **o backup é de uma versão MAIS RECENTE**: a revisão que ele traz não
          existe nas migrações deste painel, e o Alembic pára com "Can't locate
          revision". Recusa-se, e diz-se que é preciso atualizar primeiro;
        - **o backup tem dados mas não tem versão nenhuma** (uma instalação
          muito antiga, anterior às migrações): o `upgrade` tentaria criar
          tabelas que já lá estão. Recusa-se também.

        O caminho normal — um backup MAIS ANTIGO, que é o de quem vem do painel
        só-Plex — passa: as migrações levam-no para a frente no arranque, e é
        exatamente para isso que elas existem.
        """
        try:
            revisao, tem_dados = _ler_esquema(dados_da_bd)
        except ValueError as e:
            return False, str(e)

        if revisao is None:
            if tem_dados:
                return False, (
                    "A base de dados deste backup tem dados mas não tem registo de versão "
                    "(tabela 'alembic_version'). Restaurá-la deixaria o painel sem arrancar. "
                    "Atualize o painel de origem, faça um backup novo e tente outra vez."
                )
            return True, None

        conhecidas = _revisoes_conhecidas()
        if conhecidas and revisao not in conhecidas:
            return False, (
                f"Este backup foi feito numa versão MAIS RECENTE do painel (versão de base de "
                f"dados '{revisao}', desconhecida aqui). Atualize o painel antes de restaurar — "
                "restaurar agora deixava-o sem arrancar."
            )

        return True, None

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
                # ⚠️ O temporário é protegido ANTES do `os.replace`: entre a
                # escrita e a troca ele já tem o conteúdo todo, e o `replace`
                # leva as permissões do ficheiro de origem consigo. Proteger só
                # no fim deixava uma janela — curta, mas com tudo lá dentro.
                proteger_ficheiro(tmp_path)
                os.replace(tmp_path, dest_path)

            _atomic_write(self.config_file, zf.read(CONFIG_ENTRY_NAME))
            logger.info("[Backup] config.json restaurado.")

            if APP_DB_ENTRY_NAME in names:
                _atomic_write(self.app_db_path, zf.read(APP_DB_ENTRY_NAME))
                logger.info("[Backup] app_data.db restaurado.")

            if SCHEDULER_DB_ENTRY_NAME in names:
                _atomic_write(self.scheduler_db_path, zf.read(SCHEDULER_DB_ENTRY_NAME))
                logger.info("[Backup] scheduler_jobs.db restaurado.")

        # Os `-wal`/`-shm` que sobraram do processo antigo ficam ao lado dos
        # ficheiros restaurados até o SQLite os reescrever; protegem-se na mesma.
        proteger_base_de_dados(self.app_db_path)
        proteger_base_de_dados(self.scheduler_db_path)

        return True
