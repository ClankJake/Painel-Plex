# -*- coding: utf-8 -*-
"""
Guarda-fogo para toda a pasta de templates.

O Tailwind deste projeto compila APENAS o ``app/static/css/input.css`` (ver o
script ``build:css`` do package.json). Os templates entram no Tailwind só como
*content*: são lidos à procura de NOMES de classes, nunca processados como CSS.

Consequência: um ``@apply`` escrito dentro de um ``<style>`` de um template
chega ao browser como CSS inválido e é descartado em silêncio — a regra inteira
desaparece sem erro nenhum, na consola ou no build. Já aconteceu em sete
templates ao mesmo tempo (medalhas de conquistas sem estilo, botões de carrossel
invisíveis, modal sem fundo), por isso este teste existe.

Onde pôr os estilos:
  * classes partilhadas  -> app/static/css/input.css (aí o @apply é válido);
  * estilos de uma só página -> CSS normal no <style> do template.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"

# Uma declaração real acaba sempre em ";" na mesma linha. Assim uma menção em
# comentário (a explicar precisamente que não se deve usar) não dá falso
# positivo.
DECLARACAO_APPLY = re.compile(r"@apply[^\n;]*;")


def ids(caminhos):
    return [str(c.relative_to(TEMPLATES)) for c in caminhos]


FICHEIROS = sorted(TEMPLATES.rglob("*.html"))


@pytest.mark.parametrize("template", FICHEIROS, ids=ids(FICHEIROS))
def test_template_nao_usa_apply(template):
    conteudo = template.read_text(encoding="utf-8")
    encontrados = DECLARACAO_APPLY.findall(conteudo)
    assert not encontrados, (
        f"{template.relative_to(TEMPLATES)} tem @apply num <style>, que o Tailwind "
        f"nunca compila — a regra é descartada pelo browser. "
        f"Escreve CSS normal aqui, ou move a classe para app/static/css/input.css. "
        f"Encontrado: {encontrados[:2]}"
    )


def test_input_css_continua_a_poder_usar_apply():
    """O contraponto: no input.css o @apply é válido e deve continuar a existir."""
    input_css = TEMPLATES.parents[0] / "static" / "css" / "input.css"
    assert DECLARACAO_APPLY.search(input_css.read_text(encoding="utf-8")), (
        "Se o input.css deixou de usar @apply, este guarda-fogo perdeu o contexto: "
        "confirma que a pipeline do Tailwind não mudou."
    )
