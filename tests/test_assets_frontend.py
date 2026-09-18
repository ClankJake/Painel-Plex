# tests/test_assets_frontend.py
"""Os assets que os templates pedem têm de ser gerados pelo build.

🐛 REGRESSÃO REPORTADA: o navegador acusava `io is not defined` e
`Chart is not defined` fora do Docker. Os caminhos das bibliotecas estavam
escritos em DOIS sítios — o `Dockerfile`, que as copiava para a imagem, e mais
nenhum. Quem corria o painel localmente só tinha o `build:css`, e as
bibliotecas nunca chegavam a `app/static/dist/`.

O `copy:vendor` fecha isso, mas a armadilha de fundo é a duplicação: uma lista
que é preciso lembrar de atualizar noutro sítio acaba sempre por divergir. Este
teste lê o que os templates pedem e o que o build gera, e compara — para que a
próxima biblioteca que alguém acrescente ao HTML e esqueça no package.json
falhe aqui, e não no navegador de quem instalou.
"""

import json
import os
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
TEMPLATES = RAIZ / 'app' / 'templates'
DIST = RAIZ / 'app' / 'static' / 'dist'

# O `build:css` do Tailwind gera este, e não passa pelo `copy:vendor`.
GERADOS_PELO_CSS = {'output.css'}


def _pedidos_pelos_templates():
    """Tudo o que um `<script src=".../dist/X">` ou `<link href=...>` pede."""
    pedidos = {}
    for template in sorted(TEMPLATES.rglob('*.html')):
        for nome in re.findall(r"filename='dist/([^']+)'", template.read_text(encoding='utf-8')):
            pedidos.setdefault(nome, []).append(template.relative_to(RAIZ).as_posix())
    return pedidos


def _copiados_pelo_build():
    scripts = json.loads((RAIZ / 'package.json').read_text(encoding='utf-8'))['scripts']
    caminhos = re.findall(r'node_modules/\S+', scripts.get('copy:vendor', ''))
    return {caminho.rsplit('/', 1)[-1] for caminho in caminhos}


def test_todo_o_asset_pedido_e_gerado():
    pedidos = _pedidos_pelos_templates()
    disponiveis = _copiados_pelo_build() | GERADOS_PELO_CSS

    em_falta = {nome: onde for nome, onde in pedidos.items() if nome not in disponiveis}

    assert not em_falta, (
        "Estes ficheiros são pedidos pelos templates mas nada os põe em "
        f"app/static/dist/: {em_falta}. Acrescente-os ao 'copy:vendor' do "
        "package.json — senão o navegador vai acusar que a biblioteca não existe."
    )


def test_os_assets_pedidos_estao_mesmo_no_disco():
    """Depois de `npm run build`, o que os templates pedem tem de estar lá.

    ⚠️ Os outros testes deste ficheiro verificam as DECLARAÇÕES — que o
    `copy:vendor` cobre o que o HTML pede, que o `build` corre as duas metades.
    Nenhum deles chega a correr o build, por isso nenhum apanha um `npm install`
    que morre nem um Tailwind que não gera nada. Este verifica o RESULTADO.

    `app/static/dist/` não está versionado, por isso em desenvolvimento este
    teste SALTA — quem ainda não correu o build não é quem está errado. No CI a
    variável `PAINEL_EXIGE_DIST` transforma o salto numa falha: um passo que
    existe para apanhar um build partido não pode passar sem correr.
    """
    if not DIST.is_dir():
        if os.environ.get('PAINEL_EXIGE_DIST') == '1':
            pytest.fail(
                f"{DIST} não existe depois do build. O `npm run build` correu mesmo? "
                "É o passo que põe o CSS e as bibliotecas onde os templates as procuram."
            )
        pytest.skip("app/static/dist/ ainda não foi gerado (corra `npm run build`).")

    em_falta = sorted(
        nome for nome in _pedidos_pelos_templates() if not (DIST / nome).is_file()
    )

    assert em_falta == [], (
        f"O build correu mas estes ficheiros não ficaram em {DIST}: {em_falta}. "
        "É o que o navegador vai pedir e não encontrar."
    )


