# tests/test_aviso_de_teste_estendido.py

"""Estender o período de teste avisa quem está em teste.

🔔 **Era a única mudança de acesso que o painel fazia em silêncio.** O
administrador clicava em "Estender", o painel remarcava o `end_trial_job`, e do
outro lado ninguém ficava a saber: a pessoa continuava a contar com a data
antiga e o painel só voltava a falar com ela no FIM do teste, para dizer que
tinha acabado — ou seja, o único aviso sobre o teste era o da má notícia.

Três coisas que este evento (`trial_extended`) exige, todas aqui:

- ⚠️ **a hora faz parte da data.** Um teste estende-se por horas ou minutos, ao
  contrário de uma assinatura: "vai até 20/09/2026" sobre uma extensão de duas
  horas não diz a quem lê quando é que fica sem acesso;
- ⚠️ **avisar nunca derruba a extensão.** Quando isto corre, o teste já está
  estendido e a tarefa já foi remarcada. Um canal fora do ar não pode fazer o
  administrador pensar que a extensão falhou — mas a resposta diz-lhe se o
  aviso saiu, porque um aviso que não saiu não pode parecer que saiu;
- ⚠️ **os templates precisam das DUAS listas** (`load_or_create_config` e o
  `fields_to_update` do `save_settings`). Fora da segunda, o administrador
  reescreve o texto na página de Configurações e ele é descartado em silêncio
  ao salvar — foi o que aconteceu ao `JELLYFIN_URL`.
"""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")


def _autenticar(client):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": "1", "username": "dono", "email": "a@b.test", "role": "admin"}
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True


class AgendadorFalso:
    """O bastante para a rota: ela remove, acrescenta e lê o fuso."""

    timezone = timezone.utc

    def __init__(self):
        self.agendados = []

    def remove_job(self, job_id):
        pass

    def add_job(self, **kwargs):
        self.agendados.append(kwargs)


class NotificadorFalso:
    def __init__(self, resultado=None, erro=None):
        self.chamadas = []
        self._resultado = resultado if resultado is not None else {'sent': ['Telegram'], 'failed': []}
        self._erro = erro

    def send_trial_extended_notification(self, user, user_profile, novo_fim):
        self.chamadas.append((user, user_profile, novo_fim))
        if self._erro:
            raise self._erro
        return self._resultado


@pytest.fixture()
def painel(monkeypatch):
    """Instala os duplos e devolve o notificador, que é o que se inspeciona."""

    def instalar(notificador=None):
        from app import extensions
        from app.blueprints.api import decorators as decorators_module
        from app.blueprints.api import users as users_module

        class ServidorFalso:
            def get_user_by_id(self, user_id):
                return {"id": str(user_id), "username": "ana", "email": "ana@exemplo.test"}

            def unblock_user(self, user_id):
                return True

        servidor = ServidorFalso()
        notificador = notificador or NotificadorFalso()
        monkeypatch.setattr(decorators_module, 'media_server', servidor)
        monkeypatch.setattr(users_module.extensions, 'media_server', servidor, raising=False)
        monkeypatch.setattr(extensions, 'notifier_manager', notificador)
        monkeypatch.setattr(extensions, 'scheduler', AgendadorFalso())
        return notificador

    return instalar


def _perfil_em_teste(fim_do_teste):
    from app.extensions import db
    from app.models import UserProfile

    perfil = UserProfile(
        media_user_id="7",
        username="ana",
        email="ana@exemplo.test",
        status="active",
        trial_end_date=fim_do_teste.isoformat(),
    )
    db.session.add(perfil)
    db.session.commit()
    return perfil


class TestARotaAvisa:
    def test_estender_manda_a_notificacao_com_o_novo_fim(self, client, configurada, db_session, painel):
        notificador = painel()
        fim = datetime.now(timezone.utc) + timedelta(hours=2)
        _perfil_em_teste(fim)
        _autenticar(client)

        resposta = client.post('/api/users/extend-trial/7', json={'extend_minutes': 120})

        assert resposta.status_code == 200
        assert resposta.get_json()['success'] is True
        assert len(notificador.chamadas) == 1
        user, perfil, novo_fim = notificador.chamadas[0]
        assert user['username'] == 'ana'
        assert perfil['media_user_id'] == '7'
        # O que a pessoa precisa de saber é a data NOVA, não a que ficou para trás.
        assert novo_fim - fim == timedelta(minutes=120)

    def test_a_resposta_diz_que_o_utilizador_foi_avisado(self, client, configurada, db_session, painel):
        painel()
        _perfil_em_teste(datetime.now(timezone.utc) + timedelta(hours=1))
        _autenticar(client)

        corpo = client.post('/api/users/extend-trial/7', json={'extend_minutes': 60}).get_json()

        assert corpo['notificado'] is True

    def test_sem_canal_nenhum_a_resposta_nao_finge_que_avisou(self, client, configurada, db_session, painel):
        # Quem não registou contacto nenhum não recebe nada, e o administrador
        # tem de saber disso: a extensão foi feita, o aviso não saiu.
        painel(NotificadorFalso(resultado={'sent': [], 'failed': []}))
        _perfil_em_teste(datetime.now(timezone.utc) + timedelta(hours=1))
        _autenticar(client)

        corpo = client.post('/api/users/extend-trial/7', json={'extend_minutes': 60}).get_json()

        assert corpo['success'] is True
        assert corpo['notificado'] is False

    def test_uma_falha_a_avisar_nao_derruba_a_extensao(self, client, configurada, db_session, painel):
        from app.extensions import data_manager

        painel(NotificadorFalso(erro=RuntimeError("Telegram fora do ar")))
        _perfil_em_teste(datetime.now(timezone.utc) + timedelta(hours=1))
        _autenticar(client)

        resposta = client.post('/api/users/extend-trial/7', json={'extend_minutes': 60})

        assert resposta.status_code == 200
        assert resposta.get_json()['success'] is True
        assert resposta.get_json()['notificado'] is False
        # A extensão ficou mesmo gravada: é ela que interessa.
        assert data_manager.get_user_profile("7")['trial_job_id']


