# app/services/media_server/jellyfin/user_manager.py

"""Utilizadores do Jellyfin: acessos, bibliotecas e bloqueio.

A diferença importante face ao Plex: aqui as contas são LOCAIS ao servidor. Não
há amigos nem partilhas — há uma política (`UserPolicy`) por utilizador, e é
nela que vive tudo: que bibliotecas vê, se pode descarregar, quantas sessões em
simultâneo, e se a conta está suspensa.

Isso torna o bloqueio muito mais fiável do que no Plex. Lá, bloquear é retirar
as partilhas e desbloquear é repô-las — o que obriga a guardar quais eram e
falha se o Plex não responder a meio. Aqui é `IsDisabled`, um booleano: o
utilizador mantém as bibliotecas todas e volta ao que era com uma escrita só.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from flask_babel import gettext as _

from ....extensions import cache
from ....utils.identity import normalize_user_id, same_user
from ....utils.log_formatting import describe
from .api_client import JellyfinApiError

logger = logging.getLogger(__name__)


class JellyfinUserManager:
    """Cumpre o contrato `UserDirectory` para o Jellyfin."""

    def __init__(self, connection, data_manager, stats_manager=None, requests_manager=None):
        self.conn = connection
        self.data_manager = data_manager
        self.stats_manager = stats_manager
        self.requests_manager = requests_manager
        self.stream_manager = None

    # =========================================================================
    # LEITURA
    # =========================================================================

    def invalidate_user_cache(self):
        cache.delete_memoized(self.list_users)
        logger.info(_("Cache de usuários do Jellyfin invalidada."))

    @cache.memoize(timeout=300)
    def list_users(self, force_refresh_signal=None) -> Optional[List[Dict[str, Any]]]:
        """A lista crua de utilizadores do servidor."""
        if not self.conn.connected:
            return None

        try:
            utilizadores = self.conn.api.get('/Users') or []
            return [self._traduzir(u) for u in utilizadores]
        except JellyfinApiError as e:
            logger.warning(f"O Jellyfin não devolveu a lista de utilizadores: {describe(e)}")
            self.invalidate_user_cache()
            return None
        except Exception as e:
            logger.error(f"Erro inesperado ao obter utilizadores do Jellyfin: {describe(e)}", exc_info=True)
            self.invalidate_user_cache()
            return None

    def _traduzir(self, utilizador: dict) -> dict:
        """Um `UserDto` do Jellyfin na forma que o painel consome."""
        user_id = normalize_user_id(utilizador.get('Id'))
        etiqueta = utilizador.get('PrimaryImageTag')

        return {
            'id': user_id,
            'username': utilizador.get('Name'),
            # O Jellyfin não guarda email nas contas: o painel recolhe-o no
            # registo e guarda-o no perfil local. Aqui vem sempre vazio, e é
            # por isso que a sincronização de emails a partir do servidor não
            # se aplica a este backend (ver as capacidades do backend).
            'email': None,
            'thumb': f"/Users/{user_id}/Images/Primary?tag={etiqueta}" if etiqueta else None,
            'servers': [(self.conn.server_info or {}).get('ServerName', 'Jellyfin')],
            'is_disabled': bool((utilizador.get('Policy') or {}).get('IsDisabled')),
        }

    def get_user_by_id(self, user_id):
        todos = self.list_users()
        if not todos:
            return None
        return next((u for u in todos if same_user(u['id'], user_id)), None)

    # =========================================================================
    # POLÍTICA (bibliotecas, downloads, telas, suspensão)
    # =========================================================================

    def _get_policy(self, user_id) -> Optional[dict]:
        try:
            utilizador = self.conn.api.get(f'/Users/{user_id}')
        except JellyfinApiError as e:
            logger.warning(f"Não foi possível ler o utilizador {user_id} do Jellyfin: {describe(e)}")
            return None
        return (utilizador or {}).get('Policy')

    def _set_policy(self, user_id, policy: dict) -> bool:
        """Grava a política completa.

        O Jellyfin substitui a política inteira neste endpoint, por isso
        enviamos sempre o objeto que lemos com as alterações aplicadas por
        cima — nunca só os campos que queremos mudar, ou os restantes voltavam
        aos valores por omissão.
        """
        try:
            self.conn.api.post(f'/Users/{user_id}/Policy', json=policy)
            self.invalidate_user_cache()
            return True
        except JellyfinApiError as e:
            logger.error(f"O Jellyfin recusou a política do utilizador {user_id}: {describe(e)}")
            return False

    def _titulos_para_ids(self, library_titles) -> List[str]:
        """Traduz nomes de bibliotecas nos ItemId que a política usa."""
        catalogo = {b['title']: b['key'] for b in self.conn.get_libraries()}
        return [catalogo[t] for t in (library_titles or []) if t in catalogo]

    def get_user_libraries(self, user_id) -> Dict[str, Any]:
        if not self.conn.connected:
            return {"success": False, "message": _("Jellyfin não configurado.")}

        policy = self._get_policy(user_id)
        if policy is None:
            return {"success": False, "message": _("Utilizador não encontrado no Jellyfin.")}

        catalogo = self.conn.get_libraries()

        if policy.get('EnableAllFolders'):
            titulos = [b['title'] for b in catalogo]
        else:
            permitidos = set(policy.get('EnabledFolders') or [])
            titulos = [b['title'] for b in catalogo if b['key'] in permitidos]

        return {
            "success": True,
            "libraries": titulos,
            "allow_sync": bool(policy.get('EnableContentDownloading')),
        }

    def update_user_libraries(self, user_id, library_titles, allow_sync=None, bulk_mode=False) -> Dict[str, Any]:
        if not self.conn.connected:
            return {"success": False, "message": _("Jellyfin não configurado.")}

        policy = self._get_policy(user_id)
        if policy is None:
            return {"success": False, "message": _("Utilizador não encontrado no Jellyfin.")}

        catalogo = self.conn.get_libraries()
        ids = self._titulos_para_ids(library_titles)

        # "Todas as bibliotecas" é um campo próprio no Jellyfin. Sem o marcar,
        # uma biblioteca criada depois ficaria invisível para quem devia ver
        # tudo — e ninguém iria perceber porquê.
        policy['EnableAllFolders'] = len(ids) == len(catalogo) and len(catalogo) > 0
        policy['EnabledFolders'] = [] if policy['EnableAllFolders'] else ids

        if allow_sync is not None:
            policy['EnableContentDownloading'] = bool(allow_sync)

        if not self._set_policy(user_id, policy):
            return {"success": False, "message": _("O Jellyfin recusou a alteração de bibliotecas.")}

        # Guarda também no perfil local, como o backend do Plex faz: é daqui que
        # o painel repõe os acessos ao reativar uma assinatura sem ter de
        # perguntar ao servidor.
        perfil = self.data_manager.get_user_profile(user_id)
        if perfil is not None:
            perfil['libraries'] = json.dumps(library_titles or [])
            self.data_manager.set_user_profile(user_id, perfil)

        return {"success": True, "message": _("Bibliotecas atualizadas com sucesso.")}

    def update_all_users_libraries(self, library_titles) -> Dict[str, Any]:
        utilizadores = self.list_users() or []
        atualizados = 0
        for utilizador in utilizadores:
            if self.update_user_libraries(utilizador['id'], library_titles).get('success'):
                atualizados += 1
        return {"success": True, "message": _("Bibliotecas atualizadas para %(n)s utilizador(es).", n=atualizados)}

    def clear_session_limits(self) -> Dict[str, Any]:
        """Tira do servidor o limite de SESSÕES que o painel lá pôs.

        ⚠️ `Policy.MaxActiveSessions` não é um limite de telas: limita
        AUTENTICAÇÕES, não reproduções. O painel chegou a usá-lo como limite de
        telas e isso corria mal de duas maneiras:

        * não corta nada — quem já estava ligado continua a reproduzir à
          vontade, porque só as entradas NOVAS são recusadas;
        * tranca a própria pessoa fora do painel — entrar no painel autentica-se
          contra o servidor e ocupa uma sessão, por isso quem tivesse os
          aparelhos ligados tinha de sair de um para poder entrar.

        Corre uma vez (ver `JELLYFIN_SESSION_LIMIT_CLEARED` no config) para
        libertar quem ficou trancado. A partir daí o painel não volta a mexer
        neste campo: quem o quiser usar, usa-o na interface do Jellyfin.
        """
        if not self.conn.connected:
            return {"success": False, "message": _("Sem ligação ao servidor."), "limpos": 0}

        try:
            utilizadores = self.conn.api.get('/Users') or []
        except Exception as e:
            logger.warning(f"Não foi possível limpar os limites de sessões: {describe(e)}")
            return {"success": False, "message": _("O servidor não devolveu os utilizadores."), "limpos": 0}

        limpos = 0
        for bruto in utilizadores:
            user_id = normalize_user_id(bruto.get('Id'))
            politica = bruto.get('Policy')
            # Só quem o painel administra: noutra conta o limite é de quem o pôs.
            if politica is None or not self.data_manager.get_user_profile(user_id):
                continue
            anterior = int(politica.get('MaxActiveSessions') or 0)
            if not anterior:
                continue

            politica['MaxActiveSessions'] = 0
            if self._set_policy(user_id, politica):
                limpos += 1
                logger.info(
                    "Limite de sessões de '%s' removido do servidor (era %s): não é um limite de telas.",
                    bruto.get('Name') or user_id, anterior,
                )

        return {"success": True, "limpos": limpos}

    # =========================================================================
    # BLOQUEIO
    # =========================================================================

    def _definir_suspensao(self, user_id, suspenso: bool) -> bool:
        policy = self._get_policy(user_id)
        if policy is None:
            return False
        policy['IsDisabled'] = suspenso
        return self._set_policy(user_id, policy)

    def block_user(self, user_id, reason='manual') -> Dict[str, Any]:
        utilizador = self.get_user_by_id(user_id)
        if not utilizador:
            return {"success": False, "message": _("Utilizador não encontrado.")}

        if not self._definir_suspensao(user_id, True):
            return {"success": False, "message": _("O Jellyfin recusou suspender a conta.")}

        self.data_manager.add_blocked_user(user_id, utilizador['username'], reason=reason)

        # Suspender a conta impede novos inícios de sessão, mas não corta o que
        # já está a tocar: quem estava a ver continuava até ao fim do filme.
        if self.stream_manager:
            self.stream_manager.block_user_sessions(
                user_id, reason="O seu acesso ao servidor foi bloqueado pelo administrador."
            )

        logger.info(f"Conta '{utilizador['username']}' suspensa no Jellyfin (motivo: {reason}).")
        return {"success": True, "message": _("Utilizador bloqueado com sucesso.")}

    def unblock_user(self, user_id) -> Dict[str, Any]:
        if not self._definir_suspensao(user_id, False):
            return {"success": False, "message": _("O Jellyfin recusou reativar a conta.")}

        self.data_manager.remove_blocked_user(user_id)
        return {"success": True, "message": _("Utilizador desbloqueado com sucesso.")}

    # =========================================================================
    # REMOÇÃO
    # =========================================================================

    def remove_user(self, user_id) -> Dict[str, Any]:
        utilizador = self.get_user_by_id(user_id)
        username = utilizador['username'] if utilizador else str(user_id)

        if self.stream_manager:
            self.stream_manager.block_user_sessions(user_id, "A sua conta está a ser removida.")

        try:
            self.conn.api.delete(f'/Users/{user_id}')
        except JellyfinApiError as e:
            # Já não existir no servidor não é um erro para o painel: o
            # objetivo — deixar de ter acesso — está cumprido, e é preciso
            # continuar para limpar o perfil local.
            if e.status_code != 404:
                logger.error(f"O Jellyfin recusou remover '{username}': {describe(e)}")
                return {"success": False, "message": str(e)}

        self.invalidate_user_cache()
        self._desativar_perfil_local(user_id)
        logger.info(f"Conta '{username}' removida do Jellyfin.")
        return {"success": True, "message": _("Utilizador removido com sucesso.")}

    def _desativar_perfil_local(self, user_id):
        perfil = self.data_manager.get_user_profile(user_id)
        if not perfil:
            return
        perfil['status'] = 'inactive'
        self.data_manager.set_user_profile(user_id, perfil)
        self.data_manager.remove_blocked_user(user_id)

    def toggle_overseerr_access(self, user_id, access: bool) -> Dict[str, Any]:
        """O Jellyseerr importa os utilizadores do Jellyfin diretamente.

        Fica por implementar até à fase da integração de pedidos; devolver um
        erro claro é melhor do que fingir que funcionou.
        """
        return {"success": False, "message": _("Integração de pedidos ainda não disponível para o Jellyfin.")}
