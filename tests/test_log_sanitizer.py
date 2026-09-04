# tests/test_log_sanitizer.py
"""Mascaramento de dados sensíveis nos logs (RGPD/LGPD)."""

import pytest

from app.utils.log_sanitizer import (
    mask_code,
    mask_email,
    mask_link,
    mask_phone,
    mask_secret,
    mask_token,
    mask_url_credentials,
)


class TestMaskPhone:
    def test_preserva_prefixo_e_ultimos_digitos(self):
        assert mask_phone("5521985852539") == "5521*****2539"

    def test_ignora_formatacao(self):
        assert mask_phone("+55 (21) 98585-2539") == mask_phone("5521985852539")

    @pytest.mark.parametrize("valor", ["", None, 0])
    def test_valor_vazio(self, valor):
        assert mask_phone(valor) == "(vazio)"

    def test_numero_curto_esconde_quase_tudo(self):
        assert mask_phone("12345") == "1***"

    def test_sem_digitos(self):
        assert mask_phone("abc") == "(inválido)"

    def test_numero_completo_nunca_aparece(self):
        numero = "5521985852539"
        assert numero not in mask_phone(numero)


class TestMaskEmail:
    def test_preserva_inicial_e_dominio(self):
        assert mask_email("joao.silva@gmail.com") == "j*********@gmail.com"

    def test_local_de_um_caractere(self):
        assert mask_email("a@gmail.com") == "*@gmail.com"

    def test_vazio(self):
        assert mask_email("") == "(vazio)"

    def test_texto_sem_arroba(self):
        assert mask_email("nao-e-email") == "***"


class TestMaskToken:
    def test_mostra_inicio_e_comprimento(self):
        assert mask_token("9b758729835449ec966dde5d6b652987") == "9b75...(32 chars)"

    def test_token_curto_e_totalmente_ocultado(self):
        assert mask_token("abc") == "***"

    def test_numero_de_caracteres_visiveis_configuravel(self):
        assert mask_token("abcdefghij", visible=2) == "ab...(10 chars)"

    def test_vazio(self):
        assert mask_token(None) == "(vazio)"


class TestMaskSecret:
    def test_nunca_revela_caracteres(self):
        mascarado = mask_secret("super-secreta")
        assert mascarado == "***(13 chars)"
        assert "super" not in mascarado

    def test_vazio(self):
        assert mask_secret("") == "(não definido)"


class TestMaskUrlCredentials:
    def test_remove_utilizador_e_senha(self):
        url = "http://admin:senha123@plex.local:32400/status"
        assert mask_url_credentials(url) == "http://***:***@plex.local:32400/status"
        assert "senha123" not in mask_url_credentials(url)

    def test_url_sem_credenciais_fica_intacta(self):
        url = "https://plex.local:32400/status"
        assert mask_url_credentials(url) == url

    def test_none_e_devolvido_tal_como_esta(self):
        assert mask_url_credentials(None) is None


class TestMaskCode:
    """
    Um código de convite é um segredo partilhável: quem o lê nos logs consegue
    resgatar o convite e entrar no servidor.
    """

    def test_mostra_o_prefixo_e_o_comprimento(self):
        assert mask_code("9bT4xQ2mL8vZaBcDeFgHiJ") == "9bT4...(22 chars)"

    def test_codigo_curto_revela_menos(self):
        # Com 4 caracteres fixos, 'PROMO' ficaria quase todo à vista.
        assert mask_code("PROMO") == "P...(5 chars)"

    def test_codigo_muito_curto_e_totalmente_ocultado(self):
        assert mask_code("ab") == "***(2 chars)"

    def test_nunca_revela_mais_de_um_terco(self):
        for tamanho in range(1, 40):
            codigo = "abcdefghijklmnopqrstuvwxyz0123456789ABCD"[:tamanho]
            mascarado = mask_code(codigo)
            # Ou não revela nada ('***(N chars)'), ou revela um prefixo antes de '...'
            visivel = mascarado.split("...")[0] if "..." in mascarado else ""
            assert len(visivel) <= tamanho // 3
            # O que é revelado tem de ser sempre um prefixo do código original.
            assert codigo.startswith(visivel)

    def test_vazio(self):
        assert mask_code(None) == "(vazio)"


class TestMaskLink:
    """
    O encurtador embrulha links de convite do Plex (com `invite_token`, uma
    credencial viva) e links de pagamento (com o `payment_token`). Ambos eram
    registados por inteiro na criação e outra vez a cada clique.
    """

    def test_esconde_o_ultimo_segmento_do_caminho(self):
        mascarado = mask_link("https://painel.exemplo/invite/9bT4xQ2mL8vZaBcDeFgHiJ")
        assert mascarado == "https://painel.exemplo/invite/9bT4...(22 chars)"
        assert "9bT4xQ2mL8vZaBcDeFgHiJ" not in mascarado

    def test_esconde_o_valor_de_parametros_sensiveis(self):
        url = "https://clients.plex.tv/servers/shared_servers/accept?invite_token=SEGREDOabcdef123"
        mascarado = mask_link(url)
        assert "SEGREDOabcdef123" not in mascarado
        assert "invite_token=" in mascarado

    def test_preserva_o_que_serve_para_diagnosticar(self):
        mascarado = mask_link("https://painel.exemplo/pay/TOKENsecretoAqui123")
        assert mascarado.startswith("https://painel.exemplo/pay/")

    def test_parametros_inofensivos_ficam_legiveis(self):
        assert "lang=pt" in mask_link("https://painel.exemplo/x/abc?lang=pt")

    def test_descarta_o_fragmento(self):
        mascarado = mask_link("https://app.plex.tv/auth#?code=SEGREDO&clientID=xyz")
        assert "SEGREDO" not in mascarado

    def test_url_relativo_tratado_como_codigo(self):
        assert mask_link("apenas-um-codigo") == mask_code("apenas-um-codigo")

    def test_vazio(self):
        assert mask_link(None) == "(vazio)"
