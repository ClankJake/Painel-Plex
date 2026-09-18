# tests/test_pagina_publica_de_pagamento.py

"""O que a página `/pay/<token>` diz sobre a conta de quem a abre.

🐛 **A quem estava em TESTE, ela dizia "Acesso Ativo".** Quem está em teste não
tem `expiration_date` — só `trial_end_date` —, por isso caía no ramo que existe
para um assinante em dia: uma etiqueta verde a dizer que está tudo bem, e por
baixo uma linha VAZIA (a do vencimento, preenchida a partir de um campo que ali
nunca existe), numa página cujo único botão é o de pagar. Era o contrário do
que a pessoa foi lá fazer, e não dizia sequer até quando o teste ia.

⚠️ E o estado seguinte mentia por outro lado: depois de o teste acabar e a conta
ser bloqueada, a página dizia **"Sua assinatura terminou"** a quem nunca assinou.

São quatro estados e não dois, e quem os decide é o SERVIDOR
(`estado_do_teste`, em `app/utils/periodo_de_teste.py`): assinante em dia, teste
a decorrer, teste terminado, e acesso suspenso — este último com duas frases,
conforme o que terminou tenha sido um teste ou uma assinatura.
"""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


def _sem_fuso(momento):
    """Como as colunas de data deste esquema guardam: UTC, sem sufixo."""
    return momento.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")


def _perfil(**campos):
    from app.extensions import db
    from app.models import UserProfile

    dados = {
        'media_user_id': '7',
        'username': 'ana',
        'email': 'ana@exemplo.test',
        'status': 'active',
        'payment_token': 'token-de-teste',
    }
    dados.update(campos)
    perfil = UserProfile(**dados)
    db.session.add(perfil)
    db.session.commit()
    return perfil


def _pagina(client):
    resposta = client.get('/pay/token-de-teste')
    assert resposta.status_code == 200
    return resposta.get_data(as_text=True)


class TestOHelper:
    """As duas perguntas que se pareciam e não são a mesma."""

    def test_o_teste_a_decorrer(self):
        from app.utils.periodo_de_teste import estado_do_teste, teste_a_decorrer

        perfil = {'trial_end_date': _sem_fuso(datetime.now(timezone.utc) + timedelta(hours=2))}

        assert estado_do_teste(perfil) == 'a_decorrer'
        assert teste_a_decorrer(perfil) is True

    def test_o_teste_que_ja_passou(self):
        from app.utils.periodo_de_teste import estado_do_teste, teste_a_decorrer

        perfil = {'trial_end_date': _sem_fuso(datetime.now(timezone.utc) - timedelta(hours=2))}

        assert estado_do_teste(perfil) == 'terminado'
        assert teste_a_decorrer(perfil) is False

    def test_quem_tem_vencimento_nao_e_um_teste(self):
        # ⚠️ Passou a assinante: o `trial_end_date` que ficou para trás é
        # história, a mesma regra que o `trial_sweep_job` já aplica.
        from app.utils.periodo_de_teste import estado_do_teste

        assert estado_do_teste({
            'trial_end_date': _sem_fuso(datetime.now(timezone.utc) + timedelta(hours=2)),
            'expiration_date': _sem_fuso(datetime.now(timezone.utc) + timedelta(days=30)),
        }) is None

    def test_sem_data_nenhuma(self):
        from app.utils.periodo_de_teste import estado_do_teste

        assert estado_do_teste({}) is None
        assert estado_do_teste(None) is None

    def test_uma_data_impossivel_nao_rebenta(self):
        from app.utils.periodo_de_teste import estado_do_teste, fim_do_teste

        assert fim_do_teste({'trial_end_date': 'não é uma data'}) is None
        assert estado_do_teste({'trial_end_date': 'não é uma data'}) is None

    def test_a_data_sem_fuso_e_lida_como_UTC(self):
        # ⚠️ Lê-la no fuso do sistema é o engano que já custou três horas em
        # cada data de vencimento. Num contentor sem TZ dava o mesmo resultado
        # por acaso; num com TZ=America/Sao_Paulo, o teste parecia durar mais
        # três horas do que durava.
        from app.utils.periodo_de_teste import fim_do_teste

        fim = fim_do_teste({'trial_end_date': '2026-09-20T21:30:00'})

        assert fim == datetime(2026, 9, 20, 21, 30, tzinfo=timezone.utc)

    def test_le_um_modelo_e_um_dicionario_da_mesma_maneira(self, app_context, db_session):
        # A página de pagamento tem o `UserProfile`; as rotas de administração
        # têm o dicionário do DataManager.
        from app.utils.periodo_de_teste import estado_do_teste

        perfil = _perfil(trial_end_date=_sem_fuso(datetime.now(timezone.utc) + timedelta(hours=2)))

        assert estado_do_teste(perfil) == 'a_decorrer'


