# tests/test_recomendacoes_do_administrador.py

"""O cartão "Feito a Pensar em Si" também é do dono do servidor.

⚠️ **A rota sempre soube responder-lhe** — ela devolve as recomendações de QUEM
PEDE, e o administrador tem histórico como toda a gente. O que o deixava de
fora era o TEMPLATE: a secção vivia dentro do `{% else %}` da visão do
utilizador comum, por isso os ids que o `statistics.js` procura nem existiam na
página dele.

A marcação passou a um partial incluído pelas duas visões: duas cópias
divergiam ao primeiro ajuste, e o JavaScript procura esses ids pelo nome.
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

RAIZ = Path(__file__).resolve().parent.parent

ADMIN = '877f45d04a454365b14484673dd7289d'


def _historico():
    """O administrador viu dois filmes; outros dois viram esses e mais um.

    É o mínimo para o filtro colaborativo dizer alguma coisa: o `min_co` por
    omissão é 2, e um único espectador em comum é coincidência, não sinal.
    """
    linhas = []
    for utilizador, filmes in ((ADMIN, ["1", "2"]), ("9", ["1", "2", "3"]), ("10", ["1", "2", "3"])):
        for chave in filmes:
            linhas.append({
                "user_id": utilizador, "media_type": "movie", "rating_key": chave,
                "title": f"Filme {chave}", "year": 2020, "percent_complete": 100,
                "thumb": f"/library/metadata/{chave}/thumb", "genres": ["Ficção"],
            })
    return linhas


class FonteFalsa:
    """Uma fonte de estatísticas com o histórico do teste."""

    is_configured = True

    def get_history(self, **_kwargs):
        return {"data": _historico()}

    def get_metadata(self, _rating_key):
        return {}

    def image_payload(self, thumb, width=300, height=450):
        return f"tautulli:{thumb}?w={width}&h={height}" if thumb else None


@pytest.fixture()
def painel(client, config_file, db_session):
    """Um painel configurado, com a fonte falsa e uma sessão de administrador."""
    config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID=ADMIN,
                RECOMMENDATIONS_ENABLED=True, RECOMMENDATIONS_GENRE_LOOKUP_LIMIT=0)

    from app import extensions
    from app.extensions import cache

    gestor = extensions.stats_manager
    originais = (gestor.api_client, gestor.recommendations.api)
    gestor.api_client = FonteFalsa()
    gestor.recommendations.api = FonteFalsa()
    cache.clear()
    gestor.invalidate_recommendations_cache()

    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": ADMIN, "username": "dono",
                                  "email": "a@b.test", "role": "admin"}
        sessao["_user_id"] = ADMIN
        sessao["_fresh"] = True

    yield client

    gestor.api_client, gestor.recommendations.api = originais
    cache.clear()
    gestor.invalidate_recommendations_cache()


class TestARota:
    def test_o_administrador_recebe_as_recomendacoes_dele(self, painel):
        resposta = painel.get('/api/statistics/recommendations')

        assert resposta.status_code == 200
        dados = resposta.get_json()
        assert dados["success"] is True
        assert dados["reason"] == "ok", "o dono do servidor ficou sem sugestões"

    def test_sao_as_do_PROPRIO_e_nao_as_de_outra_pessoa(self, painel):
        """Ele viu o 1 e o 2; quem os viu também viu o 3."""
        seccoes = painel.get('/api/statistics/recommendations').get_json()["sections"]

        sementes = {s["seed"]["title"] for s in seccoes}
        sugeridos = {i["title"] for s in seccoes for i in s["items"]}

        assert sementes <= {"Filme 1", "Filme 2"}, "a semente não saiu do histórico dele"
        assert sugeridos == {"Filme 3"}
        # 🛡️ E nunca se recomenda o que a pessoa já viu.
        assert not sugeridos & {"Filme 1", "Filme 2"}


class TestOTemplate:
    """A secção tem de EXISTIR na página dele: é o template que a escondia."""

    @staticmethod
    def _pagina(app, role):
        from flask import render_template

        from app.models import User

        utilizador = User(id=ADMIN, username="dono", email="a@b.test", thumb=None, role=role)
        with app.test_request_context("/statistics"):
            with patch("flask_login.utils._get_user", lambda: utilizador):
                return render_template("statistics.html")

    @pytest.mark.parametrize("role", ["admin", "user"])
    def test_os_ganchos_existem_nas_duas_visoes(self, app, role):
        pagina = self._pagina(app, role)

        for gancho in ("recommendations-section", "recommendations-container"):
            assert gancho in pagina, f"'{gancho}' não existe na visão de '{role}'"

    @pytest.mark.parametrize("role", ["admin", "user"])
    def test_a_seccao_aparece_UMA_vez(self, app, role):
        """Dois ids iguais na mesma página fazem o `getElementById` escolher um."""
        pagina = self._pagina(app, role)

        assert pagina.count('id="recommendations-section"') == 1
        assert pagina.count('id="recommendations-container"') == 1

    def test_a_marcacao_nao_esta_copiada(self):
        """Duas cópias divergiam ao primeiro ajuste — daí o partial."""
        template = (RAIZ / "app" / "templates" / "statistics.html").read_text(encoding="utf-8")
        partial = RAIZ / "app" / "templates" / "partials" / "recomendacoes.html"

        assert partial.is_file()
        assert template.count("partials/recomendacoes.html") == 2
        assert 'id="recommendations-section"' not in template


class TestOJavaScript:
    def test_sao_pedidas_seja_qual_for_o_papel(self):
        js = (RAIZ / "app" / "static" / "js" / "statistics.js").read_text(encoding="utf-8")
        arranque = js[js.index("if (dom.daysFilter) {"):]

        # Fora do `if` que separa as duas visões.
        antes_do_if = arranque[:arranque.index("if (currentUser.role !== 'admin')")]
        assert "carregarRecomendacoes();" in antes_do_if, (
            "as recomendações voltaram para dentro do ramo do utilizador comum"
        )
