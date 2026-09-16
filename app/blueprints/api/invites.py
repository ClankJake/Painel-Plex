# app/blueprints/api/invites.py

import logging

from flask import Blueprint, jsonify, request
from flask_babel import gettext as _
from flask_login import login_required

from ...extensions import media_server, limiter, data_manager, notifier_manager
from ..auth import admin_required
from .decorators import validate_json, chave_de_api_necessaria
from .schemas import CreateInviteSchema, CreateInviteBotSchema
from ...utils.log_sanitizer import mask_code
from ...utils.enderecos import endereco_publico
from ...services import audit
from ...services.media_server.invitations import (
    CONTACTOS, ESTADO_HTTP, PEDIDO_INVALIDO,
    convite_esgotado, convite_expirado, normalizar_contacto,
)

logger = logging.getLogger(__name__)
invites_api_bp = Blueprint('invites_api', __name__)

def _estado_http(resultado):
    """O código HTTP de uma recusa, a partir do motivo que ela traz.

    ⚠️ O endpoint dos bots respondia 409 a TUDO o que falhasse — inclusive a
    "informe pelo menos uma biblioteca", que é um erro do PEDIDO. Do outro lado
    não havia como saber se valia a pena tentar outra vez com outro código
    (409: o estado é que não deixa) ou se o pedido estava simplesmente errado
    (400: tentar de novo dá o mesmo).
    """
    return ESTADO_HTTP.get(resultado.get('erro'), 400)


def _convite_para_a_api(convite):
    """O convite como uma integração o vê.

    ⚠️ Os nomes são os da API pública e não os das colunas: `active` e
    `uses_left` são o que um bot quer perguntar, e derivá-los no cliente
    obrigava cada integração a repetir as duas regras (esgotado, expirado) e a
    conhecer o formato da data. É o mesmo que o painel faz no cartão da lista.

    🔒 Não leva `libraries`: os nomes das bibliotecas são infraestrutura do
    servidor, e quem tem a chave de um bot não precisa deles para mandar um
    link a alguém.
    """
    expirado = convite_expirado(convite.get('expires_at'), convite.get('code', ''))
    esgotado = convite_esgotado(convite)
    usados, maximo = convite.get('use_count', 0), convite.get('max_uses', 1)

    return {
        'code': convite.get('code'),
        'invite_url': endereco_publico('main.claim_invite_page', code=convite.get('code')),
        'active': not (expirado or esgotado),
        'expired': expirado,
        'exhausted': esgotado,
        'created_at': convite.get('created_at'),
        'expires_at': convite.get('expires_at'),
        'claimed_at': convite.get('claimed_at'),
        'use_count': usados,
        'max_uses': maximo,
        'uses_left': max(maximo - usados, 0),
        'claimed_by': convite.get('claimed_by_users') or [],
        'trial_duration_minutes': convite.get('trial_duration_minutes', 0),
        'screens': convite.get('screen_limit', 0),
        'allow_downloads': bool(convite.get('allow_downloads', False)),
        'overseerr_access': bool(convite.get('overseerr_access', False)),
        'telegram_id': convite.get('telegram_id'),
        'discord_id': convite.get('discord_id'),
        'note': convite.get('note'),
    }


def _para_a_auditoria(pedido, code):
    """O que fica registado sobre um convite criado.

    ⚠️ O código vai INTEIRO, ao contrário do que se faz no `app.log`. Ele é uma
    credencial portadora, mas o `mask_code` do log existe porque o log é um
    ficheiro à parte, que se copia e se cola num chat de suporte; a auditoria
    vive na MESMA base de dados que a tabela `invitations`, onde os códigos já
    estão em claro. Mascará-lo aqui não esconderia nada de ninguém e tornava a
    linha inútil — "alguém apagou um convite" sem dizer qual.
    """
    return {
        'codigo': code,
        'bibliotecas': pedido.get('libraries') or [],
        'telas': pedido.get('screens', 0),
        'downloads': bool(pedido.get('allow_downloads', False)),
        'expira_em_minutos': pedido.get('expires_in_minutes'),
        'teste_em_minutos': pedido.get('trial_duration_minutes', 0),
        'acesso_aos_pedidos': bool(pedido.get('overseerr_access', False)),
        'usos': pedido.get('max_uses', 1),
        'telegram_id': pedido.get('telegram_id'),
        'discord_id': pedido.get('discord_id'),
        'nota': pedido.get('note'),
    }


