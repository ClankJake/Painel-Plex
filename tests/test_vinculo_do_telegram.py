# tests/test_vinculo_do_telegram.py
"""O Telegram é o único canal que um formulário digitado não resolve.

⚠️ Duas razões independentes, e as duas têm de ser resolvidas ao mesmo tempo:

1. o painel manda `chat_id = telegram_id or telegram_user` direto para a API
   (`_prepare_and_send`), e um `@username` **não endereça uma conversa
   privada** — só um canal. O que serve ali é o id NUMÉRICO do chat, que a
   pessoa não conhece nem tem como descobrir;
2. mesmo com o id certo, **um bot não pode iniciar uma conversa**: enquanto a
   pessoa não abrir o bot e tocar em "Começar", qualquer envio devolve 403.

Por isso o `telegram_id` dos convites vem sempre de um bot que JÁ falou com ela.
O deep link `t.me/<bot>?start=<codigo>` resolve os dois: ela toca (ponto 2) e o
`/start <codigo>` que o bot recebe traz o `chat.id` (ponto 1).

🛡️ **E a leitura é `getUpdates` SEM offset, de propósito.** O Telegram só
descarta o que já foi lido quando é chamado com um offset maior; sem ele
devolve o que está pendente e não confirma nada. É isso que permite ler sem
roubar as atualizações de um bot que o administrador já tenha no mesmo token —
e este painel tem uma API de convites para bots, por isso o caso não é
hipotético. Com um webhook registado o Telegram responde 409, e aí o painel DIZ
que não dá em vez de tentar tomar o lugar dele com um `deleteWebhook`.
"""

import pytest

pytestmark = pytest.mark.integration


class ChatFalso:
    def __init__(self, chat_id):
        self.id = chat_id


class MensagemFalsa:
    def __init__(self, texto, chat_id):
        self.text = texto
        self.chat = ChatFalso(chat_id)


class AtualizacaoFalsa:
    def __init__(self, texto, chat_id):
        self.message = MensagemFalsa(texto, chat_id)


class BotFalso:
    """O mínimo do `telebot` que este módulo usa."""

    token = 'token-de-teste'

    def __init__(self, atualizacoes=None, nome='painel_bot', erro=None):
        self._atualizacoes = atualizacoes or []
        self._nome = nome
        self._erro = erro
        self.chamadas = []

    def get_me(self):
        class Eu:
            username = self._nome
        return Eu()

    def get_updates(self, **kwargs):
        self.chamadas.append(kwargs)
        if self._erro:
            raise self._erro
        return self._atualizacoes


@pytest.fixture(autouse=True)
def _sem_cache_do_nome():
    from app.services import telegram_vinculo

    telegram_vinculo._nome_em_cache.clear()
    yield
    telegram_vinculo._nome_em_cache.clear()


def _com_bot(monkeypatch, bot):
    from app.services import telegram_vinculo

    monkeypatch.setattr(telegram_vinculo, '_bot', lambda config: bot)


LIGADO = {'TELEGRAM_ENABLED': True}


class TestOLinkDeVinculo:

    def test_monta_o_deep_link_com_o_nome_do_bot(self, app, monkeypatch):
        from app.services import telegram_vinculo

        _com_bot(monkeypatch, BotFalso(nome='meu_painel_bot'))
        assert telegram_vinculo.link_de_vinculo(LIGADO, 'abc123') == \
            'https://t.me/meu_painel_bot?start=abc123'

    def test_sem_bot_nao_ha_link(self, app, monkeypatch):
        from app.services import telegram_vinculo

        _com_bot(monkeypatch, None)
        assert telegram_vinculo.link_de_vinculo(LIGADO, 'abc123') is None

    def test_uma_falha_de_rede_nao_fica_em_cache(self, app, monkeypatch):
        """⚠️ Não saber não é o mesmo que não existir — a mesma regra da
        deteção dos plugins do Jellyfin. Guardar o "não" de um segundo mau
        deixava a página dez minutos sem o botão."""
        from app.services import telegram_vinculo

        class BotQueFalhaUmaVez(BotFalso):
            def __init__(self):
                super().__init__()
                self.tentativas = 0

            def get_me(self):
                self.tentativas += 1
                if self.tentativas == 1:
                    raise RuntimeError('rede em baixo')
                return super().get_me()

        bot = BotQueFalhaUmaVez()
        _com_bot(monkeypatch, bot)

        assert telegram_vinculo.nome_do_bot(LIGADO) is None
        assert telegram_vinculo.nome_do_bot(LIGADO) == 'painel_bot', \
            "a segunda tentativa tem de voltar a perguntar"


