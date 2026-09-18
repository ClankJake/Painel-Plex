# tests/test_carregamento_das_estatisticas.py
"""⚡ A página de estatísticas não espera pelas recomendações para aparecer.

As três chamadas iniciais corriam no mesmo `Promise.all`, e um `Promise.all`
só resolve com a MAIS LENTA: o `statsContainer` ficava escondido — spinner à
vista — até o motor de recomendações responder, e ele lê o histórico do
servidor inteiro. As estatísticas já tinham chegado e ninguém as via.

Recomendações e novidades são informação a mais, não a espinha da página: cada
uma pede o que é seu e aparece quando chegar.
"""

import re
from pathlib import Path

import pytest

FICHEIRO = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "statistics.js"


@pytest.fixture(scope="module")
def js():
    return FICHEIRO.read_text(encoding="utf-8")


def _corpo_de(js, nome):
    """O corpo de uma função `const nome = ... => { ... }`, até à chaveta que fecha."""
    inicio = js.index(f"const {nome} = ")
    abertura = js.index("{", js.index("=>", inicio))
    profundidade = 0
    for posicao in range(abertura, len(js)):
        if js[posicao] == "{":
            profundidade += 1
        elif js[posicao] == "}":
            profundidade -= 1
            if profundidade == 0:
                return js[abertura:posicao + 1]
    raise AssertionError(f"não encontrei o fim de '{nome}'")


class TestOFluxoPrincipalNaoEsperaPorElas:
    def test_o_mainfetch_nao_pede_recomendacoes(self, js):
        assert "recommendationsUrl" not in _corpo_de(js, "mainFetch")

    def test_o_mainfetch_nao_pede_novidades(self, js):
        assert "recentlyAddedUrl" not in _corpo_de(js, "mainFetch")

    def test_nao_ha_um_promise_all_a_juntar_as_tres(self, js):
        """O `Promise.all` é a forma exata do bug: resolve com a mais lenta."""
        assert "Promise.all" not in _corpo_de(js, "mainFetch")

    def test_o_mainfetch_nao_espera_pela_analise_pessoal(self, js):
        """A SEGUNDA chamada a segurar a página.

        O pódio e o ranking já tinham chegado na primeira e ficavam à espera
        de um pedido que não é deles.
        """
        assert "renderUserAnalysis" not in _corpo_de(js, "mainFetch")

    def test_cada_seccao_tem_a_sua_funcao(self, js):
        assert "recommendationsUrl" in _corpo_de(js, "carregarRecomendacoes")
        assert "recentlyAddedUrl" in _corpo_de(js, "carregarNovidades")
        assert "renderUserAnalysis" in _corpo_de(js, "carregarAnalisePessoal")


class TestARespostaQueChegaTarde:
    """⚠️ Soltar um pedido do fluxo principal abre uma corrida.

    Mexer no filtro depressa (30 → 90 → 30) deixa dois em voo, e o mais LENTO
    pode chegar em ÚLTIMO: a página ficava com os números de um período que já
    não é o escolhido, sem erro nenhum e sem nada que o denunciasse.
    """

    @pytest.mark.parametrize("funcao", ["carregarNovidades", "carregarAnalisePessoal"])
    def test_cada_carregamento_tira_a_sua_vez(self, js, funcao):
        corpo = _corpo_de(js, funcao)
        assert "++ultimoPedido." in corpo, "não há como saber se ainda é o pedido atual"
        assert "aMinhaVez !== ultimoPedido." in corpo, "escreve sem confirmar a vez"

    def test_a_analise_e_montada_a_parte_e_trocada_no_fim(self, js):
        """Descartar uma resposta tardia sem deixar a página meio escrita.

        `renderUserAnalysis` escreve direto no contentor que recebe — por isso
        recebe um SOLTO, como no modal, e só entra na página se ainda for o
        pedido atual.
        """
        corpo = _corpo_de(js, "carregarAnalisePessoal")
        assert "document.createElement('div')" in corpo
        assert "replaceChildren(destino)" in corpo
        # A troca vem DEPOIS da confirmação, ou o guarda não guarda nada.
        assert corpo.index("aMinhaVez !== ultimoPedido.") < corpo.index("replaceChildren")


class TestOsEsqueletos:
    def test_ha_um_esqueleto_enquanto_nao_chegam(self, js):
        corpo = _corpo_de(js, "carregarRecomendacoes")
        assert "esqueletoDeRecomendacoes()" in corpo
        # A secção aparece JÁ: escondê-la até a resposta chegar faria o resto
        # da página saltar para baixo quando ela finalmente entrasse.
        assert "classList.remove('hidden')" in corpo
        assert "aria-busy" in corpo

    def test_a_analise_pessoal_tambem_tem_esqueleto(self, js):
        corpo = _corpo_de(js, "carregarAnalisePessoal")
        assert "esqueletoDaAnalisePessoal()" in corpo

    @pytest.mark.parametrize(
        "esqueleto", ["esqueletoDeRecomendacoes", "esqueletoDaAnalisePessoal"]
    )
    def test_as_classes_do_esqueleto_sao_literais(self, js, esqueleto):
        """⚠️ O Tailwind procura nomes de classes LITERAIS nos ficheiros.

        Uma classe montada em tempo de execução nunca chega ao CSS, e o
        esqueleto ficava invisível — sem erro nenhum.
        """
        corpo = _corpo_de(js, esqueleto)
        assert "animate-pulse" in corpo
        assert not re.search(r"class=\"[^\"]*\$\{", corpo)