def _titulos_do_servidor():
    """Os nomes das bibliotecas, ou None quando não foi possível perguntar.

    ⚠️ A diferença entre `[]` e `None` é a que interessa: as duas conexões
    devolvem uma lista vazia tanto quando o servidor está em baixo como quando
    ele não tem bibliotecas nenhumas, e não há como as distinguir daqui. Tratar
    o vazio como "não sei" é o lado seguro — não saber que bibliotecas existem
    nunca pode ser motivo para impedir o administrador de criar um convite.
    """
    try:
        bibliotecas = media_server.get_libraries() or []
    except Exception as e:
        logger.warning(f"Não foi possível obter as bibliotecas do servidor: {e}")
        return None

    titulos = [b.get('title') for b in bibliotecas if isinstance(b, dict) and b.get('title')]
    return titulos or None


def _resolver_bibliotecas(pedidas):
    """Traduz o que foi pedido para a grafia do SERVIDOR.

    🐛 Um convite era aceite com QUALQUER nome de biblioteca. A falha só
    aparecia no resgate, lá dentro do `send_invite` — "Nenhuma biblioteca
    válida foi encontrada para compartilhar" — ou seja, quem pagava o engano do
    administrador era quem tinha acabado de clicar no link. Um bot, que manda
    os nomes escritos à mão, acertava ainda menos.

    E a grafia importa: o backend do Plex compara `s.title in library_titles`
    exatamente, por isso um convite criado com "filmes" num servidor que tem
    "Filmes" nascia com uma biblioteca que nunca ia ser encontrada. Devolvemos
    sempre o nome como o servidor o escreve.

    Devolve `(titulos, mensagem_de_erro)`.
    """
    do_servidor = _titulos_do_servidor()
    if do_servidor is None:
        return list(pedidas), None

    por_minusculas = {titulo.casefold(): titulo for titulo in do_servidor}

    canonicos, desconhecidas = [], []
    for pedida in pedidas:
        encontrada = por_minusculas.get(str(pedida).strip().casefold())
        if encontrada is None:
            desconhecidas.append(str(pedida))
        else:
            canonicos.append(encontrada)

    if desconhecidas:
        return None, _(
            "Estas bibliotecas não existem no servidor: %(nomes)s.",
            nomes=', '.join(desconhecidas),
        )
    return canonicos, None


@invites_api_bp.route('/create', methods=['POST'])
@login_required
@admin_required
@validate_json(CreateInviteSchema)
def create_invite_route(validated_data):
    data = validated_data.dict()

    libraries, erro = _resolver_bibliotecas(data.get('libraries', []))
    if erro:
        return jsonify({"success": False, "message": erro}), 400

    result = media_server.create_invitation(
        library_titles=libraries, 
        screens=data.get('screens', 0), 
        allow_downloads=data.get('allow_downloads', False), 
        expires_in_minutes=data.get('expires_in_minutes'),
        trial_duration_minutes=data.get('trial_duration_minutes', 0),
        overseerr_access=data.get('overseerr_access', False),
        custom_code=data.get('custom_code'),
        max_uses=data.get('max_uses', 1),
        telegram_id=data.get('telegram_id'),
        note=data.get('note'),
        discord_id=data.get('discord_id'),
    )
    if result.get('success'):
        result['invite_url'] = endereco_publico('main.claim_invite_page', code=result['code'])
        audit.registar('convite.criar', alvo_tipo='convite', alvo_id=result['code'],
                       detalhes=_para_a_auditoria({**data, 'libraries': libraries}, result['code']))
    return jsonify(result)

