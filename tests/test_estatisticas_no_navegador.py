# tests/test_estatisticas_no_navegador.py
"""A `/statistics` a correr mesmo, num Chromium.

Os testes de `test_carregamento_das_estatisticas.py` leem o TEXTO do
JavaScript: provam que a estrutura está certa e apanham uma regressão, mas não
tocam no DOM. Três das coisas que este ficheiro guarda não se veem de outra
maneira — se um gráfico do Chart.js continua vivo, se um `ResizeObserver` foi
desligado, e quanto tempo a página demora mesmo a aparecer.

⚠️ **O JavaScript é o do repositório, sem uma linha de diferença.** O que é
falso são só as respostas HTTP, servidas por um `http.server` local; e o
template é o real, renderizado pelo Jinja como em `test_statistics_template.py`.
Um duplo com a sua própria cópia do comportamento deixava de testar o painel.

Salta sozinho onde não houver Playwright, Chromium ou `app/static/dist/` — a
mesma regra do `test_assets_frontend.py`, e `PAINEL_EXIGE_NAVEGADOR=1`
transforma o salto numa falha, para um passo que exista para apanhar isto não
poder passar sem correr.

Corre com:

    pip install playwright && python -m playwright install chromium
    npm run build
    pytest tests/test_estatisticas_no_navegador.py
"""

import json
import os
import socketserver
import threading
import time
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DIST = RAIZ / "app" / "static" / "dist"
# A única pasta de onde o servidor do teste serve o que vem no pedido.
ESTATICOS = RAIZ / "app" / "static"

# Quanto tempo as recomendações demoram a responder, neste teste. É o número
# que dá sentido à pergunta "a página esperou por elas?".
ATRASO_DAS_RECOMENDACOES = 3.0

# Instrumenta o `ResizeObserver` ANTES de qualquer módulo correr: é a única
# forma de saber, do lado de fora, quais foram desligados e por quem.
ESPIAO_DOS_OBSERVADORES = """
window.__observadores = [];
const Real = window.ResizeObserver;
window.ResizeObserver = class extends Real {
    constructor(cb) { super(cb); this.__desligado = false; window.__observadores.push(this); }
    disconnect() { this.__desligado = true; return super.disconnect(); }
};
"""


def _dentro_de(pasta, alvo):
    """`alvo` se ele estiver mesmo DENTRO de `pasta`; `None` se escapar.

    🛡️ **O caminho vem do PEDIDO, e juntá-lo à raiz é uma travessia de
    diretórios**: um `GET /static/../../../../etc/hostname` saía da pasta e o
    servidor devolvia o ficheiro, com 200. Isto vive só dentro de um pytest,
    ligado ao localhost e numa porta efémera — mas o padrão é o mesmo que
    estaria errado em produção, e um teste que existe para provar que o painel
    está bem não é sítio para o deixar escrito.

    ⚠️ Compara-se depois de `resolve()`: é ele que come os `..`, e sem isso a
    verificação olharia para um caminho que ainda não é o que vai ser aberto.
    """
    try:
        resolvido = alvo.resolve()
    except OSError:  # pragma: no cover - caminho impossível de resolver
        return None
    return resolvido if resolvido.is_relative_to(pasta.resolve()) else None


def _saltar_ou_falhar(motivo):
    if os.environ.get("PAINEL_EXIGE_NAVEGADOR") == "1":
        pytest.fail(motivo)
    pytest.skip(motivo)


def _chromium():
    """O executável do Chromium, ou None se não houver nenhum."""
    pasta = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if pasta:
        for caminho in sorted(Path(pasta).glob("chromium-*/chrome-linux/chrome")):
            return str(caminho)
    return None


# ---------------------------------------------------------------------------
# As respostas falsas (com a forma que as rotas reais devolvem)
# ---------------------------------------------------------------------------
def _analise():
    return {"success": True, "details": {
        "movie_count": 12, "episode_count": 40,
        "total_movie_duration": 43200, "total_episode_duration": 72000,
        "favorite_genre": "Ficção",
        "weekly_activity_js": [3600, 7200, 0, 1800, 5400, 900, 4500],
        "recent": [{"type": "movie", "title": "Um Filme", "series": "",
                    "poster_url": "", "play_date": "01/09/2026"}],
        "achievements": [{"level": "gold", "icon": "🏆", "title": "Maratonista",
                          "description": "Muitas horas"}],
        "level_info": {"level_number": 4, "level_name": "Cinéfilo", "level_icon": "🎬",
                       "xp": 1200, "xp_for_next_level": 300, "progress_percent": 62,
                       "is_max_level": False},
    }}


