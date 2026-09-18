# tests/test_aba_sobre.py

"""A aba "Sobre": a versão que está rodando, e o fuso em que ela corre.

⚠️ **As duas perguntas não tinham resposta dentro do painel.** As entregas
saem por releases do GitHub e nada no código registava a versão — quem reportava
um problema tinha de a adivinhar pela data da imagem. E o fuso já custou um bug
de três horas em cada data de vencimento (ver `_momento_do_vencimento`, em
`api/users.py`): perceber que o contentor corria em UTC exigia entrar nele.

O que se prende aqui são as decisões que não se veem no ecrã:

- ⚠️ **a versão compara-se como NÚMERO**, nunca como texto: `'22.10' < '22.3'`
  em ordem alfabética, por isso o painel diria "está atualizado" no dia em que
  saísse a 22.10;
- ⚠️ **não conseguir falar com o GitHub responde 200**, e diz "não foi possível
  verificar" — que é diferente de "está atualizado". Há painéis sem rede de
  saída, e um 500 pintava de vermelho uma informação que é um extra;
- ⚠️ **a falha NÃO fica em cache**: guardar um "não sei" por seis horas deixava
  o painel a dizer que não consegue verificar muito depois de a rede ter
  voltado. É a mesma regra da deteção dos plugins do Jellyfin;
- 🛡️ **o endereço da release vem de FORA** e acaba num `href` na página de um
  administrador: só se aceita um `https://github.com/...`, e tudo o que se
  escreve com ele passa por `escapeHTML`.
"""

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
TEMPLATE_ABA = (RAIZ / 'app/templates/settings/tabs/about.html').read_text(encoding='utf-8')
TEMPLATE_PAGINA = (RAIZ / 'app/templates/settings.html').read_text(encoding='utf-8')
SCRIPT = (RAIZ / 'app/static/js/settings_modules/about.js').read_text(encoding='utf-8')
UI = (RAIZ / 'app/static/js/settings_modules/ui.js').read_text(encoding='utf-8')
HANDLERS = (RAIZ / 'app/static/js/settings_modules/handlers.js').read_text(encoding='utf-8')
API_JS = (RAIZ / 'app/static/js/settings_modules/api.js').read_text(encoding='utf-8')


def _sem_comentarios_js(texto):
    """O JavaScript sem comentários.

    ⚠️ Não é preciosismo: os comentários deste projeto CITAM o código errado
    para explicar porque é que ele o é (`bg-${cor}-50`). Uma varredura que os
    leia acusa a explicação de ser o problema que ela descreve.
    """
    texto = re.sub(r'/\*.*?\*/', '', texto, flags=re.S)
    return re.sub(r'^\s*//.*$', '', texto, flags=re.M)


CODIGO = _sem_comentarios_js(SCRIPT)


def _chave_do_dataset(atributo, prefixo):
    """`data-i18n-sobre-nova-versao` -> `sobreNovaVersao`.

    Repete o que o browser faz (come o traço antes de uma letra minúscula) e o
    que o `lerConfiguracaoDoScript` faz a seguir (corta o prefixo). É por aqui
    que a chave tem de passar para o script a encontrar — e é a armadilha que
    já deu `undefined` por extenso na página de convite.
    """
    camel = re.sub(r'-([a-z])', lambda m: m.group(1).upper(), atributo)
    resto = camel[len(prefixo):]
    return resto[0].lower() + resto[1:]


class TestAVersaoInstalada:
    def test_o_painel_sabe_dizer_a_versao(self):
        from app.versao import VERSAO

        assert re.fullmatch(r'\d+(\.\d+)*', VERSAO), VERSAO

    def test_o_package_json_diz_a_mesma(self):
        # ⚠️ São dois ficheiros com a mesma verdade. Divergirem é pior do que
        # não haver versão nenhuma, porque um deles passa a mentir — e é este
        # teste que obriga a mudar os dois ao publicar uma release.
        from app.versao import VERSAO

        package = json.loads((RAIZ / 'package.json').read_text(encoding='utf-8'))

        assert package['version'] == VERSAO


