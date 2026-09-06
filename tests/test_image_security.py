# tests/test_image_security.py
"""Proteções do proxy de imagens contra SSRF (acesso à rede interna do servidor)."""

import pytest

from app.blueprints import image as image_module
from app.blueprints.image import (
    build_authorized_image_url,
    build_final_url,
    get_cache_filepath,
    is_allowed_image_host,
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

    def test_fonte_url_recusa_dominio_publico_fora_da_allowlist(self):
        # Um IP/dominio publico arbitrario deixou de ser um destino valido:
        # e isso que separa um proxy de imagens de um SSRF.
        with pytest.raises(ValueError):
            build_final_url("url", "https://8.8.8.8/avatar.png")

    def test_fonte_url_aceita_dominio_da_allowlist(self, monkeypatch):
        monkeypatch.setattr(
            image_module, "validate_external_url", lambda url: url
        )

        url, params = build_final_url("url", "https://secure.gravatar.com/avatar/abc?s=200")

        assert url == "https://secure.gravatar.com/avatar/abc?s=200"
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


class TestAllowlistDeDominios:
    """
    A allowlist é o que separa um proxy de imagens de um SSRF: sem ela,
    `/image/?source=<base64>` faz o servidor buscar qualquer URL escolhido por
    quem faz o pedido, já a partir de dentro da rede.
    """

    @pytest.mark.parametrize("hostname", [
        "plex.tv",
        "metadata-static.plex.tv",
        "secure.gravatar.com",
        "1-2-3-4.abcdef.plex.direct",
        "PLEX.TV",          # comparação insensível a maiúsculas
        "plex.tv.",         # ponto final da raiz DNS
    ])
    def test_dominios_autorizados(self, hostname):
        assert is_allowed_image_host(hostname) is True

    @pytest.mark.parametrize("hostname", [
        "atacante.exemplo",
        "plex.tv.atacante.com",   # sufixo colado: o `in netloc` deixava passar
        "naoeplex.tv",            # sem fronteira de rótulo
        "gravatar.com.evil.net",
        "",
        None,
    ])
    def test_dominios_recusados(self, hostname):
        assert is_allowed_image_host(hostname) is False

    def test_admin_pode_acrescentar_dominios(self, monkeypatch):
        monkeypatch.setenv("IMAGE_PROXY_ALLOWED_HOSTS", "cdn.exemplo.com, outro.net")

        assert is_allowed_image_host("img.cdn.exemplo.com") is True
        assert is_allowed_image_host("outro.net") is True
        assert is_allowed_image_host("terceiro.org") is False

    def test_o_host_do_plex_configurado_e_autorizado(self, monkeypatch):
        class PlexFalso:
            _baseurl = "https://plex.meudominio.com:32400"

        class ManagerFalso:
            plex = PlexFalso()

        monkeypatch.setattr(image_module, "plex_manager", ManagerFalso())

        assert is_allowed_image_host("plex.meudominio.com") is True


class TestBuildAuthorizedImageUrl:
    @pytest.fixture(autouse=True)
    def _sem_dns(self, monkeypatch):
        # Isola a allowlist da camada de DNS/IP (já testada acima).
        monkeypatch.setattr(image_module, "validate_external_url", lambda url: url)

    def test_url_e_remontado_a_partir_dos_componentes_validados(self):
        url = build_authorized_image_url("https://plex.tv/users/1/avatar?c=123")

        assert url == "https://plex.tv/users/1/avatar?c=123"

    def test_caminho_vazio_gera_raiz(self):
        assert build_authorized_image_url("https://plex.tv") == "https://plex.tv/"

    def test_fragmento_e_descartado(self):
        url = build_authorized_image_url("https://plex.tv/a.png#fragmento")

        assert url == "https://plex.tv/a.png"

    @pytest.mark.parametrize("url", [
        "https://atacante.exemplo/x.png",
        "https://plex.tv.atacante.com/x.png",
        "https://169.254.169.254/latest/meta-data/",
    ])
    def test_dominios_fora_da_allowlist_sao_recusados(self, url):
        with pytest.raises(ValueError):
            build_authorized_image_url(url)

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "gopher://plex.tv/x",
        "ftp://plex.tv/x.png",
    ])
    def test_esquemas_nao_http_sao_recusados(self, url):
        with pytest.raises(ValueError):
            build_authorized_image_url(url)

    def test_porta_do_plex_e_aceite(self):
        url = build_authorized_image_url("https://1-2-3-4.abc.plex.direct:32400/photo/x")

        assert url == "https://1-2-3-4.abc.plex.direct:32400/photo/x"

    @pytest.mark.parametrize("porta", [22, 3306, 6379, 8080])
    def test_portas_fora_da_lista_sao_recusadas(self, porta):
        # Sem isto, o proxy servia para varrer portas de serviços internos.
        with pytest.raises(ValueError):
            build_authorized_image_url(f"https://plex.tv:{porta}/x.png")

    def test_porta_malformada_e_recusada(self):
        with pytest.raises(ValueError):
            build_authorized_image_url("https://plex.tv:porta/x.png")

    def test_a_validacao_de_ip_continua_a_correr(self, monkeypatch):
        chamadas = []

        def valida(url):
            chamadas.append(url)
            raise ValueError("IP interno")

        monkeypatch.setattr(image_module, "validate_external_url", valida)

        with pytest.raises(ValueError):
            build_authorized_image_url("https://plex.tv/x.png")

        assert chamadas == ["https://plex.tv/x.png"]


