# tests/test_image_security.py
"""Proteções do proxy de imagens contra SSRF (acesso à rede interna do servidor)."""

import pytest

from app.blueprints import image as image_module
from app.blueprints.image import (
    build_final_url,
    get_cache_filepath,
    is_private_ip,
    validate_external_url,
)


class TestIsPrivateIp:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1",        # loopback
        "10.0.0.1",         # rede privada
        "172.16.0.1",       # rede privada
        "192.168.1.1",      # rede privada
        "169.254.169.254",  # metadados de cloud (AWS/GCP) — alvo clássico de SSRF
        "0.0.0.0",          # não especificado
        "::1",              # loopback IPv6
        "fe80::1",          # link-local IPv6
        "::ffff:127.0.0.1", # IPv4 mapeado em IPv6
        "224.0.0.1",        # multicast
    ])
    def test_enderecos_internos_sao_bloqueados(self, ip):
        assert is_private_ip(ip) is True

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"])
    def test_enderecos_publicos_sao_permitidos(self, ip):
        assert is_private_ip(ip) is False

    @pytest.mark.parametrize("valor", ["não é um ip", "", "999.999.999.999"])
    def test_valores_invalidos_falham_de_forma_segura(self, valor):
        # Na dúvida, bloqueia.
        assert is_private_ip(valor) is True


class TestValidateExternalUrl:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/imagem.png",
        "http://10.0.0.1/imagem.png",
        "http://192.168.1.10:8080/imagem.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/imagem.png",
    ])
    def test_urls_para_a_rede_interna_sao_recusados(self, url):
        with pytest.raises(ValueError):
            validate_external_url(url)

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://exemplo.com/x.png",
        "gopher://exemplo.com",
    ])
    def test_esquemas_nao_http_sao_recusados(self, url):
        with pytest.raises(ValueError):
            validate_external_url(url)

    def test_url_sem_hostname(self):
        with pytest.raises(ValueError):
            validate_external_url("http:///imagem.png")

    def test_url_publico_e_aceite(self):
        # IP literal: não depende de DNS nem de rede para o teste.
        assert validate_external_url("https://8.8.8.8/avatar.png") == "https://8.8.8.8/avatar.png"

    def test_hostname_que_nao_resolve(self, monkeypatch):
        import socket

        def rebenta(*args, **kwargs):
            raise socket.gaierror("não resolve")

        monkeypatch.setattr(image_module.socket, "getaddrinfo", rebenta)

        with pytest.raises(ValueError):
            validate_external_url("https://dominio-inexistente.exemplo/x.png")

    def test_dominio_que_resolve_para_ip_interno(self, monkeypatch):
        # Ataque clássico: um domínio público com um registo A para 127.0.0.1.
        import socket

        monkeypatch.setattr(
            image_module.socket, "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
        )

        with pytest.raises(ValueError):
            validate_external_url("http://malicioso.exemplo/x.png")

    def test_todos_os_ips_do_dominio_sao_validados(self, monkeypatch):
        # Um domínio round-robin não pode passar só porque o primeiro IP é público.
        import socket

        monkeypatch.setattr(
            image_module.socket, "getaddrinfo",
            lambda *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80)),
            ],
        )

        with pytest.raises(ValueError):
            validate_external_url("http://misto.exemplo/x.png")


class TestBuildFinalUrl:
    class PlexFalso:
        _token = "token-plex"

        def url(self, path, includeToken=False):
            return f"http://plex.local:32400{path}"

    class ContaFalsa:
        _token = "token-conta"

    class PlexManagerFalso:
        def __init__(self, plex=None, account=None):
            self.plex = plex
            self.account = account

    class TautulliFalso:
        class _Api:
            is_configured = True
            base_url = "http://tautulli.local:8181"
            api_key = "chave-tautulli"

        api_client = _Api()

    def test_fonte_plex_injeta_o_token(self, monkeypatch):
        monkeypatch.setattr(
            image_module, "plex_manager", self.PlexManagerFalso(plex=self.PlexFalso())
        )

        url, params = build_final_url("plex", "/library/metadata/1/thumb")

        assert url == "http://plex.local:32400/library/metadata/1/thumb"
        assert params["X-Plex-Token"] == "token-plex"

    def test_plex_account_aceita_caminho_relativo(self, monkeypatch):
        monkeypatch.setattr(
            image_module, "plex_manager", self.PlexManagerFalso(account=self.ContaFalsa())
        )

        url, params = build_final_url("plex_account", "users/avatar.png")

        assert url == "https://plex.tv/users/avatar.png"
        assert params["X-Plex-Token"] == "token-conta"

    def test_plex_account_aceita_url_absoluto_do_plex(self, monkeypatch):
        monkeypatch.setattr(
            image_module, "plex_manager", self.PlexManagerFalso(account=self.ContaFalsa())
        )

        url, _params = build_final_url("plex_account", "https://plex.tv/users/avatar.png?w=100")

        assert url == "https://plex.tv/users/avatar.png?w=100"

    def test_plex_account_recusa_dominios_estranhos(self, monkeypatch):
        # Sem esta verificação, o token da conta Plex seria enviado a terceiros.
        monkeypatch.setattr(
            image_module, "plex_manager", self.PlexManagerFalso(account=self.ContaFalsa())
        )

        with pytest.raises(ValueError):
            build_final_url("plex_account", "https://atacante.exemplo/roubar.png")

    def test_fonte_url_passa_pela_validacao_ssrf(self, monkeypatch):
        with pytest.raises(ValueError):
            build_final_url("url", "http://127.0.0.1/x.png")

    def test_fonte_url_publica(self):
        url, params = build_final_url("url", "https://8.8.8.8/avatar.png")

        assert url == "https://8.8.8.8/avatar.png"
        assert params == {}

    def test_fonte_tautulli_monta_o_proxy(self, monkeypatch):
        monkeypatch.setattr(image_module, "tautulli_manager", self.TautulliFalso())

        url, params = build_final_url("tautulli", "/pms_image_proxy?img=/x&width=200")

        assert url == "http://tautulli.local:8181/api/v2"
        assert params["cmd"] == "pms_image_proxy"
        assert params["apikey"] == "chave-tautulli"
        assert params["img"] == "/x"
        assert params["width"] == "200"

    def test_fonte_desconhecida_nao_gera_url(self):
        assert build_final_url("outra-coisa", "/x") == (None, {})

    def test_plex_nao_ligado(self, monkeypatch):
        monkeypatch.setattr(image_module, "plex_manager", self.PlexManagerFalso())

        assert build_final_url("plex", "/x") == (None, {})


class TestCacheFilepath:
    def test_o_nome_do_ficheiro_e_um_hash(self):
        caminho = get_cache_filepath("https://exemplo.com/imagem.png")

        assert len(caminho.name) == 64
        assert caminho.name.isalnum()

    def test_identificadores_diferentes_geram_ficheiros_diferentes(self):
        assert get_cache_filepath("a") != get_cache_filepath("b")

    def test_o_mesmo_identificador_gera_sempre_o_mesmo_ficheiro(self):
        assert get_cache_filepath("a") == get_cache_filepath("a")

    def test_caminhos_maliciosos_nao_escapam_da_pasta(self):
        # O hash elimina qualquer tentativa de "../../etc/passwd".
        caminho = get_cache_filepath("../../etc/passwd")

        assert ".." not in str(caminho)
        assert caminho.parent == image_module.IMAGE_CACHE_DIR
