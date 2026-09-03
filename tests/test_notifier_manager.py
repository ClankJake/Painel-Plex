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


class TestSubstitutePlaceholders:
    def test_substitui_numa_unica_passagem(self):
        # Um valor que contenha o texto de outro marcador NÃO pode ser
        # substituído: seria uma injeção de template a partir do conteúdo.
        resultado = notifier_module.substitute_placeholders(
            "{message} para {name}", {"message": "olá {name}", "name": "Ana"}
        )

        assert resultado == "olá {name} para Ana"

    def test_marcador_desconhecido_fica_intacto(self):
        assert notifier_module.substitute_placeholders("{a} {b}", {"a": "1"}) == "1 {b}"

    def test_nao_confunde_a_abertura_de_um_objeto_json(self):
        template = '{"content": "{name}"}'

        assert notifier_module.substitute_placeholders(template, {"name": "Ana"}) == '{"content": "Ana"}'


class TestTruncate:
    def test_texto_curto_fica_igual(self):
        assert notifier_module.truncate("abc", 10) == "abc"

    def test_corta_e_sinaliza(self):
        resultado = notifier_module.truncate("abcdefghij", 5)

        assert len(resultado) == 5
        assert resultado.endswith("…")

    def test_texto_vazio(self):
        assert notifier_module.truncate("", 5) == ""


class TestConvertMdToHtmlPreservaLinks:
    def test_underscores_de_um_url_nao_viram_italico(self, notifier):
        # 🐛 'https://x.com/a_b_c' tornava-se 'https://x.com/a<i>b</i>c' e o
        # utilizador recebia um link partido.
        texto = "Renove aqui: https://painel.exemplo.com/pay/tok_a_b"

        assert notifier._convert_md_to_html(texto) == texto

    def test_asteriscos_de_um_url_nao_viram_negrito(self, notifier):
        texto = "https://x.com/a*b*c"

        assert notifier._convert_md_to_html(texto) == texto

    def test_o_markdown_a_volta_do_url_continua_a_funcionar(self, notifier):
        resultado = notifier._convert_md_to_html("*Aviso*: https://x.com/a_b")

        assert resultado == "<b>Aviso</b>: https://x.com/a_b"

    def test_dois_urls_na_mesma_mensagem(self, notifier):
        texto = "https://a.com/x_1 e https://b.com/y_2"

        assert notifier._convert_md_to_html(texto) == texto


class TestSplitMessage:
    def test_mensagem_curta_fica_inteira(self):
        assert NotifierManager._split_message("abc", 10) == ["abc"]

    def test_parte_na_quebra_de_linha(self):
        texto = "linha um\nlinha dois"

        pedacos = NotifierManager._split_message(texto, 12)

        assert pedacos == ["linha um", "linha dois"]

    def test_parte_no_limite_quando_nao_ha_quebra_util(self):
        pedacos = NotifierManager._split_message("a" * 25, 10)

        assert pedacos == ["a" * 10, "a" * 10, "a" * 5]

    def test_nenhum_pedaco_excede_o_limite(self):
        texto = ("palavra " * 2000).strip()

        for pedaco in NotifierManager._split_message(texto, notifier_module.TELEGRAM_MAX_MESSAGE_LEN):
            assert len(pedaco) <= notifier_module.TELEGRAM_MAX_MESSAGE_LEN

    def test_texto_vazio(self):
        assert NotifierManager._split_message("", 10) == []


class BotFalso:
    """Bot do Telegram de mentira, para observar o que teria sido enviado."""

    token = "tok"

    def __init__(self, erro=None):
        self.mensagens = []
        self.fotos = []
        self._erro = erro

    def send_message(self, **kwargs):
        if self._erro:
            raise self._erro
        self.mensagens.append(kwargs)

    def send_photo(self, **kwargs):
        if self._erro:
            raise self._erro
        self.fotos.append(kwargs)


def _erro_telegram(codigo, descricao="erro", result_json=None):
    import telebot

    excecao = telebot.apihelper.ApiTelegramException.__new__(telebot.apihelper.ApiTelegramException)
    Exception.__init__(excecao, descricao)
    excecao.error_code = codigo
    excecao.description = descricao
    excecao.result_json = result_json or {}
    return excecao