class TestAComparacaoDeVersoes:
    """🐛 Comparadas como TEXTO, elas trocam de ordem exatamente quando importa."""

    def test_a_ordem_e_numerica_e_nao_alfabetica(self):
        from app.versao import comparar

        assert comparar('22.10', '22.3') == 1
        assert comparar('22.3', '22.10') == -1

    def test_o_v_da_etiqueta_nao_conta(self):
        from app.versao import comparar

        assert comparar('v22.3', '22.3') == 0

    def test_os_pedacos_em_falta_valem_zero(self):
        from app.versao import comparar

        assert comparar('22.3', '22.3.0') == 0
        assert comparar('22.3.1', '22.3') == 1

    def test_ha_atualizacao_so_para_a_frente(self):
        from app.versao import ha_atualizacao

        assert ha_atualizacao('22.3', 'v22.4') is True
        assert ha_atualizacao('22.3', 'v22.3') is False
        assert ha_atualizacao('22.4', 'v22.3') is False

    def test_uma_versao_publicada_ilegivel_nao_e_uma_atualizacao(self):
        # ⚠️ Anunciar uma versão que não existe manda alguém procurar o que não
        # há. Ficar calado só adia uma boa notícia.
        from app.versao import ha_atualizacao

        assert ha_atualizacao('22.3', '') is False
        assert ha_atualizacao('22.3', None) is False


pytestmark_integracao = pytest.mark.integration


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")


def _autenticar(client, admin=True):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {
            "id": "1", "username": "dono", "email": "a@b.test",
            "role": "admin" if admin else "user",
        }
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True


@pytest.fixture(autouse=True)
def _cache_limpa():
    """A cache vive no processo: sem isto, um teste herdava o que outro leu."""
    from app.services import atualizacoes

    atualizacoes.limpar_cache()
    yield
    atualizacoes.limpar_cache()


@pytest.mark.integration
class TestARotaSobre:
    def test_responde_com_a_versao_e_o_fuso(self, client, configurada, db_session):
        from app.versao import VERSAO

        _autenticar(client)

        corpo = client.get('/api/system/about').get_json()

        assert corpo['success'] is True
        assert corpo['versao'] == VERSAO
        assert corpo['fuso']['nome']
        assert corpo['fuso']['agora']
        assert corpo['url_das_releases'].startswith('https://github.com/')

    def test_diz_de_onde_veio_o_fuso(self, client, configurada, db_session, monkeypatch):
        # ⚠️ "Veio da variável TZ" e "foi detectado do sistema" são coisas
        # diferentes de corrigir, e é por isso que a aba as distingue.
        _autenticar(client)
        monkeypatch.setenv('TZ', 'America/Sao_Paulo')

        fuso = client.get('/api/system/about').get_json()['fuso']

        assert fuso['origem'] == 'TZ'
        assert fuso['nome'] == 'America/Sao_Paulo'

    def test_sem_a_variavel_diz_que_foi_detectado(self, client, configurada, db_session, monkeypatch):
        _autenticar(client)
        monkeypatch.delenv('TZ', raising=False)

        assert client.get('/api/system/about').get_json()['fuso']['origem'] == 'sistema'

    def test_um_fuso_impossivel_nao_derruba_a_aba(self, client, configurada, db_session, monkeypatch):
        # O que se perde é a hora de exemplo, não a versão nem o diagnóstico.
        _autenticar(client)
        monkeypatch.setenv('TZ', 'Nao/Existe')

        corpo = client.get('/api/system/about').get_json()

        assert corpo['success'] is True
        assert corpo['fuso']['agora']

    def test_nao_e_para_quem_nao_administra(self, client, configurada, db_session):
        from app.extensions import db
        from app.models import UserProfile

        db.session.add(UserProfile(media_user_id="1", username="dono", status="active"))
        db.session.commit()
        _autenticar(client, admin=False)

        assert client.get('/api/system/about').status_code in (302, 403)


class GitHubFalso:
    """Um duplo do `requests.get` que conta quantas vezes foi chamado."""

    def __init__(self, dados=None, erro=None):
        self.chamadas = 0
        self._dados = dados
        self._erro = erro

    def __call__(self, url, **kwargs):
        self.chamadas += 1
        if self._erro:
            raise self._erro
        return RespostaFalsa(self._dados)


class RespostaFalsa:
    def __init__(self, dados):
        self._dados = dados

    def raise_for_status(self):
        pass

    def json(self):
        return self._dados


def _release(tag='v99.0', url='https://github.com/ClankJake/Painel-Plex/releases/tag/v99.0'):
    return {
        'tag_name': tag,
        'name': f'Versão {tag}',
        'html_url': url,
        'published_at': '2026-09-12T20:23:28Z',
    }


