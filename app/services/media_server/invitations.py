# app/services/media_server/invitations.py

"""O ciclo de vida de um convite — a parte que não sabe o que é um servidor.

Criar um código, verificar se já foi todo usado, ver se expirou, apagá-lo,
reativá-lo: nada disto toca no Plex nem no Jellyfin. É tudo base de dados, e
vive aqui para que um backend novo não tenha de reescrever (nem de deixar
divergir) regras já testadas — designadamente a da data mal formada, que já
devolveu 500 a quem abria um link de convite.

O que MUDA de servidor para servidor é o que acontece no resgate: o Plex
convida uma conta que já existe, um servidor de contas locais tem de a criar.
Essa parte fica em `claim_invitation`, em cada backend.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from flask_babel import gettext as _

from ...utils.identity import same_user
from ...utils.log_sanitizer import mask_code

logger = logging.getLogger(__name__)

# ⚠️ O mesmo teto que os esquemas impõem à ENTRADA (`MAX_MINUTOS`), repetido
# aqui de propósito. Os esquemas defendem as rotas; isto defende o que JÁ ESTÁ
# gravado — um convite criado antes deste limite existir continua na base de
# dados com o valor absurdo, e é no RESGATE que ele seria somado a `now()`:
#
#     >>> datetime.now(timezone.utc) + timedelta(minutes=10**12)
#     OverflowError: date value out of range
#
# Ali o 500 já não é do administrador a criar o convite, é de quem o está a
# resgatar. Cortar pelo teto é o lado seguro do erro: o convite vale o máximo
# que o painel sabe representar, em vez de não valer nada.
TETO_DE_MINUTOS = 5 * 365 * 24 * 60


def _minutos_seguros(valor):
    """O valor em minutos, cortado pelo que uma data consegue representar."""
    try:
        minutos = int(valor)
    except (TypeError, ValueError):
        return 0
    if minutos <= 0:
        return 0
    if minutos > TETO_DE_MINUTOS:
        logger.warning(
            f"Um convite pedia {minutos} minutos, acima do máximo que o painel "
            f"representa. Foi usado o teto de {TETO_DE_MINUTOS}."
        )
        return TETO_DE_MINUTOS
    return minutos


class Contacto(NamedTuple):
    """Um canal de notificação que pode ser pré-atribuído a um convite."""

    canal: str            # 'telegram'
    no_convite: str       # a coluna em `invitations`
    no_perfil: str        # a coluna em `user_profiles`
    rotulo: str           # o que a pessoa lê numa mensagem de erro


# ⚠️ **Os nomes das colunas DIVERGEM entre o convite e o perfil**, e por razões
# históricas diferentes em cada canal: `invitations.telegram_id` contra
# `user_profiles.telegram_user`, `invitations.discord_id` contra
# `user_profiles.discord_user_id`. Já houve código a ler
# `profile.get("telegram_id")` — que devolve SEMPRE None, porque essa coluna não
# existe no perfil — e a parecer funcionar por causa de um `or` à frente.
#
# Este mapa é a ponte, e é a razão de ele existir: acrescentar um canal é uma
# linha aqui, e não a terceira cópia das mesmas quatro verificações espalhadas
# entre a criação, o resgate do Plex e o resgate do Jellyfin.
CONTACTOS = (
    Contacto('telegram', 'telegram_id', 'telegram_user', 'Telegram'),
    Contacto('discord', 'discord_id', 'discord_user_id', 'Discord'),
)


def normalizar_contacto(valor):
    """O ID como texto e sem espaços, ou None.

    Um bot manda o ID como NÚMERO e um formulário como texto — sem isto, `123`
    e `' 123 '` eram identificadores diferentes e escapavam à verificação de
    duplicados.
    """
    if valor is None:
        return None
    return str(valor).strip() or None


def convite_expirado(expires_at, code=''):
    """Já passou da validade? Uma data ilegível conta como expirada.

    ⚠️ O lado seguro do erro. Esta pergunta é feita a partir de rotas PÚBLICAS,
    e uma data mal formada na base de dados (edição manual, importação antiga,
    um valor sem fuso — comparar um datetime ingénuo com um consciente levanta
    `TypeError`) devolvia 500 a quem abrisse o link do convite.
    """
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) < datetime.now(timezone.utc)
    except (TypeError, ValueError):
        logger.warning(
            f"O convite '{mask_code(code)}' tem uma data de expiração inválida "
            f"({expires_at!r}). Tratado como expirado."
        )
        return True


def convite_esgotado(invitation):
    """Já não há vagas."""
    return invitation.get('use_count', 0) >= invitation.get('max_uses', 1)


# ⚠️ **O motivo de uma recusa é uma CHAVE, não a mensagem.** Quem chama esta
# camada precisa de distinguir "o pedido está mal" de "já existe" para escolher
# o código HTTP, e a mensagem não serve para isso: é texto escrito para uma
# pessoa ler, pode ser reescrito a qualquer momento e um dia será traduzido.
# O endpoint dos bots respondia 409 a TUDO o que falhasse — inclusive a
# "informe pelo menos uma biblioteca", que é um 400 — e do outro lado não havia
# como saber se valia a pena tentar outra vez com outro código ou se o pedido
# estava simplesmente errado.
CONFLITO = 'conflito'          # já existe: o pedido é válido, o estado é que não deixa
PEDIDO_INVALIDO = 'invalido'   # falta alguma coisa, ou está mal
CREDENCIAIS = 'credenciais'    # o que falhou foi provar quem é

# O código HTTP de cada motivo, num sítio só. ⚠️ Uma senha curta demais é um
# pedido MAL FEITO e não uma falha de autenticação: responder 401 a tudo o que
# saísse da validação de credenciais fazia o cliente concluir que o token
# estava errado quando o problema era o formato do corpo.
ESTADO_HTTP = {CONFLITO: 409, PEDIDO_INVALIDO: 400, CREDENCIAIS: 401}


def recusa(motivo, mensagem):
    """Uma recusa pronta a ir para o `jsonify`."""
    return {"success": False, "erro": motivo, "message": mensagem}


class InvitationLifecycle:
    """Métodos de convite partilhados por todos os backends.

    Quem herda tem de ter `self.data_manager`.
    """

    # =========================================================================
    # O QUE O RESGATE FAZ, EM QUALQUER SERVIDOR
    # =========================================================================
    #
    # 🐛 Estas duas viviam só no backend do Plex, e o do Jellyfin nasceu sem
    # elas: um convite de teste criava uma conta que nunca expirava, e o
    # "Indique e Ganhe" nunca chegava a saber quem tinha indicado quem. Nada
    # nelas é do Plex — é a sessão, o config e o agendador — por isso a casa é
    # aqui, onde um backend novo as herda em vez de as reescrever.

    def agendar_fim_do_teste(self, media_user_id, duration_minutes):
        """Marca a hora a que este acesso de teste termina.

        Devolve `(fim_em_utc, id_da_tarefa)` para quem chama gravar no perfil:
        sem os DOIS, o `end_trial_job` não tem como ser cancelado se a pessoa
        entretanto pagar.
        """
        from ...extensions import scheduler
        from ...scheduler import end_trial_job

        fim_utc = datetime.now(timezone.utc) + timedelta(minutes=_minutos_seguros(duration_minutes))
        quando = fim_utc.astimezone(scheduler.timezone).replace(tzinfo=None)
        id_da_tarefa = f"trial_end_{media_user_id}_{secrets.token_hex(4)}"

        scheduler.add_job(
            id=id_da_tarefa, func=end_trial_job, args=[media_user_id],
            trigger='date', run_date=quando, replace_existing=True,
        )
        return fim_utc, id_da_tarefa

    def resolver_indicacao_pendente(self, media_user_id, username=''):
        """Converte o código de indicação guardado na sessão no ID de quem indicou.

        Devolve None (sem nunca lançar) em qualquer situação inválida: fora de
        um contexto HTTP, sistema desativado, código inexistente ou
        auto-indicação. Um problema no programa de indicações nunca pode
        impedir alguém de resgatar um convite legítimo.
        """
        try:
            from flask import session, has_request_context
            from ...config import load_or_create_config

            if not has_request_context():
                return None

            codigo = session.pop('pending_referral_code', None)
            if not codigo:
                return None

            if not load_or_create_config().get("REFERRAL_ENABLED", False):
                return None

            quem_indicou = self.data_manager.get_user_profile_by_referral_code(codigo)
            if not quem_indicou:
                logger.info(f"Código de indicação '{mask_code(codigo)}' não corresponde a nenhum utilizador. Ignorado.")
                return None

            id_de_quem_indicou = quem_indicou.get('media_user_id')
            # 🛡️ Bloqueia a auto-indicação (usar o próprio código numa segunda
            # conta é o abuso mais óbvio deste tipo de sistema).
            if same_user(id_de_quem_indicou, media_user_id):
                logger.warning(f"Auto-indicação bloqueada no resgate do convite (ID {media_user_id}).")
                return None

            logger.info(f"Indicação registada: '{username}' foi indicado por '{quem_indicou.get('username')}'.")
            return id_de_quem_indicou
        except Exception as e:
            logger.error(f"Erro ao resolver a indicação pendente: {e}", exc_info=True)
            return None

    def create_invitation(self, **kwargs):
        if not kwargs.get('library_titles'):
            return {"success": False, "erro": PEDIDO_INVALIDO,
                    "message": _("Pelo menos uma biblioteca deve ser selecionada para o convite.")}

        custom_code = kwargs.get('custom_code')
        max_uses = kwargs.get('max_uses', 1)

        # Normalizados logo à entrada, todos pela mesma porta.
        contactos = {
            c.no_convite: normalizar_contacto(kwargs.get(c.no_convite))
            for c in CONTACTOS
        }

        if custom_code:
            # ⚠️ Olha também para os REMOVIDOS. Um convite removido continua na
            # tabela — é ele que guarda o "membro desde" de quem entrou por ele
            # — e o código é a chave primária: sem esta leitura, criar outro com
            # o mesmo código passava na validação e rebentava com um
            # IntegrityError depois de a resposta já parecer estar a caminho.
            existente = self.data_manager.get_invitation(custom_code, incluir_apagados=True)
            if existente:
                if not existente.get('deleted_at'):
                    return {"success": False, "erro": CONFLITO,
                            "message": _("Este código personalizado já está em uso.")}

                # Um convite removido que NINGUÉM resgatou não guarda histórico
                # nenhum, e a única razão para a linha ficar era essa: o código
                # volta a estar livre.
                if existente.get('claimed_by_users'):
                    return {"success": False, "erro": CONFLITO, "message": _(
                        "Este código já foi usado num convite anterior, que foi resgatado. "
                        "O registro de quem entrou por ele é mantido, por isso escolha outro código."
                    )}
                self.data_manager.libertar_codigo_apagado(custom_code)
            code = custom_code
        else:
            code = secrets.token_urlsafe(16)
        
        # Duas pessoas ligadas ao mesmo chat recebiam as notificações uma da
        # outra — a de vencimento, com nome e valor, e o link de pagamento, que
        # é uma credencial portadora. A verificação é a mesma para cada canal.
        for contacto in CONTACTOS:
            recusa = self._contacto_ja_tomado(contacto, contactos[contacto.no_convite])
            if recusa:
                return recusa

        expires_in_minutes = kwargs.get('expires_in_minutes')
        minutos_de_validade = _minutos_seguros(expires_in_minutes)
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(minutes=minutos_de_validade)).isoformat()
            if minutos_de_validade else None
        )
        
        invitation_details = {
            "libraries": kwargs.get('library_titles', []),
            "screen_limit": kwargs.get('screens', 0),
            "allow_downloads": kwargs.get('allow_downloads', False),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
            "trial_duration_minutes": kwargs.get('trial_duration_minutes', 0),
            "overseerr_access": kwargs.get('overseerr_access', False),
            "max_uses": max_uses,
            "use_count": 0,
            "claimed_by_users": [],
            "note": (kwargs.get('note') or '').strip() or None,
            **contactos,
        }

        self.data_manager.add_invitation(code, invitation_details)
        return {"success": True, "code": code, "message": _("Código de convite criado com sucesso.")}

    def _contacto_ja_tomado(self, contacto, valor):
        """Uma recusa, se este contacto já for de outra pessoa ou de outro convite.

        Devolve None quando está livre — ou quando não foi pedido nenhum, que é
        o caso da esmagadora maioria dos convites.
        """
        if not valor:
            return None

        dono = self.data_manager.get_user_profile_by_contacto(contacto.canal, valor)
        if dono:
            return {"success": False, "erro": CONFLITO, "message": _(
                "Este ID do %(canal)s já está vinculado ao usuário '%(username)s'.",
                canal=contacto.rotulo, username=dono['username'],
            )}

        if self.data_manager.contacto_em_convite_ativo(contacto.canal, valor):
            return {"success": False, "erro": CONFLITO, "message": _(
                "Já existe um convite ativo gerado para este ID do %(canal)s.",
                canal=contacto.rotulo,
            )}
        return None

    def resolver_contactos_do_convite(self, invitation, username):
        """Os contactos a gravar no perfil de quem acabou de resgatar.

        🐛 **Isto vivia só no backend do Plex e chamava-se
        `_handle_telegram_linking`.** O do Jellyfin nasceu sem: um convite
        gerado por um bot para um Telegram ID concreto criava a conta e o perfil
        ficava SEM o vínculo — a pessoa entrava e nunca mais recebia um aviso de
        vencimento, porque o painel não sabia por onde lhe falar. É a mesma
        família de esquecimentos do `agendar_fim_do_teste` e do
        `resolver_indicacao_pendente`, e a casa é a mesma: aqui, onde um backend
        novo herda em vez de reescrever.

        🛡️ **Revalida no momento do RESGATE.** Entre a geração do convite e o
        seu uso pode passar muito tempo, e nesse intervalo o mesmo ID pode ter
        sido vinculado a outra conta. Nesse caso o registo prossegue — o que se
        ignora é só o vínculo, para nunca ficarem duas pessoas a apontar para o
        mesmo chat: as notificações de uma iriam para a outra, e entre elas está
        o link de pagamento, que é uma credencial portadora.
        """
        resolvidos = {}
        for contacto in CONTACTOS:
            valor = normalizar_contacto(invitation.get(contacto.no_convite))
            if not valor:
                continue

            dono = self.data_manager.get_user_profile_by_contacto(contacto.canal, valor)
            if dono and dono.get('username') != username:
                logger.warning(
                    f"Conflito de ID do {contacto.rotulo}: o convite trazia um ID que "
                    f"entretanto ficou vinculado a '{dono.get('username')}'. O registo "
                    f"continua, mas sem esse vínculo."
                )
                continue

            resolvidos[contacto.no_perfil] = valor
            logger.info(f"ID do {contacto.rotulo} vinculado ao utilizador {username}.")
        return resolvidos

    def get_invitation_by_code(self, code):
        invitation = self.data_manager.get_invitation(code)
        if not invitation: 
            return None, _("Convite não encontrado.")
        
        if convite_esgotado(invitation):
            return None, _("Este convite já atingiu o limite máximo de usos.")

        if convite_expirado(invitation.get('expires_at'), code):
            return None, _("Este convite expirou.")

        return invitation, _("Convite válido.")

    def list_invitations(self):
        return self.data_manager.get_all_invitations()

    def delete_invitation(self, code):
        # 🐛 O retorno do `data_manager` era deitado fora e a resposta era
        # sempre "removido com sucesso" — mesmo para um código que nunca
        # existiu. Um script que apagasse pelo código errado ficava convencido
        # de que tinha apagado, e o convite que ele queria travar continuava
        # de pé. É a mesma verificação que `reactivate_invitation` já fazia.
        if self.data_manager.delete_invitation(code):
            return {"success": True, "message": _("Convite removido com sucesso.")}
        return {"success": False, "message": _("Convite não encontrado.")}

    def reactivate_invitation(self, code):
        if not self.data_manager.reset_invitation_usage(code):
            return {"success": False, "message": _("Convite não encontrado.")}

        # A mensagem diz o que aconteceu de facto: um convite que nunca teve
        # prazo não teve validade nenhuma renovada, e dizer o contrário era o
        # que escondia o bug de a validade ser APAGADA em vez de estendida.
        convite = self.data_manager.get_invitation(code) or {}
        if convite.get('expires_at'):
            return {"success": True, "message": _(
                "Convite reativado: o contador voltou a zero e a validade foi "
                "renovada pelo mesmo prazo que ele tinha originalmente."
            )}
        return {"success": True, "message": _(
            "Convite reativado: o contador voltou a zero. Este convite não tem prazo de validade."
        )}
