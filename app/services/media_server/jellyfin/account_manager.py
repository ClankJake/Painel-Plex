# app/services/media_server/jellyfin/account_manager.py

"""Dar acesso ao servidor: no Jellyfin, isso é CRIAR a conta.

Esta é a diferença de produto entre os dois backends, e não é um detalhe de
implementação. No Plex, o utilizador traz a conta dele e o painel limita-se a
convidá-la. No Jellyfin as contas são locais: o painel cria-as, e passa a ser
responsável por entregar as credenciais a quem se registou.

Consequências que quem mexer aqui deve ter presentes:

* O resgate de um convite precisa de um nome de utilizador e de uma
  palavra-passe, que o Plex nunca pediu. `claim_invitation` recebe-os em vez de
  uma conta já autenticada.
* A proteção anti-abuso de períodos de teste é mais fraca do que no Plex. Lá,
  uma conta plex.tv é uma identidade global e reconhecível; aqui, criar uma
  conta nova é grátis e não há nada que ligue duas contas à mesma pessoa. Por
  isso o convite de teste deve exigir um contacto verificável (email ou
  Telegram) — ver `_verificar_abuso_de_teste`.

O ciclo de vida do convite em si (código, vagas, validade) é partilhado e vem
de `InvitationLifecycle`.
"""

import logging
import secrets
from typing import Any, Dict

from flask import url_for
from flask_babel import gettext as _

from ....utils.identity import normalize_user_id
from ....utils.log_formatting import describe
from ....utils.log_sanitizer import mask_code
from ..invitations import InvitationLifecycle
from .api_client import JellyfinApiError

logger = logging.getLogger(__name__)


