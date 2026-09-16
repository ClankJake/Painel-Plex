# tests/test_notificacoes_push.py
"""As notificações push: a cifra, a entrega e quem recebe o quê.

O painel não usa nenhuma biblioteca de web push (a `pywebpush` arrasta a
`http-ece`, que já não compila), por isso a cifra é dele — e uma cifra própria
sem teste de vetor é um "funciona no meu navegador" à espera de acontecer. O
primeiro teste deste ficheiro é o exemplo da **RFC 8291**, byte a byte: se ele
falhar, nenhuma notificação chega a lado nenhum, e o log não vai dizer porquê.
"""

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.services import web_push


# --- A cifra ---------------------------------------------------------------

class TestCifra:
    """RFC 8291, secção 5: o exemplo completo, com as chaves e o sal fixados."""

    P256DH = ("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcx"
              "aOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4")
    AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
    PRIVADA_DO_SERVIDOR = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
    SAL = "DGv6ra1nlYgDCS1FRnbzlw"
    TEXTO = b"When I grow up, I want to be a watermelon"
    ESPERADO = (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIg"
        "Dll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVB"
        "St2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )

    def test_reproduz_o_exemplo_da_norma(self):
        efemera = ec.derive_private_key(
            int.from_bytes(web_push.de_b64url(self.PRIVADA_DO_SERVIDOR), 'big'),
            ec.SECP256R1(),
        )
        corpo = web_push.cifrar(
            self.TEXTO, self.P256DH, self.AUTH,
            sal=web_push.de_b64url(self.SAL), efemera=efemera,
        )
        assert web_push.b64url(corpo) == self.ESPERADO

    def test_o_corpo_comeca_pelo_cabecalho_do_aes128gcm(self):
        """Sal (16) + tamanho do registo (4) + comprimento (1) + chave (65)."""
        corpo = web_push.cifrar(b"ola", self.P256DH, self.AUTH)
        assert len(corpo) > 86
        assert corpo[20] == 65
        assert corpo[21] == 0x04  # ponto não comprimido

    def test_dois_envios_iguais_dao_corpos_diferentes(self):
        """O sal e a chave efémera TÊM de ser novos: repeti-los quebra a cifra."""
        primeiro = web_push.cifrar(b"ola", self.P256DH, self.AUTH)
        segundo = web_push.cifrar(b"ola", self.P256DH, self.AUTH)
        assert primeiro != segundo

    def test_recusa_um_payload_grande_demais(self):
        """Acima do limite, o serviço de push responde 413 e a mensagem perde-se."""
        with pytest.raises(web_push.PushRecusado):
            web_push.cifrar(b"x" * 5000, self.P256DH, self.AUTH)

    def test_recusa_uma_chave_do_navegador_malformada(self):
        with pytest.raises(web_push.PushRecusado):
            web_push.cifrar(b"ola", web_push.b64url(b"curta"), self.AUTH)

    @pytest.mark.parametrize("escrita", [
        "BTBZMqHH6r4Tts7J_aSIgg",     # base64 de URL, sem preenchimento
        "BTBZMqHH6r4Tts7J_aSIgg==",   # com preenchimento
        "BTBZMqHH6r4Tts7J+aSIgg",     # alfabeto normal
    ])
    def test_le_o_segredo_em_qualquer_das_escritas(self, escrita):
        """🐛 Nem todos os navegadores escrevem as chaves da mesma maneira."""
        assert len(web_push.de_b64url(escrita)) == 16


# --- VAPID -----------------------------------------------------------------

class TestVapid:
    def test_o_par_gerado_corresponde(self):
        par = web_push.gerar_par_de_chaves()
        assert web_push.publica_de(par['privada']) == par['publica']
        assert len(web_push.de_b64url(par['publica'])) == 65
        assert len(web_push.de_b64url(par['privada'])) == 32

    def test_o_cabecalho_leva_um_jwt_verificavel(self):
        from cryptography.hazmat.primitives.asymmetric import utils as asym
        from cryptography.hazmat.primitives.hashes import SHA256

        par = web_push.gerar_par_de_chaves()
        cabecalho = web_push.cabecalho_vapid(
            "https://fcm.googleapis.com/fcm/send/abc123", par['privada'],
            "mailto:dono@exemplo.test")

        assert cabecalho.startswith("vapid t=")
        jwt, chave = cabecalho[len("vapid t="):].split(",k=")
        assert chave == par['publica']

        cabecalho_b64, corpo_b64, assinatura_b64 = jwt.split('.')
        reivindicacoes = json.loads(web_push.de_b64url(corpo_b64))
        assert reivindicacoes['aud'] == "https://fcm.googleapis.com"
        assert reivindicacoes['sub'] == "mailto:dono@exemplo.test"
        assert reivindicacoes['exp'] > time.time()

        # A assinatura é dois inteiros crus de 32 bytes (JWS), não DER: mandar
        # DER dá um 401 do serviço de push que não explica nada.
        bruta = web_push.de_b64url(assinatura_b64)
        assert len(bruta) == 64
        publica = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), web_push.de_b64url(par['publica']))
        publica.verify(
            asym.encode_dss_signature(
                int.from_bytes(bruta[:32], 'big'), int.from_bytes(bruta[32:], 'big')),
            f"{cabecalho_b64}.{corpo_b64}".encode(),
            ec.ECDSA(SHA256()),
        )

    def test_recusa_um_endereco_sem_forma(self):
        par = web_push.gerar_par_de_chaves()
        with pytest.raises(web_push.PushRecusado):
            web_push.cabecalho_vapid("nao-e-um-url", par['privada'], "mailto:a@b.c")