_ESTATISTICAS = {"success": True, "stats": [
    {"username": "ana", "original_username": "ana", "user_id": "7", "thumb": "",
     "total_duration": 115200, "plays": 52, "is_private": False,
     "level_info": {"level_number": 4, "level_name": "Cinéfilo", "level_icon": "🎬"}},
    {"username": "bruno", "original_username": "bruno", "user_id": "9", "thumb": "",
     "total_duration": 90000, "plays": 31, "is_private": False,
     "level_info": {"level_number": 3, "level_name": "Espectador", "level_icon": "🍿"}},
]}

# ⚠️ TRÊS, e não uma dúzia: o teste das setas precisa que o conteúdo CAIBA numa
# janela larga e transborde numa estreita. Com capas a mais, a seta da direita
# fica ativa em qualquer largura e o teste passa sem provar nada.
_NOVIDADES = {"success": True, "media": [
    {"title": f"Novidade {i}", "year": 2026, "media_type": "movie",
     "poster_url": "", "added_at": 1757000000} for i in range(3)]}

_RECOMENDACOES = {"success": True, "reason": "ok", "sections": [
    {"seed": {"key": "movie:1", "title": "Duna", "year": 2021, "media_type": "movie",
              "rating_key": "1", "poster_url": "", "plays": 3, "item_url": None},
     "source": "viewers",
     "items": [{"key": f"movie:{i}", "title": f"Sugestão {i}", "year": 2020,
                "media_type": "movie", "rating_key": str(i), "poster_url": "",
                "genres": ["Ficção"], "match_type": "viewers", "shared_viewers": 4,
                "shared_genres": [], "score": 0.5, "item_url": None} for i in range(6)]}]}


def _construir_servidor(pagina_html):
    class Servidor(SimpleHTTPRequestHandler):
        def do_GET(self):
            caminho = self.path.split("?")[0]

            if caminho.startswith("/api/"):
                self._api(caminho)
                return

            if caminho.startswith("/static/"):
                ficheiro = _dentro_de(ESTATICOS, RAIZ / "app" / caminho.lstrip("/"))
            elif caminho == "/service-worker.js":
                # O painel serve-o da RAIZ, e é daí que vem o alcance dele.
                ficheiro = RAIZ / "app" / "static" / "js" / "service-worker.js"
            elif caminho in ("/", "/statistics"):
                ficheiro = pagina_html
            else:
                self.send_error(404)
                return

            if ficheiro is None or not ficheiro.is_file():
                self.send_error(404)
                return

            tipos = {".html": "text/html", ".js": "text/javascript",
                     ".mjs": "text/javascript", ".css": "text/css"}
            self._responder(ficheiro.read_bytes(),
                            tipos.get(ficheiro.suffix, "application/octet-stream"))

        def _api(self, caminho):
            # ⏳ O atraso é aqui, numa thread do servidor: é uma resposta lenta
            # de verdade, e não o cliente a fingir que espera.
            if caminho.startswith("/api/statistics/recommendations"):
                time.sleep(ATRASO_DAS_RECOMENDACOES)
                corpo = _RECOMENDACOES
            elif caminho.startswith("/api/statistics/user/"):
                corpo = _analise()
            elif caminho.startswith("/api/statistics/recently-added"):
                corpo = _NOVIDADES
            elif caminho.rstrip("/") == "/api/statistics":
                corpo = _ESTATISTICAS
            elif caminho.startswith("/api/notifications"):
                corpo = {"success": True, "notifications": [], "unread_count": 0}
            else:
                corpo = {"success": True}
            self._responder(json.dumps(corpo).encode(), "application/json")

        def _responder(self, dados, tipo):
            self.send_response(200)
            self.send_header("Content-Type", f"{tipo}; charset=utf-8")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def log_message(self, *_a):
            pass

    return Servidor


