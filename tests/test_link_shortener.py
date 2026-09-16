# tests/test_link_shortener.py
"""Encurtador de links usado nas mensagens de cobrança e convite."""

from datetime import datetime

import pytest

from app.extensions import db
from app.models import ShortLink
from app.services import link_shortener as link_shortener_module
from app.services.link_shortener import LinkShortener

pytestmark = pytest.mark.integration


@pytest.fixture()
def shortener(db_session, monkeypatch):
    """LinkShortener com APP_BASE_URL definido, como numa instalação real."""
    monkeypatch.setattr(
        link_shortener_module,
        "load_or_create_config",
        lambda: {"APP_BASE_URL": "https://painel.exemplo.com/"},
    )
    return LinkShortener()


class TestCreateShortLink:
    def test_devolve_um_link_no_dominio_configurado(self, shortener):
        curto = shortener.create_short_link("https://painel.exemplo.com/pay/token123")

        assert curto.startswith("https://painel.exemplo.com/s/")
        assert ShortLink.query.count() == 1

    def test_a_barra_final_do_dominio_nao_e_duplicada(self, shortener):
        assert "//s/" not in shortener.create_short_link("https://destino.exemplo.com")

    def test_o_codigo_resolve_para_a_url_original(self, shortener):
        original = "https://painel.exemplo.com/pay/token123"
        codigo = shortener.create_short_link(original).rsplit("/", 1)[-1]

        assert shortener.get_original_url(codigo) == original

    def test_codigos_diferentes_para_destinos_diferentes(self, shortener):
        primeiro = shortener.create_short_link("https://exemplo.com/a")
        segundo = shortener.create_short_link("https://exemplo.com/b")

        assert primeiro != segundo
        assert ShortLink.query.count() == 2

    def test_o_mesmo_destino_reutiliza_o_codigo_ja_emitido(self, shortener):
        """🐛 REGRESSÃO: o segundo envio MATAVA o link do primeiro.

        O código apagava os links do mesmo destino antes de criar outro, "para
        evitar acumular dezenas de códigos". Só que o destino é o mesmo entre
        envios — `garantir_payment_token` mantém o token enquanto for válido,
        limitando-se a estender a validade — e o aviso de vencimento é DIÁRIO.
        Quem recebia o lembrete de hoje ficava com o de ontem morto: ao rolar a
        conversa para cima tocava num "link expirado" cujo destino continuava
        perfeitamente válido.

        Reutilizar cumpre o objetivo original melhor do que apagar: continua a
        haver UMA linha por destino, e nenhuma mensagem entregue deixa de
        funcionar.
        """
        destino = "https://painel.exemplo.com/pay/token123"
        primeiro = shortener.create_short_link(destino).rsplit("/", 1)[-1]
        segundo = shortener.create_short_link(destino).rsplit("/", 1)[-1]

        assert primeiro == segundo, "o segundo envio emitiu um código novo"
        assert ShortLink.query.count() == 1, "a tabela não pode crescer por envio"
        assert shortener.get_original_url(primeiro) == destino, (
            "o link já entregue à pessoa deixou de funcionar"
        )

    def test_reutilizar_repoe_a_data_para_a_limpeza_nao_o_apagar(self, shortener):
        """⚠️ O `cleanup_job` apaga por `created_at`.

        Sem repor a data, um link reutilizado ao dia 29 morria no dia 30 — logo
        a seguir a ter sido enviado. É a mesma regra do `garantir_payment_token`:
        o que conta é a data do ÚLTIMO envio, não a do primeiro.
        """
        destino = "https://painel.exemplo.com/pay/token456"
        shortener.create_short_link(destino)

        linha = ShortLink.query.filter_by(original_url=destino).one()
        linha.created_at = datetime(2020, 1, 1)
        db.session.commit()

        shortener.create_short_link(destino)

        linha = ShortLink.query.filter_by(original_url=destino).one()
        assert linha.created_at.year > 2020, (
            "a data ficou a de 2020: a limpeza seguinte apagaria um link "
            "acabado de enviar."
        )
        assert linha.created_at.tzinfo is None, (
            "a coluna é sem fuso; misturar as duas formas nela é pior do que a "
            "inconsistência que já existe."
        )

    def test_sem_dominio_configurado_usa_o_url_for(self, db_session, monkeypatch):
        monkeypatch.setattr(
            link_shortener_module, "load_or_create_config", lambda: {"APP_BASE_URL": ""}
        )

        curto = LinkShortener().create_short_link("https://exemplo.com/a")

        assert curto.startswith("http://localhost/s/")

    def test_falha_na_base_de_dados_devolve_a_url_original(self, shortener, monkeypatch):
        from sqlalchemy.exc import SQLAlchemyError

        def rebenta(*args, **kwargs):
            raise SQLAlchemyError("base de dados indisponível")

        monkeypatch.setattr(LinkShortener, "_generate_short_code", rebenta)

        # Nunca se pode devolver um link partido a um utilizador à espera de pagar.
        assert shortener.create_short_link("https://exemplo.com/a") == "https://exemplo.com/a"