@invites_api_bp.route('/bot/create', methods=['POST'])
@limiter.limit("30 per minute")
@chave_de_api_necessaria('convites')
@validate_json(CreateInviteBotSchema)
def create_invite_for_bot(validated_data):
    """
    Endpoint de integração para bots: cria um convite já vinculado a um Telegram ID.

    🔒 AUTENTICAÇÃO: esta rota não usa sessão de navegador (um bot não tem uma),
    por isso é protegida pela chave de API — ver `chave_de_api_necessaria`, que
    é o único sítio do painel onde essa verificação vive.

    Exemplo:
        curl -X POST https://o-seu-painel/api/invites/bot/create \\
             -H "X-API-Key: SUA_CHAVE" \\
             -H "Content-Type: application/json" \\
             -d '{"telegram_id": "123456789", "screens": 1, "trial_duration_minutes": 60}'
    """
    data = validated_data.dict()

    # Se o bot não indicar bibliotecas, usamos todas as do servidor — é o
    # comportamento esperado numa automação, que normalmente não as conhece.
    libraries = data.get('libraries')
    if not libraries:
        # 🐛 Isto nunca funcionou. `get_libraries()` devolve uma LISTA e o
        # código pedia-lhe `.get('success')` — um `AttributeError` que caía no
        # `except` mesmo com o servidor a responder perfeitamente. Resultado: o
        # campo que a documentação anuncia como opcional dava sempre 400, e
        # nenhum bot podia deixar de conhecer os nomes das bibliotecas.
        libraries = _titulos_do_servidor()

        if not libraries:
            return jsonify({
                "success": False,
                "message": _("Não foi possível determinar as bibliotecas automaticamente. Informe 'libraries' na requisição.")
            }), 400
    else:
        libraries, erro = _resolver_bibliotecas(libraries)
        if erro:
            return jsonify({"success": False, "message": erro}), 400

    result = media_server.create_invitation(
        library_titles=libraries,
        screens=data.get('screens', 0),
        allow_downloads=data.get('allow_downloads', False),
        expires_in_minutes=data.get('expires_in_minutes'),
        trial_duration_minutes=data.get('trial_duration_minutes', 0),
        overseerr_access=data.get('overseerr_access', False),
        custom_code=data.get('custom_code'),
        max_uses=data.get('max_uses', 1),
        telegram_id=data.get('telegram_id'),
        note=data.get('note'),
        discord_id=data.get('discord_id'),
    )

    if result.get('success'):
        result['invite_url'] = endereco_publico('main.claim_invite_page', code=result['code'])
        result['telegram_id'] = data.get('telegram_id')
        result['discord_id'] = data.get('discord_id')
        logger.info(f"Convite '{mask_code(result['code'])}' criado via API para o Telegram ID {data.get('telegram_id')}.")
        # Sem sessão, o ator fica vazio e o que identifica quem agiu é o
        # ENDEREÇO — que é a verdade sobre um convite criado por uma máquina.
        audit.registar('convite.criar', alvo_tipo='convite', alvo_id=result['code'],
                       detalhes={**_para_a_auditoria({**data, 'libraries': libraries}, result['code']),
                                 'origem': 'bot'})
        return jsonify(result), 201

    return jsonify(result), _estado_http(result)


@invites_api_bp.route('/bot/invite/<string:code>', methods=['GET'])
@limiter.limit("60 per minute")
@chave_de_api_necessaria('convites')
def bot_invite_status(code):
    """O estado de um convite: já foi usado? ainda vale?

    Sem isto, um bot que gera um convite fica sem saber o que lhe aconteceu. A
    única alternativa era perguntar à pessoa — ou esperar que ela diga que já
    entrou.

    ⚠️ A leitura é a CRUA (`data_manager.get_invitation`) e não a
    `get_invitation_by_code`: esta última é a porta do RESGATE e devolve `None`
    para um convite expirado ou esgotado. Aqui, "expirado" é precisamente a
    resposta que se veio buscar — com a outra porta, um convite gasto seria
    indistinguível de um que nunca existiu.

    O caminho é `/bot/invite/<code>` e não `/bot/<code>` porque um código
    personalizado pode ser a palavra `create`.
    """
    convite = data_manager.get_invitation(code)
    if not convite:
        return jsonify({"success": False, "message": _("Convite não encontrado.")}), 404
    return jsonify({"success": True, "invite": _convite_para_a_api(convite)})