class TestAPaginaEmPeriodoDeTeste:
    def test_nao_diz_acesso_ativo(self, client, configurada, db_session):
        _perfil(trial_end_date=_sem_fuso(datetime.now(timezone.utc) + timedelta(hours=2)))

        html = _pagina(client)

        assert 'Acesso Ativo' not in html
        assert 'Período de Teste' in html

    def test_diz_ate_quando_o_teste_vai_COM_a_hora(self, client, configurada, db_session):
        # ⚠️ Um teste mede-se em horas: "termina em 20/09/2026" não diz a quem
        # lê quando é que fica sem acesso.
        fim = datetime.now(timezone.utc) + timedelta(hours=5)
        _perfil(trial_end_date=_sem_fuso(fim))

        html = _pagina(client)

        assert 'Seu teste termina em' in html
        assert fim.strftime('%d/%m/%Y') in html
        assert 'às' in html

    def test_o_teste_ja_terminado_diz_que_terminou(self, client, configurada, db_session):
        # A conta ainda está ativa porque a tarefa datada corre a seguir (ou a
        # varredura de 15 em 15 minutos). Dizer "Acesso Ativo" aqui era
        # prometer o que está a acabar.
        _perfil(trial_end_date=_sem_fuso(datetime.now(timezone.utc) - timedelta(minutes=5)))

        html = _pagina(client)

        assert 'Período de Teste Encerrado' in html
        assert 'Seu teste terminou em' in html
        assert 'Acesso Ativo' not in html


class TestAPaginaDepoisDoTeste:
    def test_bloqueado_depois_do_teste_nao_fala_de_assinatura(self, client, configurada, db_session):
        # ⚠️ "Sua assinatura terminou" a quem nunca assinou.
        _perfil(status='inactive',
                trial_end_date=_sem_fuso(datetime.now(timezone.utc) - timedelta(hours=1)))

        html = _pagina(client)

        assert 'Período de Teste Encerrado' in html
        assert 'Sua assinatura terminou' not in html
        assert 'Seu período de teste terminou' in html

    def test_um_assinante_suspenso_continua_a_ver_o_texto_de_sempre(self, client, configurada, db_session):
        _perfil(status='inactive',
                expiration_date=_sem_fuso(datetime.now(timezone.utc) - timedelta(days=1)))

        html = _pagina(client)

        assert 'Acesso Suspenso' in html
        assert 'Sua assinatura terminou' in html
        assert 'Período de Teste' not in html


class TestQuemNaoEstaEmTeste:
    def test_o_assinante_em_dia_continua_a_ver_acesso_ativo(self, client, configurada, db_session):
        _perfil(expiration_date=_sem_fuso(datetime.now(timezone.utc) + timedelta(days=2)))

        html = _pagina(client)

        assert 'Acesso Ativo' in html
        assert 'Período de Teste' not in html
        # A linha do vencimento continua a ser preenchida pelo JavaScript.
        assert 'id="user-expiration"' in html

    def test_uma_conta_sem_datas_nenhumas_nao_inventa_um_teste(self, client, configurada, db_session):
        _perfil()

        html = _pagina(client)

        assert 'Acesso Ativo' in html
        assert 'Período de Teste' not in html
