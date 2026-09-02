# tests/conftest.py
"""
Configuração partilhada dos testes.

⚠️ IMPORTANTE: este ficheiro redireciona o diretório de configuração através da
variável de ambiente ``PAINEL_PLEX_CONFIG_DIR`` **antes** de qualquer módulo da
aplicação ser importado. Sem isto, correr os testes escreveria por cima do
``config/config.json`` e da base de dados reais de quem está a desenvolver.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# --- Isolamento total do ambiente -----------------------------------------
# O diretório é criado aqui (e não numa fixture) porque `app.config` lê a
# variável de ambiente no momento do import.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

_TEST_CONFIG_DIR = tempfile.mkdtemp(prefix="painel-plex-tests-")
os.environ["PAINEL_PLEX_CONFIG_DIR"] = _TEST_CONFIG_DIR
# Fuso fixo: várias rotinas usam a hora local e os resultados têm de ser iguais
# na máquina do programador e no CI.
os.environ["TZ"] = "UTC"
# Chave previsível para as sessões de teste — nunca é um segredo real.
os.environ.setdefault("SECRET_KEY", "chave-de-teste-nao-secreta")


def pytest_sessionfinish(session, exitstatus):
    """Remove o diretório temporário no fim da execução."""
    shutil.rmtree(_TEST_CONFIG_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def config_dir():
    """Caminho do diretório de configuração usado pelos testes."""
    return Path(_TEST_CONFIG_DIR)


@pytest.fixture(scope="session")
def app():
    """
    Instância única da aplicação Flask.

    É de âmbito 'session' porque `create_app()` regista blueprints e extensões
    globais (que só podem ser inicializadas uma vez por processo). O isolamento
    entre testes é garantido pelas fixtures `db_session` e `config_file`.
    """
    from app import create_app
    from app.extensions import db

    application = create_app()
    application.config.update(TESTING=True, SERVER_NAME="localhost")

    with application.app_context():
        db.create_all()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def app_context(app):
    """Contexto de aplicação ativo (necessário para o SQLAlchemy e o Babel)."""
    with app.app_context():
        yield app


@pytest.fixture()
def db_session(app_context):
    """
    Sessão da base de dados limpa: no fim de cada teste todas as tabelas são
    esvaziadas, para que a ordem dos testes nunca influencie o resultado.
    """
    from app.extensions import db

    yield db.session

    db.session.rollback()
    for table in reversed(db.metadata.sorted_tables):
        db.session.execute(table.delete())
    db.session.commit()
    db.session.remove()


@pytest.fixture()
def client(app):
    """Cliente HTTP de teste."""
    return app.test_client()


@pytest.fixture()
def config_file(config_dir):
    """
    Devolve uma função `set_config(**valores)` que escreve no config.json usado
    pelos testes. O ficheiro original é reposto no fim, mesmo se o teste falhar.
    """
    from app import config as config_module

    path = Path(config_module.CONFIG_FILE)
    original = path.read_bytes() if path.exists() else None

    def set_config(**values):
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        current.update(values)
        path.write_text(json.dumps(current, indent=4), encoding="utf-8")
        return current

    yield set_config

    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(original)


@pytest.fixture()
def data_manager(db_session):
    """DataManager ligado à base de dados de teste."""
    from app.services.data_manager import DataManager

    return DataManager()


class FakeDataManager:
    """
    Substituto em memória do DataManager, para testar a lógica de negócio dos
    serviços (preços, indicações) sem tocar na base de dados.
    """

    def __init__(self, profiles=None, coupons=None, used_coupons=None, blocked=None):
        self.profiles = profiles or {}
        self.coupons = coupons or {}
        self.used_coupons = set(used_coupons or [])
        self.blocked = blocked or {}
        self.achievements = {}
        self.notifications = []
        self.terminations = []

    # --- Perfis ---
    def get_user_profile(self, plex_user_id):
        return self.profiles.get(int(plex_user_id))

    def set_user_profile(self, plex_user_id, profile_data):
        profile = self.profiles.setdefault(int(plex_user_id), {"plex_user_id": int(plex_user_id)})
        profile.update(profile_data)
        return profile

    def get_user_profiles_by_id(self, plex_user_ids):
        return {
            int(user_id): self.profiles[int(user_id)]
            for user_id in plex_user_ids
            if int(user_id) in self.profiles
        }

    def get_user_profile_by_email(self, email):
        if not email:
            return None
        alvo = str(email).strip().lower()
        for profile in self.profiles.values():
            if (profile.get("email") or "").lower() == alvo:
                return profile
        return None

    def get_user_profile_by_referral_code(self, code):
        for profile in self.profiles.values():
            if (profile.get("referral_code") or "").upper() == str(code).strip().upper():
                return profile
        return None

    def get_users_referred_by(self, plex_user_id):
        return [p for p in self.profiles.values() if p.get("referred_by") == int(plex_user_id)]

    def reset_all_users_xp(self):
        for profile in self.profiles.values():
            profile["xp"] = 0
        return len(self.profiles)

    # --- Bloqueios ---
    def get_blocked_user(self, plex_user_id):
        return self.blocked.get(int(plex_user_id))

    # --- Conquistas ---
    def get_unlocked_achievements(self, plex_user_id):
        return set(self.achievements.get(int(plex_user_id), set()))

    def add_unlocked_achievements(self, plex_user_id, username, achievements_to_add):
        atuais = self.achievements.setdefault(int(plex_user_id), set())
        atuais.update(ach["id"] for ach in achievements_to_add)

    # --- Cupões ---
    def get_coupon_by_code(self, code):
        return self.coupons.get(code)

    def has_user_used_coupon(self, plex_user_id, code):
        return (int(plex_user_id), code) in self.used_coupons

    # --- Auditoria ---
    def log_stream_termination(self, plex_user_id, username, media_title, platform, reason):
        registo = {
            "plex_user_id": plex_user_id, "username": username, "media_title": media_title,
            "platform": platform, "reason": reason,
        }
        self.terminations.append(registo)
        return registo

    # --- Notificações ---
    def create_notification(self, message, category="info", link=None, user_plex_id=None):
        self.notifications.append(
            {"message": message, "category": category, "link": link, "user_plex_id": user_plex_id}
        )
        return True


@pytest.fixture()
def fake_data_manager():
    """Fábrica do DataManager falso (ver `FakeDataManager`)."""
    return FakeDataManager
