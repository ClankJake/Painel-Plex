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
        assert 'aplicativo do Jellyfin' in pagina

    def test_o_passo_do_ecra_de_sucesso_tem_uma_versao_local(self, client, db_session, servidor):
        # 🐛 "Baixe o aplicativo Plex..." era mostrado nos DOIS servidores: quem
        # acabava de criar uma conta no Jellyfin era mandado buscar o Plex.
        servidor(cria_contas=True)

        pagina = client.get('/invite/ABC').get_data(as_text=True)

        assert 'data-i18n-step1-onboarding-local' in pagina
        assert 'Instale o aplicativo do Jellyfin' in pagina


class TestOComoComecar:
    """🐛 A secção mostrava "undefined", duas vezes, em vez das instruções.

    `data-i18n-step-local-1` chega ao `dataset` como `i18nStepLocal-1` — o
    browser só come o traço quando o que vem a seguir é uma letra minúscula — e
    o JavaScript pedia `stepLocal1`. A regra vive agora num teste que percorre
    todos os templates (`tests/test_assets_frontend.py`); aqui guarda-se o que a
    pessoa lê.
    """

    def test_os_tres_passos_estao_la(self, client, db_session, servidor):
        servidor(cria_contas=True)

        pagina = client.get('/invite/ABC').get_data(as_text=True)

        for chave in ('data-i18n-step-local-one', 'data-i18n-step-local-two',
                      'data-i18n-step-local-three'):
            assert chave in pagina

    def test_dizem_o_que_e_preciso_fazer(self, client, db_session, servidor):
        servidor(cria_contas=True)

        pagina = client.get('/invite/ABC').get_data(as_text=True)

        # Escolher as credenciais, guardá-las (não se recuperam) e onde entrar.
        assert 'Escolha abaixo o usuário e a senha' in pagina
        assert 'não consegue recuperá-las' in pagina
        assert 'Instale o aplicativo do Jellyfin' in pagina

    def test_explicam_primeiro_o_que_e_o_servidor(self, client, db_session, servidor):
        # A versão do Plex tinha um "O que é o Plex?"; a de contas locais não
        # tinha nada — quem chega por um link de um amigo não sabe o que é isto.
        servidor(cria_contas=True)

        pagina = client.get('/invite/ABC').get_data(as_text=True)

        assert 'data-i18n-what-is-local' in pagina
        assert 'Sua conta é criada aqui mesmo' in pagina

    def test_o_javascript_nao_pede_nada_que_o_template_nao_envie(self, client, db_session, servidor):
        """A cadeia inteira: atributo → `dataset` do browser → chave do `invite.js`.

        ⚠️ A conversão é feita aqui como o BROWSER a faz — o traço só desaparece
        quando o que vem a seguir é uma letra minúscula. Derivar a chave "como
        seria de esperar" faria este teste passar sobre o bug que ele devia
        apanhar.
        """
        import re
        from pathlib import Path

        servidor(cria_contas=True)
        pagina = client.get('/invite/ABC').get_data(as_text=True)

        def do_dataset(atributo):
            """`data-i18n-step-local-one` -> `i18nStepLocalOne`."""
            return re.sub(r'-([a-z])', lambda m: m.group(1).upper(), atributo[len('data-'):])

        def do_invite_js(chave):
            """O que o `chaveEmCamelCase(chave, 4)` produz."""
            return chave[4].lower() + re.sub(r'-(\w)', lambda m: m.group(1).upper(), chave[5:])

        enviadas = {do_invite_js(do_dataset(a))
                    for a in re.findall(r'(data-i18n-[a-z0-9-]+)=', pagina)}

        js = (Path(__file__).resolve().parent.parent / 'app' / 'static' / 'js' / 'invite.js').read_text(encoding='utf-8')
        pedidas = (set(re.findall(r"texto\('([a-zA-Z0-9]+)'", js))
                   | set(re.findall(r"i18n\.([a-zA-Z0-9]+)", js)))

        assert pedidas - enviadas == set()


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