# --- A entrega -------------------------------------------------------------

class _Resposta:
    def __init__(self, status, texto=''):
        self.status_code = status
        self.text = texto


class _SessaoFalsa:
    """Guarda o que foi enviado, em vez de o mandar para a internet."""

    def __init__(self, status=201):
        self.status = status
        self.pedidos = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.pedidos.append({'url': url, 'data': data, 'headers': headers})
        return _Resposta(self.status)


@pytest.fixture()
def subscricao():
    par = web_push.gerar_par_de_chaves()
    aparelho = ec.generate_private_key(ec.SECP256R1())
    from cryptography.hazmat.primitives import serialization

    publica = aparelho.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {
        'vapid': par,
        'aparelho': {
            'endpoint': 'https://updates.push.services.mozilla.com/wpush/v2/abc',
            'p256dh': web_push.b64url(publica),
            'auth': web_push.b64url(b'0123456789abcdef'),
        },
    }


class TestEntrega:
    def test_manda_o_corpo_cifrado_com_os_cabecalhos_certos(self, subscricao):
        sessao = _SessaoFalsa(201)
        web_push.enviar(subscricao['aparelho'], {'title': 'Oi', 'body': 'tudo bem'},
                        subscricao['vapid']['privada'], 'mailto:a@b.c', sessao=sessao)

        pedido = sessao.pedidos[0]
        assert pedido['url'] == subscricao['aparelho']['endpoint']
        assert pedido['headers']['Content-Encoding'] == 'aes128gcm'
        assert pedido['headers']['Authorization'].startswith('vapid t=')
        # O conteúdo não pode viajar em claro: quem transporta é o serviço de
        # push, e ele não tem de conseguir ler o que vai lá dentro.
        assert b'tudo bem' not in pedido['data']

    @pytest.mark.parametrize("codigo", [404, 410])
    def test_uma_subscricao_morta_levanta_push_expirado(self, subscricao, codigo):
        with pytest.raises(web_push.PushExpirado):
            web_push.enviar(subscricao['aparelho'], {'title': 'Oi'},
                            subscricao['vapid']['privada'], 'mailto:a@b.c',
                            sessao=_SessaoFalsa(codigo))

    def test_uma_recusa_do_servico_levanta_push_recusado(self, subscricao):
        with pytest.raises(web_push.PushRecusado):
            web_push.enviar(subscricao['aparelho'], {'title': 'Oi'},
                            subscricao['vapid']['privada'], 'mailto:a@b.c',
                            sessao=_SessaoFalsa(403))


# --- O destino: de onde o painel aceita um endereço de entrega ---------------

class TestDestinoPermitido:
    """🛡️ Regressão de um SSRF (CWE-918) apanhado na revisão do PR.

    O endereço de entrega é escolhido por quem subscreve e o painel faz-lhe POST
    a partir de DENTRO da rede. Enquanto só se verificava o esquema, qualquer
    pessoa com sessão — sem ser administrador — registava um aparelho a apontar
    para um serviço interno e usava o painel para lhe bater, com a rota
    `/push/test` por gatilho.
    """

    @pytest.mark.parametrize("endereco", [
        "https://fcm.googleapis.com/wp/abc",
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://web.push.apple.com/abc",
        "https://db5p.notify.windows.com/w/?token=abc",
    ])
    def test_aceita_os_servicos_de_push_a_serio(self, endereco):
        assert web_push.endpoint_permitido(endereco) is True

    @pytest.mark.parametrize("endereco", [
        "https://127.0.0.1/push",                  # o próprio painel
        "https://10.0.0.5:8443/api",               # rede interna
        "https://169.254.169.254/latest/meta-data",  # metadados da nuvem
        "https://localhost:9200/_cluster/health",
        "http://fcm.googleapis.com/wp/abc",        # sem TLS
        "ftp://fcm.googleapis.com/wp/abc",
        "",
    ])
    def test_recusa_um_destino_que_nao_e_de_push(self, endereco):
        assert web_push.endpoint_permitido(endereco) is False

    @pytest.mark.parametrize("endereco", [
        "https://fcm.googleapis.com.atacante.net/wp/abc",
        "https://naoefcm.googleapis.com/wp/abc",
        "https://push.apple.com.atacante.net/abc",
    ])
    def test_a_comparacao_e_pela_fronteira_do_dominio(self, endereco):
        """Os três CONTÊM o texto do domínio e nenhum deles É o domínio."""
        assert web_push.endpoint_permitido(endereco) is False

    def test_quem_corre_um_servico_proprio_acrescenta_o_no_ambiente(self, monkeypatch):
        interno = "https://push.minhaempresa.test/wpush/abc"
        assert web_push.endpoint_permitido(interno) is False

        monkeypatch.setenv(web_push.EXTRA_HOSTS_ENV_VAR, "push.minhaempresa.test")
        assert web_push.endpoint_permitido(interno) is True

    def test_a_entrega_recusa_o_que_ja_estiver_gravado(self, subscricao):
        """A segunda porta: uma linha vinda de um backup antigo não é usável.

        A verificação à entrada dá o erro a quem subscreve, mas não protege uma
        linha que tenha entrado na tabela por outro caminho.
        """
        aparelho = dict(subscricao['aparelho'], endpoint="https://10.0.0.5/push")
        sessao = _SessaoFalsa(201)

        with pytest.raises(web_push.PushRecusado):
            web_push.enviar(aparelho, {'title': 'Oi'},
                            subscricao['vapid']['privada'], 'mailto:a@b.c', sessao=sessao)

        # E o pedido não chegou a sair.
        assert sessao.pedidos == []