class TestEnvioTelegram:
    def test_mensagem_longa_e_dividida(self, notifier, monkeypatch):
        bot = BotFalso()
        monkeypatch.setattr(notifier, "_get_bot", lambda config=None: bot)

        notifier._send_telegram_notification("a" * 9000, "123", "req")

        assert len(bot.mensagens) == 3
        assert all(len(m["text"]) <= notifier_module.TELEGRAM_MAX_MESSAGE_LEN for m in bot.mensagens)

    def test_o_teclado_vai_so_no_ultimo_pedaco(self, notifier, monkeypatch):
        bot = BotFalso()
        monkeypatch.setattr(notifier, "_get_bot", lambda config=None: bot)

        notifier._send_telegram_notification("a" * 9000, "123", "req", reply_markup="TECLADO")

        assert [m["reply_markup"] for m in bot.mensagens] == [None, None, "TECLADO"]

    def test_legenda_longa_envia_foto_e_texto_separados(self, notifier, monkeypatch):
        # O limite da legenda (1024) é bem menor que o do texto (4096): sem esta
        # separação a API recusava a mensagem inteira.
        bot = BotFalso()
        monkeypatch.setattr(notifier, "_get_bot", lambda config=None: bot)

        notifier._send_telegram_notification("a" * 2000, "123", "req", photo_url="https://img/1.jpg")

        assert len(bot.fotos) == 1
        assert "caption" not in bot.fotos[0]
        assert len(bot.mensagens) == 1
        assert len(bot.mensagens[0]["text"]) == 2000

    def test_legenda_curta_vai_com_a_foto(self, notifier, monkeypatch):
        bot = BotFalso()
        monkeypatch.setattr(notifier, "_get_bot", lambda config=None: bot)

        notifier._send_telegram_notification("Olá", "123", "req", photo_url="https://img/1.jpg")

        assert bot.fotos[0]["caption"] == "Olá"
        assert bot.mensagens == []

    def test_limite_de_ritmo_esgotado_falha_em_vez_de_fingir_sucesso(self, notifier, monkeypatch):
        # 🐛 Antes o ciclo terminava em silêncio e quem chamava contabilizava
        # a mensagem como entregue.
        bot = BotFalso(erro=_erro_telegram(429, result_json={"parameters": {"retry_after": 0}}))
        monkeypatch.setattr(notifier, "_get_bot", lambda config=None: bot)
        monkeypatch.setattr(notifier, "_sleep", lambda _s: None)

        with pytest.raises(notifier_module.NotificationError):
            notifier._send_telegram_notification("Olá", "123", "req")

    def test_bot_bloqueado_limpa_o_contacto_e_assinala_a_falha(self, notifier, monkeypatch):
        bot = BotFalso(erro=_erro_telegram(403, "bot was blocked by the user"))
        monkeypatch.setattr(notifier, "_get_bot", lambda config=None: bot)

        atualizacoes = []

        class GestorFalso:
            def update_user_profile(self, plex_user_id, dados):
                atualizacoes.append((plex_user_id, dados))

        from app import extensions

        monkeypatch.setattr(extensions, "data_manager", GestorFalso(), raising=False)

        with pytest.raises(notifier_module.NotificationError):
            notifier._send_telegram_notification("Olá", "123", "req", plex_user_id=7)

        assert atualizacoes == [(7, {"telegram_id": None, "telegram_user": None})]


class TestLimitesDoDiscord:
    def test_corta_o_content_acima_do_limite(self):
        payload = NotifierManager._enforce_discord_limits({"content": "a" * 3000})

        assert len(payload["content"]) == notifier_module.DISCORD_MAX_CONTENT_LEN

    def test_corta_a_descricao_do_embed(self):
        payload = NotifierManager._enforce_discord_limits({"embeds": [{"description": "a" * 5000}]})

        assert len(payload["embeds"][0]["description"]) == notifier_module.DISCORD_MAX_EMBED_DESCRIPTION_LEN

    def test_payload_dentro_dos_limites_fica_igual(self):
        payload = {"content": "olá", "embeds": [{"description": "curto"}]}

        assert NotifierManager._enforce_discord_limits(dict(payload)) == payload


