# tests/test_aba_de_auditoria.py

"""A aba de Auditoria: o contrato entre o template, o script e a rota.

O comportamento vive no navegador e não há aqui um. O que se pode prender — e é
onde os enganos deste projeto aconteceram — são os CONTRATOS: os ganchos que o
template oferece e o script procura, os rótulos que o script pede e o template
tem de definir, e as ações que o backend grava e o filtro tem de saber nomear.

⚠️ É a armadilha que já deu `undefined` por extenso na página de convite: o
script pede uma chave, o template não a tem, e a pessoa lê o buraco. Aqui o
buraco seria pior — uma linha de auditoria sem dizer o que aconteceu.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
TEMPLATE_ABA = (RAIZ / 'app/templates/settings/tabs/audit.html').read_text(encoding='utf-8')
TEMPLATE_PAGINA = (RAIZ / 'app/templates/settings.html').read_text(encoding='utf-8')
SCRIPT = (RAIZ / 'app/static/js/settings_modules/audit.js').read_text(encoding='utf-8')
UI = (RAIZ / 'app/static/js/settings_modules/ui.js').read_text(encoding='utf-8')
HANDLERS = (RAIZ / 'app/static/js/settings_modules/handlers.js').read_text(encoding='utf-8')
API_JS = (RAIZ / 'app/static/js/settings_modules/api.js').read_text(encoding='utf-8')
SYSTEM_PY = (RAIZ / 'app/blueprints/api/system.py').read_text(encoding='utf-8')


def _sem_comentarios_js(texto):
    """O JavaScript sem comentários.

    ⚠️ Não é um preciosismo: os comentários deste projeto CITAM o código errado
    para explicar porque é que ele está errado (`bg-${cor}-100`, "o botão
    Limpar"). Uma varredura que os leia acusa exatamente a explicação de ser o
    problema que ela descreve — foi o que aconteceu ao escrever estes testes.
    """
    texto = re.sub(r'/\*.*?\*/', '', texto, flags=re.S)
    return re.sub(r'^\s*//.*$', '', texto, flags=re.M)


def _sem_comentarios_jinja(texto):
    """O template sem os blocos `{# ... #}`, pela mesma razão."""
    return re.sub(r'\{#.*?#\}', '', texto, flags=re.S)


CODIGO = _sem_comentarios_js(SCRIPT)
ABA_VISIVEL = _sem_comentarios_jinja(TEMPLATE_ABA)


def _chave_do_dataset(atributo):
    """`data-i18n-audit-ver-detalhes` -> `auditVerDetalhes`.

    Repete a conversão que o browser faz (come o traço antes de uma letra) e
    a que o `config.js` faz a seguir (corta o prefixo). É por aqui que a chave
    tem de passar para o script a encontrar.
    """
    camel = re.sub(r'-([a-z])', lambda m: m.group(1).upper(), atributo)
    return camel[4].lower() + camel[5:]


class TestOContratoEntreOTemplateEOScript:
    def test_todas_as_chaves_i18n_usadas_existem(self):
        oferecidas = {
            _chave_do_dataset(a)
            for a in re.findall(r'\bdata-(i18n-[a-z0-9-]+)=', TEMPLATE_PAGINA)
        }
        # Só o que é lido como `i18n.x`. As chaves dos rótulos entram pela
        # tabela ROTULOS e têm um teste próprio, mais abaixo.
        usadas = set(re.findall(r'\bi18n\.([A-Za-z0-9_]+)', CODIGO))

        em_falta = sorted(usadas - oferecidas)
        assert em_falta == [], (
            f"o audit.js pede chaves que o settings.html não define: {em_falta}"
        )

    def test_a_url_da_rota_existe(self):
        oferecidas = {
            _chave_do_dataset(a)
            for a in re.findall(r'\bdata-(urls-[a-z0-9-]+)=', TEMPLATE_PAGINA)
        }
        assert 'auditLogs' in oferecidas
        assert 'urls.auditLogs' in API_JS


class TestOsGanchosDoTemplate:
    """Os ids que o script procura têm de existir, e vice-versa."""

    @pytest.mark.parametrize('gancho', [
        'audit-list', 'audit-action-filter', 'audit-refresh',
        'audit-load-more', 'audit-count',
    ])
    def test_o_gancho_existe_no_template(self, gancho):
        assert f'id="{gancho}"' in ABA_VISIVEL
        assert f"'{gancho}'" in CODIGO

    def test_a_aba_esta_ligada_a_pagina(self):
        assert 'data-tab="auditoria"' in TEMPLATE_PAGINA
        assert 'settings/tabs/audit.html' in TEMPLATE_PAGINA
        # O conteúdo tem de ter o id que a navegação por abas monta (`tab-` + id).
        assert 'id="tab-auditoria"' in ABA_VISIVEL

    def test_a_lista_e_buscada_ao_ABRIR_a_aba(self):
        # Carregá-la no arranque era mais um pedido a cada visita às
        # Configurações, para uma aba que quase ninguém abre.
        assert re.search(r"tabId === 'auditoria'", UI)
        assert 'carregarAuditoria' in UI

    def test_os_ouvintes_sao_ligados_na_inicializacao(self):
        assert 'initAuditListeners()' in HANDLERS


class TestNaoHaComoApagarAAuditoria:
    """🛡️ É a diferença que faz esta aba existir ao lado dos Logs do Sistema.

    O `app.log` roda sozinho e tem um botão que o trunca. Uma trilha que a
    pessoa auditada apaga com um clique não responde à pergunta para que serve.
    """

    def test_a_aba_nao_tem_botao_de_limpar(self):
        botoes = re.findall(r'<button\b.*?</button>', ABA_VISIVEL, re.S)
        assert botoes, "a aba deixou de ter botões — o teste ficaria vazio"
        for botao in botoes:
            assert 'Limpar' not in botao, botao[:120]
            assert 'clear' not in botao.lower(), botao[:120]

    def test_nao_ha_rota_que_apague_a_auditoria(self):
        rotas = re.findall(r"@system_api_bp\.route\('([^']*audit[^']*)'([^)]*)\)", SYSTEM_PY)
        assert rotas, "a rota da auditoria desapareceu"
        for caminho, resto in rotas:
            assert 'DELETE' not in resto, caminho
            assert 'POST' not in resto, caminho

    def test_o_servico_nao_expoe_nada_que_apague(self):
        servico = _sem_comentarios_js(
            (RAIZ / 'app/services/audit.py').read_text(encoding='utf-8'))
        for proibido in ('def apagar', 'def limpar', '.delete()', 'db.session.delete'):
            assert proibido not in servico, proibido


class TestOsRotulosDasAcoes:
    """Toda a ação que o backend grava tem de ter um nome legível.

    ⚠️ Sem isto, uma ação nova aparece na lista com a chave crua
    (`utilizador.apagar_permanentemente`) e no filtro com a mesma coisa — que é
    vocabulário interno, e num painel que fala brasileiro nem sequer é
    português do Brasil.
    """

    def _acoes_gravadas(self):
        acoes = set()
        for caminho in (RAIZ / 'app/blueprints').rglob('*.py'):
            texto = caminho.read_text(encoding='utf-8')
            acoes |= set(re.findall(r"audit\.registar\(\s*'([a-z_.]+)'", texto))
        return acoes

    def test_ha_acoes_para_testar(self):
        assert len(self._acoes_gravadas()) >= 10

    def test_toda_a_acao_gravada_tem_rotulo_no_script(self):
        no_script = set(re.findall(r"^\s*'([a-z_.]+)':\s*'[a-z]", CODIGO, re.M))

        em_falta = sorted(self._acoes_gravadas() - no_script)
        assert em_falta == [], (
            f"estas ações são gravadas e não têm rótulo em audit.js: {em_falta}"
        )

    def test_todo_o_rotulo_do_script_existe_no_template(self):
        chaves = set(re.findall(r"^\s*'[a-z_.]+':\s*'([A-Za-z0-9_]+)',", CODIGO, re.M))
        oferecidas = {
            _chave_do_dataset(a)
            for a in re.findall(r'\bdata-(i18n-[a-z0-9-]+)=', TEMPLATE_PAGINA)
        }

        em_falta = sorted(chaves - oferecidas)
        assert em_falta == [], (
            f"o audit.js aponta para rótulos que o settings.html não define: {em_falta}"
        )


class TestAsClassesDoTailwind:
    """⚠️ O Tailwind lê os ficheiros à procura de nomes de classes LITERAIS.

    Uma classe montada em tempo de execução (`bg-${cor}-100`) nunca chega ao
    CSS final — e o ícone fica sem cor nenhuma, sem erro nenhum. É por isso que
    o mapa CORES existe em vez de se interpolar a cor.
    """

    def test_nenhuma_classe_e_montada_com_interpolacao(self):
        suspeitas = re.findall(r'`[^`]*\b(?:bg|text|border|ring)-\$\{[^`]*`', CODIGO)
        assert suspeitas == [], f"classes montadas em tempo de execução: {suspeitas}"

    def test_as_cores_estao_escritas_por_extenso(self):
        bloco = re.search(r'const CORES = \{(.*?)\n\};', CODIGO, re.S)
        assert bloco, "o mapa CORES desapareceu"
        # Cada família declarada tem de ter uma cor no mapa.
        familias = re.findall(r"cor: '([a-z]+)'", CODIGO)
        for cor in familias:
            assert f'{cor}:' in bloco.group(1), f"a cor '{cor}' não está no mapa CORES"


class TestNadaEntraNoHTMLSemEscapar:
    """🛡️ O que a auditoria mostra vem, em parte, de fora.

    O `username` é escolhido por quem cria a conta no servidor de mídia, e vai
    parar aos `detalhes` de meia dúzia de ações. O `alvo_id` e os valores de
    "antes/depois" seguem o mesmo caminho. Tudo isto é injetado com
    `innerHTML` — alguém chamado `<img src=x onerror=...>` executava código na
    sessão de quem abrisse a Auditoria, que é sempre um administrador.
    """

    def _interpolacoes(self):
        """Tudo o que é interpolado dentro de um literal de template."""
        return re.findall(r'\$\{([^}]+)\}', CODIGO)

    def test_ha_interpolacoes_para_testar(self):
        assert len(self._interpolacoes()) > 10

    def test_todo_o_dado_vindo_do_servidor_passa_por_escapeHTML(self):
        # O que vem da resposta da API (`registo.x`) ou das linhas derivadas
        # dela (`linha.x`) nunca pode ser interpolado em cru.
        cruas = [
            trecho for trecho in self._interpolacoes()
            if re.search(r'\b(registo|linha)\.', trecho) and 'escapeHTML' not in trecho
        ]
        assert cruas == [], f"interpolado sem escapar: {cruas}"

    def test_o_helper_de_escape_e_mesmo_o_partilhado(self):
        # Um `escapeHTML` local que esquecesse as aspas voltava a abrir o
        # buraco dentro de um atributo. Tem de vir do utils.js.
        assert re.search(r"import \{[^}]*\bescapeHTML\b[^}]*\} from '\.\./utils\.js'", CODIGO)


class TestABarraDeGravacao:
    """O botão "Salvar Alterações" some nas abas que nada têm para salvar.

    ⚠️ **Quem decide é a ABA, com `data-somente-leitura`.** Deduzi-lo pela
    presença de campos de formulário dá a resposta errada nas DUAS pontas: a
    Auditoria tem um `<select>` (o filtro por ação) e não grava nada, e a de
    Logs parece só leitura mas guarda o nível de log no `LOG_LEVEL` — esconder
    o botão lá tirava a única forma de o mudar.
    """

    def _campos_que_o_formulario_grava(self):
        """Os ids que o `saveSettings` recolhe: o `fieldMap` mais o nível de log."""
        config_js = (RAIZ / 'app/static/js/settings_modules/config.js').read_text(encoding='utf-8')
        bloco = re.search(r'export const fieldMap = \{(.*?)\n\};', config_js, re.S)
        assert bloco, "o fieldMap mudou de forma"
        return set(re.findall(r"^\s*'([A-Za-z0-9_]+)':", bloco.group(1), re.M)) | {'log_level_selector'}

    def _abas_somente_leitura(self):
        for caminho in sorted((RAIZ / 'app/templates/settings/tabs').glob('*.html')):
            texto = _sem_comentarios_jinja(caminho.read_text(encoding='utf-8'))
            if 'data-somente-leitura="true"' in texto:
                yield caminho.name, texto

    def test_a_auditoria_esta_marcada(self):
        marcadas = {nome for nome, _ in self._abas_somente_leitura()}
        assert 'audit.html' in marcadas

    def test_a_aba_de_LOGS_nao_esta_marcada(self):
        # 🐛 Ela guarda o LOG_LEVEL. Escondê-la era tirar a única forma de o mudar.
        marcadas = {nome for nome, _ in self._abas_somente_leitura()}
        assert 'logs.html' not in marcadas

    def test_nenhuma_aba_marcada_contem_um_campo_que_se_grava(self):
        campos = self._campos_que_o_formulario_grava()
        for nome, texto in self._abas_somente_leitura():
            ids = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', texto))
            colisao = sorted(ids & campos)
            assert colisao == [], (
                f"{nome} declara-se só leitura mas tem campos que o formulário "
                f"grava: {colisao}"
            )

    def test_a_barra_tem_o_gancho_e_o_script_usa_o(self):
        pagina = _sem_comentarios_jinja(TEMPLATE_PAGINA)
        assert 'id="save-bar"' in pagina
        assert "getElementById('save-bar')" in (
            RAIZ / 'app/static/js/settings_modules/dom.js').read_text(encoding='utf-8')
        ui = _sem_comentarios_js(UI)
        assert 'somenteLeitura' in ui
        assert 'dom.saveBar' in ui


class TestAFormatacaoDeDataEUmaSo:
    """📌 Havia TRÊS cópias de `formatDateTime`, e elas não concordavam.

    A do painel principal pedia dia/mês/ano e hora:minuto explícitos; as da
    página de usuários faziam `toLocaleString()`, que em pt-BR sai com vírgula
    e SEGUNDOS. A mesma data de vencimento aparecia de duas maneiras conforme a
    página em que se estava a olhar para ela.
    """

    MODULOS = [
        'app/static/js/dashboard_modules/formatters.js',
        'app/static/js/users_modules/modals.js',
        'app/static/js/users_modules/ui.js',
        'app/static/js/settings_modules/audit.js',
        'app/static/js/settings_modules/about.js',
    ]

    def test_so_o_utils_formata_datas(self):
        # ⚠️ Formatar NÚMEROS (`{style: 'currency'}`) é outra conversa e pode
        # continuar onde está: o que não pode voltar a haver são duas ideias
        # diferentes de como se escreve uma DATA.
        for caminho in self.MODULOS:
            codigo = _sem_comentarios_js((RAIZ / caminho).read_text(encoding='utf-8'))
            chamadas = re.findall(r'\.toLocale(?:Date|Time)?String\s*\(([^)]*)\)', codigo)
            sobras = [c for c in chamadas if 'currency' not in c and 'style:' not in c]
            assert sobras == [], (
                f"{caminho} volta a formatar datas por sua conta: {sobras}. "
                "A porta única é `formatarDataHora`/`formatarData` em utils.js."
            )

    def test_cada_modulo_importa_o_helper_partilhado(self):
        for caminho in self.MODULOS:
            codigo = (RAIZ / caminho).read_text(encoding='utf-8')
            assert re.search(r"import \{[^}]*\bformatarData(Hora)?\b[^}]*\} from '\.\./utils\.js'", codigo), caminho

    def test_o_helper_trata_o_valor_em_falta(self):
        # É a diferença que a página de usuários tinha e não podia perder: a
        # data de fim de teste pode mesmo não existir, e um espaço em branco no
        # lugar dela não diz nada a ninguém.
        utils = (RAIZ / 'app/static/js/utils.js').read_text(encoding='utf-8')
        assert 'ausente' in utils
        assert re.search(r'export function formatarDataHora\(valor, \{ ausente', utils)
