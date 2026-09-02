# tests/test_notifier_manager.py
"""Notificações: normalização de telefones, templates e pedidos de WhatsApp."""

import base64
import json

import pytest

from app.services import notifier_manager as notifier_module
from app.services.notifier_manager import (
    DEFAULT_TEMPLATES,
    NotifierManager,
    resolve_seerr_template_key,
)


@pytest.fixture()
def configurar(monkeypatch):
    def _configurar(**valores):
        config = {"WHATSAPP_DEFAULT_COUNTRY_CODE": "55"}
        config.update(valores)
        monkeypatch.setattr(notifier_module, "load_or_create_config", lambda: config)
        return config

    return _configurar


@pytest.fixture()
def notifier(app_context, configurar):
    configurar()
    return NotifierManager()


class TestNormalizePhone:
    def test_remove_a_formatacao(self, notifier):
        assert notifier.normalize_phone("+55 (11) 98888-7777") == "5511988887777"

    def test_acrescenta_o_codigo_do_pais_a_um_telemovel_nacional(self, notifier):
        assert notifier.normalize_phone("11988887777") == "5511988887777"

    def test_acrescenta_o_codigo_do_pais_a_um_fixo_nacional(self, notifier):
        assert notifier.normalize_phone("1133334444") == "551133334444"

    def test_remove_o_prefixo_internacional_00(self, notifier):
        assert notifier.normalize_phone("005511988887777") == "5511988887777"

    def test_numero_internacional_nao_e_corrompido(self, notifier):
        # +1 415 555 2671 tem 11 dígitos: sem a heurística conservadora, virava
        # "5514155552671" — um número brasileiro inexistente.
        assert notifier.normalize_phone("+1 415 555 2671") == "14155552671"

    def test_onze_digitos_sem_o_nove_nao_recebe_o_ddi(self, notifier):
        # Um telemóvel brasileiro com 11 dígitos tem sempre o 9 na 3ª posição.
        assert notifier.normalize_phone("11888887777") == "11888887777"

    def test_ddd_invalido_nao_recebe_o_ddi(self, notifier):
        assert notifier.normalize_phone("0198888777") == "0198888777"

    def test_numero_ja_com_codigo_do_pais_fica_igual(self, notifier):
        assert notifier.normalize_phone("5511988887777") == "5511988887777"

    @pytest.mark.parametrize("valor", [None, "", "sem dígitos"])
    def test_valores_invalidos(self, notifier, valor):
        assert notifier.normalize_phone(valor) is None

    def test_outro_codigo_de_pais_configurado(self, app_context, configurar):
        configurar(WHATSAPP_DEFAULT_COUNTRY_CODE="351")

        assert NotifierManager().normalize_phone("1133334444") == "3511133334444"


class TestFormatTemplate:
    def test_substitui_os_marcadores(self, notifier):
        resultado = notifier._format_template("Olá {name}, vence a {date}.", {"name": "Ana", "date": "10/05"})

        assert resultado == "Olá Ana, vence a 10/05."

    def test_valores_nulos_ficam_vazios(self, notifier):
        assert notifier._format_template("Olá {name}!", {"name": None}) == "Olá !"

    def test_marcador_desconhecido_devolve_o_template_intacto(self, notifier):
        template = "Olá {nome_errado}!"

        assert notifier._format_template(template, {"name": "Ana"}) == template

    def test_template_vazio(self, notifier):
        assert notifier._format_template("", {"name": "Ana"}) is None

    def test_escapa_html_quando_pedido(self, notifier):
        resultado = notifier._format_template("Olá {name}", {"name": "<b>Ana</b>"}, use_html_escape=True)

        assert "&lt;b&gt;" in resultado

    def test_links_nao_sao_escapados(self, notifier):
        # Escapar o '&' de um link partiria a URL na mensagem.
        resultado = notifier._format_template(
            "{payment_link}", {"payment_link": "https://x.com/a?b=1&c=2"}, use_html_escape=True
        )

        assert resultado == "https://x.com/a?b=1&c=2"

    def test_template_json(self, notifier):
        resultado = notifier._format_template('{"content": "Olá {name}"}', {"name": "Ana"}, is_json=True)

        assert resultado == {"content": "Olá Ana"}

    def test_json_escapa_aspas_do_valor(self, notifier):
        # Sem escape, um nome com aspas partiria o JSON enviado ao Discord.
        resultado = notifier._format_template('{"content": "{name}"}', {"name": 'Ana "A"'}, is_json=True)

        assert resultado == {"content": 'Ana "A"'}

    def test_json_invalido_devolve_none(self, notifier):
        assert notifier._format_template('{"content": ', {}, is_json=True) is None