@invites_api_bp.route('/bot/invite/<string:code>', methods=['DELETE'])
@limiter.limit("30 per minute")
@chave_de_api_necessaria('convites')
def bot_invite_delete(code):
    """Revoga um convite que já foi enviado.

    Um link mandado para o chat errado não se desfaz do lado do Telegram, e até
    aqui a única forma de o travar era entrar no painel — o que uma automação,
    por definição, não faz.

    ⚠️ Revogar NÃO apaga quem já resgatou: quem entrou, entrou, e a conta dessa
    pessoa não é assunto deste pedido. O que deixa de valer é o link.
    """
    convite = data_manager.get_invitation(code) or {}
    if not convite:
        return jsonify({"success": False, "message": _("Convite não encontrado.")}), 404

    resultado = media_server.delete_invitation(code)
    if resultado.get('success'):
        audit.registar('convite.apagar', alvo_tipo='convite', alvo_id=code, detalhes={
            'codigo': code,
            'usos': f"{convite.get('use_count', 0)}/{convite.get('max_uses', 1)}",
            'resgatado_por': convite.get('claimed_by_users') or [],
            'origem': 'bot',
        })
    return jsonify(resultado)


@invites_api_bp.route('/bot/invites', methods=['GET'])
@limiter.limit("60 per minute")
@chave_de_api_necessaria('convites')
def bot_invites_por_contacto():
    """Os convites gerados para uma pessoa, do mais recente para o mais antigo.

    Aceita `telegram_id` ou `discord_id` — os mesmos dois canais que a criação.

    É a pergunta que um bot faz antes de gerar outro: a criação recusa-se
    (409) quando já existe um convite ATIVO para aquele contacto, e sem esta
    rota o bot só descobria isso ao levar com o erro — sem saber qual é o link
    que já tinha mandado, nem se a pessoa já o usou.
    """
    procurados = {
        contacto.no_convite: (request.args.get(contacto.no_convite) or '').strip()
        for contacto in CONTACTOS
    }
    pedidos = {campo: valor for campo, valor in procurados.items() if valor}

    if not pedidos:
        return jsonify({
            "success": False, "erro": PEDIDO_INVALIDO,
            "message": _("Informe 'telegram_id' ou 'discord_id' na consulta."),
        }), 400

    def corresponde(convite):
        # A mesma normalização da criação: um bot manda o ID como número e um
        # formulário como texto, e '123' tem de encontrar ' 123 '. Basta UM dos
        # contactos bater — pedir os dois devolveria vazio para o caso normal,
        # em que o convite só tem um.
        return any(
            normalizar_contacto(convite.get(campo)) == valor
            for campo, valor in pedidos.items()
        )

    convites = [
        _convite_para_a_api(convite)
        for convite in media_server.list_invitations() if corresponde(convite)
    ]
    return jsonify({"success": True, **pedidos, "invites": convites})


