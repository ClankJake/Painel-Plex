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

# ⚠️ **A maiúscula escondia dezoito textos.** A varredura corria toda sem
# `re.I` por causa de TRÊS marcas em que a maiúscula é o que as distingue de
# português do Brasil correto (o rótulo "A guardar...", o botão "Guardar", e o
# particípio "aceite"). O preço era todo o resto: "Utilizador não encontrado",
# "Painel de Controlo", "Limite de Utilizações", "Fã do Realizador", "INSERIR
# CUPÃO" — e o `[Cc]up` do cupão era o remendo que se tinha posto sobre este
# buraco, uma marca de cada vez.
#
# Hoje a maiúscula é uma propriedade DA MARCA (`SENSIVEIS`), e não da varredura:
# o que precisa dela declara-o, e todo o resto apanha as duas formas.
#
# ⚠️ O particípio "aceite" é o caso que obriga a isto a existir: "Aceite o seu
# convite" é o IMPERATIVO de aceitar, português do Brasil correto, e uma
# varredura sem maiúsculas recusá-lo-ia.

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
    # ⚠️ As três conjugações, pela mesma razão do rótulo mais abaixo: só com
    # `-ar`, um "está a correr" ou um "está a decorrer" passava à vontade.
    (r'\bestá a [a-zà-ú]+[aei]r\b', 'está + gerúndio ("está salvando")'),
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
    # ⚠️ O "cupão" entrou por TODO o lado — a página financeira, as respostas
    # da API e as notificações de renovação que chegam a quem paga — porque
    # nunca esteve nesta lista. É a palavra mais repetida do painel a seguir a
    # "usuário". (O `[Cc]` que aqui estava era o remendo da maiúscula; sem ele
    # o "INSERIR CUPÃO" de dois botões continuava a passar.)
    (r'\bcup(ão|ões)\b', 'cupom / cupons'),
    # O artigo antes de "certeza" é europeu: no Brasil é "Tem certeza".
    (r'\bTem a certeza\b', 'Tem certeza'),
    # ⚠️ Só o RÓTULO, pela MAIÚSCULA no início — a mesma técnica do "A guardar".
    # O verbo "guardar" no meio de uma frase ("o Plex guarda os filmes", "o
    # painel guarda apenas um resumo") é português do Brasil correto e não pode
    # ser apanhado aqui.
    (r'^(Guardar|Gravar)\b', 'Salvar'),
    # A vogal fechada: em pt-PT bónus/eletrónico/anónimo, no Brasil com ô.
    (r'\b\w*(ónimo|ónico|ómico|ónus|ónia)\w*\b', 'ô (bônus, eletrônico, anônimo)'),
    (r'\bem falta\b', 'ausente / faltando'),
    (r'\bao fim de\b', 'após / depois de'),
    (r'\bconsoante\b', 'conforme / de acordo com'),
    # "ligação" no Brasil é uma chamada telefónica; uma conexão de rede é
    # "conexão", que é como o resto do painel lhe chama (a aba "Conexões").
    (r'\bligaç(ão|ões)\b', 'conexão / conexões'),
    # Quem dirige um filme: em pt-PT "realizador", no Brasil "diretor". Aparece
    # nas conquistas, que vão por notificação.
    (r'\brealizador(es)?\b', 'diretor / diretores'),
    # "gerir um perfil" é europeu; no Brasil gerencia-se. ⚠️ "gere" fica de
    # fora de propósito: é o imperativo de GERAR ("Gere um relatório", "Gere
    # uma chave no Jellyfin"), que aparece quatro vezes e está certo.
    (r'\bgeri(r|do|da|dos|das)\b', 'gerenciar / gerenciado'),
    (r'\bde momento\b', 'no momento'),
    # As "definições" de um programa são "configurações" — que é como as
    # próprias Configurações do painel se chamam.
    (r'\bdefiniç(ão|ões)\b', 'configuração / configurações'),
]

# ⚠️ As marcas em que a MAIÚSCULA é o que separa o europeu do brasileiro
# correto. Tudo o que não estiver aqui é procurado nas duas formas.
SENSIVEIS = {
    # "Aceite o seu convite" é o imperativo de aceitar, e está certo; o que se
    # recusa é o particípio ("convite aceite" -> "convite aceito").
    r'\baceites?\b',
}

LITERAL = re.compile(r"_\(\s*(['\"])(.*?)\1", re.S)
# A alternativa de uma chave de tradução em falta: `i18n.algumaCoisa || 'texto'`.
ALTERNATIVA_JS = re.compile(r"i18n\.[A-Za-z0-9_]+\s*\|\|\s*(['\"])(.*?)\1", re.S)
MODELO = re.compile(r'^\s*"[A-Z_]+_MESSAGE_TEMPLATE":\s*(.*)$', re.M)