@pytest.mark.integration
class TestARotaDaUltimaRelease:
    def _instalar(self, monkeypatch, duplo):
        from app.services import atualizacoes

        monkeypatch.setattr(atualizacoes.requests, 'get', duplo)
        return duplo

    def test_diz_quando_ha_versao_nova(self, client, configurada, db_session, monkeypatch):
        self._instalar(monkeypatch, GitHubFalso(_release('v99.0')))
        _autenticar(client)

        corpo = client.get('/api/system/latest-release').get_json()

        assert corpo['disponivel'] is True
        assert corpo['versao'] == '99.0'
        assert corpo['ha_atualizacao'] is True

    def test_diz_quando_esta_atualizado(self, client, configurada, db_session, monkeypatch):
        from app.versao import VERSAO

        self._instalar(monkeypatch, GitHubFalso(_release(f'v{VERSAO}')))
        _autenticar(client)

        corpo = client.get('/api/system/latest-release').get_json()

        assert corpo['disponivel'] is True
        assert corpo['ha_atualizacao'] is False

    def test_sem_rede_responde_200_a_dizer_que_nao_sabe(self, client, configurada, db_session, monkeypatch):
        # ⚠️ Um 500 aqui pintava de vermelho a aba inteira por causa de uma
        # informação que é um extra — e há painéis sem rede de saída.
        self._instalar(monkeypatch, GitHubFalso(erro=OSError("sem rede")))
        _autenticar(client)

        resposta = client.get('/api/system/latest-release')

        assert resposta.status_code == 200
        assert resposta.get_json()['disponivel'] is False
        assert resposta.get_json()['versao_instalada']

    def test_uma_release_sem_etiqueta_vale_por_nao_saber(self, client, configurada, db_session, monkeypatch):
        self._instalar(monkeypatch, GitHubFalso({'html_url': 'https://github.com/x'}))
        _autenticar(client)

        assert client.get('/api/system/latest-release').get_json()['disponivel'] is False

    def test_um_endereco_que_nao_e_do_github_e_trocado(self, client, configurada, db_session, monkeypatch):
        # 🛡️ Ele vem de fora e vai parar a um `href` na página do administrador.
        self._instalar(monkeypatch, GitHubFalso(_release(url='https://algum-site.test/isto')))
        _autenticar(client)

        assert client.get('/api/system/latest-release').get_json()['url'].startswith(
            'https://github.com/')

    def test_a_resposta_fica_em_cache(self, client, configurada, db_session, monkeypatch):
        duplo = self._instalar(monkeypatch, GitHubFalso(_release()))
        _autenticar(client)

        client.get('/api/system/latest-release')
        client.get('/api/system/latest-release')

        assert duplo.chamadas == 1

    def test_a_falha_NAO_fica_em_cache(self, client, configurada, db_session, monkeypatch):
        # ⚠️ Guardar um "não sei" por seis horas deixava o painel a dizer que
        # não consegue verificar muito depois de a rede ter voltado.
        duplo = self._instalar(monkeypatch, GitHubFalso(erro=OSError("sem rede")))
        _autenticar(client)

        client.get('/api/system/latest-release')
        client.get('/api/system/latest-release')

        assert duplo.chamadas == 2

    def test_forcar_salta_a_cache(self, client, configurada, db_session, monkeypatch):
        duplo = self._instalar(monkeypatch, GitHubFalso(_release()))
        _autenticar(client)

        client.get('/api/system/latest-release')
        client.get('/api/system/latest-release?forcar=1')

        assert duplo.chamadas == 2

    def test_nao_e_para_quem_nao_administra(self, client, configurada, db_session, monkeypatch):
        from app.extensions import db
        from app.models import UserProfile

        duplo = self._instalar(monkeypatch, GitHubFalso(_release()))
        db.session.add(UserProfile(media_user_id="1", username="dono", status="active"))
        db.session.commit()
        _autenticar(client, admin=False)

        assert client.get('/api/system/latest-release').status_code in (302, 403)
        assert duplo.chamadas == 0


