# tests/test_pagina_de_convite.py

"""A página pública onde um convite é resgatado.

Ela serve dois fluxos que não se parecem: no Plex a pessoa TRAZ a conta dela e o
painel convida-a; num servidor de contas locais a conta NASCE aqui, com um
utilizador e uma palavra-passe que a pessoa escolhe no momento — e que ninguém
pode recuperar depois.

O segundo fluxo é novo, e o que estes testes guardam são as arestas que ficaram
por acertar quando ele foi acrescentado a uma página escrita só para o primeiro.
"""

import pytest

pytestmark = pytest.mark.integration


class BackendFalso:
    def __init__(self, cria_contas):
        from app.services.media_server.base import MediaServerCapabilities

        self.SERVER_TYPE = 'jellyfin' if cria_contas else 'plex'
        self.DISPLAY_NAME = 'Jellyfin' if cria_contas else 'Plex Media Server'
        self.SHORT_NAME = 'Jellyfin' if cria_contas else 'Plex'
        self.CAPABILITIES = MediaServerCapabilities(
            convites_nativos=not cria_contas, cria_contas=cria_contas,
            fontes_media_online=not cria_contas, login_delegado=not cria_contas,
            desativa_conta=cria_contas, links_profundos=True,
            estatisticas=True, estatisticas_externas=not cria_contas,
        )

    @property
    def capabilities(self):
        return self.CAPABILITIES

    def estatisticas_disponiveis(self):
        return False


@pytest.fixture()
def servidor(monkeypatch):
    def instalar(cria_contas):
        from app import extensions
        from app.blueprints import main as main_module
        from app.blueprints.api import invites as invites_module

        backend = BackendFalso(cria_contas)
        monkeypatch.setattr(extensions, 'media_server', backend)
        monkeypatch.setattr(main_module, 'media_server', backend, raising=False)
        monkeypatch.setattr(invites_module, 'media_server', backend, raising=False)
        return backend

    return instalar


@pytest.fixture(autouse=True)
def configurada(config_file):
    return config_file(IS_CONFIGURED=True)


@pytest.fixture(autouse=True)
def sem_rate_limit():
    from app.extensions import limiter

    limiter.reset()
    yield
    limiter.reset()


class TestOsTextosSeguemOServidor:
    """⚠️ A marca não pode estar escrita à mão numa página que serve os dois."""

    def test_num_servidor_de_contas_locais_os_passos_falam_dele(self, client, db_session, servidor):
        servidor(cria_contas=True)

        pagina = client.get('/invite/ABC').get_data(as_text=True)

        assert 'conta no Jellyfin' in pagina
        assert 'aplicação do Jellyfin' in pagina

    def test_o_passo_do_ecra_de_sucesso_tem_uma_versao_local(self, client, db_session, servidor):
        # 🐛 "Baixe o aplicativo Plex..." era mostrado nos DOIS servidores: quem
        # acabava de criar uma conta no Jellyfin era mandado buscar o Plex.
        servidor(cria_contas=True)

        pagina = client.get('/invite/ABC').get_data(as_text=True)

        assert 'data-i18n-step1-onboarding-local' in pagina
        assert 'Instale a aplicação do Jellyfin' in pagina


class TestOFormularioDeRegisto:
    def test_avisa_que_a_palavra_passe_nao_se_recupera(self, client, db_session, servidor):
        # O painel não a guarda nem tem como a repor: dizê-lo antes vale mais do
        # que qualquer mensagem de erro depois.
        servidor(cria_contas=True)

        pagina = client.get('/invite/ABC').get_data(as_text=True)

        assert 'data-i18n-password-help' in pagina
        assert 'não pode ser recuperada' in pagina

    def test_tem_como_mostrar_o_que_se_escreveu(self, client, db_session, servidor):
        servidor(cria_contas=True)

        pagina = client.get('/invite/ABC').get_data(as_text=True)

        assert 'data-i18n-show-password' in pagina
        assert 'data-i18n-hide-password' in pagina


class TestOResgateLimitaOQueAceita:
    """🛡️ Rota PÚBLICA: o que aqui chega cria uma conta no servidor de média."""

    def _resgatar(self, client, **campos):
        dados = {'code': 'ABC', 'username': 'ana', 'password': 'segredo123'}
        dados.update(campos)
        return client.post('/api/invites/claim', json=dados)

    def test_um_utilizador_enorme_e_recusado(self, client, db_session, servidor):
        servidor(cria_contas=True)

        resposta = self._resgatar(client, username='u' * 5000)

        assert resposta.status_code == 400
        assert 'longos' in resposta.get_json()['message']

    def test_uma_palavra_passe_enorme_e_recusada(self, client, db_session, servidor):
        servidor(cria_contas=True)

        assert self._resgatar(client, password='p' * 100000).status_code == 400

    def test_um_email_enorme_e_recusado(self, client, db_session, servidor):
        # Ia parar ao perfil, e a verificação anti-abuso de testes lê-o.
        servidor(cria_contas=True)

        assert self._resgatar(client, email='e' * 5000 + '@exemplo.test').status_code == 400

    def test_uma_palavra_passe_curta_continua_a_ser_recusada(self, client, db_session, servidor):
        servidor(cria_contas=True)

        assert self._resgatar(client, password='123').status_code == 400


class TestOConviteExpirado:
    """🐛 O aviso existe para a pessoa não fazer trabalho em vão.

    Só desativava o botão do Plex. Num servidor de contas locais não há botão
    nenhum — há um FORMULÁRIO — e ele ficava a funcionar: a pessoa escolhia
    utilizador e palavra-passe, submetia, e só então descobria que o convite
    tinha expirado.

    O comportamento é do `invite.js` e não há aqui runner de JavaScript; o que
    se guarda é que os dois casos continuam a ser tratados no mesmo sítio.
    """

    def _invite_js(self):
        from pathlib import Path

        raiz = Path(__file__).resolve().parent.parent
        return (raiz / 'app' / 'static' / 'js' / 'invite.js').read_text(encoding='utf-8')

    def test_desativar_o_resgate_trata_do_botao_e_do_formulario(self):
        codigo = self._invite_js()

        inicio = codigo.index('function desativarResgate()')
        corpo = codigo[inicio:codigo.index('\n}', inicio)]

        assert "getElementById('login-button')" in corpo
        assert "getElementById('register-form')" in corpo
        assert 'disabled = true' in corpo

    def test_o_ramo_do_convite_expirado_usa_o(self):
        codigo = self._invite_js()

        assert 'desativarResgate();' in codigo
