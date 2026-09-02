# tests/test_log_sanitizer.py
"""Mascaramento de dados sensíveis nos logs (RGPD/LGPD)."""

import pytest

from app.utils.log_sanitizer import (
    mask_email,
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