class TestSegredosNosLogs:
    def test_o_token_do_webhook_do_discord_nao_vai_para_o_log(self, notifier, monkeypatch, caplog):
        import requests

        url = "https://discord.com/api/webhooks/123/TOKEN_SUPER_SECRETO"

        def _post_falhado(*_args, **_kwargs):
            resposta = requests.Response()
            resposta.status_code = 500
            resposta._content = b"erro interno"
            resposta.url = url
            raise requests.exceptions.HTTPError(f"500 Server Error for url: {url}", response=resposta)

        monkeypatch.setattr(notifier._http, "post", _post_falhado)

        with caplog.at_level("ERROR"):
            with pytest.raises(notifier_module.NotificationError):
                notifier._send_discord_notification({"content": "x"}, "req", {"DISCORD_WEBHOOK_URL": url})

        assert "TOKEN_SUPER_SECRETO" not in caplog.text

    def test_as_credenciais_do_webhook_generico_sao_mascaradas(self, notifier, monkeypatch, caplog):
        import requests

        url = "https://utilizador:senha_secreta@n8n.exemplo.com/hook"

        def _post_falhado(*_args, **_kwargs):
            raise requests.exceptions.ConnectionError("falha de ligação")

        monkeypatch.setattr(notifier._http, "post", _post_falhado)

        with caplog.at_level("ERROR"):
            with pytest.raises(notifier_module.NotificationError):
                notifier._send_webhook_notification({"content": "x"}, "req", {"WEBHOOK_URL": url})

        assert "senha_secreta" not in caplog.text


class TestElegibilidadeDoEnvioEmMassa:
    def _users(self):
        return [{"id": 1, "username": "com_telegram"}, {"id": 2, "username": "sem_contacto"}]

    def _profiles(self):
        return {1: {"telegram_id": "111"}, 2: {}}

    def test_sem_contacto_e_ignorado(self):
        elegiveis, ignorados = NotifierManager._split_by_reachability(
            self._users(), self._profiles(), {"TELEGRAM_ENABLED": True}
        )

        assert [u["id"] for u in elegiveis] == [1]
        assert [u["id"] for u in ignorados] == [2]

    def test_o_webhook_generico_torna_todos_elegiveis(self):
        # O webhook não depende de contacto pessoal (n8n, Slack, sistemas internos).
        elegiveis, ignorados = NotifierManager._split_by_reachability(
            self._users(), self._profiles(), {"WEBHOOK_ENABLED": True, "WEBHOOK_URL": "https://x"}
        )

        assert len(elegiveis) == 2
        assert ignorados == []

    def test_contacto_de_um_canal_desativado_nao_conta(self):
        # Ter telefone não adianta se o canal de WhatsApp está desligado.
        elegiveis, ignorados = NotifierManager._split_by_reachability(
            [{"id": 3, "username": "so_whatsapp"}], {3: {"phone_number": "5511988887777"}},
            {"TELEGRAM_ENABLED": True},
        )

        assert elegiveis == []
        assert len(ignorados) == 1


