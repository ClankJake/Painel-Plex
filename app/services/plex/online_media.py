# app/services/plex/online_media.py

"""
Gestão das "Fontes de Mídia Online" do Plex (Online Media Sources).

São os conteúdos que a própria Plex oferece dentro da aplicação — TV ao Vivo,
Filmes e Programas de TV gratuitos, Podcasts, etc. — e que, num servidor
privado, competem visualmente com as bibliotecas do dono do servidor.

A Plex expõe estas preferências em ``/api/v2/user/{uuid}/settings/opt_outs``,
que é uma definição *da conta de cada utilizador*, não do servidor. Ou seja:
só é possível alterá-las enquanto temos o token do próprio utilizador em mão —
exatamente o que acontece no momento em que ele aceita o convite no painel.

O pedido é feito aqui à mão, em vez de `MyPlexAccount.onlineMediaSources()`,
porque o leitor do plexapi assume uma forma exata de resposta: exige XML (parte
se a Plex responder JSON) e só reconhece `<optOut>` como filho *direto* da raiz.
Qualquer mudança de forma do lado da Plex devolveria uma lista vazia sem erro
nenhum — a funcionalidade deixaria de fazer o que promete, em silêncio.
"""

import json
import logging
import xml.etree.ElementTree as ET

import requests
from flask_babel import gettext as _

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

OPT_OUTS_URL = "https://plex.tv/api/v2/user/{uuid}/settings/opt_outs"

REQUEST_TIMEOUT = 15

# Catálogo das fontes conhecidas, pela ordem em que fazem sentido para o admin.
# As chaves reais são lidas da conta do Plex sempre que possível (ver
# `get_catalog`); esta lista serve para as ordenar, para lhes dar um nome
# legível e para a interface continuar utilizável com o Plex offline.
KNOWN_SOURCE_KEYS = [
    "tv.plex.provider.vod",
    "tv.plex.provider.epg",
    "tv.plex.provider.music",
    "tv.plex.provider.news",
    "tv.plex.provider.podcasts",
    "tv.plex.provider.metadata",
]


def source_label(key):
    """Nome legível de uma fonte. Chaves novas da Plex aparecem como estão."""
    # As traduções são resolvidas aqui (e não num dicionário de módulo) porque
    # `gettext` precisa do contexto da aplicação, que não existe no import.
    labels = {
        "tv.plex.provider.vod": _("Filmes e Programas de TV (grátis, com anúncios)"),
        "tv.plex.provider.epg": _("TV ao Vivo (canais gratuitos do Plex)"),
        "tv.plex.provider.music": _("Vídeos Musicais"),
        "tv.plex.provider.news": _("Notícias"),
        "tv.plex.provider.podcasts": _("Podcasts"),
        "tv.plex.provider.metadata": _("Descobrir (Plex Discover)"),
    }
    return labels.get(key, key)


# O pedido mais comum: esconder o catálogo próprio da Plex (Filmes e Séries e
# TV ao Vivo) para que o utilizador veja apenas o conteúdo do servidor.
DEFAULT_SOURCES_TO_DISABLE = [
    "tv.plex.provider.vod",
    "tv.plex.provider.epg",
]

# Valor devolvido pela Plex para uma fonte já desativada.
OPT_OUT = "opt_out"


def sanitize_source_keys(keys, limit=25):
    """
    Filtra uma lista de chaves vinda do exterior (interface ou config.json).

    Mantém apenas identificadores plausíveis da Plex, sem duplicados e com um
    teto de segurança — uma lista corrompida nunca deve transformar o aceite do
    convite numa enxurrada de chamadas à API.
    """
    if not isinstance(keys, (list, tuple)):
        return []

    clean = []
    for key in keys:
        if not isinstance(key, str):
            continue
        key = key.strip()
        if not key or len(key) > 100:
            continue
        if not all(c.isalnum() or c in "._-" for c in key):
            continue
        if key not in clean:
            clean.append(key)
        if len(clean) >= limit:
            break
    return clean


