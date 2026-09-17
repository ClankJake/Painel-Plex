# tests/test_caixa_da_chave_de_api.py
"""A caixa que mostra a chave de API acabada de criar.

🐛 Ela era acrescentada DENTRO do `#apiKeysList`, e a criação recarrega a lista
logo a seguir — `carregarChavesDeApi()` reescreve o `innerHTML` desse contentor
e levava a caixa com ela. Quem criava uma chave via-a piscar e desaparecer
antes de conseguir copiá-la, e a chave só é mostrada UMA vez: o painel guarda
só um resumo e não a consegue mostrar de novo. A integração ficava por ligar e
não havia nada a fazer senão criar outra chave — e ver o mesmo.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MODULO = RAIZ / "app" / "static" / "js" / "settings_modules" / "api_keys.js"
ABA = RAIZ / "app" / "templates" / "settings" / "tabs" / "general.html"
PAGINA = RAIZ / "app" / "templates" / "settings.html"


def corpo_da_funcao(fonte: str, nome: str) -> str:
    """O corpo de uma função de topo, do `{` até à `}` na coluna zero."""
    inicio = fonte.index(f"function {nome}(")
    fim = fonte.index("\n}", inicio)
    return fonte[inicio:fim]


def ids_reescritos(corpo: str, fonte: str) -> set:
    """Os ids de elementos cujo `innerHTML` este corpo substitui.

    As funções do módulo guardam o elemento numa variável vinda de um ajudante
    (`lista()`, `caixaDaChaveNova()`), por isso segue-se o ajudante até ao
    `getElementById`.
    """
    ids = set()
    for ajudante in re.findall(r"=\s*(\w+)\(\);", corpo):
        if f"function {ajudante}(" not in fonte:
            continue
        ids.update(re.findall(r"getElementById\('([^']+)'\)", corpo_da_funcao(fonte, ajudante)))
    return ids


class TestAChaveNaoDesapareceAntesDeSerCopiada:
    def test_a_caixa_nao_vive_no_contentor_que_a_listagem_reescreve(self):
        """
        🐛 O bug inteiro numa linha: os dois contentores eram o mesmo, e a
        recarga da lista apagava a única cópia da chave que existe.
        """
        fonte = MODULO.read_text(encoding="utf-8")

        da_lista = ids_reescritos(corpo_da_funcao(fonte, "carregarChavesDeApi"), fonte)
        da_caixa = ids_reescritos(corpo_da_funcao(fonte, "mostrarAChaveNova"), fonte)

        assert da_lista, "a listagem deixou de escrever num contentor conhecido"
        assert da_caixa, "a caixa da chave nova deixou de escrever num contentor conhecido"
        assert not (da_lista & da_caixa), (
            "a chave acabada de criar é escrita no mesmo contentor que a "
            f"listagem reescreve ({da_lista & da_caixa}): a recarga apaga-a"
        )

    def test_o_contentor_da_caixa_existe_no_template(self):
        """Um id que o HTML não tem é uma caixa que nunca aparece, sem erro."""
        fonte = MODULO.read_text(encoding="utf-8")
        html = ABA.read_text(encoding="utf-8")

        for identificador in ids_reescritos(corpo_da_funcao(fonte, "mostrarAChaveNova"), fonte):
            assert f'id="{identificador}"' in html

    def test_a_lista_recarrega_antes_de_a_chave_ser_mostrada(self):
        """
        A ordem é a segunda metade da correção: com a recarga a seguir, bastava
        alguém voltar a pô-la dentro do contentor errado para o bug regressar.
        """
        criar = corpo_da_funcao(MODULO.read_text(encoding="utf-8"), "criar")

        recarga = criar.index("carregarChavesDeApi(")
        mostra = criar.index("mostrarAChaveNova(")
        assert recarga < mostra


class TestOsRotulosDaCaixa:
    def test_os_textos_que_a_caixa_lê_existem_no_template(self):
        """
        Uma chave `i18n` em falta não dá erro: dá o texto por omissão escrito no
        JavaScript, que não passa pelo tradutor nem pela revisão do português.
        """
        fonte = MODULO.read_text(encoding="utf-8")
        pagina = PAGINA.read_text(encoding="utf-8")

        declaradas = {
            "".join(
                parte if i == 0 else parte.capitalize()
                for i, parte in enumerate(atributo.split("-"))
            )
            for atributo in re.findall(r'data-i18n-([\w-]+)=', pagina)
        }

        for usada in re.findall(r"i18n\.(\w+)", corpo_da_funcao(fonte, "mostrarAChaveNova")):
            assert usada in declaradas, f"i18n.{usada} não tem data-i18n-* no settings.html"