class TestGetOriginalUrl:
    def test_codigo_inexistente(self, shortener):
        assert shortener.get_original_url("nao-existe") is None


class TestARotaQueAPessoaAbre:
    """`/s/<code>` visto de fora — que é como ele é sempre usado.

    Os testes acima falam com o serviço; nenhum seguia o caminho que a pessoa
    faz de facto: tocar num link do Telegram, sem sessão no painel.
    """

    def test_sem_sessao_o_link_leva_ao_destino(self, client, config_file, shortener):
        config_file(IS_CONFIGURED=True)
        destino = "https://painel.exemplo.com/pay/token789"
        codigo = shortener.create_short_link(destino).rsplit("/", 1)[-1]

        resposta = client.get(f"/s/{codigo}", follow_redirects=False)

        assert resposta.status_code in (301, 302), (
            "quem recebe o link não tem sessão no painel; se esta rota deixar "
            "de responder a um visitante anónimo, o link de pagamento morre."
        )
        assert resposta.headers["Location"] == destino

    def test_o_lembrete_de_ontem_continua_a_funcionar(self, client, config_file, shortener):
        """A regressão, pelo caminho por onde ela aparecia."""
        config_file(IS_CONFIGURED=True)
        destino = "https://painel.exemplo.com/pay/token789"

        ontem = shortener.create_short_link(destino).rsplit("/", 1)[-1]
        shortener.create_short_link(destino)  # o aviso de hoje

        resposta = client.get(f"/s/{ontem}", follow_redirects=False)

        assert resposta.status_code in (301, 302), (
            "a mensagem de ontem passou a levar à página de 'link expirado'."
        )
        assert resposta.headers["Location"] == destino

    def test_um_codigo_que_nao_existe_explica_se(self, client, config_file):
        config_file(IS_CONFIGURED=True)
        resposta = client.get("/s/naoexiste", follow_redirects=False)

        assert resposta.status_code == 200, (
            "um código desconhecido mostra uma página com explicação, não um 404 cru."
        )


class TestOTetoDePedidos:
    """`/s/<code>` é pública, sem sessão, e consulta a base de dados por pedido.

    ⚠️ Eu tinha escrito que ela "não tem limite de pedidos". É FALSO, e medi-o:
    o `RATELIMIT_DEFAULT` da aplicação ("200 per day; 50 per hour") aplica-se a
    toda a rota que não declare um limite próprio, e esta não declara. O 429
    chega ao 51.º pedido.

    ⚠️ E acrescentar-lhe um `@limiter.limit` teria AFROUXADO a rota, não
    apertado: no Flask-Limiter um limite de rota SUBSTITUI o padrão. Medido:
    com `60 per minute` o 429 passava do 51.º para o 61.º pedido, e o teto
    diário desaparecia. Este teste existe para que a rota fique sem decorador
    — e para ninguém lhe acrescentar um a pensar que a está a proteger.
    """

    @pytest.fixture(autouse=True)
    def contador_limpo(self):
        """O limitador conta em memória, partilhada pela sessão de testes
        inteira. Sem limpar, este teste gastaria a quota de `/s/` e os outros
        desta suíte apanhariam 429 sem nada a ver com o que testam."""
        from app.extensions import limiter

        limiter.reset()
        yield
        limiter.reset()

    def test_a_rota_e_travada_pelo_padrao_global(self, client, config_file, shortener):
        config_file(IS_CONFIGURED=True)
        codigo = shortener.create_short_link(
            "https://painel.exemplo.com/pay/tk"
        ).rsplit("/", 1)[-1]

        estados = [client.get(f"/s/{codigo}").status_code for _ in range(70)]

        assert 429 in estados, "a rota aceitou 70 pedidos seguidos sem travar."
        primeiro = estados.index(429) + 1
        assert primeiro <= 51, (
            f"o 429 só apareceu ao {primeiro}.º pedido. O padrão global trava "
            f"ao 51.º; um número maior quer dizer que alguém pôs um "
            f"`@limiter.limit` nesta rota e, com isso, AFROUXOU-A."
        )

    def test_um_clique_normal_nunca_e_travado(self, client, config_file, shortener):
        """O caso que não pode partir: a pessoa toca no link e entra."""
        config_file(IS_CONFIGURED=True)
        destino = "https://painel.exemplo.com/pay/tk"
        codigo = shortener.create_short_link(destino).rsplit("/", 1)[-1]

        for _ in range(5):
            resposta = client.get(f"/s/{codigo}", follow_redirects=False)
            assert resposta.status_code in (301, 302)
            assert resposta.headers["Location"] == destino
