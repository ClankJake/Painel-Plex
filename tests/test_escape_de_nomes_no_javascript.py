# tests/test_escape_de_nomes_no_javascript.py
"""Nada escolhido por quem usa o painel entra em `innerHTML` sem ser escapado.

🛡️ REGRESSÃO REAL: o `username` é escolhido por quem cria a conta no servidor
de mídia — no Jellyfin, por quem resgata o convite, e sem restrição de
caracteres (`conta_a_partir_de_credenciais` valida só o comprimento e o
formato do email). Esse nome era interpolado cru em `innerHTML`:

- no SINO de notificações (`notifications.js`), que está no `base.html` e
  portanto em TODAS as páginas. O painel escreve lá mensagens como
  "Renovação manual de %(username)s registrada" — ou seja, alguém chamado
  `<img src=x onerror=…>` executava código na sessão do administrador sem
  este sequer clicar em nada;
- no pódio e na tabela da página `/statistics`, que é a casa de quem não é
  administrador;
- no modal de envio em massa do painel principal.

É exatamente a classe que o CLAUDE.md já regista como tendo acontecido uma vez,
na aba de Auditoria. Este teste percorre o JavaScript e recusa que volte.

⚠️ O que se procura é uma interpolação numa linha COM MARCAÇÃO (é isso que
distingue um `${}` que vira HTML de um que vira um log ou um `textContent`).

⚠️ E um `${message}` NU não é apanhado de propósito: nos modais de confirmação
(`showConfirmationModal`) o `message` é um fragmento de HTML por contrato — quem
chama já sanitiza o que lá põe e acrescenta o `<strong>`. Escapá-lo ali partia o
negrito e escapava duas vezes o nome.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
JS = RAIZ / 'app' / 'static' / 'js'

# `${ ... }`, tolerando um nível de chavetas lá dentro (um ternário com objeto).
INTERPOLACAO = re.compile(r'\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}')

# Só os ACESSOS A PROPRIEDADE (`user.username`, `n.message`, `u.thumb`) e os
# dois nomes que o painel usa soltos para o nome de quem se está a ver.
# O `(?!i18n\.)` deixa de fora os RÓTULOS (`i18n.username` é a palavra
# "Usuário" no formulário do convite, não o nome de ninguém).
PERIGOSOS = re.compile(
    r'(?:(?<![\w.$])(?!i18n\.)[\w$]+(?:\[[^\]]*\])?'
    r'(?:\.[\w$]+(?:\[[^\]]*\])?)*'
    r'\.(?:username|original_username|thumb|message)\b'
    r'|(?<![\w.$])(?:username|original_username)\b)'
)

ESCAPADO = re.compile(r'\b(?:escapeHTML|escapeHtml|sanitizeHTML|escapeLogHtml|esc)\s*\(')

# `<div`, `</p`, `<img` — o que faz da linha um pedaço de HTML.
MARCACAO = re.compile(r'<[a-zA-Z/]')


def _ficheiros():
    return sorted(JS.rglob('*.js'))


def test_nenhum_nome_de_utilizador_entra_cru_no_html():
    faltas = []
    for ficheiro in _ficheiros():
        texto = ficheiro.read_text(encoding='utf-8')
        for numero, linha in enumerate(texto.splitlines(), 1):
            if not MARCACAO.search(linha):
                continue
            for encontro in INTERPOLACAO.finditer(linha):
                corpo = encontro.group(1)
                if PERIGOSOS.search(corpo) and not ESCAPADO.search(corpo):
                    caminho = ficheiro.relative_to(RAIZ)
                    faltas.append(f'{caminho}:{numero}  ${{{corpo.strip()}}}')

    assert not faltas, (
        'Interpolação de um valor escolhido por quem usa o painel dentro de HTML, '
        'sem escapar. Envolva-o em `escapeHTML(...)` (ou no `escapeHtml` do '
        'módulo, onde é esse o nome importado):\n  ' + '\n  '.join(faltas)
    )


def test_o_sino_de_notificacoes_escapa_a_mensagem():
    """A mensagem do sino é a pior das entradas: aparece em todas as páginas.

    Fica preso à parte porque é o sítio concreto que o bug atingia, e porque a
    varredura acima depende de heurísticas que uma reescrita do ficheiro podia
    contornar sem intenção nenhuma.
    """
    texto = (JS / 'notifications.js').read_text(encoding='utf-8')
    assert '${n.message}' not in texto
    assert 'escapeHTML(n.message)' in texto


def test_a_varredura_apanharia_o_bug_original():
    """Um guarda que não falha sobre o código partido não é um guarda."""
    linha = '<p class="break-words">${n.message}</p>'
    assert MARCACAO.search(linha)
    corpo = INTERPOLACAO.search(linha).group(1)
    assert PERIGOSOS.search(corpo) and not ESCAPADO.search(corpo)

    corrigida = '<p class="break-words">${escapeHTML(n.message)}</p>'
    corpo = INTERPOLACAO.search(corrigida).group(1)
    assert ESCAPADO.search(corpo)
