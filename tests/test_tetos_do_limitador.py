# tests/test_tetos_do_limitador.py
"""Um `@limiter.limit` de rota SUBSTITUI o padrão global — não se soma a ele.

🐛 É contraintuitivo, e custou caro: a aplicação define
`RATELIMIT_DEFAULT = "200 per day; 50 per hour"`, que vale para toda a rota
sem limite próprio. Pôr-lhe um `@limiter.limit("30 per minute")` não a aperta:
**afrouxa-a**, de 50/hora para 1800/hora, e deita fora o teto diário por
completo.

Medido, com uma rota a declarar um limite folgado de propósito:

    @limiter.limit("100 per minute")                          → 429 nunca chega
    @limiter.limit("100 per minute", override_defaults=False) → 429 ao 51.º

O caso que doeu foi o `get_invite_details_route`, cujo próprio docstring dizia
que o decorador existia para travar força bruta sobre códigos de convite — e
que o estava a acelerar 36×.

⚠️ E a correção NÃO é mecânica: há rotas onde o padrão partiria o produto. O
PIN do Plex faz polling de 3 em 3 segundos e o estado do pagamento de 5 em 5;
um só login de três minutos são 60 pedidos, e o teto de 200/dia mataria o
fluxo à terceira tentativa. Por isso este teste não exige `override_defaults`
em toda a parte: exige que cada exceção esteja NOMEADA aqui, com o motivo.
"""

import ast
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
APP = RAIZ / 'app'

# As rotas que ficam ACIMA do padrão global de propósito. A chave é o nome da
# função; o valor é a razão, que é o que interessa a quem ler isto daqui a um
# ano. Acrescentar uma linha aqui é uma decisão; esquecê-la dá um teste
# vermelho, que é exatamente o que falta ao código de hoje.
ACIMA_DO_PADRAO_DE_PROPOSITO = {
    # Polling do navegador: o teto diário mataria o fluxo a meio.
    'check_plex_pin': 'o navegador faz polling de 3 em 3 segundos durante o login',
    'check_plex_pin_for_token': 'o mesmo polling, no fluxo do token de pagamento',
    'get_payment_status_route': 'a página do QR code faz polling de 5 em 5 segundos',

    # API de integrações: autenticada por chave, e um bot ativo passa os 200/dia.
    'create_invite_for_bot': 'API de bots, autenticada por chave de API',
    'bot_invite_status': 'API de bots, autenticada por chave de API',
    'bot_invite_delete': 'API de bots, autenticada por chave de API',
    'bot_invites_por_contacto': 'API de bots, autenticada por chave de API',

    # Páginas e ações de administrador, onde o teto por ENDEREÇO apanharia uma
    # equipa atrás do mesmo NAT sem nada comprar em segurança.
    'login': 'a página de login (GET); um escritório atrás de um NAT partiria',
    'get_plex_auth_context': 'arranca o fluxo de PIN, uma vez por tentativa',
    'redirect_to_auth': 'redireciona para o plex.tv, uma vez por tentativa',
    'get_payment_options': 'a página da conta carrega-a a cada visita',
    'subscribe_push_route': 'autenticada; 30/hora já é mais apertado que o padrão',
    'test_push_route': 'autenticada; 10/hora já é mais apertado que o padrão',
    'setup_restore_backup': '5/hora já é mais apertado que o padrão',
    'save_setup': '10/hora já é mais apertado que o padrão',
    'get_jellyfin_users_for_setup': '20/hora já é mais apertado que o padrão',
    'test_gates2b_connection': 'administrador a testar uma ligação, repetidamente',
    'test_whatsapp_connection': 'administrador a testar uma ligação, repetidamente',
    'sync_profiles_route': 'administrador a sincronizar perfis, repetidamente',
}


def _rotas_com_limite():
    """(ficheiro, linha, nome da função, tem override_defaults=False)."""
    achados = []
    for ficheiro in sorted(APP.rglob('*.py')):
        texto = ficheiro.read_text(encoding='utf-8')
        linhas = texto.split('\n')
        try:
            arvore = ast.parse(texto)
        except SyntaxError:  # pragma: no cover
            continue
        for no in ast.walk(arvore):
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in no.decorator_list:
                fonte = ast.unparse(dec)
                if not re.match(r'limiter\.limit\(', fonte):
                    continue
                achados.append((
                    ficheiro.relative_to(RAIZ).as_posix(),
                    dec.lineno,
                    no.name,
                    'override_defaults=False' in fonte.replace(' ', ''),
                ))
    return achados


