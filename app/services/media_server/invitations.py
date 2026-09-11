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

from ...utils.log_sanitizer import mask_code

logger = logging.getLogger(__name__)


class InvitationLifecycle:
    """Métodos de convite partilhados por todos os backends.

    Quem herda tem de ter `self.data_manager`.
    """

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
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=int(expires_in_minutes))).isoformat() if expires_in_minutes else None
        
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
        self.data_manager.delete_invitation(code)
        return {"success": True, "message": _("Convite removido com sucesso.")}

    def reactivate_invitation(self, code):
        if self.data_manager.reset_invitation_usage(code):
             return {"success": True, "message": _("Convite reativado com sucesso (Contador resetado e validade estendida).")}
        return {"success": False, "message": _("Convite não encontrado.")}
