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
"""

import logging

from flask_babel import gettext as _

from app.config import load_or_create_config

logger = logging.getLogger(__name__)

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


class PlexOnlineMediaManager:
    """
    Lê e aplica as preferências de Fontes de Mídia Online.
    """

    def __init__(self, connection):
        self.conn = connection

    # =========================================================================
    # LEITURA (para a interface de configurações)
    # =========================================================================
    def get_catalog(self):
        """
        Devolve as fontes que o admin pode escolher desativar, no formato
        ``[{"key": ..., "label": ..., "selected": bool}]``.

        As chaves vêm da própria conta Plex do administrador quando há ligação,
        de modo a acompanhar automaticamente qualquer fonte que a Plex adicione
        ou renomeie. Sem ligação, cai para o catálogo conhecido para que a
        página de configurações continue utilizável.
        """
        config = load_or_create_config()
        selected = sanitize_source_keys(config.get("ONLINE_MEDIA_SOURCES_TO_DISABLE"))

        keys = []
        if self.conn and self.conn.account:
            try:
                keys = [s.key for s in self.conn.account.onlineMediaSources() if s.key]
            except Exception as e:
                logger.debug(f"Não foi possível listar as fontes de mídia online do Plex: {e}")

        if not keys:
            keys = list(KNOWN_SOURCE_KEYS)

        # Uma fonte já escolhida pelo admin nunca desaparece da lista, mesmo que
        # a Plex deixe de a devolver — caso contrário sumia da interface e o
        # admin não teria como a desmarcar.
        for key in selected:
            if key not in keys:
                keys.append(key)

        keys.sort(key=lambda k: KNOWN_SOURCE_KEYS.index(k) if k in KNOWN_SOURCE_KEYS else len(KNOWN_SOURCE_KEYS))

        return [
            {"key": key, "label": source_label(key), "selected": key in selected}
            for key in keys
        ]

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
            sources = user_account.onlineMediaSources()
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

        disabled, already, failed = [], [], []
        found = set()

        for source in sources:
            key = getattr(source, "key", None)
            found.add(key)
            if key not in wanted:
                continue

            if getattr(source, "value", None) == OPT_OUT:
                already.append(key)
                continue

            try:
                source.optOut()
                disabled.append(key)
            except Exception as e:
                failed.append(key)
                logger.warning(f"Falha ao desativar '{key}' para '{username}': {e}")

        missing = [k for k in wanted if k not in found]
        if missing:
            logger.debug(
                f"Fontes de mídia online desconhecidas para a conta de '{username}': {missing}"
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