class TestOEnderecoDoRepositorioNaoVemDoConfig:
    """🛡️ O painel faz este pedido de DENTRO da rede onde corre.

    Um endereço vindo do config.json fazia de uma sessão de administrador
    tomada uma forma de apontar o painel a um serviço interno — o mesmo SSRF que
    a lista de serviços de push e a de hosts de imagem existem para fechar.
    """

    def test_o_repositorio_e_uma_constante_do_codigo(self):
        from app import versao

        assert versao.REPOSITORIO
        assert 'REPOSITORIO' not in (RAIZ / 'app/config.py').read_text(encoding='utf-8')

    def test_a_api_consultada_e_a_do_github(self):
        from app.services.atualizacoes import URL_DA_API

        assert URL_DA_API.startswith('https://api.github.com/repos/')


class TestOContratoDaAba:
    def test_a_aba_esta_ligada_a_pagina(self):
        assert 'data-tab="sobre"' in TEMPLATE_PAGINA
        assert 'settings/tabs/about.html' in TEMPLATE_PAGINA
        assert 'id="tab-sobre"' in TEMPLATE_ABA

    def test_a_aba_declara_se_somente_leitura(self):
        # Não há nada para salvar aqui, e o botão verde a pairar por cima só
        # levantava a pergunta do que é que ele faria.
        assert 'data-somente-leitura="true"' in TEMPLATE_ABA

    def test_e_carregada_ao_ABRIR_a_aba(self):
        ui = _sem_comentarios_js(UI)
        assert "tabId === 'sobre'" in ui
        assert 'carregarSobre' in ui

    def test_os_ouvintes_sao_ligados_na_inicializacao(self):
        handlers = _sem_comentarios_js(HANDLERS)
        assert 'ligarOuvintesDoSobre' in handlers

    def test_as_duas_rotas_estao_no_modulo_de_api(self):
        assert 'getAbout' in API_JS
        assert 'getLatestRelease' in API_JS

    def test_todos_os_ganchos_que_o_script_procura_existem_no_template(self):
        ids = set(re.findall(r"getElementById\('([a-z-]+)'\)", CODIGO))
        ids |= set(re.findall(r"escrever\('([a-z-]+)'", CODIGO))
        do_template = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', TEMPLATE_ABA))

        em_falta = sorted(i for i in ids if i.startswith('sobre-') and i not in do_template)

        assert em_falta == [], f"o script procura ganchos que o template não tem: {em_falta}"

    def test_toda_a_chave_i18n_pedida_existe_no_template(self):
        pedidas = set(re.findall(r'i18n\.([A-Za-z0-9_]+)', CODIGO))
        definidas = {
            _chave_do_dataset(atributo, 'i18n')
            for atributo in re.findall(r'data-(i18n-[a-z0-9-]+)=', TEMPLATE_PAGINA)
        }

        em_falta = sorted(c for c in pedidas if c.startswith('sobre') and c not in definidas)

        assert em_falta == [], f"chaves sem texto no template: {em_falta}"

    def test_toda_a_url_pedida_existe_no_template(self):
        pedidas = set(re.findall(r'urls\.([A-Za-z0-9_]+)', CODIGO))
        definidas = {
            _chave_do_dataset(atributo, 'urls')
            for atributo in re.findall(r'data-(urls-[a-z0-9-]+)=', TEMPLATE_PAGINA)
        }

        assert sorted(pedidas - definidas) == []


@pytest.mark.integration
class TestAPaginaRenderiza:
    """⚠️ Um `url_for` com o nome errado não falha no teste da rota: falha na
    PÁGINA, e leva consigo as Configurações inteiras. É por isso que a aba
    nova se prova a partir do HTML servido, e não só do ficheiro no disco."""

    def test_as_configuracoes_abrem_com_a_aba_nova(self, client, configurada, db_session, monkeypatch):
        from app.blueprints import main as main_module

        class ServidorFalso:
            SERVER_TYPE = 'plex'
            SHORT_NAME = 'Plex'

            def __getattr__(self, nome):
                return lambda *a, **k: None

        monkeypatch.setattr(main_module, 'media_server', ServidorFalso(), raising=False)
        _autenticar(client)

        resposta = client.get('/settings')

        assert resposta.status_code == 200
        html = resposta.get_data(as_text=True)
        assert 'id="tab-sobre"' in html
        assert 'data-urls-about=' in html
        assert 'data-urls-latest-release=' in html