class TestPrepareAndSend:
    def _config(self, **extra):
        config = {
            "TELEGRAM_ENABLED": True,
            "DISCORD_ENABLED": True,
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/abc",
            "WEBHOOK_ENABLED": True,
            "WEBHOOK_URL": "https://hook.exemplo.com",
            "WHATSAPP_ENABLED": True,
            "WHATSAPP_PROVIDER": "evolution",
            "WHATSAPP_API_URL": "https://wa.exemplo.com",
            "WHATSAPP_INSTANCE": "painel",
            "APP_BASE_URL": "https://painel.exemplo.com",
            "SCREEN_PRICES": {},
            "RENEWAL_PRICE": "10.00",
        }
        config.update(extra)
        return config

    def _perfil(self):
        return {
            "plex_user_id": 1,
            "name": "Ana",
            "telegram_id": "111",
            "discord_user_id": "222",
            "phone_number": "5511988887777",
        }

    def test_relata_o_que_foi_entregue_e_o_que_falhou(self, notifier, monkeypatch):
        monkeypatch.setattr(notifier, "_send_telegram_notification", lambda *a, **k: None)
        monkeypatch.setattr(notifier, "_send_discord_notification", lambda *a, **k: None)
        monkeypatch.setattr(notifier, "_send_whatsapp_notification", lambda *a, **k: None)

        def _webhook_falha(*_a, **_k):
            raise notifier_module.NotificationError("Webhook: HTTP 500")

        monkeypatch.setattr(notifier, "_send_webhook_notification", _webhook_falha)

        resultado = notifier._prepare_and_send(
            "bulk", {"username": "ana"}, self._perfil(), {"message": "Olá"}, config=self._config()
        )

        assert sorted(resultado["sent"]) == ["Discord", "Telegram", "WhatsApp"]
        assert [canal for canal, _erro in resultado["failed"]] == ["Webhook"]

    def test_a_falha_de_um_canal_nao_impede_os_restantes(self, notifier, monkeypatch):
        enviados = []

        def _telegram_falha(*_a, **_k):
            raise notifier_module.NotificationError("Telegram: bloqueado")

        monkeypatch.setattr(notifier, "_send_telegram_notification", _telegram_falha)
        monkeypatch.setattr(notifier, "_send_discord_notification", lambda *a, **k: enviados.append("discord"))
        monkeypatch.setattr(notifier, "_send_webhook_notification", lambda *a, **k: enviados.append("webhook"))
        monkeypatch.setattr(notifier, "_send_whatsapp_notification", lambda *a, **k: enviados.append("whatsapp"))

        resultado = notifier._prepare_and_send(
            "bulk", {"username": "ana"}, self._perfil(), {"message": "Olá"}, config=self._config()
        )

        assert sorted(enviados) == ["discord", "webhook", "whatsapp"]
        assert resultado["sent"]

    def test_sem_canais_ativos_nao_envia_nada(self, notifier, monkeypatch):
        chamado = []
        monkeypatch.setattr(notifier, "_send_telegram_notification", lambda *a, **k: chamado.append(1))

        resultado = notifier._prepare_and_send(
            "bulk", {"username": "ana"}, self._perfil(), {"message": "Olá"},
            config={"TELEGRAM_ENABLED": False},
        )

        assert resultado == {"sent": [], "failed": []}
        assert chamado == []

    def test_a_mencao_do_discord_usa_o_id_do_utilizador(self, notifier, monkeypatch):
        # 🐛 O marcador {discord_user_id} não existia: a menção era publicada
        # literalmente como "<@{discord_user_id}>" e ninguém era notificado.
        payloads = []
        monkeypatch.setattr(notifier, "_send_discord_notification", lambda p, *a, **k: payloads.append(p))

        notifier._prepare_and_send(
            "bulk", {"username": "ana"}, self._perfil(), {"message": "Olá"},
            config=self._config(TELEGRAM_ENABLED=False, WEBHOOK_ENABLED=False, WHATSAPP_ENABLED=False),
        )

        assert payloads[0]["content"] == "<@222>"

    def test_o_config_recebido_nao_e_relido_do_disco(self, notifier, monkeypatch):
        # Numa base grande, reler o config.json por utilizador era um acesso a
        # disco por envio.
        def _explode():
            raise AssertionError("o config não devia ser relido")

        monkeypatch.setattr(notifier_module, "load_or_create_config", _explode)
        monkeypatch.setattr(notifier, "_send_telegram_notification", lambda *a, **k: None)

        notifier._prepare_and_send(
            "bulk", {"username": "ana"}, self._perfil(), {"message": "Olá"},
            config=self._config(DISCORD_ENABLED=False, WEBHOOK_ENABLED=False, WHATSAPP_ENABLED=False),
        )