def _walk_json(node, found):
    """Recolhe recursivamente qualquer objeto JSON que tenha uma chave `key`."""
    if isinstance(node, dict):
        key = node.get("key")
        if isinstance(key, str) and key:
            found.append({"key": key, "value": node.get("value")})
        for value in node.values():
            _walk_json(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, found)


def parse_opt_outs(body):
    """
    Lê a resposta do endpoint de opt-outs, seja ela JSON ou XML.

    A leitura é deliberadamente tolerante — procura, a qualquer profundidade,
    entradas com um atributo `key` — porque a forma exata desta resposta já é
    a segunda coisa que a Plex mudou por baixo desta funcionalidade.
    """
    body = (body or "").strip()
    if not body:
        return []

    found = []

    if body[0] in "[{":
        try:
            _walk_json(json.loads(body), found)
        except ValueError:
            found = []
    else:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return []
        for elem in root.iter():
            key = elem.attrib.get("key")
            if key:
                found.append({"key": key, "value": elem.attrib.get("value")})

    # A mesma chave pode aparecer aninhada mais do que uma vez; fica a primeira.
    unique = {}
    for entry in found:
        unique.setdefault(entry["key"], entry)
    return list(unique.values())


class PlexOnlineMediaManager:
    """
    Lê e aplica as preferências de Fontes de Mídia Online.
    """

    def __init__(self, connection, session=None):
        self.conn = connection
        self._session = session or requests

    # =========================================================================
    # CAMADA HTTP
    # =========================================================================
    def _credentials(self, account):
        """Extrai o UUID e o token de um MyPlexAccount."""
        uuid = getattr(account, "uuid", None)
        token = getattr(account, "authToken", None) or getattr(account, "_token", None)
        if not uuid or not token:
            raise ValueError("conta Plex sem UUID ou sem token")
        return uuid, token

    def _headers(self, uuid, token):
        return {
            "Accept": "application/json",
            "X-Plex-Token": token,
            "X-Plex-Product": "PlexPanel",
            "X-Plex-Version": "1.0",
            "X-Plex-Client-Identifier": uuid,
        }

    def read_sources(self, account):
        """
        Devolve as fontes que a conta conhece, como ``[{"key", "value"}]``.

        Levanta exceção se o pedido falhar — quem chama decide o que fazer.
        """
        uuid, token = self._credentials(account)
        response = self._session.get(
            OPT_OUTS_URL.format(uuid=uuid),
            headers=self._headers(uuid, token),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        sources = parse_opt_outs(response.text)
        if not sources:
            # Sem isto, uma mudança de forma da resposta seria indistinguível de
            # uma conta genuinamente sem fontes. O corpo cru é o que permite
            # perceber o que a Plex passou a devolver.
            logger.warning(
                "A Plex não devolveu nenhuma fonte de mídia online. "
                f"Resposta crua (até 500 caracteres): {response.text[:500]!r}"
            )
        return sources

    def _disable_source(self, account, key):
        """Marca uma fonte como `opt_out` na conta indicada."""
        uuid, token = self._credentials(account)
        response = self._session.post(
            OPT_OUTS_URL.format(uuid=uuid),
            headers=self._headers(uuid, token),
            params={"key": key, "value": OPT_OUT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

    # =========================================================================
    # LEITURA (para a interface de configurações)
    # =========================================================================
    def get_catalog(self):
        """
        Devolve ``{"sources": [...], "account_read": bool}`` para a interface.

        Cada fonte é ``{"key", "label", "selected", "available"}``. As chaves vêm
        da própria conta Plex do administrador quando há ligação, de modo a
        acompanhar automaticamente qualquer fonte que a Plex adicione ou renomeie.

        `account_read` é False quando a conta não respondeu ou não devolveu nada:
        nesse caso a lista mostrada é apenas o catálogo conhecido e pode não
        corresponder à realidade — a interface tem de o dizer ao admin.
        """
        config = load_or_create_config()
        selected = sanitize_source_keys(config.get("ONLINE_MEDIA_SOURCES_TO_DISABLE"))

        account_keys = []
        if self.conn and getattr(self.conn, "account", None):
            try:
                account_keys = [s["key"] for s in self.read_sources(self.conn.account)]
            except Exception as e:
                logger.warning(f"Não foi possível listar as fontes de mídia online do Plex: {e}")

        # Sem resposta da conta não sabemos o que existe: nada é marcado como
        # indisponível, para não acusar falsamente uma chave perfeitamente boa.
        keys = list(account_keys) if account_keys else list(KNOWN_SOURCE_KEYS)

        # Uma fonte já escolhida pelo admin nunca desaparece da lista, mesmo que
        # a Plex deixe de a devolver — caso contrário sumia da interface e o
        # admin não teria como a desmarcar.
        for key in selected:
            if key not in keys:
                keys.append(key)

        keys.sort(key=lambda k: KNOWN_SOURCE_KEYS.index(k) if k in KNOWN_SOURCE_KEYS else len(KNOWN_SOURCE_KEYS))

        return {
            "account_read": bool(account_keys),
            "sources": [
                {
                    "key": key,
                    "label": source_label(key),
                    "selected": key in selected,
                    "available": (key in account_keys) if account_keys else True,
                }
                for key in keys
            ],
        }

    # =========================================================================
    # ESCRITA (no momento do aceite do convite)
    # =========================================================================
    def apply_to_account(self, user_account, keys=None):
        """
        Desativa, na conta do utilizador, as fontes configuradas pelo admin.

        `user_account` é um :class:`~plexapi.myplex.MyPlexAccount` autenticado
        com o token do próprio utilizador — é a única forma de mexer nestas
        preferências, que pertencem à conta dele e não ao servidor.

        Nunca levanta exceções: falhar a esconder a TV ao Vivo não pode impedir
        alguém de entrar no servidor.
        """
        if keys is None:
            config = load_or_create_config()
            if not config.get("DISABLE_ONLINE_MEDIA_SOURCES_ON_CLAIM", False):
                return {"success": True, "skipped": True, "disabled": [], "failed": []}
            keys = config.get("ONLINE_MEDIA_SOURCES_TO_DISABLE")

        wanted = sanitize_source_keys(keys)
        if not wanted:
            return {"success": True, "skipped": True, "disabled": [], "failed": []}

        username = getattr(user_account, "username", None) or "?"

        try:
            sources = self.read_sources(user_account)
        except Exception as e:
            logger.warning(
                f"Não foi possível ler as fontes de mídia online de '{username}': {e}"
            )
            return {
                "success": False,
                "disabled": [],
                "failed": wanted,
                "message": _("Não foi possível ler as fontes de mídia online da conta."),
            }

        by_key = {s["key"]: s for s in sources}
        disabled, already, failed = [], [], []

        for key in wanted:
            source = by_key.get(key)
            if source is None:
                continue
            if source.get("value") == OPT_OUT:
                already.append(key)
                continue
            try:
                self._disable_source(user_account, key)
                disabled.append(key)
            except Exception as e:
                failed.append(key)
                logger.warning(f"Falha ao desativar '{key}' para '{username}': {e}")

        missing = [k for k in wanted if k not in by_key]
        if missing:
            # Uma chave configurada que a conta não reconhece não desativa nada.
            # O que a conta devolveu vai junto: é a única forma de o admin saber
            # qual é o nome novo da fonte que quer desligar.
            logger.warning(
                f"Fontes de mídia online configuradas mas inexistentes na conta de "
                f"'{username}': {missing}. A conta devolveu: {sorted(by_key) or 'nada'}. "
                f"Reveja a seleção em Configurações > Conexões."
            )

        if disabled:
            logger.info(
                f"Fontes de mídia online desativadas para '{username}': {', '.join(disabled)}"
            )
        elif already and not failed:
            logger.debug(
                f"As fontes de mídia online de '{username}' já estavam desativadas."
            )

        return {
            "success": not failed,
            "disabled": disabled,
            "already_disabled": already,
            "failed": failed,
            "missing": missing,
        }
