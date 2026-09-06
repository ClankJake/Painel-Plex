"""
Testes de regressão para o template ``statistics.html``.

Cobrem duas falhas que já existiram e que passavam despercebidas porque
nenhum teste chegava a renderizar a página:

1. ``@apply`` dentro do ``<style>`` do template. O Tailwind deste projeto só
   compila o ``app/static/css/input.css`` (ver ``npm run build:css``); os
   templates são lidos apenas para descobrir nomes de classes. Um ``@apply``
   escrito aqui chega ao browser como CSS inválido e é descartado — as
   medalhas de conquistas e os botões das carrosséis ficavam sem estilo.

2. ``data-current-user`` com ``| safe`` dentro de um atributo entre plicas. O
   JSON traz o ``username`` vindo do Plex: um apóstrofo (``O'Brien``) fechava
   o atributo a meio, o ``JSON.parse`` rebentava e a página inteira deixava
   de carregar; um nome com HTML conseguia injetar markup.
"""

import html
import json
import re

import pytest
from flask import render_template

# Nome de utilizador que o Plex aceita e que partia o atributo/injetava HTML.
USERNAME_HOSTIL = "O'Brien \"X\" <img src=x onerror=alert(1)>"


@pytest.fixture
def _render(app, monkeypatch):
    def _do(role):
        from app.models import User

        utilizador = User(
            id=1, username=USERNAME_HOSTIL, email="a@b.c", thumb=None, role=role
        )
        with app.test_request_context("/statistics"):
            monkeypatch.setattr("flask_login.utils._get_user", lambda: utilizador)
            return render_template("statistics.html")

    return _do


@pytest.mark.parametrize("role", ["admin", "user"])
def test_sem_declaracoes_apply_no_html_servido(_render, role):
    """O @apply nunca é compilado a partir de um template: seria CSS morto."""
    # Uma declaração real acaba sempre em ";" na mesma linha (ao contrário de
    # uma menção em comentário, que queremos poder manter).
    assert not re.search(r"@apply[^\n;]*;", _render(role))


@pytest.mark.parametrize("role", ["admin", "user"])
def test_username_hostil_nao_parte_o_data_current_user(_render, role):
    """O JS faz JSON.parse(dataset.currentUser) — tem de continuar válido."""
    pagina = _render(role)

    correspondencia = re.search(r"data-current-user='([^']*)'", pagina)
    assert correspondencia, "o apóstrofo do username fechou o atributo a meio"

    atributo = correspondencia.group(1)
    assert "<img" not in atributo, "HTML por escapar dentro do atributo"

    # O browser descodifica as entidades ao ler o dataset, tal como aqui.
    assert json.loads(html.unescape(atributo))["username"] == USERNAME_HOSTIL


@pytest.mark.parametrize(
    "role, esperado",
    [
        ("admin", ["mainBarChart", "podiumContainer", "userList", 'scope="col"']),
        ("user", ["newly-added-container", "recommendations-container", "leaderboard-list"]),
    ],
)
def test_ganchos_esperados_pelo_statistics_js(_render, role, esperado):
    """Os ids/atributos que o statistics.js procura têm de existir."""
    pagina = _render(role)
    for gancho in esperado:
        assert gancho in pagina, f"'{gancho}' desapareceu do template"