class TestATraducaoDaData:
    def test_a_mensagem_leva_a_HORA_e_nao_so_o_dia(self, app_context, monkeypatch):
        from app.services import notifier_manager as modulo

        gestor = modulo.NotifierManager()
        enviados = {}
        monkeypatch.setattr(
            gestor, '_prepare_and_send',
            lambda evento, user, perfil, contexto, **kw: enviados.update(
                {'evento': evento, 'contexto': contexto}))

        gestor.send_trial_extended_notification(
            {"username": "ana"}, {}, datetime(2026, 9, 20, 21, 30, tzinfo=timezone.utc))

        assert enviados['evento'] == 'trial_extended'
        # O fuso dos testes é UTC (ver o conftest), por isso a hora sai tal e qual.
        assert enviados['contexto']['new_date'] == '20/09/2026 às 21:30'

    def test_uma_data_impossivel_nao_rebenta(self, app_context, monkeypatch):
        from app.services import notifier_manager as modulo

        gestor = modulo.NotifierManager()
        enviados = {}
        monkeypatch.setattr(
            gestor, '_prepare_and_send',
            lambda evento, user, perfil, contexto, **kw: enviados.update({'contexto': contexto}))

        gestor.send_trial_extended_notification({"username": "ana"}, {}, "não é uma data")

        assert enviados['contexto']['new_date']


class TestOsTemplates:
    CANAIS = ('TELEGRAM', 'DISCORD', 'WEBHOOK', 'WHATSAPP')

    def test_todos_os_canais_tem_texto_padrao(self):
        from app.services.notifier_manager import DEFAULT_TEMPLATES

        em_falta = [c for c in self.CANAIS
                    if not DEFAULT_TEMPLATES.get(f'{c}_TRIAL_EXTENDED_MESSAGE_TEMPLATE')]

        assert em_falta == []

    def test_o_push_tem_as_duas_partes(self):
        # ⚠️ O sistema operativo trata o título e o corpo de forma diferente:
        # um push sem título não é meia notificação, é nenhuma.
        from app.services.notifier_manager import DEFAULT_TEMPLATES

        assert DEFAULT_TEMPLATES['PUSH_TRIAL_EXTENDED_TITLE_TEMPLATE']
        assert DEFAULT_TEMPLATES['PUSH_TRIAL_EXTENDED_MESSAGE_TEMPLATE']

    def test_o_json_dos_canais_que_o_usam_e_valido(self):
        import json

        from app.services.notifier_manager import DEFAULT_TEMPLATES

        for canal in ('DISCORD', 'WEBHOOK'):
            texto = DEFAULT_TEMPLATES[f'{canal}_TRIAL_EXTENDED_MESSAGE_TEMPLATE']
            # Os marcadores saem antes: o que se valida é a FORMA do payload.
            cru = texto.replace('{discord_user_id}', '1').replace('{username}', 'ana')
            cru = cru.replace('{new_date}', '20/09/2026').replace('{server_name}', 'Jellyfin')
            json.loads(cru)

    def test_estao_no_config_com_o_padrao(self, config_file, db_session):
        from app.config import load_or_create_config

        config = load_or_create_config()

        for canal in self.CANAIS:
            assert config.get(f'{canal}_TRIAL_EXTENDED_MESSAGE_TEMPLATE')
        assert config.get('PUSH_TRIAL_EXTENDED_TITLE_TEMPLATE')
        assert config.get('PUSH_TRIAL_EXTENDED_MESSAGE_TEMPLATE')

    def test_o_administrador_consegue_mesmo_grava_los(self, client, configurada, db_session):
        # ⚠️ A segunda lista: fora de `fields_to_update`, o texto reescrito na
        # página de Configurações é descartado em silêncio ao salvar.
        from app.config import load_or_create_config

        _autenticar(client)
        campos = {f'{c}_TRIAL_EXTENDED_MESSAGE_TEMPLATE': f'texto de {c}' for c in self.CANAIS}
        campos['PUSH_TRIAL_EXTENDED_TITLE_TEMPLATE'] = 'Teste estendido'
        campos['PUSH_TRIAL_EXTENDED_MESSAGE_TEMPLATE'] = 'Vai até {new_date}.'

        assert client.post('/api/system/settings', json=campos).status_code == 200

        config = load_or_create_config()
        for chave, valor in campos.items():
            assert config[chave] == valor

    def test_a_aba_de_notificacoes_tem_um_campo_para_cada_um(self):
        # O template padrão sem campo na interface é um texto que ninguém pode
        # mudar; o campo sem chave no `fieldMap` é uma caixa que nunca enche.
        from pathlib import Path

        html = Path('app/templates/settings/tabs/notifications.html').read_text(encoding='utf-8')
        js = Path('app/static/js/settings_modules/config.js').read_text(encoding='utf-8')

        chaves = [f'{c}_TRIAL_EXTENDED_MESSAGE_TEMPLATE' for c in self.CANAIS]
        chaves += ['PUSH_TRIAL_EXTENDED_TITLE_TEMPLATE', 'PUSH_TRIAL_EXTENDED_MESSAGE_TEMPLATE']

        assert [c for c in chaves if f'id="{c}"' not in html] == []
        assert [c for c in chaves if f"'{c}'" not in js] == []
