import pytest

from app import extensions


@pytest.fixture
def admin(client, config_file):
    config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": "1", "username": "dono", "role": "admin", "email": "a@b.test"}
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True
    return client


def test_falha_de_leitura_do_log_oculta_o_caminho(admin, app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "LOG_FILE", str(tmp_path))
    resposta = admin.get("/api/system/logs")
    assert resposta.status_code == 500
    assert resposta.get_json() == {"success": False, "message": "Não foi possível ler o arquivo de log."}


def test_pagamento_manual_oculta_falha_interna(admin, monkeypatch):
    def falhar(*args):
        raise RuntimeError("detalhes internos da transação")

    monkeypatch.setattr(extensions.media_server, "get_user_by_id", lambda uid: {"username": "dono"})
    monkeypatch.setattr(extensions.data_manager, "add_manual_payment", falhar)
    resposta = admin.post("/api/payments/financial/add-manual", json={
        "media_user_id": "1", "value": 20, "description": "Teste", "payment_date": "2026-01-01",
    })
    assert resposta.status_code == 500
    assert resposta.get_json() == {"success": False, "message": "Não foi possível registrar o pagamento manual."}
