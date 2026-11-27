import logging
from pathlib import Path

from app.config import load_or_create_config
from app.logging_config import apply_log_level


def test_troca_de_nivel_em_tempo_real(app, client, config_file, db_session, monkeypatch):
    config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1", LOG_LEVEL="INFO")
    with client.session_transaction() as session:
        session["user_details"] = {"id": "1", "username": "dono", "role": "admin"}
        session["_user_id"] = "1"
        session["_fresh"] = True

    original = app.config['LOG_LEVEL']
    handler = app.extensions['log_file_handler']
    path = Path(handler.baseFilename)
    endpoint = '/api/system/settings'
    try:
        for level in ('DEBUG', 'CRITICAL', 'INFO'):
            response = client.post(endpoint, json={'LOG_LEVEL': level})
            assert response.status_code == 200
            assert load_or_create_config()['LOG_LEVEL'] == app.config['LOG_LEVEL'] == level
            assert handler.level == app.logger.level == logging.getLogger().level == getattr(logging, level)
            app.logger.debug('teste de troca %s', level)
            app.logger.debug('teste de troca %s', level)

        logs = path.read_text(encoding='utf-8')
        assert logs.count('teste de troca DEBUG') == 2
        assert 'teste de troca CRITICAL' not in logs
        assert 'teste de troca INFO' not in logs

        assert client.post(endpoint, json={'LOG_LEVEL': 'INVALIDO'}).status_code == 400
        monkeypatch.setattr('app.blueprints.api.system.save_app_config', lambda config: False)
        assert client.post(endpoint, json={'LOG_LEVEL': 'ERROR'}).status_code == 500
        assert load_or_create_config()['LOG_LEVEL'] == app.config['LOG_LEVEL'] == 'INFO'
        assert handler.level == logging.INFO
    finally:
        app.config['LOG_LEVEL'] = original
        apply_log_level(app, original)