class JellyfinAccountManager(InvitationLifecycle):
    """Cumpre o contrato `AccountProvisioning` para o Jellyfin."""

    def __init__(self, connection, user_manager, data_manager, backend, requests_manager=None, notifier_manager=None):
        self.conn = connection
        self.user_manager = user_manager
        self.data_manager = data_manager
        self.backend = backend
        self.requests_manager = requests_manager
        self.notifier_manager = notifier_manager

    # =========================================================================
    # CRIAÇÃO DE CONTAS
    # =========================================================================

    def create_account(self, username: str, password: str) -> Dict[str, Any]:
        """Cria a conta no Jellyfin e devolve o utilizador criado."""
        if not self.conn.connected:
            return {"success": False, "message": _("Jellyfin não configurado.")}

        nome = (username or '').strip()
        if not nome:
            return {"success": False, "message": _("O nome de utilizador é obrigatório.")}

        if not password:
            return {"success": False, "message": _("A palavra-passe é obrigatória.")}

        # O Jellyfin recusa nomes repetidos com um 400 pouco explícito; verificar
        # antes dá uma mensagem que o utilizador percebe.
        existentes = self.user_manager.list_users() or []
        if any((u.get('username') or '').lower() == nome.lower() for u in existentes):
            return {"success": False, "message": _("Já existe um utilizador com esse nome neste servidor.")}

        try:
            criado = self.conn.api.post('/Users/New', json={'Name': nome, 'Password': password})
        except JellyfinApiError as e:
            logger.error(f"O Jellyfin recusou criar a conta '{nome}': {describe(e)}")
            return {"success": False, "message": str(e)}

        self.user_manager.invalidate_user_cache()
        user_id = normalize_user_id((criado or {}).get('Id'))

        if not user_id:
            return {"success": False, "message": _("O Jellyfin criou a conta mas não devolveu o identificador.")}

        logger.info(f"Conta '{nome}' criada no Jellyfin.")
        return {"success": True, "user_id": user_id, "username": nome}

    def send_invite(self, identifier, library_titles, plex_user_id=None, allow_sync=False):
        """Dar acesso, no vocabulário partilhado com o backend do Plex.

        Aqui `identifier` é o nome da conta a criar. A palavra-passe é gerada
        pelo painel quando não é indicada — é isso que permite um fluxo
        administrativo ("adicionar utilizador") sem pedir nada ao utilizador
        final, que depois a muda no Jellyfin.
        """
        resultado = self.create_account(identifier, secrets.token_urlsafe(12))
        if not resultado.get('success'):
            return resultado

        self.user_manager.update_user_libraries(
            resultado['user_id'], library_titles, allow_sync=allow_sync
        )
        return resultado

    # =========================================================================
    # RESGATE DE CONVITE
    # =========================================================================

    def claim_invitation(self, code, account) -> Dict[str, Any]:
        """Resgata um convite criando a conta no servidor.

        `account` traz o que o formulário de registo recolheu: `.username` e
        `.password` (e, opcionalmente, `.email`). Não é uma conta já
        autenticada como no Plex — aqui ela ainda não existe.
        """
        invitation, message = self.get_invitation_by_code(code)
        if not invitation:
            return {"success": False, "message": message}

        username = (getattr(account, 'username', '') or '').strip()
        password = getattr(account, 'password', '') or ''
        email = (getattr(account, 'email', '') or '').strip()

        if not username or not password:
            return {"success": False, "message": _("Indique um nome de utilizador e uma palavra-passe.")}

        if invitation.get("trial_duration_minutes", 0) > 0:
            recusa = self._verificar_abuso_de_teste(email)
            if recusa:
                return recusa

        # 🛡️ RESERVA A VAGA ANTES DE FALAR COM O SERVIDOR, pelo mesmo motivo do
        # backend do Plex: entre a validação e a criação da conta há chamadas de
        # rede, e com o worker gevent cada uma delas é um ponto de troca entre
        # greenlets. Dois resgates simultâneos do mesmo código passavam ambos.
        if not self.data_manager.reserve_invitation_use(code, username, None):
            logger.warning(f"Resgate do convite '{mask_code(code)}' recusado: as vagas esgotaram-se entretanto.")
            return {"success": False, "message": _("Este convite já atingiu o seu limite máximo de utilizações.")}

        try:
            criado = self.create_account(username, password)
            if not criado.get('success'):
                self.data_manager.release_invitation_use(code, username, None)
                return criado

            user_id = criado['user_id']
            self.user_manager.update_user_libraries(
                user_id, invitation.get('libraries', []),
                allow_sync=invitation.get('allow_downloads', False),
            )

            # O limite de telas do convite fica no perfil (é o painel que o
            # impõe); não há nada a escrever no servidor — ver
            # `user_manager.clear_session_limits`.
        except Exception:
            self.data_manager.release_invitation_use(code, username, None)
            raise

        # A reserva foi feita sem ID (a conta ainda não existia): agora que
        # existe, regista-se o ID no convite, que é a identidade estável para a
        # verificação de resgates repetidos.
        self.data_manager.release_invitation_use(code, username, None)
        self.data_manager.reserve_invitation_use(code, username, user_id)

        perfil = self._criar_perfil_local(user_id, username, email, invitation)

        try:
            self.data_manager.create_notification(
                message=_("'%(username)s' resgatou um convite.", username=username),
                category='success', link=url_for('main.users_page'),
            )
        except RuntimeError:
            self.data_manager.create_notification(
                message=_("'%(username)s' resgatou um convite.", username=username), category='success',
            )

        return {
            "success": True,
            "message": _("Conta criada e acesso concedido! Bem-vindo(a), %(username)s.", username=username),
            "user_data": perfil,
        }

    def _verificar_abuso_de_teste(self, email):
        """Já houve um período de teste para esta pessoa?

        ⚠️ Mais fraco do que no Plex de propósito, porque não há como ser
        melhor: no Plex, uma conta plex.tv é uma identidade global e comparável;
        aqui, criar uma conta nova não custa nada e nada liga duas contas à
        mesma pessoa. O email é o único sinal que temos — e só existe se o
        formulário o pedir. Sem email, um convite de teste é confiança pura, e
        quem o gera deve sabê-lo.
        """
        if not email:
            logger.warning(
                "Convite de teste resgatado sem email: não há como verificar se esta "
                "pessoa já usou um período de teste neste servidor."
            )
            return None

        existente = self.data_manager.get_user_profile_by_email(email)
        if existente and (existente.get('trial_end_date') or existente.get('status') == 'inactive'):
            return {
                "success": False,
                "message": _("Já utilizou um período de teste anteriormente. Para continuar a utilizar o serviço, adquira um plano."),
            }
        return None

    def _criar_perfil_local(self, user_id, username, email, invitation):
        perfil = self.data_manager.get_user_profile(user_id) or {}
        perfil.update({
            'username': username,
            'email': email or perfil.get('email'),
            'media_server_type': 'jellyfin',
            'status': 'active',
            'screen_limit': invitation.get('screen_limit', 0),
        })
        gravado = self.data_manager.set_user_profile(user_id, perfil) or {}

        # 🔒 O endereço do servidor vai APENAS na resposta de um resgate
        # concluído — quem a recebe acabou de ganhar acesso. Estava a ser
        # colocado no HTML da página de convite, que é pública: qualquer pessoa
        # com o código, mesmo sem o resgatar, ficava a saber onde está o
        # servidor.
        return {**gravado, 'server_url': self.conn.api.base_url}

    # =========================================================================
    # NÃO APLICÁVEL A ESTE SERVIDOR
    # =========================================================================

    def accept_invite_via_token(self, token):
        """O Plex tem convites pendentes que o utilizador aceita; aqui não.

        Uma conta criada pelo painel já tem acesso no momento em que existe.
        """
        return {"success": False, "message": _("Este servidor não usa convites pendentes.")}