@invites_api_bp.route('/list', methods=['GET'])
@login_required
@admin_required
@limiter.exempt
def list_invites_route():
    """Uma PÁGINA de convites, já filtrada pela aba que está aberta.

    ⚡ Isto devolvia a tabela inteira, com o histórico de resgates de cada
    convite, e a página pedia-a de dez em dez segundos — para contar quantos
    estavam abertos e para desenhar as duas abas, que filtrava do lado do
    navegador. Num painel com anos de uso, é uma lista que só cresce a
    atravessar a rede 360 vezes por hora, e uma que o Python serializa inteira
    de cada vez.

    Quem faz o polling passa a ser `/summary`, que são dois `COUNT(*)`; esta
    rota é pedida quando a pessoa abre a aba ou muda de página.
    """
    estado = request.args.get('estado')
    if estado not in ('ativos', 'historico'):
        estado = None

    try:
        pagina, por_pagina = data_manager.normalizar_paginacao(
            request.args.get('pagina', 1), request.args.get('por_pagina', 20)
        )
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": _("Página inválida.")}), 400

    convites, total = data_manager.get_invitations_page(estado, pagina, por_pagina)
    ativos, _total_geral = data_manager.contar_convites()

    return jsonify({
        "success": True,
        "invites": convites,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "total": total,
        # Quantas páginas há nesta aba. Calculado aqui porque é a mesma conta
        # em todo o lado e errá-la dá um botão "seguinte" que não leva a nada.
        "paginas": max((total + por_pagina - 1) // por_pagina, 1),
        "ativos": ativos,
    })


@invites_api_bp.route('/summary', methods=['GET'])
@login_required
@admin_required
@limiter.exempt  # é o polling da página de utilizadores
def invites_summary_route():
    """Quantos convites estão abertos, e quantos existem ao todo.

    É a pergunta que o polling faz — "já foi usado algum?" — e é toda a razão
    por que a lista inteira era carregada de dez em dez segundos.
    """
    ativos, total = data_manager.contar_convites()
    return jsonify({"success": True, "ativos": ativos, "total": total})

@invites_api_bp.route('/delete', methods=['POST'])
@login_required
@admin_required
def delete_invite_route():
    code = (request.get_json(silent=True) or {}).get('code')
    if not code:
        return jsonify({"success": False, "message": "Código do convite não fornecido."}), 400

    # A auditoria fica com o que o convite ERA: depois de apagado não há a quem
    # perguntar, e "apagou um convite" sem dizer qual não responde a nada.
    #
    # ⚠️ A leitura é a CRUA (`data_manager.get_invitation`) e não a
    # `get_invitation_by_code`, que recusa um convite expirado ou esgotado
    # devolvendo `None` — e esses são precisamente os que mais se apagam. Com a
    # outra porta, a linha da auditoria ficava vazia justamente no caso comum.
    convite = data_manager.get_invitation(code) or {}
    resultado = media_server.delete_invitation(code)
    if resultado.get('success'):
        audit.registar('convite.apagar', alvo_tipo='convite', alvo_id=code, detalhes={
            'codigo': code,
            'usos': f"{convite.get('use_count', 0)}/{convite.get('max_uses', 1)}",
            'resgatado_por': convite.get('claimed_by_users') or [],
        })
    return jsonify(resultado)

# **NOVA ROTA**: Reativar convite (resetar uso)
@invites_api_bp.route('/reactivate', methods=['POST'])
@login_required
@admin_required
def reactivate_invite_route():
    code = (request.get_json(silent=True) or {}).get('code')
    if not code:
        return jsonify({"success": False, "message": "Código do convite não fornecido."}), 400

    resultado = media_server.reactivate_invitation(code)
    if resultado.get('success'):
        audit.registar('convite.reativar', alvo_tipo='convite', alvo_id=code,
                       detalhes={'codigo': code})
    return jsonify(resultado)

@invites_api_bp.route('/details/<string:code>', methods=['GET'])
@limiter.limit("30 per minute", override_defaults=False)
def get_invite_details_route(code):
    """
    🔒 Rota PÚBLICA (sem sessão): responde 200 para um código válido e 404 para
    um inválido, ou seja, é um oráculo que diz se um código existe. 30/min chega
    para qualquer utilização legítima (a página valida o convite uma vez ao
    abrir).

    🐛 Este comentário dizia que "o limitador global não define `default_limits`,
    por isso sem este decorador não havia limite nenhum". É FALSO nas duas
    pontas: a aplicação define `RATELIMIT_DEFAULT = "200 per day; 50 per hour"`,
    e um `@limiter.limit` de rota SUBSTITUI esse padrão em vez de se somar a
    ele. O decorador que existia para travar a força bruta estava a passá-la de
    50/hora para 1800/hora — 36× mais rápida. É o que o `override_defaults=False`
    corrige: com ele valem os três tetos, e o mais apertado ganha.
    """
    invitation, message = media_server.get_invitation_by_code(code)
    if not invitation: return jsonify({"success": False, "message": message}), 404
    return jsonify({"success": True, "details": {"expires_at": invitation.get("expires_at")}})

def _registar_resgate(code, resultado, username):
    """Deixa na auditoria que um convite foi resgatado — e por quem.

    🛡️ Só os campos escolhidos à mão. O corpo deste pedido traz a PALAVRA-PASSE
    que a pessoa acabou de escolher num servidor de contas locais, e a
    auditoria vai dentro do ZIP de backup: passar o corpo inteiro aqui era a
    forma mais fácil de a fazer viajar para onde nunca devia ir.

    O ator fica vazio de propósito — quem resgata ainda não tem sessão no
    painel — e o que identifica o pedido é o endereço, que o `registar` já
    guarda.
    """
    if not resultado.get('success'):
        return
    audit.registar('convite.resgatar', alvo_tipo='convite', alvo_id=code, detalhes={
        'codigo': code,
        'usuario': username,
    })

    # O sino do painel só avisa quem está com ele aberto. Quem gera convites e
    # fecha o portátil ficava a saber no dia seguinte, e quem os gera por um bot
    # não ficava a saber de todo — o link era mandado e o ciclo acabava ali.
    #
    # A nota do convite vai no aviso porque é ela que diz PARA QUEM ele era, e
    # é isso que o torna útil. Uma falha aqui nunca derruba o resgate: a pessoa
    # já tem acesso, e o aviso é sobre isso ter acontecido.
    try:
        convite = data_manager.get_invitation(code) or {}
        notifier_manager.send_invite_claimed_admin_notification(
            username, code, convite.get('note'))
    except Exception as e:
        logger.warning(f"Não foi possível avisar o administrador do convite resgatado: {e}")


@invites_api_bp.route('/claim', methods=['POST'])
@limiter.limit("10 per minute", override_defaults=False)
def claim_invite_route():
    """
    🔒 Rota PÚBLICA e a mais cara de todas: valida credenciais junto do servidor
    de média e, se o convite for válido, concede acesso. Sem limite, servia para
    testar códigos e tokens à vontade.

    ⚠️ **O que é uma "conta" aqui muda com o servidor, e esta rota já não
    sabe qual é.** No Plex a conta JÁ EXISTE e chega um token do plex.tv; num
    servidor de contas locais a conta ainda não existe e chegam as credenciais
    que a pessoa acabou de escolher. Isto estava escrito aqui, e para o fazer a
    rota importava `plexapi.myplex.MyPlexAccount` — um blueprint a saber que o
    servidor é o Plex, que é exatamente o que a fachada existe para impedir (e
    num painel Jellyfin esse import era carregado para nada).

    Hoje é `conta_a_partir_de_credenciais`, do contrato, que interpreta o que
    vem no corpo; aqui fica o que é mesmo da rota: o limite de pedidos, o
    código do convite e a auditoria.
    """
    data = request.get_json(silent=True) or {}

    conta, erro = media_server.conta_a_partir_de_credenciais(data)
    if conta is None:
        # ⚠️ 400 e 401 não são a mesma coisa: uma senha curta demais é um pedido
        # MAL FEITO, não uma falha a provar quem se é. Responder 401 a tudo
        # fazia o cliente concluir que as credenciais estavam erradas quando o
        # problema era o formato do corpo. Quem decide é o motivo que a recusa
        # traz.
        return jsonify(erro), _estado_http(erro)

    resultado = media_server.claim_invitation(data.get('code'), conta)
    _registar_resgate(data.get('code'), resultado, getattr(conta, 'username', ''))
    return jsonify(resultado)
