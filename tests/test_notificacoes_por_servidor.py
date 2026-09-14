# tests/test_notificacoes_por_servidor.py

"""O que as notificações dizem, num painel que pode ser de dois servidores.

⚠️ **A marca aparecia no meio das frases entregues ao UTILIZADOR** — "O seu
acesso ao Plex está prestes a expirar", "Clique aqui para aceitar o convite do
Plex", "aceite o convite no link abaixo". Num painel Jellyfin era a marca
errada a chegar a quem paga, e "aceitar um convite" é um passo que ali nem
existe: as contas são locais e a reativação é o painel a tirar-lhes a suspensão.

É a mesma armadilha do `default.svg` e do "Ver no Plex" das estatísticas, e
resolve-se da mesma maneira: quem sabe o nome é o servidor
(`media_server.short_name`), exposto aos templates como `{server_name}`.

⚠️ Isto muda os templates PADRÃO. Quem já os reescreveu na página de
Configurações mantém o que lá tem — é o texto dele.
"""

import pytest

from app.config import load_or_create_config
from app.services.notifier_manager import DEFAULT_TEMPLATES, NotifierManager

pytestmark = pytest.mark.integration


@pytest.fixture()
def notifier():
    return NotifierManager()


@pytest.fixture()
def servidor(monkeypatch):
    def instalar(short_name):
        from app import extensions

        class BackendFalso:
            SHORT_NAME = short_name

        monkeypatch.setattr(extensions, 'media_server', BackendFalso())

    return instalar


class TestOMarcadorDoServidor:
    def test_o_nome_vem_do_servidor_configurado(self, app_context, notifier, servidor):
        servidor('Jellyfin')

        marcadores = notifier._build_placeholders({"username": "ana"}, {}, {})

        assert marcadores['server_name'] == 'Jellyfin'

    def test_num_painel_plex_diz_plex(self, app_context, notifier, servidor):
        servidor('Plex')

        assert notifier._build_placeholders({"username": "ana"}, {}, {})['server_name'] == 'Plex'

    def test_sem_backend_nao_rebenta(self, app_context, notifier, monkeypatch):
        from app import extensions

        monkeypatch.setattr(extensions, 'media_server', None)

        assert notifier._build_placeholders({"username": "ana"}, {}, {})['server_name'] == 'Plex'


class TestOsTemplatesPadrao:
    """Nenhum deles pode trazer a marca escrita à mão."""

    def _padroes(self):
        config = load_or_create_config()
        chaves = [c for c in config if c.endswith('_MESSAGE_TEMPLATE')]
        return {**{c: config[c] for c in chaves}, **DEFAULT_TEMPLATES}

    def test_nenhum_template_diz_plex(self):
        com_marca = sorted(
            chave for chave, texto in self._padroes().items()
            if 'plex' in str(texto).lower()
        )

        assert com_marca == []

    def test_nenhum_template_de_reativacao_fala_de_aceitar_convites(self):
        # No Jellyfin não há convite nenhum para aceitar: a conta já existe e o
        # painel limita-se a tirar-lhe a suspensão.
        falam = sorted(
            chave for chave, texto in self._padroes().items()
            if 'REACTIVATION' in chave and 'convite' in str(texto).lower()
        )

        assert falam == []


class TestAMensagemDeReativacao:
    """O que chega mesmo a quem pagou, num painel de cada servidor."""

    def _mensagem(self, notifier, monkeypatch, config_file, short_name, link):
        from app import extensions

        class BackendFalso:
            SHORT_NAME = short_name

        monkeypatch.setattr(extensions, 'media_server', BackendFalso())
        config_file(IS_CONFIGURED=True, TELEGRAM_ENABLED=True, TELEGRAM_BOT_TOKEN="x",
                    DISCORD_ENABLED=False, WHATSAPP_ENABLED=False, WEBHOOK_ENABLED=False)

        enviadas = []
        monkeypatch.setattr(notifier, '_send_telegram_notification',
                            lambda mensagem, *a, **k: enviadas.append(mensagem))

        notifier.send_reactivation_notification(
            {"username": "ana", "id": "1"}, "2027-01-01T23:59:00+00:00",
            {"media_user_id": "1", "telegram_id": "123"}, link,
        )
        return enviadas[0]

    def test_num_painel_jellyfin_leva_o_endereco_do_servidor(self, app_context, notifier,
                                                             monkeypatch, config_file):
        mensagem = self._mensagem(notifier, monkeypatch, config_file,
                                  'Jellyfin', 'https://media.exemplo.test')

        assert 'https://media.exemplo.test' in mensagem
        assert 'plex' not in mensagem.lower()
        # ⚠️ Ali não há convite nenhum para aceitar.
        assert 'convite' not in mensagem.lower()

    def test_num_painel_plex_leva_o_link_do_convite(self, app_context, notifier,
                                                    monkeypatch, config_file):
        mensagem = self._mensagem(notifier, monkeypatch, config_file, 'Plex',
                                  'https://clients.plex.tv/servers/shared_servers/accept?invite_token=x')

        assert 'invite_token=x' in mensagem

    def test_sem_endereco_nenhum_a_mensagem_sai_na_mesma(self, app_context, notifier,
                                                         monkeypatch, config_file):
        # Um servidor que não saiba dizer um endereço não pode impedir a pessoa
        # de saber que a assinatura dela foi reativada.
        mensagem = self._mensagem(notifier, monkeypatch, config_file, 'Jellyfin', None)

        assert 'None' not in mensagem
        assert '2027' in mensagem