class GestorDeDadosFalso:
    def __init__(self):
        self.tarefas = {}

    def update_task(self, task_id, dados):
        self.tarefas.setdefault(task_id, {}).update(dados)


class ExtensionsFalso:
    """Substitui o módulo app.extensions dentro do worker de envio em massa."""

    socketio = None

    def __init__(self):
        self.data_manager = GestorDeDadosFalso()


class TestWorkerDeEnvioEmMassa:
    def _correr(self, notifier, extensions, resultados, users=None, ignorados=None):
        users = users if users is not None else [{"id": 1, "username": "ana"}]
        chamadas = iter(resultados)

        def _envio(*_a, **_k):
            proximo = next(chamadas)
            if isinstance(proximo, Exception):
                raise proximo
            return proximo

        notifier._prepare_and_send = _envio
        notifier._run_bulk_worker(
            None, extensions, "tarefa-1", "Olá", {"BULK_SEND_INTERVAL_SECONDS": 0},
            users, {}, ignorados or []
        )
        return extensions.data_manager.tarefas["tarefa-1"]

    def test_conta_apenas_as_entregas_reais(self, notifier):
        # 🐛 A consola anunciava "✅ Sucesso" para toda a gente porque as falhas
        # de cada canal eram engolidas antes de chegarem aqui.
        extensions = ExtensionsFalso()

        tarefa = self._correr(
            notifier, extensions,
            [{"sent": [], "failed": [("Telegram", "bloqueado")]}],
        )

        assert tarefa["status"] == "completed"
        assert "0" in tarefa["result"]

    def test_entrega_parcial_conta_como_entregue(self, notifier):
        extensions = ExtensionsFalso()

        tarefa = self._correr(
            notifier, extensions,
            [{"sent": ["Telegram"], "failed": [("Discord", "HTTP 500")]}],
        )

        assert tarefa["status"] == "completed"
        assert tarefa["progress_current"] == 1

    def test_um_erro_num_utilizador_nao_para_o_lote(self, notifier):
        extensions = ExtensionsFalso()

        tarefa = self._correr(
            notifier, extensions,
            [RuntimeError("falha inesperada"), {"sent": ["Telegram"], "failed": []}],
            users=[{"id": 1, "username": "ana"}, {"id": 2, "username": "bruno"}],
        )

        assert tarefa["status"] == "completed"
        assert tarefa["progress_current"] == 2

    def test_uma_falha_do_worker_nao_deixa_a_tarefa_presa_em_running(self, notifier):
        # 🐛 O try/except de quem chama já terminou quando o worker corre, por
        # isso um erro aqui dentro deixava a tarefa 'running' para sempre.
        extensions = ExtensionsFalso()

        class GestorQueFalhaUmaVez(GestorDeDadosFalso):
            def update_task(self, task_id, dados):
                if dados.get("status") == "completed":
                    raise RuntimeError("base de dados indisponível")
                super().update_task(task_id, dados)

        extensions.data_manager = GestorQueFalhaUmaVez()

        tarefa = self._correr(notifier, extensions, [{"sent": ["Telegram"], "failed": []}])

        assert tarefa["status"] == "failed"


class TestCanaisMalConfigurados:
    def test_discord_sem_url_de_webhook_nao_conta_como_entregue(self, notifier):
        # O canal ligado mas sem URL entregava zero mensagens e mesmo assim era
        # contabilizado como sucesso.
        resultado = notifier._prepare_and_send(
            "bulk", {"username": "ana"}, {"discord_user_id": "222"}, {"message": "Olá"},
            config={"DISCORD_ENABLED": True, "DISCORD_WEBHOOK_URL": ""},
        )

        assert resultado == {"sent": [], "failed": []}

    def test_telegram_sem_token_e_uma_falha(self, notifier):
        resultado = notifier._prepare_and_send(
            "bulk", {"username": "ana"}, {"telegram_id": "111"}, {"message": "Olá"},
            config={"TELEGRAM_ENABLED": True, "TELEGRAM_BOT_TOKEN": ""},
        )

        assert resultado["sent"] == []
        assert [canal for canal, _erro in resultado["failed"]] == ["Telegram"]