def test_o_build_corre_o_copy_e_o_css():
    # `npm run build` é o comando único que a documentação manda correr.
    scripts = json.loads((RAIZ / 'package.json').read_text(encoding='utf-8'))['scripts']

    assert 'copy:vendor' in scripts['build']
    assert 'build:css' in scripts['build']


def test_os_caminhos_do_copy_vendor_existem_no_pacote_instalado():
    """Sem `npm install` feito, não há o que verificar — o teste salta."""
    scripts = json.loads((RAIZ / 'package.json').read_text(encoding='utf-8'))['scripts']
    caminhos = re.findall(r'node_modules/\S+', scripts.get('copy:vendor', ''))

    if not (RAIZ / 'node_modules').is_dir():
        pytest.skip("node_modules não está instalado")

    em_falta = [c for c in caminhos if not (RAIZ / c).is_file()]

    assert not em_falta, (
        f"O 'copy:vendor' aponta para ficheiros que não existem: {em_falta}. "
        "Um deles mudou de sítio na biblioteca, e o `cp` falha o build inteiro."
    )


def test_o_dockerfile_nao_repete_os_caminhos_das_bibliotecas():
    """
    🐛 Era daqui que vinha a divergência: o Dockerfile sabia os caminhos, o
    package.json não. Agora quem os sabe é só o package.json, e o Dockerfile
    copia a pasta já construída.
    """
    dockerfile = (RAIZ / 'Dockerfile').read_text(encoding='utf-8')

    repetidos = [
        linha.strip() for linha in dockerfile.splitlines()
        if linha.startswith('COPY') and 'node_modules/' in linha
    ]

    assert not repetidos, (
        f"O Dockerfile voltou a copiar bibliotecas por caminho: {repetidos}. "
        "Isso duplica a lista do 'copy:vendor' e as duas vão divergir."
    )


# ============================================================================
# A conversão de `data-*` para a chave do `dataset`
# ============================================================================

# Um `data-*` cujo traço final é seguido de um NÚMERO.
DATA_TERMINADO_EM_NUMERO = re.compile(r'\bdata-[a-z0-9-]*-\d+\s*=')


def test_nenhum_data_attribute_termina_em_traco_e_numero():
    """🐛 A página de convite escrevia "undefined" no "Como começar".

    O browser só come o '-' de um `data-*` quando o que vem a seguir é uma
    LETRA MINÚSCULA. `data-i18n-step-local-1` chega ao `dataset` como
    `i18nStepLocal-1`, com o traço intacto — e o JavaScript, que corta o
    prefixo e pede `stepLocal1`, recebia `undefined`. Era isso que a pessoa
    lia, escrito por extenso, nas instruções de um servidor de contas locais.

    ⚠️ Desde que os leitores passaram a ser UM só (`lerConfiguracaoDoScript`,
    que usa o `chaveEmCamelCase`), os traços que sobram são comidos em todas as
    páginas — antes havia doze leitores e nem todos o faziam. Este teste fica
    como segunda linha de defesa, e porque a regra continua a ser a mais simples
    de seguir: **nunca termine um `data-*` com traço e número**. Escreva o
    número por extenso (`-one`, `-two`) ou junte-o à palavra (`step1-text`).
    """
    infratores = []
    for template in sorted(TEMPLATES.rglob('*.html')):
        for linha_num, linha in enumerate(template.read_text(encoding='utf-8').splitlines(), 1):
            for achado in DATA_TERMINADO_EM_NUMERO.findall(linha):
                infratores.append(f"{template.relative_to(RAIZ).as_posix()}:{linha_num} {achado}")

    assert infratores == [], (
        "Estes atributos chegam ao dataset com o traço intacto e o JavaScript "
        "não os encontra:\n  " + "\n  ".join(infratores)
    )


# Um leitor do `dataset` escrito à mão: `for (const k in x.dataset)` ou
# `Object.entries(x.dataset)`. É o que o carregador único substituiu.
LEITOR_A_MAO = re.compile(r'(?:for\s*\([^)]*\bin\s+\w+\.dataset\b|Object\.entries\(\s*\w+\.dataset\s*\))')

JS = RAIZ / 'app' / 'static' / 'js'
CARREGADOR = JS / 'utils.js'