class TestUmaFalhaDelasNaoDerrubaAPagina:
    @pytest.mark.parametrize("funcao", ["carregarRecomendacoes", "carregarNovidades"])
    def test_a_falha_esconde_a_seccao_e_mais_nada(self, js, funcao):
        corpo = _corpo_de(js, funcao)
        assert "catch" in corpo
        # Nada de `errorContainer`: as estatísticas — que é o que a pessoa veio
        # ver — já estão na página, e pintá-la de vermelho por causa de um
        # extra dizia-lhe que falhou o que não falhou.
        assert "errorContainer" not in corpo


class TestOFiltroDeDiasNaoRepedeORecomendador:
    def test_mudar_de_periodo_nao_volta_a_pedir_as_recomendacoes(self, js):
        """🎯 Elas usam a janela longa do administrador e IGNORAM este filtro.

        Voltar a pedi-las a cada mudança era pagar a chamada mais cara do
        painel para receber exatamente a mesma resposta.
        """
        inicio = js.index("dom.daysFilter?.addEventListener('change'")
        tratador = js[inicio:js.index("});", inicio)]

        assert "carregarNovidades" in tratador
        assert "carregarAnalisePessoal" in tratador
        assert "carregarRecomendacoes" not in tratador

    def test_mas_sao_pedidas_uma_vez_no_arranque(self, js):
        arranque = js[js.index("if (dom.daysFilter) {"):]
        assert "carregarRecomendacoes();" in arranque
        assert "carregarNovidades(" in arranque
        assert "carregarAnalisePessoal(" in arranque


class TestCadaSitioEDonoDosSeusGraficos:
    """🐛 Havia UM espaço para os gráficos e UM para os observadores.

    O modal desenha a MESMA análise que a página (`renderUserAnalysis` serve os
    dois), e enquanto partilhavam esses espaços globais:

    - abrir a análise de outra pessoa destruía os gráficos da análise da
      página, e fechar o modal rematava — os dois canvas ficavam em branco até
      alguém mexer no filtro de dias;
    - o `closeModal` desligava TODOS os `ResizeObserver`, incluindo os das
      "Novidades" e os de cada faixa de recomendações: as setas desses
      carrosséis deixavam de se atualizar ao redimensionar.

    Num painel onde o ranking é clicável para quem não é administrador, bastava
    espreitar a análise de outra pessoa.
    """

    def test_os_espacos_globais_deixaram_de_existir(self, js):
        codigo = "\n".join(
            linha for linha in js.splitlines() if not linha.lstrip().startswith("*")
        )
        assert "state.charts" not in codigo
        assert "state.observers" not in codigo

    def test_o_modal_tem_o_seu_proprio_contexto(self, js):
        corpo = _corpo_de(js, "showUserDetailsModal")
        assert "state.contextos.modal = novoContexto()" in corpo
        assert "state.contextos.modal)" in corpo, "renderiza fora do contexto do modal"

    def test_fechar_o_modal_leva_so_o_que_o_modal_desenhou(self, js):
        corpo = _corpo_de(js, "closeModal")
        assert "destruirContexto(state.contextos.modal)" in corpo
        for outro in ("pagina", "novidades", "recomendacoes", "analise"):
            assert f"contextos.{outro}" not in corpo, f"ainda mexe no contexto '{outro}'"

    @pytest.mark.parametrize(
        "funcao, contexto",
        [
            ("renderNewlyAdded", "novidades"),
            ("renderRecommendations", "recomendacoes"),
        ],
    )
    def test_cada_carrossel_limpa_o_seu_antes_de_redesenhar(self, js, funcao, contexto):
        """Sem isto, cada mudança de filtro prendia mais um observador a uma
        fila que já não está na página."""
        corpo = _corpo_de(js, funcao)
        assert f"destruirContexto(state.contextos.{contexto})" in corpo
        assert f"state.contextos.{contexto})" in corpo

    def test_uma_analise_que_chegou_tarde_nao_deixa_graficos_vivos(self, js):
        """A resposta descartada pelo guarda da corrida criou gráficos na mesma."""
        corpo = _corpo_de(js, "carregarAnalisePessoal")
        descarte = corpo[corpo.index("aMinhaVez !== ultimoPedido.analise"):]
        assert "destruirContexto(contexto)" in descarte[:descarte.index("return")]

    def test_a_analise_anterior_sai_quando_a_nova_entra(self, js):
        corpo = _corpo_de(js, "carregarAnalisePessoal")
        assert "destruirContexto(state.contextos.analise)" in corpo
        assert corpo.index("destruirContexto(state.contextos.analise)") < corpo.index("replaceChildren")

    def test_o_tema_chega_aos_graficos_de_todos_os_sitios(self, js):
        """Antes só alcançava os três espaços globais — com o modal aberto, os
        gráficos dele ficavam com as cores do tema anterior."""
        inicio = js.index("window.addEventListener('themeChanged'")
        tratador = js[inicio:js.index("\n    });", inicio)]
        assert "Object.values(state.contextos)" in tratador