@pytest.fixture(scope="module")
def pagina(app, tmp_path_factory):
    """A `/statistics` real, carregada num Chromium com as recomendações lentas."""
    playwright_api = pytest.importorskip(
        "playwright.sync_api", reason="o Playwright não está instalado"
    )
    executavel = _chromium()
    if not executavel:
        _saltar_ou_falhar("não há um Chromium do Playwright neste ambiente.")
    if not DIST.is_dir():
        _saltar_ou_falhar("app/static/dist/ ainda não foi gerado (corra `npm run build`).")

    from app.models import User

    utilizador = User(id="7", username="ana", email="ana@exemplo.pt", thumb=None, role="user")
    with app.test_request_context("/statistics"):
        with patch("flask_login.utils._get_user", lambda: utilizador):
            from flask import render_template
            html = render_template("statistics.html")

    ficheiro = tmp_path_factory.mktemp("navegador") / "statistics.html"
    ficheiro.write_text(html, encoding="utf-8")

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    servidor = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _construir_servidor(ficheiro))
    porta = servidor.server_address[1]
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    erros = []
    with playwright_api.sync_playwright() as pw:
        navegador = pw.chromium.launch(executable_path=executavel)
        aba = navegador.new_page(viewport={"width": 1280, "height": 900})
        aba.add_init_script(ESPIAO_DOS_OBSERVADORES)
        aba.on("console", lambda m: erros.append(m.text) if m.type == "error" else None)
        aba.on("pageerror", lambda e: erros.append(str(e)))

        aba.goto(f"http://127.0.0.1:{porta}/statistics")
        aba.erros_da_consola = erros
        yield aba

        navegador.close()
    servidor.shutdown()


class TestAPaginaNaoEsperaPelasRecomendacoes:
    """⚡ O `Promise.all` só resolve com a mais LENTA.

    As estatísticas chegavam e ninguém as via: o `statsContainer` ficava
    escondido, com o spinner à frente, até o motor de recomendações responder —
    e ele lê o histórico do servidor inteiro.
    """

    def test_as_estatisticas_aparecem_primeiro(self, pagina):
        pagina.wait_for_selector("#statsContainer:not(.hidden)", timeout=15_000)
        decorrido = pagina.evaluate("performance.now()") / 1000

        assert decorrido < ATRASO_DAS_RECOMENDACOES, (
            f"a página só apareceu ao fim de {decorrido:.1f}s, com as recomendações "
            f"a demorar {ATRASO_DAS_RECOMENDACOES}s: esperou por elas."
        )

    def test_o_ranking_ja_esta_preenchido(self, pagina):
        pagina.wait_for_selector("#statsContainer:not(.hidden)", timeout=15_000)
        assert pagina.locator("#leaderboard-list div[data-plex-user-id]").count() == 2

    def test_o_esqueleto_ocupa_o_lugar_das_faixas(self, pagina):
        pagina.wait_for_selector("#statsContainer:not(.hidden)", timeout=15_000)
        assert pagina.locator("#recommendations-container .animate-pulse").count() == 1

    def test_as_faixas_entram_quando_chegam(self, pagina):
        pagina.wait_for_selector("#recommendation-row-0", timeout=20_000)

        assert pagina.locator("#recommendation-row-0 > *").count() == 6
        assert pagina.locator("#recommendations-container .animate-pulse").count() == 0


