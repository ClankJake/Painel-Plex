# tests/test_referencias_do_javascript.py
"""O passo que verifica as referências do JavaScript tem de continuar ligado.

🐛 REGRESSÃO REPORTADA: `Uncaught ReferenceError: allInvites is not defined`,
na consola do navegador, ao clicar em "Detalhes" num convite. Quando a
filtragem das abas passou para o servidor, as duas listas do `renderInvites()`
colapsaram numa só — mas o nome antigo ficou para trás dentro do `onclick` do
botão.

O que torna esta classe de erro perigosa é QUANDO ela aparece: um módulo ES é
código estrito, e um identificador que não existe só levanta quando a linha
CORRE. A página carregava sem uma queixa, o servidor não registava nada, e o
modal simplesmente não abria.

Nada na suíte podia ver isso — o pytest não executa JavaScript — e o
`npm run build` também não, porque o Tailwind lê os templates à procura de
NOMES DE CLASSES e nunca analisa os módulos. Quem passou a ver é o `npm run
lint`, com a regra `no-undef` e mais nenhuma.

🐛 SEGUNDA REGRESSÃO, e é a que explica a segunda regra: `TypeError:
ui.showToast is not a function`, quando um pagamento entrava e o socket
disparava `user_list_updated`. `import * as ui` só traz o que o módulo
EXPORTA, e `showToast` era uma função que o `ui.js` apenas IMPORTAVA do
`utils.js`. Para o `no-undef` estava tudo bem — o `ui` existe. E o aviso
perdido era o menor dos estragos: a exceção matava a linha SEGUINTE, que era a
que recarregava a lista, por isso o painel aberto não atualizava quando um
pagamento chegava.

Este ficheiro não corre o ESLint (o job do pytest não tem Node). Prende o
CONTRATO: que o script existe, que as duas regras estão em "error", que a
configuração cobre todo o JavaScript do painel, e que o CI o executa. Um guarda que alguém desligue em silêncio deixa
de ser um guarda, e o sintoma só reaparecia no navegador de quem usa o painel.
"""

import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PACKAGE_JSON = RAIZ / 'package.json'
CONFIG_ESLINT = RAIZ / 'eslint.config.mjs'
WORKFLOW = RAIZ / '.github' / 'workflows' / 'tests.yml'
JS = RAIZ / 'app' / 'static' / 'js'


def _pacote():
    return json.loads(PACKAGE_JSON.read_text(encoding='utf-8'))


def test_o_script_de_lint_existe_e_chama_o_eslint():
    scripts = _pacote()['scripts']
    assert 'lint' in scripts, (
        'O `npm run lint` desapareceu do package.json. É ele que apanha uma '
        'referência que não existe num módulo ES — um erro que só levanta '
        'quando a linha corre, na cara de quem clica no botão.'
    )
    assert 'eslint' in scripts['lint']


def test_o_eslint_e_uma_dependencia_de_desenvolvimento():
    # Sem isto o `npm ci` do CI não o instala e o passo morre com "not found",
    # que é um vermelho confuso em vez de um guarda a funcionar.
    assert 'eslint' in _pacote()['devDependencies']


def test_a_configuracao_do_eslint_existe_e_impoe_o_no_undef():
    assert CONFIG_ESLINT.is_file(), 'Falta o eslint.config.mjs.'
    texto = CONFIG_ESLINT.read_text(encoding='utf-8')
    assert re.search(r'["\']no-undef["\']\s*:\s*["\']error["\']', texto), (
        'A regra `no-undef` deixou de estar em "error". Sem ela o passo passa '
        'a não verificar uma referência que não existe — continuando verde.'
    )


def test_a_configuracao_impoe_o_namespace_dos_imports():
    """A regra que apanha `ui.showToast` quando o `ui.js` não o exporta."""
    texto = CONFIG_ESLINT.read_text(encoding='utf-8')
    assert re.search(r'["\']import-x/namespace["\']\s*:\s*\[?\s*["\']error["\']', texto), (
        'A regra `import-x/namespace` deixou de estar em "error". É ela — e '
        'não o `no-undef` — que vê um `NS.membro` que o módulo não exporta: '
        'para o `no-undef` o NS existe e está tudo certo.'
    )
    assert 'eslint-plugin-import-x' in _pacote()['devDependencies'], (
        'O plugin saiu das devDependencies: o `npm ci` do CI não o instala e '
        'o ESLint morre a carregar a configuração.'
    )


def test_a_configuracao_cobre_todo_o_javascript_do_painel():
    """Um ficheiro fora do alcance da configuração não é verificado, e o ESLint
    não se queixa disso — limita-se a ignorá-lo em silêncio."""
    texto = CONFIG_ESLINT.read_text(encoding='utf-8')
    padroes = re.findall(r'["\'](app/static/js/[^"\']*)["\']', texto)
    assert padroes, 'A configuração não aponta para app/static/js/.'

    # O alcance largo tem de lá estar; os `ignores` do service worker são uma
    # exceção declarada (ele corre noutro contexto e tem outros globais), mas
    # a configuração dá-lhe um bloco próprio — e é isso que se exige.
    assert 'app/static/js/**/*.js' in padroes
    assert 'app/static/js/service-worker.js' in padroes

    # Se alguém criar uma pasta de JavaScript fora de app/static/js/, ela fica
    # de fora sem ninguém dar por isso.
    fora = [
        caminho.relative_to(RAIZ).as_posix()
        for caminho in RAIZ.joinpath('app', 'static').rglob('*.js')
        if JS not in caminho.parents and caminho.parent != JS
        and 'dist' not in caminho.parts
    ]
    assert not fora, (
        f'Estes ficheiros de JavaScript estão fora do alcance do lint: {fora}. '
        'Acrescente-os ao eslint.config.mjs ou mova-os para app/static/js/.'
    )


def test_o_ci_corre_o_lint_antes_do_build():
    texto = WORKFLOW.read_text(encoding='utf-8')
    assert 'npm run lint' in texto, (
        'O job do frontend deixou de correr o `npm run lint`. O passo existe '
        'porque nenhum outro consegue ver uma referência pendurada no JS.'
    )
    # Antes do build: o lint é barato e falha depressa; um build partido não
    # deve esconder um erro de referência que já está ali para ser visto.
    assert texto.index('npm run lint') < texto.index('npm run build')
