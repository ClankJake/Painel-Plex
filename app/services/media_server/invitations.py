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
            return {"success": False, "message": _("Pelo menos uma biblioteca deve ser selecionada para o convite.")}

        custom_code = kwargs.get('custom_code')
        max_uses = kwargs.get('max_uses', 1)
        telegram_id = kwargs.get('telegram_id')
        # Normaliza logo à entrada: um bot pode enviar o ID como número inteiro e um
        # formulário como texto com espaços — sem isto, '123' e ' 123 ' seriam
        # tratados como IDs diferentes e escapariam à validação de duplicados.
        if telegram_id is not None:
            telegram_id = str(telegram_id).strip() or None

        if custom_code:
            if self.data_manager.get_invitation(custom_code):
                return {"success": False, "message": _("Este código personalizado já está em uso.")}
            code = custom_code
        else:
            code = secrets.token_urlsafe(16)
        
        if telegram_id:
            existing_user = self.data_manager.get_user_profile_by_telegram(telegram_id)
            if existing_user:
                 return {"success": False, "message": _("Este Telegram ID já está vinculado ao usuário '%(username)s'.", username=existing_user['username'])}
            
            if self.data_manager.check_telegram_id_exists_in_invites(telegram_id):
                 return {"success": False, "message": _("Já existe um convite ativo gerado para este Telegram ID.")}

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
            "telegram_id": telegram_id
        }

        self.data_manager.add_invitation(code, invitation_details)
        return {"success": True, "code": code, "message": _("Código de convite criado com sucesso.")}

    def get_invitation_by_code(self, code):
        invitation = self.data_manager.get_invitation(code)
        if not invitation: 
            return None, _("Convite não encontrado.")
        
        if invitation.get('use_count', 0) >= invitation.get('max_uses', 1):
            return None, _("Este convite já atingiu o seu limite máximo de utilizações.")

        # Esta rota é pública: uma data mal formada na base de dados (edição
        # manual, importação antiga, valor sem fuso horário — comparar um
        # datetime ingénuo com um consciente levanta TypeError) devolvia 500 a
        # quem abrisse o link. Tratamos o convite como expirado, que é o lado
        # seguro do erro.
        expires_at = invitation.get('expires_at')
        if expires_at:
            try:
                expirado = datetime.fromisoformat(expires_at) < datetime.now(timezone.utc)
            except (TypeError, ValueError):
                logger.warning(f"O convite '{mask_code(code)}' tem uma data de expiração inválida ({expires_at!r}). Tratado como expirado.")
                expirado = True
            if expirado:
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
        if self.data_manager.reset_invitation_usage(code):
             return {"success": True, "message": _("Convite reativado com sucesso (Contador resetado e validade estendida).")}
        return {"success": False, "message": _("Convite não encontrado.")}
