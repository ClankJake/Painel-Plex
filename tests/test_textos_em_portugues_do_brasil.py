# tests/test_textos_em_portugues_do_brasil.py

"""Os textos que a pessoa lê são em português do BRASIL.

O painel sempre falou brasileiro — "Usuário", "Senha", "telas", "Você" — mas
funcionalidades novas foram entrando com vocabulário europeu ("palavra-passe",
"utilizador", "aceder", "A guardar..."), e o resultado eram duas variantes na
mesma página. Para quem usa, isso não é um detalhe de estilo: é o painel a
parecer traduzido por máquina.

⚠️ Isto vale para o que é VISÍVEL — os literais dentro de `_()` e os modelos de
notificação, que chegam ao telefone de quem paga. Os comentários e as docstrings
do código ficam de fora de propósito: são internos, e não é onde o utilizador
final olha.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ('app/templates', 'app/blueprints', 'app/services', 'app/utils')

# ⚠️ **O JavaScript também fala com a pessoa.** Os textos dele vêm quase todos
# do HTML (`data-i18n-*`, que já entra por `app/templates`), mas cada um tem uma
# ALTERNATIVA escrita no próprio ficheiro — o `i18n.x || 'texto'` que aparece
# quando a chave falta. Oito delas ficaram em português europeu depois da
# varredura, precisamente porque esta lista não chegava aqui: "A guardar...",
# "A enviar...", "descarregue filmes", "ficheiro de backup".
PASTAS_JS = ('app/static/js',)

# Cada marca vem com a forma brasileira, para a mensagem de erro dizer logo o
# que escrever em vez de mandar procurar.
MARCAS = [
    (r'\bpalavras?-passe\b', 'senha'),
    (r'\butilizador(es)?\b', 'usuário / usuários'),
    (r'\baceder\b', 'acessar'),
    (r'\btelemó(vel|veis)\b', 'celular'),
    (r'\becrã\b', 'tela'),
    (r'\bficheiros?\b', 'arquivo'),
    (r'\bpercentagem\b', 'porcentagem'),
    (r'\bsubscriç(ão|ões)\b', 'assinatura'),
    (r'\bcontactos?\b', 'contato'),
    (r'(?<!com)\bpartilh\w*', 'compartilhar / compartilhamento'),
    # O gerúndio: em pt-PT "está a guardar", no Brasil "está guardando".
    (r'\bestá a [a-zç]+ar\b', 'está + gerúndio ("está salvando")'),
    # Só o RÓTULO de carregamento: "obriga o usuário a entrar" é português do
    # Brasil correto e não pode ser apanhado aqui.
    #
    # ⚠️ A lista de verbos era escrita à mão, e por isso tinha buracos: "A
    # reativar...", "A restaurar...", "A zerar...", "A gravar..." e mais uma
    # dúzia passavam à vontade — um rótulo que a pessoa lê a cada operação
    # demorada do painel. Hoje vale para QUALQUER verbo no infinitivo, que é o
    # que define a forma ("A" + infinitivo), em vez de uma lista que alguém tem
    # de se lembrar de alargar. A maiúscula é o que distingue isto de português
    # do Brasil correto: só se apanha no INÍCIO do texto.
    (r'^A [a-zà-ú]+[aei]r\b',
     'gerúndio ("Salvando...", "Enviando...")'),
    # A família do "registo": em pt-PT regista-se, no Brasil registra-se.
    # O 'r' é o que distingue as duas famílias: registo/registado/registar de
    # um lado, registro/registrado/registrar do outro. A negação apanha todas as
    # formas europeias de uma vez, incluindo as que ninguém se lembraria de
    # listar ("registámos", "registando").
    (r'\bregist(?!r)[a-zà-ú]*\b', 'registro / registrado / registra'),
    # O particípio: em pt-PT "aceite", no Brasil "aceito".
    (r'\baceites?\b', 'aceito'),
    (r'\butiliza(ção|ções)\b', 'uso / usos'),
    # "precisa de resgatar" -> "precisa resgatar"; "necessita de" não existe
    # no registro falado brasileiro.
    (r'\b(precisa|necessita|necessitam|precisam) de [a-zà-ú]+[aei]r\b',
     'o verbo sem a preposição ("precisa resgatar")'),
    # A ênclise antes do infinitivo: "para a criar" -> "para criá-la".
    (r'\bpara (a|o|as|os) [a-zà-ú]+ar\b', 'o pronome depois do verbo ("para criá-la")'),
    (r'\bcontrolo\b', 'controle'),
    (r'\bdescarreg\w+', 'baixar / download'),
    (r'\bbases? de dados\b', 'banco de dados'),
]

LITERAL = re.compile(r"_\(\s*(['\"])(.*?)\1", re.S)
# A alternativa de uma chave de tradução em falta: `i18n.algumaCoisa || 'texto'`.
ALTERNATIVA_JS = re.compile(r"i18n\.[A-Za-z0-9_]+\s*\|\|\s*(['\"])(.*?)\1", re.S)
MODELO = re.compile(r'^\s*"[A-Z_]+_MESSAGE_TEMPLATE":\s*(.*)$', re.M)


def _textos_visiveis():
    """(ficheiro, linha, texto) de tudo o que a pessoa chega a ler."""
    for pasta in PASTAS:
        for caminho in sorted((RAIZ / pasta).rglob('*')):
            if caminho.suffix not in ('.html', '.py'):
                continue
            conteudo = caminho.read_text(encoding='utf-8')
            nome = caminho.relative_to(RAIZ).as_posix()

            for padrao, grupo in ((LITERAL, 2), (MODELO, 1)):
                for achado in padrao.finditer(conteudo):
                    linha = conteudo.count('\n', 0, achado.start()) + 1
                    yield nome, linha, achado.group(grupo)

    for pasta in PASTAS_JS:
        for caminho in sorted((RAIZ / pasta).rglob('*.js')):
            conteudo = caminho.read_text(encoding='utf-8')
            nome = caminho.relative_to(RAIZ).as_posix()
            for achado in ALTERNATIVA_JS.finditer(conteudo):
                linha = conteudo.count('\n', 0, achado.start()) + 1
                yield nome, linha, achado.group(2)


@pytest.mark.parametrize('marca, sugestao', MARCAS)
def test_nenhum_texto_visivel_usa_vocabulario_europeu(marca, sugestao):
    # ⚠️ Sem `re.I`: há padrões (o rótulo 'A guardar...') em que a
    # MAIÚSCULA é o que os distingue de português do Brasil correto.
    padrao = re.compile(marca)
    infratores = [
        f"{nome}:{linha} — {texto[:80]}"
        for nome, linha, texto in _textos_visiveis()
        if padrao.search(texto)
    ]

    assert infratores == [], (
        f"Use '{sugestao}'. Encontrado em:\n  " + "\n  ".join(infratores[:10])
    )


def test_a_varredura_encontra_mesmo_alguma_coisa():
    """Um teste que não lê nada passa sempre — e não guarda nada."""
    textos = list(_textos_visiveis())

    assert len(textos) > 1000