class TestPinnedIPAdapter:
    """
    O adaptador tem de fixar mesmo a ligação ao IP validado. Se o pedido seguir
    com o hostname, o 'requests' resolve o DNS outra vez e o atacante pode
    devolver um IP interno nessa segunda resolução (DNS rebinding).
    """

    class PedidoFalso:
        def __init__(self, url):
            self.url = url
            self.headers = {}

    def _envia(self, monkeypatch, adapter, pedido):
        enviados = {}

        def super_send(self, request, **kwargs):
            enviados["url"] = request.url
            enviados["host"] = request.headers.get("Host")
            return "resposta"

        monkeypatch.setattr(image_module.HTTPAdapter, "send", super_send)
        adapter.send(pedido)
        return enviados

    def test_https_liga_ao_ip_e_preserva_o_host(self, monkeypatch):
        adapter = image_module._PinnedIPAdapter("93.184.216.34")
        pedido = self.PedidoFalso("https://plex.tv/users/avatar.png?c=1")

        enviados = self._envia(monkeypatch, adapter, pedido)

        assert enviados["url"] == "https://93.184.216.34/users/avatar.png?c=1"
        assert enviados["host"] == "plex.tv"
        # SNI e validação do certificado continuam a usar o nome original.
        assert adapter.poolmanager.connection_pool_kw["server_hostname"] == "plex.tv"
        assert adapter.poolmanager.connection_pool_kw["assert_hostname"] == "plex.tv"

    def test_a_porta_e_preservada(self, monkeypatch):
        adapter = image_module._PinnedIPAdapter("93.184.216.34")
        pedido = self.PedidoFalso("https://x.plex.direct:32400/photo/a.png")

        enviados = self._envia(monkeypatch, adapter, pedido)

        assert enviados["url"] == "https://93.184.216.34:32400/photo/a.png"
        assert enviados["host"] == "x.plex.direct:32400"

    def test_http_nao_recebe_parametros_de_tls(self, monkeypatch):
        # 'server_hostname'/'assert_hostname' só existem em ligações HTTPS:
        # numa ligação HTTP o urllib3 rebentaria com TypeError.
        adapter = image_module._PinnedIPAdapter("93.184.216.34")
        pedido = self.PedidoFalso("http://plex.tv/a.png")

        enviados = self._envia(monkeypatch, adapter, pedido)

        assert enviados["url"] == "http://93.184.216.34/a.png"
        assert "server_hostname" not in adapter.poolmanager.connection_pool_kw
        assert "assert_hostname" not in adapter.poolmanager.connection_pool_kw

    def test_ipv6_e_escrito_entre_parenteses_retos(self, monkeypatch):
        adapter = image_module._PinnedIPAdapter("2606:2800:220:1:248:1893:25c8:1946")
        pedido = self.PedidoFalso("https://plex.tv/a.png")

        enviados = self._envia(monkeypatch, adapter, pedido)

        assert enviados["url"] == "https://[2606:2800:220:1:248:1893:25c8:1946]/a.png"