class TestConvertMdToHtml:
    def test_negrito_italico_e_codigo(self, notifier):
        html = notifier._convert_md_to_html("*negrito* _itálico_ `código`")

        assert html == "<b>negrito</b> <i>itálico</i> <code>código</code>"

    def test_texto_vazio(self, notifier):
        assert notifier._convert_md_to_html("") == ""


class TestResolveSeerrTemplateKey:
    @pytest.mark.parametrize("evento,esperado", [
        ("MEDIA_PENDING", "TELEGRAM_MEDIA_PENDING_MESSAGE_TEMPLATE"),
        ("MEDIA_APPROVED", "TELEGRAM_MEDIA_APPROVED_MESSAGE_TEMPLATE"),
        ("MEDIA_AUTO_APPROVED", "TELEGRAM_MEDIA_APPROVED_MESSAGE_TEMPLATE"),
        ("MEDIA_AVAILABLE", "TELEGRAM_MEDIA_AVAILABLE_MESSAGE_TEMPLATE"),
        ("MEDIA_DECLINED", "TELEGRAM_MEDIA_DECLINED_MESSAGE_TEMPLATE"),
        ("MEDIA_FAILED", "TELEGRAM_MEDIA_FAILED_MESSAGE_TEMPLATE"),
    ])
    def test_eventos_conhecidos(self, evento, esperado):
        assert resolve_seerr_template_key("TELEGRAM", evento) == esperado

    def test_minusculas_sao_aceites(self):
        assert resolve_seerr_template_key("WHATSAPP", "media_approved") == "WHATSAPP_MEDIA_APPROVED_MESSAGE_TEMPLATE"

    @pytest.mark.parametrize("evento", ["ISSUE_CREATED", "", None])
    def test_evento_desconhecido_usa_o_template_generico(self, evento):
        # Uma notificação nova do Seerr nunca pode ficar sem mensagem.
        assert resolve_seerr_template_key("DISCORD", evento) == "DISCORD_MEDIA_REQUEST_MESSAGE_TEMPLATE"

    def test_todos_os_eventos_tem_template_padrao(self):
        for canal in ("TELEGRAM", "WHATSAPP", "DISCORD"):
            for evento in ("MEDIA_PENDING", "MEDIA_APPROVED", "MEDIA_AVAILABLE", "MEDIA_DECLINED", "MEDIA_FAILED"):
                assert DEFAULT_TEMPLATES.get(resolve_seerr_template_key(canal, evento))