class TestAProcuraDoStart:

    def test_encontra_o_chat_de_quem_mandou_o_codigo(self, app, monkeypatch):
        from app.services import telegram_vinculo

        bot = BotFalso([
            AtualizacaoFalsa('/start outro-codigo', 111),
            AtualizacaoFalsa('/start abc123', 222),
        ])
        _com_bot(monkeypatch, bot)

        assert telegram_vinculo.procurar_chat(LIGADO, 'abc123').chat_id == '222'

    def test_o_codigo_de_outra_pessoa_nao_serve(self, app, monkeypatch):
        from app.services import telegram_vinculo

        _com_bot(monkeypatch, BotFalso([AtualizacaoFalsa('/start outro', 111)]))
        procura = telegram_vinculo.procurar_chat(LIGADO, 'abc123')
        assert procura.chat_id is None and procura.motivo is None, \
            'sem motivo quer dizer "ainda não chegou", que não é "não dá"'

    def test_le_SEM_offset_para_nao_consumir_as_atualizacoes(self, app, monkeypatch):
        """🛡️ É isto que impede o painel de roubar o que o bot do
        administrador ainda não leu."""
        from app.services import telegram_vinculo

        bot = BotFalso([AtualizacaoFalsa('/start abc123', 222)])
        _com_bot(monkeypatch, bot)
        telegram_vinculo.procurar_chat(LIGADO, 'abc123')

        assert bot.chamadas, "o getUpdates tem de ser chamado"
        for chamada in bot.chamadas:
            assert 'offset' not in chamada or chamada['offset'] is None, (
                "com offset, o Telegram descarta o que o bot do administrador "
                "ainda não tinha lido"
            )

    def test_nao_fica_a_espera_dentro_do_pedido(self, app, monkeypatch):
        """⚠️ Um worker gevent: um long polling aqui é o painel inteiro parado."""
        from app.services import telegram_vinculo

        bot = BotFalso([])
        _com_bot(monkeypatch, bot)
        telegram_vinculo.procurar_chat(LIGADO, 'abc123')

        assert bot.chamadas[0].get('long_polling_timeout') == 0

    def test_um_409_diz_que_nao_da_em_vez_de_tomar_o_lugar(self, app, monkeypatch):
        """🛡️ Há um webhook registado ou outro bot no mesmo token. Um
        `deleteWebhook` aqui partia, em silêncio, o bot do administrador."""
        from telebot.apihelper import ApiTelegramException

        from app.services import telegram_vinculo

        erro = ApiTelegramException('getUpdates', None, {'error_code': 409, 'description': 'Conflict'})
        _com_bot(monkeypatch, BotFalso(erro=erro))

        assert telegram_vinculo.procurar_chat(LIGADO, 'abc123').motivo == telegram_vinculo.OCUPADO

    def test_sem_telegram_configurado_o_motivo_diz_qual_e(self, app, monkeypatch):
        """"Ainda não chegou" e "não dá" são respostas diferentes: a página
        precisa da distinção para dizer a coisa certa a quem está à espera."""
        from app.services import telegram_vinculo

        _com_bot(monkeypatch, None)
        assert telegram_vinculo.procurar_chat(LIGADO, 'abc123').motivo == \
            telegram_vinculo.SEM_TELEGRAM

    def test_o_erro_de_baixo_nunca_sai_no_motivo(self, app, monkeypatch):
        """🛡️ O motivo é um CÓDIGO, e a frase é da rota.

        O que o cliente do Telegram levanta pode trazer o endereço da API e o
        que mais lá estiver; ele vai para o log e não para a resposta. Era uma
        exceção cuja `str()` a rota devolvia, e o CodeQL marcou-a numa rota
        PÚBLICA (alerta 89 do PR #44).
        """
        from app.services import telegram_vinculo

        _com_bot(monkeypatch, BotFalso(erro=RuntimeError('https://api.telegram.org/botSEGREDO/x')))
        resultado = telegram_vinculo.procurar_chat(LIGADO, 'abc123')

        assert resultado.motivo == telegram_vinculo.FALHOU
        assert 'SEGREDO' not in str(resultado)
        assert 'api.telegram.org' not in str(resultado)


class TestOQueOPainelNuncaFaz:

    def test_o_modulo_nao_chama_deleteWebhook_nem_setWebhook(self):
        """O painel lê; não toma conta do bot de ninguém."""
        from pathlib import Path

        import re

        fonte = Path('app/services/telegram_vinculo.py').read_text(encoding='utf-8')
        # ⚠️ Procura-se a CHAMADA, não a palavra: o comentário que explica
        # porque é que isto não se faz tem de poder nomeá-la.
        chamada = re.compile(r'\.\s*(delete_webhook|set_webhook)\s*\(')
        encontrado = chamada.findall(fonte)
        assert encontrado == [], (
            f"{encontrado} tomaria conta do bot que o administrador já tem a correr"
        )