class TestOModalNaoApagaAPagina:
    """🐛 Havia UM espaço para os gráficos e UM para os observadores.

    O modal desenha a MESMA análise que a página, e enquanto partilhavam esses
    espaços, espreitar a análise de outra pessoa deixava os canvas da própria
    em branco e desligava os observadores de todos os carrosséis.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def depois_do_modal(pagina):
        """Abre a análise de outra pessoa, fecha-a, e diz o que ficou."""
        pagina.wait_for_selector("#personal-analysis canvas#contentTypeChart", timeout=20_000)
        pagina.wait_for_selector("#recommendation-row-0", timeout=20_000)

        vivos = """() => ({
            contentType: !!Chart.getChart(document.querySelector('#personal-analysis #contentTypeChart')),
            activity: !!Chart.getChart(document.querySelector('#personal-analysis #activityBarChart')),
        })"""
        antes = pagina.evaluate(vivos)
        observadores_da_pagina = pagina.evaluate("window.__observadores.length")

        pagina.locator("#leaderboard-list div[data-plex-user-id='9']").click()
        pagina.wait_for_selector("#userDetailsModal canvas#contentTypeChart", timeout=20_000)
        pagina.locator("#modalCloseBtn").click()
        pagina.wait_for_selector("#userDetailsModal.hidden", state="attached", timeout=20_000)

        return {
            "antes": antes,
            "depois": pagina.evaluate(vivos),
            "observadores_da_pagina": observadores_da_pagina,
            "desligados": pagina.evaluate(
                "n => window.__observadores.slice(0, n).filter(o => o.__desligado).length",
                observadores_da_pagina),
        }

    def test_os_graficos_estavam_vivos_antes(self, depois_do_modal):
        assert depois_do_modal["antes"] == {"contentType": True, "activity": True}

    def test_os_graficos_da_pagina_sobrevivem(self, depois_do_modal):
        assert depois_do_modal["depois"] == {"contentType": True, "activity": True}, (
            "abrir e fechar a análise de outra pessoa destruiu os gráficos da "
            "análise da própria — os dois canvas ficam em branco."
        )

    def test_nenhum_observador_da_pagina_foi_desligado(self, depois_do_modal):
        assert depois_do_modal["observadores_da_pagina"] > 0, "o espião não viu nada"
        assert depois_do_modal["desligados"] == 0, (
            f"o modal desligou {depois_do_modal['desligados']} dos "
            f"{depois_do_modal['observadores_da_pagina']} observadores da página."
        )

    def test_as_setas_dos_carrosseis_continuam_a_reagir(self, pagina, depois_do_modal):
        """A pergunta que interessa: continuam a FUNCIONAR?

        Alargar a janela até as três capas caberem tem de desativar a seta da
        direita — e só um `ResizeObserver` vivo dá por isso, porque alargar não
        dispara nenhum evento de scroll.
        """
        seta = "document.getElementById('scroll-right-btn').disabled"

        pagina.set_viewport_size({"width": 520, "height": 900})
        pagina.wait_for_timeout(400)
        estreito = pagina.evaluate(seta)

        pagina.set_viewport_size({"width": 2400, "height": 900})
        pagina.wait_for_timeout(700)
        largo = pagina.evaluate(seta)

        assert (estreito, largo) == (False, True), (
            f"a seta ficou presa (estreito: desativada={estreito}, "
            f"largo: desativada={largo}): o observador dela já não corre."
        )


class TestOServidorDoTesteNaoSaiDaPasta:
    """🛡️ Um `GET /static/../../../../etc/hostname` devolvia o ficheiro, com 200.

    Isto vive só dentro de um pytest, ligado ao localhost e numa porta
    efémera — mas o padrão é o mesmo que estaria errado em produção, e um
    teste que existe para provar que o painel está bem não é sítio para o
    deixar escrito. Foi o CodeQL a apanhá-lo no PR.

    ⚠️ Não precisa de navegador: é sobre o servidor, não sobre a página.
    """

    @pytest.mark.parametrize("caminho", [
        "/static/../../../../../../etc/hostname",
        "/static/../config/config.json",
        "/static/js/../../../run.py",
    ])
    def test_recusa_o_que_esta_fora(self, caminho):
        assert _dentro_de(ESTATICOS, RAIZ / "app" / caminho.lstrip("/")) is None

    def test_e_continua_a_servir_o_que_esta_dentro(self):
        """Um guarda que recusa tudo passaria neste ficheiro sem servir nada."""
        dentro = _dentro_de(ESTATICOS, RAIZ / "app" / "static/js/statistics.js")

        assert dentro is not None
        assert dentro.is_file()


def test_sem_erros_de_javascript(pagina):
    """Um `TypeError` num módulo ES não aparece no log do servidor.

    Ele fica na consola de quem usa o painel — ver o `renderInvites()` no
    CLAUDE.md. Aqui há uma consola para o ver.
    """
    pagina.wait_for_selector("#recommendation-row-0", timeout=20_000)

    # As capas de exemplo vêm de um domínio externo que o ambiente de teste não
    # alcança, e não há servidor de socket.io deste lado: isso não é do painel.
    ruido = ("Failed to load resource", "placehold.co", "favicon",
             "manifest", "ERR_", "socket")
    do_painel = [e for e in pagina.erros_da_consola
                 if not any(p.lower() in e.lower() for p in ruido)]

    assert do_painel == []