class TestBuildWhatsappRequest:
    def _config(self, **extra):
        config = {
            "WHATSAPP_PROVIDER": "evolution",
            "WHATSAPP_API_URL": "https://wa.exemplo.com/",
            "WHATSAPP_API_KEY": "chave",
            "WHATSAPP_INSTANCE": "painel",
        }
        config.update(extra)
        return config

    def test_evolution(self, notifier):
        url, headers, payload = notifier._build_whatsapp_request(
            self._config(), "5511988887777", "Olá"
        )

        assert url == "https://wa.exemplo.com/message/sendText/painel"
        assert headers["apikey"] == "chave"
        assert payload == {"number": "5511988887777", "text": "Olá"}

    def test_evolution_exige_instancia(self, notifier):
        with pytest.raises(ValueError):
            notifier._build_whatsapp_request(self._config(WHATSAPP_INSTANCE=""), "5511988887777", "Olá")

    def test_gowa_com_basic_auth(self, notifier):
        url, headers, payload = notifier._build_whatsapp_request(
            self._config(WHATSAPP_PROVIDER="gowa", WHATSAPP_API_KEY="user:senha"), "5511988887777", "Olá"
        )

        esperado = base64.b64encode(b"user:senha").decode("ascii")
        assert url == "https://wa.exemplo.com/send/message"
        assert headers["Authorization"] == f"Basic {esperado}"
        assert payload["phone"] == "5511988887777@s.whatsapp.net"

    def test_gowa_com_bearer(self, notifier):
        _url, headers, _payload = notifier._build_whatsapp_request(
            self._config(WHATSAPP_PROVIDER="gowa", WHATSAPP_API_KEY="token123"), "5511988887777", "Olá"
        )

        assert headers["Authorization"] == "Bearer token123"

    def test_gowa_sem_autenticacao(self, notifier):
        _url, headers, _payload = notifier._build_whatsapp_request(
            self._config(WHATSAPP_PROVIDER="gowa", WHATSAPP_API_KEY=""), "5511988887777", "Olá"
        )

        assert "Authorization" not in headers

    def test_waha(self, notifier):
        url, headers, payload = notifier._build_whatsapp_request(
            self._config(WHATSAPP_PROVIDER="waha"), "5511988887777", "Olá"
        )

        assert url == "https://wa.exemplo.com/api/sendText"
        assert headers["X-Api-Key"] == "chave"
        assert payload["chatId"] == "5511988887777@c.us"
        assert payload["session"] == "painel"

    def test_waha_sem_instancia_usa_default(self, notifier):
        _url, _headers, payload = notifier._build_whatsapp_request(
            self._config(WHATSAPP_PROVIDER="waha", WHATSAPP_INSTANCE=""), "5511988887777", "Olá"
        )

        assert payload["session"] == "default"

    def test_provedor_personalizado(self, notifier):
        url, headers, payload = notifier._build_whatsapp_request(
            self._config(
                WHATSAPP_PROVIDER="custom",
                WHATSAPP_CUSTOM_PAYLOAD_TEMPLATE='{"to": "{phone}", "body": "{message}"}',
            ),
            "5511988887777",
            "Olá",
        )

        assert url == "https://wa.exemplo.com"
        assert headers["Authorization"] == "Bearer chave"
        assert payload == {"to": "5511988887777", "body": "Olá"}

    def test_template_personalizado_invalido(self, notifier):
        with pytest.raises(ValueError):
            notifier._build_whatsapp_request(
                self._config(WHATSAPP_PROVIDER="custom", WHATSAPP_CUSTOM_PAYLOAD_TEMPLATE="{ inválido"),
                "5511988887777",
                "Olá",
            )

    def test_url_em_falta(self, notifier):
        with pytest.raises(ValueError):
            notifier._build_whatsapp_request(self._config(WHATSAPP_API_URL=""), "5511988887777", "Olá")


class TestBuildWhatsappMediaRequest:
    def _config(self, provider):
        return {
            "WHATSAPP_PROVIDER": provider,
            "WHATSAPP_API_URL": "https://wa.exemplo.com",
            "WHATSAPP_API_KEY": "chave",
            "WHATSAPP_INSTANCE": "painel",
        }

    def test_evolution(self, notifier):
        url, _headers, payload = notifier._build_whatsapp_media_request(
            self._config("evolution"), "5511988887777", "Legenda", "https://img/1.jpg"
        )

        assert url == "https://wa.exemplo.com/message/sendMedia/painel"
        assert payload["media"] == "https://img/1.jpg"
        assert payload["caption"] == "Legenda"

    def test_gowa(self, notifier):
        url, _headers, payload = notifier._build_whatsapp_media_request(
            self._config("gowa"), "5511988887777", "Legenda", "https://img/1.jpg"
        )

        assert url == "https://wa.exemplo.com/send/image"
        assert payload["image_url"] == "https://img/1.jpg"

    def test_waha(self, notifier):
        url, _headers, payload = notifier._build_whatsapp_media_request(
            self._config("waha"), "5511988887777", "Legenda", "https://img/1.jpg"
        )

        assert url == "https://wa.exemplo.com/api/sendImage"
        assert payload["file"] == {"url": "https://img/1.jpg"}

    def test_provedor_personalizado_nao_suporta_imagem(self, notifier):
        # Nestes casos o chamador cai para o envio de texto simples.
        with pytest.raises(ValueError):
            notifier._build_whatsapp_media_request(
                self._config("custom"), "5511988887777", "Legenda", "https://img/1.jpg"
            )


