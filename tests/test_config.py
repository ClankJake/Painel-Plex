# tests/test_config.py
"""Carregamento, criação e migração automática do config.json."""

import json

import pytest

from app import config as config_module


@pytest.fixture()
def config_env(tmp_path, monkeypatch):
    """
    Isola o módulo de configuração num diretório temporário próprio de cada teste.
    A variável SECRET_KEY é removida para que a lógica do ficheiro seja exercitada.
    """
    monkeypatch.setattr(config_module, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config_module, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    return tmp_path


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


class TestCriacaoInicial:
    def test_cria_o_ficheiro_quando_nao_existe(self, config_env):
        ficheiro = config_env / "config.json"
        assert not ficheiro.exists()

        config = config_module.load_or_create_config()

        assert ficheiro.exists()
        assert config["IS_CONFIGURED"] is False
        assert config["APP_TITLE"] == "Painel Plex"
        assert config["SCREEN_PRICES"]["1"] == "10.00"

    def test_gera_chaves_secretas(self, config_env):
        config = config_module.load_or_create_config()

        assert len(config["SECRET_KEY"]) >= 32
        assert len(config["INTERNAL_TRIGGER_KEY"]) >= 32

    def test_cada_instalacao_recebe_uma_chave_diferente(self, config_env, tmp_path, monkeypatch):
        primeira = config_module.load_or_create_config()["SECRET_KEY"]

        outro = tmp_path / "outro"
        monkeypatch.setattr(config_module, "CONFIG_DIR", str(outro))
        monkeypatch.setattr(config_module, "CONFIG_FILE", str(outro / "config.json"))
        segunda = config_module.load_or_create_config()["SECRET_KEY"]

        assert primeira != segunda

    def test_variavel_de_ambiente_tem_prioridade(self, config_env, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "chave-do-ambiente")

        assert config_module.load_or_create_config()["SECRET_KEY"] == "chave-do-ambiente"


class TestChaveSecreta:
    def test_ambiente_sobrepoe_se_ao_ficheiro(self, config_env, monkeypatch):
        _write(config_env / "config.json", {"SECRET_KEY": "chave-do-ficheiro"})
        monkeypatch.setenv("SECRET_KEY", "chave-do-ambiente")

        assert config_module.load_or_create_config()["SECRET_KEY"] == "chave-do-ambiente"

    def test_chave_em_branco_e_regenerada_e_guardada(self, config_env):
        ficheiro = config_env / "config.json"
        _write(ficheiro, {"SECRET_KEY": "", "IS_CONFIGURED": True})

        config = config_module.load_or_create_config()

        assert config["SECRET_KEY"]
        # A nova chave tem de ficar gravada, senão as sessões seriam invalidadas
        # a cada arranque.
        assert json.loads(ficheiro.read_text(encoding="utf-8"))["SECRET_KEY"] == config["SECRET_KEY"]


class TestMigracaoDeChaves:
    def test_preenche_chaves_em_falta_num_ficheiro_antigo(self, config_env):
        _write(config_env / "config.json", {"SECRET_KEY": "abc", "IS_CONFIGURED": True})

        config = config_module.load_or_create_config()

        assert config["REFERRAL_ENABLED"] is False
        assert config["BACKUP_MAX_COUNT"] == 7
        assert config["SCREEN_PRICES"]["6"] == "40.00"
        # O que já estava definido não pode ser alterado.
        assert config["IS_CONFIGURED"] is True

    def test_a_migracao_e_persistida_em_disco(self, config_env):
        ficheiro = config_env / "config.json"
        _write(ficheiro, {"SECRET_KEY": "abc", "IS_CONFIGURED": True})

        config_module.load_or_create_config()

        assert "REFERRAL_ENABLED" in json.loads(ficheiro.read_text(encoding="utf-8"))

    def test_nao_sobrepoe_valores_personalizados(self, config_env):
        _write(config_env / "config.json", {
            "SECRET_KEY": "abc",
            "RENEWAL_PRICE": "42.00",
            "DAYS_TO_NOTIFY_EXPIRATION": 15,
        })

        config = config_module.load_or_create_config()

        assert config["RENEWAL_PRICE"] == "42.00"
        assert config["DAYS_TO_NOTIFY_EXPIRATION"] == 15

    def test_planos_de_5_e_6_telas_sao_acrescentados(self, config_env):
        _write(config_env / "config.json", {
            "SECRET_KEY": "abc",
            "SCREEN_PRICES": {"1": "12.00", "2": "20.00"},
        })

        precos = config_module.load_or_create_config()["SCREEN_PRICES"]

        assert precos["1"] == "12.00"
        assert precos["5"] == "35.00"
        assert precos["6"] == "40.00"

    def test_migracao_bpix_para_gates2b(self, config_env):
        _write(config_env / "config.json", {
            "SECRET_KEY": "abc",
            "BPIX_AUTH_TOKEN": "token-antigo",
            "BPIX_ENABLED": True,
        })

        config = config_module.load_or_create_config()

        assert config["GATES2B_AUTH_TOKEN"] == "token-antigo"
        assert config["GATES2B_ENABLED"] is True
        # As chaves antigas são removidas para não confundirem.
        assert "BPIX_AUTH_TOKEN" not in config
        assert "BPIX_ENABLED" not in config

    def test_gates2b_ja_configurada_nao_e_sobreposta(self, config_env):
        _write(config_env / "config.json", {
            "SECRET_KEY": "abc",
            "BPIX_AUTH_TOKEN": "token-antigo",
            "GATES2B_AUTH_TOKEN": "token-novo",
        })

        config = config_module.load_or_create_config()

        assert config["GATES2B_AUTH_TOKEN"] == "token-novo"


class TestAutoCuraDosTemplates:
    def test_template_em_branco_e_restaurado(self, config_env):
        _write(config_env / "config.json", {
            "SECRET_KEY": "abc",
            "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "",
        })

        template = config_module.load_or_create_config()["TELEGRAM_RENEWAL_MESSAGE_TEMPLATE"]

        assert template.strip()
        assert "{new_date}" in template

    def test_template_personalizado_e_preservado(self, config_env):
        _write(config_env / "config.json", {
            "SECRET_KEY": "abc",
            "TELEGRAM_RENEWAL_MESSAGE_TEMPLATE": "Renovado até {new_date}.",
        })

        config = config_module.load_or_create_config()

        assert config["TELEGRAM_RENEWAL_MESSAGE_TEMPLATE"] == "Renovado até {new_date}."


class TestCaminhoDoLog:
    def test_caminho_relativo_e_convertido_para_absoluto(self, config_env):
        _write(config_env / "config.json", {"SECRET_KEY": "abc", "LOG_FILE": "app.log"})

        caminho = config_module.load_or_create_config()["LOG_FILE"]

        assert caminho == str(config_env / "app.log")


class TestFicheiroCorrompido:
    def test_devolve_configuracao_minima_de_emergencia(self, config_env):
        (config_env / "config.json").write_text("{ isto não é json", encoding="utf-8")

        config = config_module.load_or_create_config()

        # A aplicação tem de conseguir arrancar mesmo assim.
        assert "SECRET_KEY" in config
        assert config.get("IS_CONFIGURED") is None


class TestSaveAppConfig:
    def test_guarda_e_le_de_volta(self, config_env):
        assert config_module.save_app_config({"IS_CONFIGURED": True, "SECRET_KEY": "abc"}) is True

        assert json.loads((config_env / "config.json").read_text(encoding="utf-8"))["IS_CONFIGURED"] is True

    def test_devolve_false_quando_nao_consegue_escrever(self, config_env, monkeypatch):
        monkeypatch.setattr(config_module, "CONFIG_FILE", str(config_env / "sem-pasta" / "config.json"))

        assert config_module.save_app_config({"a": 1}) is False


class TestIsConfigured:
    def test_instalacao_nova_nao_esta_configurada(self, config_env):
        assert config_module.is_configured() is False

    def test_reflete_a_flag_do_ficheiro(self, config_env):
        _write(config_env / "config.json", {"SECRET_KEY": "abc", "IS_CONFIGURED": True})

        assert config_module.is_configured() is True
