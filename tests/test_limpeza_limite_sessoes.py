# tests/test_limpeza_limite_sessoes.py
"""
Reparação de uma vez do limite de sessões posto no servidor.

🐛 O painel escreveu `Policy.MaxActiveSessions` no Jellyfin a pensar que era um
limite de telas. Não é: limita AUTENTICAÇÕES. Não cortava quem já estava a ver
(só recusa entradas novas) e trancava a pessoa fora do PAINEL, porque entrar no
painel autentica-se contra o servidor e ocupa uma sessão.

A limpeza corre uma vez e marca-se como feita. É isso que estes testes guardam:
correr todos os dias desfaria, às escondidas, um limite que o administrador
tenha posto de propósito na interface do Jellyfin.
"""

import pytest

from app import scheduler as scheduler_module

pytestmark = pytest.mark.integration


class BackendFalso:
    def __init__(self, resultado):
        self.resultado = resultado
        self.chamadas = 0

    def clear_session_limits(self):
        self.chamadas += 1
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


@pytest.fixture()
def ambiente(monkeypatch, app_context):
    """A limpeza, com o backend e o config.json sob controlo do teste."""
    from app import extensions

    config = {"JELLYFIN_SESSION_LIMIT_CLEARED": False}
    gravados = []

    monkeypatch.setattr(scheduler_module, "load_or_create_config", lambda: config)
    monkeypatch.setattr("app.config.save_app_config", lambda novo: gravados.append(dict(novo)))

    def correr(resultado):
        backend = BackendFalso(resultado)
        monkeypatch.setattr(extensions, "media_server", backend)
        scheduler_module.limpar_limite_de_sessoes_do_servidor()
        return backend

    return correr, config, gravados


def test_limpa_e_marca_como_feita(ambiente):
    correr, config, gravados = ambiente

    backend = correr({"success": True, "limpos": 2})

    assert backend.chamadas == 1
    assert gravados[-1]["JELLYFIN_SESSION_LIMIT_CLEARED"] is True


def test_nao_volta_a_correr_depois_de_feita(ambiente):
    correr, config, gravados = ambiente
    config["JELLYFIN_SESSION_LIMIT_CLEARED"] = True

    backend = correr({"success": True, "limpos": 2})

    assert backend.chamadas == 0
    assert gravados == []


def test_nada_a_limpar_tambem_conta_como_feita(ambiente):
    # Um servidor onde o painel nunca escreveu nada (o Plex, ou um Jellyfin
    # novo): não há motivo para voltar a perguntar todos os dias.
    correr, config, gravados = ambiente

    correr({"success": True, "limpos": 0})

    assert gravados[-1]["JELLYFIN_SESSION_LIMIT_CLEARED"] is True


def test_servidor_em_baixo_nao_se_da_por_feita(ambiente):
    # Se ficasse marcada, quem estivesse trancado ficava trancado para sempre.
    correr, config, gravados = ambiente

    correr({"success": False, "limpos": 0})

    assert gravados == []
    assert config["JELLYFIN_SESSION_LIMIT_CLEARED"] is False


def test_uma_excecao_nao_rebenta_a_limpeza_geral(ambiente):
    # Corre dentro do `cleanup_job`: rebentar aqui levaria com ela a limpeza de
    # pagamentos pendentes e de links curtos.
    correr, config, gravados = ambiente

    correr(RuntimeError("boom"))

    assert gravados == []