# ⚠️ **Nem toda a mensagem visível passa por `_()`.** As rotas de cupões
# respondiam com `"message": "Cupão apagado com sucesso."` em texto cru — o
# painel mostra isso num toast, e a varredura nunca lá chegou porque só olhava
# para dentro do `_()`. O mesmo vale para o `raise ValueError(...)` de um
# validador do Pydantic, cuja mensagem sai no corpo do 400 e aparece por baixo
# do campo. As duas são texto para uma pessoa ler.
MENSAGEM_PY = re.compile(r'"message":\s*(["\'])(.*?)\1', re.S)
ERRO_PY = re.compile(r'raise ValueError\(\s*(["\'])(.*?)\1', re.S)

# ⚠️ **E o JavaScript também escreve texto à mão.** A alternativa do `i18n.x ||`
# era a única coisa que se lia, mas o `financial.js` tinha "Nenhum cupão ativo
# ou criado." e `title="Apagar Cupão"` escritos dentro da própria marcação.
# Procura-se o que é TEXTO — entre `>` e `<`, ou dentro de um `title=`/`alt=` —,
# e não toda a string do ficheiro: um nome de classe do Tailwind não é uma frase.
TEXTO_EM_MARCACAO_JS = re.compile(r'>([^<>{}`$\n]{4,}?)<')
ATRIBUTO_DE_TEXTO_JS = re.compile(r'\b(?:title|alt|placeholder)\s*[=:]\s*["\']([^"\'\n]{4,})["\']')


def _textos_visiveis():
    """(ficheiro, linha, texto) de tudo o que a pessoa chega a ler."""
    for pasta in PASTAS:
        for caminho in sorted((RAIZ / pasta).rglob('*')):
            if caminho.suffix not in ('.html', '.py'):
                continue
            conteudo = caminho.read_text(encoding='utf-8')
            nome = caminho.relative_to(RAIZ).as_posix()

            for padrao, grupo in ((LITERAL, 2), (MODELO, 1),
                                  (MENSAGEM_PY, 2), (ERRO_PY, 2)):
                for achado in padrao.finditer(conteudo):
                    linha = conteudo.count('\n', 0, achado.start()) + 1
                    yield nome, linha, achado.group(grupo)

    for pasta in PASTAS_JS:
        for caminho in sorted((RAIZ / pasta).rglob('*.js')):
            conteudo = caminho.read_text(encoding='utf-8')
            nome = caminho.relative_to(RAIZ).as_posix()
            for padrao, grupo in ((ALTERNATIVA_JS, 2),
                                  (TEXTO_EM_MARCACAO_JS, 1),
                                  (ATRIBUTO_DE_TEXTO_JS, 1)):
                for achado in padrao.finditer(conteudo):
                    linha = conteudo.count('\n', 0, achado.start()) + 1
                    yield nome, linha, achado.group(grupo)


def _compilar(marca):
    """⚠️ A maiúscula é uma propriedade da MARCA, não da varredura.

    As que começam por `^` dependem dela por construção (é o que distingue o
    rótulo "A guardar..." de "obriga o usuário A ENTRAR"); as outras estão
    nomeadas em `SENSIVEIS`. Todo o resto procura-se nas duas formas.
    """
    if marca.startswith('^') or marca in SENSIVEIS:
        return re.compile(marca)
    return re.compile(marca, re.I)


@pytest.mark.parametrize('marca, sugestao', MARCAS)
def test_nenhum_texto_visivel_usa_vocabulario_europeu(marca, sugestao):
    padrao = _compilar(marca)
    infratores = [
        f"{nome}:{linha} — {texto[:80]}"
        for nome, linha, texto in _textos_visiveis()
        if padrao.search(texto)
    ]

    assert infratores == [], (
        f"Use '{sugestao}'. Encontrado em:\n  " + "\n  ".join(infratores[:10])
    )


def test_so_o_que_precisa_da_maiuscula_e_que_depende_dela():
    """🐛 Foi uma varredura inteira sem `re.I` que escondeu dezoito textos.

    Voltar a pôr uma marca comum em `SENSIVEIS` — ou a varredura inteira —
    abre outra vez o buraco, e a próxima "Utilizador não encontrado" passa.
    """
    assert SENSIVEIS <= {marca for marca, _ in MARCAS}, (
        "há uma marca em SENSIVEIS que já não existe na lista"
    )
    # A prova de que a distinção é mesmo precisa: o imperativo passa, o
    # particípio não.
    aceite = _compilar(r'\baceites?\b')
    assert not aceite.search('Aceite o seu convite e comece a assistir.')
    assert aceite.search('O convite foi aceite.')

    # E a prova de que o resto NÃO depende dela.
    assert _compilar(r'\butilizador(es)?\b').search('Utilizador não encontrado.')
    assert _compilar(r'\bcup(ão|ões)\b').search('INSERIR CUPÃO')


def test_a_varredura_encontra_mesmo_alguma_coisa():
    """Um teste que não lê nada passa sempre — e não guarda nada."""
    textos = list(_textos_visiveis())

    assert len(textos) > 1000