class TestPrecoEPlano:
    def test_preco_do_plano_do_utilizador(self, notifier):
        config = {"SCREEN_PRICES": {"2": "18.00"}, "RENEWAL_PRICE": "10.00"}

        preco, plano = notifier._get_price_and_plan(config, 2)

        assert preco == "R$ 18,00"
        assert "2" in plano

    def test_sem_plano_usa_o_preco_de_renovacao(self, notifier):
        config = {"SCREEN_PRICES": {}, "RENEWAL_PRICE": "10.00"}

        preco, plano = notifier._get_price_and_plan(config, 0)

        assert preco == "R$ 10,00"
        assert plano

    def test_milhares_sao_formatados_a_portuguesa(self, notifier):
        preco, _plano = notifier._get_price_and_plan({"SCREEN_PRICES": {"1": "1234.50"}}, 1)

        assert preco == "R$ 1.234,50"

    def test_preco_com_virgula(self, notifier):
        preco, _plano = notifier._get_price_and_plan({"SCREEN_PRICES": {"1": "18,90"}}, 1)

        assert preco == "R$ 18,90"

    def test_preco_invalido(self, notifier):
        preco, _plano = notifier._get_price_and_plan({"SCREEN_PRICES": {"1": "grátis"}}, 1)

        assert preco == "N/A"


class TestPaymentLink:
    def test_usa_o_dominio_configurado(self, notifier):
        config = {"APP_BASE_URL": "https://painel.exemplo.com/", "ENABLE_LINK_SHORTENER": False}

        link = notifier._get_payment_link(config, "expiration", {"payment_token": "tok123"})

        assert link == "https://painel.exemplo.com/pay/tok123"

    def test_renovacoes_nao_levam_link_de_pagamento(self, notifier):
        config = {"APP_BASE_URL": "https://painel.exemplo.com"}

        for evento in ("renewal", "reactivation"):
            assert notifier._get_payment_link(config, evento, {"payment_token": "tok123"}) is None

    def test_sem_token_nao_ha_link(self, notifier):
        assert notifier._get_payment_link({"APP_BASE_URL": "https://x.com"}, "expiration", {}) is None

    def test_usa_o_encurtador_quando_ativo(self, app_context, configurar):
        class EncurtadorEspiao:
            def __init__(self):
                self.pedidos = []

            def create_short_link(self, url):
                self.pedidos.append(url)
                return "https://painel.exemplo.com/s/abc"

        encurtador = EncurtadorEspiao()
        gestor = NotifierManager(link_shortener_service=encurtador)
        config = {"APP_BASE_URL": "https://painel.exemplo.com", "ENABLE_LINK_SHORTENER": True}

        link = gestor._get_payment_link(config, "expiration", {"payment_token": "tok123"})

        assert link == "https://painel.exemplo.com/s/abc"
        assert encurtador.pedidos == ["https://painel.exemplo.com/pay/tok123"]


class TestBuildPlaceholders:
    def test_junta_utilizador_perfil_e_contexto(self, notifier):
        marcadores = notifier._build_placeholders(
            {"username": "ana", "email": "ana@exemplo.com"},
            {"name": "Ana Silva", "phone_number": "5511988887777"},
            {"days": 3},
        )

        assert marcadores["username"] == "ana"
        assert marcadores["name"] == "Ana Silva"
        assert marcadores["email"] == "ana@exemplo.com"
        assert marcadores["days"] == 3
        assert marcadores["greeting"]

    def test_sem_nome_usa_o_username(self, notifier):
        marcadores = notifier._build_placeholders({"username": "ana"}, {}, {})

        assert marcadores["name"] == "ana"

    def test_email_do_perfil_serve_de_alternativa(self, notifier):
        marcadores = notifier._build_placeholders({"username": "ana"}, {"email": "perfil@exemplo.com"}, {})

        assert marcadores["email"] == "perfil@exemplo.com"


class TestTemplatesPadrao:
    def test_os_templates_json_do_discord_sao_validos(self):
        for chave, template in DEFAULT_TEMPLATES.items():
            if not chave.startswith("DISCORD"):
                continue
            # Um template inválido só rebentaria na hora de notificar em produção.
            json.loads(template.replace("{title}", "t").replace("{overview}", "o")
                       .replace("{username}", "u").replace("{status}", "s")
                       .replace("{media_url}", "https://x"))