def test_toda_a_rota_limitada_soma_ou_esta_nomeada_como_excecao():
    esquecidas = [
        f'{f}:{l}  {nome}'
        for f, l, nome, soma in _rotas_com_limite()
        if not soma and nome not in ACIMA_DO_PADRAO_DE_PROPOSITO
    ]
    assert not esquecidas, (
        'Estas rotas declaram um limite próprio que SUBSTITUI o padrão global '
        '("200 per day; 50 per hour") — provavelmente afrouxando-as:\n  '
        + '\n  '.join(esquecidas)
        + '\n\nOu acrescente `override_defaults=False` (os três tetos passam a '
          'valer, e o mais apertado ganha), ou nomeie a rota em '
          '`ACIMA_DO_PADRAO_DE_PROPOSITO`, neste ficheiro, com o motivo.'
    )


def test_a_lista_de_excecoes_nao_tem_nomes_a_mais():
    """Uma exceção que já não existe é uma autorização esquecida em aberto."""
    reais = {nome for _, _, nome, _ in _rotas_com_limite()}
    fantasmas = sorted(set(ACIMA_DO_PADRAO_DE_PROPOSITO) - reais)
    assert not fantasmas, (
        f'Estas entradas já não correspondem a nenhuma rota limitada: {fantasmas}. '
        'Tire-as da lista — senão uma rota futura com o mesmo nome herda uma '
        'dispensa que ninguém lhe deu.'
    )


def test_o_padrao_global_continua_definido():
    """Sem ele, `override_defaults=False` deixa de somar seja o que for."""
    texto = (APP / '__init__.py').read_text(encoding='utf-8')
    assert re.search(r"RATELIMIT_DEFAULT'\]\s*=\s*\"[^\"]+\"", texto), (
        'O `RATELIMIT_DEFAULT` desapareceu do `create_app`. É ele que dá os '
        'tetos que as rotas com `override_defaults=False` somam ao seu.'
    )


# ==========================================================================
# E o que o limitador REGISTOU, não só o que o texto declara
# ==========================================================================

def _limites_resolvidos(app, endpoint):
    """O que o Flask-Limiter vai mesmo aplicar a este endpoint.

    Uma asserção sobre o texto do decorador diz o que alguém escreveu; esta diz
    o que o limitador faz. É a diferença entre ler a intenção e medir o efeito —
    e foi a ler a intenção que este bug passou.
    """
    from app.extensions import limiter

    grupos = limiter.limit_manager.resolve_limits(
        app, endpoint, endpoint.split('.')[0], None, False, False
    )
    return {
        str(limite.limit)
        for grupo in grupos
        for limite in (grupo if isinstance(grupo, list) else [grupo])
    }


@pytest.mark.integration
class TestOQueOLimitadorRegistou:

    def test_uma_rota_sensivel_herda_os_tetos_globais(self, app):
        """Com `override_defaults=False`, os TRÊS limites valem."""
        limites = _limites_resolvidos(app, 'invites_api.get_invite_details_route')

        assert '30 per 1 minute' in limites, 'o limite próprio da rota sumiu'
        assert '50 per 1 hour' in limites and '200 per 1 day' in limites, (
            f'O oráculo de códigos de convite só tem {sorted(limites)}. Sem os '
            f'tetos globais, o 429 recomeça a cada minuto — 1800/hora — e o '
            f'decorador que existe para travar a força bruta acelera-a.'
        )

    def test_uma_rota_de_polling_fica_só_com_o_seu(self, app):
        """A exceção, e é uma escolha: o teto de 200/dia mataria o login por PIN
        à terceira tentativa (o navegador pergunta de 3 em 3 segundos)."""
        limites = _limites_resolvidos(app, 'auth.check_plex_pin')

        assert limites == {'60 per 1 minute'}, (
            f'O `check_plex_pin` ficou com {sorted(limites)}. Se lhe entrarem os '
            f'tetos globais, um login de três minutos gasta 60 pedidos e o '
            f'fluxo morre ao fim de três tentativas no mesmo dia.'
        )

    def test_uma_rota_sem_decorador_fica_com_o_padrao(self, app):
        """O caso base, que é o que a maioria das rotas do painel tem."""
        assert _limites_resolvidos(app, 'redirect.redirect_to_url') == {
            '200 per 1 day', '50 per 1 hour',
        }


# ==========================================================================
# E que ninguém legítimo é travado
# ==========================================================================

@pytest.mark.integration
class TestOTetoValeMesmo:
    @pytest.fixture(autouse=True)
    def contador_limpo(self):
        from app.extensions import limiter

        limiter.reset()
        yield
        limiter.reset()

    def test_uma_pessoa_a_abrir_o_convite_nunca_e_travada(self, client, config_file):
        """O caso que não pode partir: a página valida o convite uma vez."""
        config_file(IS_CONFIGURED=True)

        for _ in range(5):
            assert client.get("/api/invites/details/NAOEXISTE").status_code == 404
