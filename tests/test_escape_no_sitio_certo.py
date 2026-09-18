# tests/test_escape_no_sitio_certo.py
"""Escapa-se ao ESCREVER HTML, nunca ao ler um campo do formulário.

🐛 REGRESSÃO REAL: o `account.js` passava o que a pessoa escrevia por
`sanitizeHTML()` ANTES de o enviar ao servidor. Escapar à entrada não é uma
defesa a mais — é gravar a entidade: quem se chama "Ana & Bia" ficava com
`Ana &amp; Bia` na base de dados, e com mais um `amp;` a cada gravação, porque
o campo era preenchido com o valor já escapado e regravado por cima.

E esse `name` é o `{name}` dos modelos de notificação
(`NotifierManager._build_placeholders`), por isso o que chegava ao WhatsApp,
ao Telegram e ao Discord de quem paga era literalmente "Ana &amp; Bia".

Valia para mais três valores, com sintomas diferentes e igualmente mudos:

- o **código do cupão**, que o servidor compara com `func.upper(Coupon.code)`.
  Um cupão "PROMO&VERAO" (que a criação aceita: só espaços e ';' são recusados)
  viajava como "PROMO&AMP;VERAO" e devolvia "cupom inválido" a quem o tinha
  escrito exatamente como o recebeu;
- o **termo de busca** do histórico, que entra num `encodeURIComponent` da
  query string: procurar "Tom & Jerry" pedia "Tom &amp; Jerry" e não devolvia
  nada;
- o **telegram_user** e o **discord_user_id**, pelo mesmo caminho do nome.

⚠️ Não se perdeu defesa nenhuma, e a prova de que o escape era acidental é a
página de utilizadores: ela sempre gravou os MESMOS campos em cru
(`.value.trim()`). Quem escapa é quem escreve HTML — o `escapeHTML`, que o
`test_escape_de_nomes_no_javascript.py` obriga — e o Jinja, do lado do servidor.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
JS = RAIZ / 'app' / 'static' / 'js'

ESCAPADORES = r'(?:escapeHTML|escapeHtml|sanitizeHTML|sanitizeHtml)'

# `escapeHTML(algumaCoisa.value)` — com ou sem `.trim()`/`.toUpperCase()` pelo
# meio, e tolerando um `document.getElementById('x')` ou `modal.querySelector`
# lá dentro. É o que distingue LER um campo de escrever HTML.
LER_UM_CAMPO = re.compile(
    ESCAPADORES + r'\s*\([^;]*?\.value\b',
)

# Uma cópia local de um helper que o `utils.js` já exporta.
#
# ⚠️ Um ALIAS que delega (`const sanitizeHTML = (str) => escapeHTML(str);`) é o
# padrão certo e passa: o nome curto fica onde é usado e a regra continua a
# viver num sítio só. O que se recusa é um CORPO próprio — foi assim que a
# cópia do `financial.js` ficou sem escapar aspas enquanto o resultado dela era
# interpolado dentro de `title="..."` e `data-code="..."`, e a mesma armadilha
# das três cópias do `formatDateTime`.
DEFINICAO = re.compile(
    r'^[ \t]*(?:const|let|var|function)\s+(' + ESCAPADORES + r')\b(.*)$',
    re.M,
)
# `= (x) => escapeHTML(x)` ou `= escapeHTML` — delega, não reimplementa.
DELEGA = re.compile(r'=>\s*' + ESCAPADORES + r'\s*\(|=\s*' + ESCAPADORES + r'\s*;')


def _ficheiros():
    return sorted(p for p in JS.rglob('*.js') if p.name != 'utils.js')


def test_nada_e_escapado_ao_sair_de_um_campo_do_formulario():
    infratores = []
    for caminho in _ficheiros():
        for numero, linha in enumerate(caminho.read_text(encoding='utf-8').splitlines(), 1):
            if LER_UM_CAMPO.search(linha):
                nome = caminho.relative_to(RAIZ).as_posix()
                infratores.append(f"{nome}:{numero} — {linha.strip()[:100]}")

    assert infratores == [], (
        "Escapar o que se LÊ de um campo grava a entidade HTML (o nome com "
        "'&amp;' que chega ao WhatsApp, o cupão que deixa de ser encontrado). "
        "Escape ao ESCREVER HTML, não à entrada:\n  " + "\n  ".join(infratores)
    )


def test_ninguem_redefine_o_escapador_partilhado():
    infratores = []
    for caminho in _ficheiros():
        texto = caminho.read_text(encoding='utf-8')
        for achado in DEFINICAO.finditer(texto):
            if DELEGA.search(achado.group(2)):
                continue
            numero = texto.count('\n', 0, achado.start()) + 1
            nome = caminho.relative_to(RAIZ).as_posix()
            infratores.append(f"{nome}:{numero} — {achado.group(0).strip()[:90]}")

    assert infratores == [], (
        "O `utils.js` já exporta `escapeHTML`, e ele escapa `& < > \" '`. Uma "
        "reimplementação local diverge dela sem ninguém dar por isso — a que "
        "existia no `financial.js` não escapava aspas, e o resultado entrava "
        "em `title=\"...\"`. Delegue nela:\n  " + "\n  ".join(infratores)
    )