class TestOQueVemDoGitHubEEscapado:
    """🛡️ O nome e a etiqueta são escritos por quem publica a release."""

    def test_o_script_importa_o_escapador_partilhado(self):
        assert re.search(r"import \{[^}]*\bescapeHTML\b[^}]*\} from '\.\./utils\.js'", SCRIPT)

    def test_nada_da_release_entra_cru_na_marcacao(self):
        # Cada interpolação de um campo da release dentro de marcação tem de
        # trazer o `escapeHTML` à volta — é a mesma varredura que o
        # `test_escape_de_nomes_no_javascript` faz aos nomes de utilizador.
        campos = ('dados.nome', 'dados.etiqueta', 'dados.versao', 'dados.url', 'url')
        for linha in CODIGO.splitlines():
            if '${' not in linha or '<' not in linha:
                continue
            for campo in campos:
                if f'${{{campo}' in linha:
                    assert 'escapeHTML' in linha, linha.strip()

    def test_as_classes_do_tailwind_nao_sao_montadas(self):
        # ⚠️ O compilador procura nomes de classes LITERAIS: uma classe montada
        # em tempo de execução nunca chega ao CSS, e o cartão ficava sem cor.
        assert not re.search(r'(bg|text|border)-\$\{', CODIGO)


class TestAPublicacaoDaImagem:
    """A imagem do Docker sai por RELEASE, e a versão dela é a da tag.

    ⚠️ **Antes ela era publicada a cada push para a branch `stable`**, que é a
    tag que o README manda pôr no docker-compose: quem reiniciasse o contentor
    a meio de um dia de trabalho levava com o que estava a ser feito, e não com
    uma versão que alguém decidiu publicar. Hoje a imagem só se move quando se
    cria uma tag `v*` — a mesma que a aba "Sobre" lê como "a última release".

    🐛 **E é por isso que o build confere a versão.** Uma tag `v22.4` sobre um
    código que ainda diz `22.3` publicava uma imagem que mente sobre si própria,
    e o sintoma não aparecia no build: aparecia meses depois, em cada painel
    atualizado, a anunciar para sempre uma atualização que já está instalada.

    📌 O workflow é lido como TEXTO, e não com um parser de YAML, para não
    acrescentar uma dependência ao projeto por causa de um teste — é o que o
    `test_referencias_do_javascript.py` já faz com o outro workflow.
    """

    @property
    def _workflow(self):
        return (RAIZ / '.github/workflows/docker-publish.yml').read_text(encoding='utf-8')

    def _sem_comentarios(self, texto):
        # Os comentários deste projeto CITAM o que está errado para explicar
        # porquê ("corria a cada push para a branch stable"), e uma varredura
        # que os leia acusa a própria explicação.
        return re.sub(r'^\s*#.*$', '', texto, flags=re.M)

    def test_dispara_com_uma_tag_de_release(self):
        gatilhos = self._sem_comentarios(self._workflow)

        assert re.search(r'push:\s*\n\s*tags:\s*\n\s*-\s*[\'"]v\*[\'"]', gatilhos), (
            'O workflow deixou de disparar nas tags `v*`.'
        )

    def test_NAO_dispara_a_cada_push_para_a_branch(self):
        gatilhos = self._sem_comentarios(self._workflow)

        assert 'branches:' not in gatilhos, (
            'O workflow voltou a publicar a imagem a cada push de branch. '
            'A imagem que o README manda usar não pode mudar fora de uma release.'
        )

    def test_continua_a_publicar_a_tag_que_o_README_manda_usar(self):
        # ⚠️ Deixar de a publicar não parte o build: parte as instalações que
        # já existem, em silêncio, e só se nota quando alguém repara que o
        # painel não atualiza há meses.
        readme = (RAIZ / 'README.md').read_text(encoding='utf-8')
        etiqueta = re.search(r'ghcr\.io/clankjake/painel-plex:([a-z0-9._-]+)', readme)
        assert etiqueta, 'O README deixou de dizer que imagem usar.'

        assert f'value={etiqueta.group(1)}' in self._workflow, (
            f"O README manda usar a tag '{etiqueta.group(1)}' e o workflow já não a publica."
        )

    def test_o_build_confere_a_versao_do_codigo_contra_a_tag(self):
        workflow = self._sem_comentarios(self._workflow)

        assert 'app/versao.py' in workflow and 'package.json' in workflow, (
            'O passo que compara a tag com a versão do código desapareceu. '
            'Sem ele, uma release publica uma imagem que mente sobre a versão '
            'que tem lá dentro — e isso só se vê no painel de quem instalou.'
        )