def test_so_o_utils_le_o_dataset_do_script():
    """⚠️ Havia DOZE leitores do `dataset`, e não concordavam entre si.

    Cinco regras diferentes para decidir o nome da chave: `data-url-x` numa
    página, `data-urls-x` noutra, `data-x-url` numa terceira, e a de
    estatísticas a cortar o SUFIXO `Url` em vez de um prefixo. Nenhuma estava
    errada — mas quem trabalhasse em duas páginas tinha de se lembrar de qual
    era qual, e uma chave que não resolve não dá erro nenhum: dá um `fetch`
    para `undefined`, ou um texto em branco.

    Hoje é um só, `lerConfiguracaoDoScript` no `utils.js`, e aceita os prefixos
    todos que já existiam nos templates — nenhum `data-*` teve de ser
    renomeado. Este teste existe para o décimo-terceiro não nascer: a seguir a
    um copiar-colar de outra página, um leitor novo passaria despercebido.
    """
    infratores = []
    for ficheiro in sorted(JS.rglob('*.js')):
        if ficheiro == CARREGADOR:
            continue
        for numero, linha in enumerate(ficheiro.read_text(encoding='utf-8').splitlines(), 1):
            if LEITOR_A_MAO.search(linha):
                infratores.append(f'{ficheiro.relative_to(RAIZ).as_posix()}:{numero}')

    assert infratores == [], (
        'Estes ficheiros voltaram a ler o `dataset` à mão:\n  '
        + '\n  '.join(infratores)
        + '\n\nUse `lerConfiguracaoDoScript(\'<id>-script\')` do utils.js. Ele '
          'devolve {i18n, urls, config, dataset} e já trata dos traços que o '
          'browser deixa para trás.'
    )


def test_o_carregador_unico_existe_e_e_exportado():
    """Se ele desaparecer, o teste acima passa a permitir tudo em silêncio."""
    texto = CARREGADOR.read_text(encoding='utf-8')
    assert 'export function lerConfiguracaoDoScript' in texto, (
        'O carregador único saiu do utils.js. Sem ele, o guarda que impede '
        'leitores à mão deixa de ter alternativa a oferecer.'
    )


def test_nenhum_template_carrega_o_tailwind_por_CDN():
    """⚠️ **O CSS vem do BUILD, e de mais lado nenhum.**

    A página pública de pagamento era a única a carregar
    `https://cdn.tailwindcss.com`, e isso custava três coisas: um pedido a um
    terceiro no meio do fluxo de quem vai pagar (numa rede sem saída, a página
    chegava sem estilo nenhum), a compilação do CSS no navegador de cada
    pessoa, e uma regra de tema DIFERENTE da do resto do painel — o CDN decide
    o modo escuro pela preferência do sistema, e a configuração deste projeto
    decide-o pela classe `dark`, que é a que o painel escreve.

    O `output.css` já é gerado a partir dos templates e do `app/static/js`
    (ver o `content` do tailwind.config.js), por isso não há nada a ganhar em
    trocar — só a perder, e em silêncio: com o CDN, uma classe montada em tempo
    de execução funciona, e passa a não funcionar assim que alguém tire o CDN.
    """
    # ⚠️ Os comentários `{# ... #}` ficam de fora: os deste projeto CITAM o que
    # está errado para explicar porquê, e uma varredura que os leia acusa
    # exatamente a explicação de ser o problema que ela descreve.
    com_cdn = [
        template.relative_to(RAIZ).as_posix()
        for template in sorted(TEMPLATES.rglob('*.html'))
        if 'cdn.tailwindcss.com' in re.sub(
            r'\{#.*?#\}', '', template.read_text(encoding='utf-8'), flags=re.S)
    ]

    assert com_cdn == [], (
        'Estes templates carregam o Tailwind por CDN:\n  ' + '\n  '.join(com_cdn)
        + "\n\nUse o CSS do build: "
          "<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='dist/output.css') }}\">"
    )


def test_a_pagina_de_pagamento_carrega_o_css_do_build():
    """O guarda de cima só diz o que NÃO pode; este diz o que tem de haver.

    Sem ele, apagar a linha do CDN e não pôr nada no lugar passava — e a página
    por onde entra o dinheiro ficava sem estilo nenhum.
    """
    pagina = (TEMPLATES / 'payment_public.html').read_text(encoding='utf-8')

    assert "filename='dist/output.css'" in pagina
