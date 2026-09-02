# app/services/tautulli/recommendations_handler.py
"""
Motor de recomendações "Porque assistiu X, pode gostar de Y".

A ideia é simples de explicar e barata de calcular: cruzamos o histórico de
TODOS os utilizadores do servidor (fornecido pelo Tautulli) e procuramos itens
que costumam ser vistos pelas mesmas pessoas. Se muita gente que viu *Interstellar*
também viu *Arrival*, então quem acabou de ver *Interstellar* provavelmente vai
gostar de *Arrival*.

Isto é o clássico **filtro colaborativo item-item**, com semelhança de cosseno
sobre uma matriz binária utilizador × item:

        sim(A, B) = |quem viu A ∩ quem viu B| / sqrt(|quem viu A| × |quem viu B|)

O denominador é o que impede que "o filme que toda a gente viu" apareça como
recomendação para tudo — sem ele, popularidade seria confundida com afinidade.

⚠️ Servidores pequenos (ou recém-instalados) não têm sobreposição suficiente
entre utilizadores para o filtro colaborativo dizer alguma coisa. Nesse caso há
um plano B baseado em conteúdo: recomendamos itens que partilham géneros com a
"semente" (o X do "porque assistiu X"). Cada recomendação diz qual dos dois
caminhos a gerou (``match_type``), para a interface poder ser honesta com o
utilizador sobre o motivo.

Notas de desenho:

* O índice (catálogo + matriz) é construído UMA vez para o servidor inteiro e
  reaproveitado por todos os utilizadores — é a parte cara (uma chamada ao
  Tautulli e, opcionalmente, alguns pedidos de metadados). Quem faz a cache é o
  ``TautulliManager``; aqui só se calcula.
* A privacidade é respeitada: quem ativou "esconder do ranking" não entra na
  matriz como *vizinho* (o histórico dele nunca influencia o que os outros veem),
  mas continua a receber recomendações a partir do seu próprio histórico.
* Nada do que é devolvido identifica *quem* viu o quê — apenas quantos.
"""

import base64
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Any, Dict, List, Optional, Set

from flask import url_for

from app.config import load_or_create_config

logger = logging.getLogger(__name__)


# Valores usados quando a chave ainda não existe no config.json (instalações
# antigas) ou quando o administrador gravou lixo por cima.
DEFAULTS: Dict[str, Any] = {
    "RECOMMENDATIONS_ENABLED": True,
    "RECOMMENDATIONS_HISTORY_DAYS": 180,
    "RECOMMENDATIONS_MIN_PERCENT_WATCHED": 25,
    "RECOMMENDATIONS_MIN_CO_OCCURRENCE": 2,
    "RECOMMENDATIONS_MAX_SECTIONS": 4,
    "RECOMMENDATIONS_ITEMS_PER_SECTION": 8,
    "RECOMMENDATIONS_RESPECT_PRIVACY": True,
    "RECOMMENDATIONS_GENRE_LOOKUP_LIMIT": 40,
}


def _config_int(config: Dict[str, Any], key: str, minimum: int = 0) -> int:
    """Lê um inteiro do config com um padrão seguro (nunca rebenta, nunca devolve lixo)."""
    default = DEFAULTS[key]
    try:
        value = int(float(config.get(key, default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, value)


def _config_bool(config: Dict[str, Any], key: str) -> bool:
    value = config.get(key, DEFAULTS[key])
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "sim")
    return bool(value)


def build_poster_url(thumb: Optional[str], width: int = 300, height: int = 450) -> Optional[str]:
    """
    Constrói o URL do proxy interno de imagens para uma miniatura do Tautulli.

    O proxy recebe o caminho já codificado em base64 (ver ``blueprints/image.py``),
    o que evita expor o URL/token do Tautulli ao browser do utilizador.
    """
    if not thumb:
        return None

    tautulli_path = f"/pms_image_proxy?img={thumb}&width={width}&height={height}"
    b64_payload = base64.urlsafe_b64encode(f"tautulli:{tautulli_path}".encode("utf-8")).decode("utf-8")

    # 🛡️ Fora de um pedido HTTP (ex: tarefa agendada) o url_for rebenta.
    try:
        return url_for("image.proxy_image", source=b64_payload)
    except RuntimeError:
        return f"/image/?source={b64_payload}"


def _item_identity(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Reduz uma linha do histórico à obra a que pertence.

    Um episódio não é recomendável por si só — o que interessa é a *série*. Por
    isso os episódios são agrupados pela série (``grandparent_*``) e os filmes
    ficam como estão. Tudo o resto (música, clipes, trailers) é ignorado.

    A chave usa o ``rating_key`` do Plex quando existe, com o título como
    alternativa, para que a mesma obra não se parta em várias entradas.
    """
    media_type = item.get("media_type")

    if media_type == "movie":
        title = (item.get("title") or "").strip()
        rating_key = str(item.get("rating_key") or "").strip()
        if not title and not rating_key:
            return None
        year = item.get("year")
        return {
            "key": f"movie:{rating_key}" if rating_key else f"movie:{title.lower()}|{year or ''}",
            "media_type": "movie",
            "title": title,
            "year": year if isinstance(year, int) and year > 0 else None,
            "rating_key": rating_key or None,
            "thumb": item.get("thumb"),
        }

    if media_type == "episode":
        title = (item.get("grandparent_title") or "").strip()
        rating_key = str(item.get("grandparent_rating_key") or "").strip()
        if not title and not rating_key:
            return None
        return {
            "key": f"show:{rating_key}" if rating_key else f"show:{title.lower()}",
            "media_type": "show",
            "title": title,
            # O 'year' de um episódio é o do episódio, não o da série: seria
            # enganador mostrá-lo por baixo do nome da série.
            "year": None,
            "rating_key": rating_key or None,
            "thumb": item.get("grandparent_thumb") or item.get("thumb"),
        }

    return None


def _percent_complete(item: Dict[str, Any]) -> float:
    """Percentagem vista de uma sessão, tolerante a campos ausentes ou inválidos."""
    raw = item.get("percent_complete")
    if raw in (None, ""):
        # Versões antigas do Tautulli podem não expor a percentagem; nesse caso
        # o 'watched_status' (0 / 0.5 / 1) serve de aproximação.
        status = item.get("watched_status")
        try:
            return float(status) * 100 if status not in (None, "") else 100.0
        except (TypeError, ValueError):
            return 100.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 100.0


class RecommendationsHandler:
    """Constrói o índice de co-visualização e gera as secções de recomendação."""

    def __init__(self, api_client, data_manager=None):
        self.api = api_client
        self.data_manager = data_manager

    # ======================================================================
    # ÍNDICE (PARTILHADO POR TODOS OS UTILIZADORES)
    # ======================================================================

    def build_index(self, days: Optional[int] = None) -> Dict[str, Any]:
        """
        Lê o histórico global e devolve a estrutura usada pelas recomendações:

        ``catalog``     {chave: {title, year, media_type, rating_key, poster_url, genres}}
        ``user_items``  {user_id: {chave: nº de reproduções}} — TODOS os utilizadores
        ``item_users``  {chave: {user_id, ...}} — apenas os que podem servir de vizinhos

        ``user_items`` inclui toda a gente porque cada um precisa do seu próprio
        histórico para gerar as "sementes"; ``item_users`` exclui quem pediu
        privacidade, porque essa é a estrutura que faz um histórico influenciar
        as recomendações dos outros.
        """
        config = load_or_create_config()
        days = days if days is not None else _config_int(config, "RECOMMENDATIONS_HISTORY_DAYS", minimum=1)
        min_percent = _config_int(config, "RECOMMENDATIONS_MIN_PERCENT_WATCHED")

        after_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        response = self.api.get_history(after=after_date)
        history = response.get("data", []) if isinstance(response, dict) else []

        catalog: Dict[str, Dict[str, Any]] = {}
        user_items: Dict[str, Counter] = defaultdict(Counter)
        genres_seen: Dict[str, List[str]] = {}

        for entry in history:
            user_id = entry.get("user_id")
            if user_id in (None, ""):
                continue

            identity = _item_identity(entry)
            if not identity:
                continue

            # Uma amostra de dois minutos não é um sinal de gosto.
            if _percent_complete(entry) < min_percent:
                continue

            key = identity["key"]
            existing = catalog.get(key)
            if existing is None:
                catalog[key] = {
                    "key": key,
                    "title": identity["title"],
                    "year": identity["year"],
                    "media_type": identity["media_type"],
                    "rating_key": identity["rating_key"],
                    "thumb": identity["thumb"],
                    "genres": [],
                }
            elif not existing.get("thumb") and identity.get("thumb"):
                existing["thumb"] = identity["thumb"]

            # Alguns servidores devolvem os géneros já no histórico: aproveitamos
            # de graça e poupamos uma chamada de metadados mais à frente.
            if key not in genres_seen:
                entry_genres = entry.get("genres")
                if isinstance(entry_genres, list) and entry_genres:
                    genres_seen[key] = [str(g) for g in entry_genres if g]

            user_items[str(user_id)][key] += 1

        private_users = self._get_private_users(config, user_items.keys())

        item_users: Dict[str, Set[str]] = defaultdict(set)
        for user_id, items in user_items.items():
            if user_id in private_users:
                continue
            for key in items:
                item_users[key].add(user_id)

        for key, genres in genres_seen.items():
            if key in catalog:
                catalog[key]["genres"] = genres

        self._enrich_with_genres(config, catalog, item_users)

        for item in catalog.values():
            item["poster_url"] = build_poster_url(item.pop("thumb", None))

        return {
            "catalog": catalog,
            "user_items": {user_id: dict(items) for user_id, items in user_items.items()},
            "item_users": {key: set(users) for key, users in item_users.items()},
            "days": days,
        }

    def _get_private_users(self, config: Dict[str, Any], user_ids) -> Set[str]:
        """IDs (como texto) de quem pediu para ficar fora das estatísticas dos outros."""
        if not self.data_manager or not _config_bool(config, "RECOMMENDATIONS_RESPECT_PRIVACY"):
            return set()

        numeric_ids = []
        for user_id in user_ids:
            try:
                numeric_ids.append(int(user_id))
            except (TypeError, ValueError):
                continue

        if not numeric_ids:
            return set()

        try:
            profiles = self.data_manager.get_user_profiles_by_id(numeric_ids) or {}
        except Exception as e:  # pragma: no cover - defensivo
            logger.debug(f"Não foi possível ler os perfis para as recomendações: {e}")
            return set()

        return {
            str(user_id)
            for user_id, profile in profiles.items()
            if (profile or {}).get("hide_from_leaderboard")
        }

    def _enrich_with_genres(self, config: Dict[str, Any], catalog: Dict[str, Dict[str, Any]],
                            item_users: Dict[str, Set[str]]) -> None:
        """
        Preenche os géneros em falta para os itens mais vistos do servidor.

        Os géneros só são precisos para o plano B (semelhança por conteúdo), por
        isso limitamos os pedidos de metadados aos itens com mais hipóteses de
        aparecerem — os mais populares. O limite é configurável e ``0`` desliga
        de vez esta etapa (e, com ela, o plano B).
        """
        limit = _config_int(config, "RECOMMENDATIONS_GENRE_LOOKUP_LIMIT")
        if limit <= 0:
            return

        pending = [
            key for key in catalog
            if not catalog[key].get("genres") and catalog[key].get("rating_key")
        ]
        pending.sort(key=lambda key: (-len(item_users.get(key, ())), key))

        for key in pending[:limit]:
            try:
                metadata = self.api.get_metadata(catalog[key]["rating_key"]) or {}
            except Exception as e:
                logger.debug(f"Metadados indisponíveis para '{key}' nas recomendações: {e}")
                continue

            genres = metadata.get("genres") if isinstance(metadata, dict) else None
            if isinstance(genres, list):
                catalog[key]["genres"] = [str(g) for g in genres if g]

    # ======================================================================
    # RECOMENDAÇÕES DE UM UTILIZADOR
    # ======================================================================

    def recommend(self, index: Dict[str, Any], plex_user_id: str) -> Dict[str, Any]:
        """
        Gera as secções "Porque assistiu X, pode gostar de Y" para um utilizador.

        Devolve sempre ``success: True`` com uma lista (possivelmente vazia) de
        secções; ``reason`` explica uma lista vazia para a interface poder dar
        uma mensagem útil em vez de um espaço em branco.
        """
        config = load_or_create_config()
        max_sections = _config_int(config, "RECOMMENDATIONS_MAX_SECTIONS", minimum=1)
        per_section = _config_int(config, "RECOMMENDATIONS_ITEMS_PER_SECTION", minimum=1)
        min_co = _config_int(config, "RECOMMENDATIONS_MIN_CO_OCCURRENCE", minimum=1)

        catalog = index.get("catalog") or {}
        user_items = (index.get("user_items") or {}).get(str(plex_user_id)) or {}

        if not user_items:
            return {"success": True, "sections": [], "reason": "no_history"}

        watched = set(user_items)
        already_suggested: Set[str] = set()
        sections: List[Dict[str, Any]] = []

        # As sementes são o que o utilizador mais viu: começamos pelo sinal mais
        # forte. Pedimos mais candidatas do que secções porque algumas não vão
        # produzir nada (obras que mais ninguém viu).
        seeds = sorted(user_items.items(), key=lambda pair: (-pair[1], pair[0]))
        seed_budget = max_sections * 4

        for seed_key, seed_plays in seeds[:seed_budget]:
            if len(sections) >= max_sections:
                break
            if seed_key not in catalog:
                continue

            excluded = watched | already_suggested
            items = self._similar_by_viewers(index, seed_key, excluded, min_co, per_section)

            # Plano B: em servidores pequenos o cruzamento entre utilizadores não
            # chega para nada. Completamos com semelhança por género.
            if len(items) < per_section:
                excluded_now = excluded | {item["key"] for item in items}
                items += self._similar_by_genre(index, seed_key, excluded_now, per_section - len(items))

            if not items:
                continue

            already_suggested.update(item["key"] for item in items)
            seed = catalog[seed_key]
            match_types = {item["match_type"] for item in items}

            sections.append({
                "seed": {
                    "key": seed_key,
                    "title": seed["title"],
                    "year": seed["year"],
                    "media_type": seed["media_type"],
                    "rating_key": seed["rating_key"],
                    "poster_url": seed["poster_url"],
                    "plays": seed_plays,
                },
                "source": match_types.pop() if len(match_types) == 1 else "mixed",
                "items": items,
            })

        return {
            "success": True,
            "sections": sections,
            "reason": "ok" if sections else "not_enough_data",
            "days": index.get("days"),
        }

    def _similar_by_viewers(self, index: Dict[str, Any], seed_key: str, excluded: Set[str],
                            min_co: int, limit: int) -> List[Dict[str, Any]]:
        """Filtro colaborativo item-item: quem viu a semente, que mais viu?"""
        item_users = index.get("item_users") or {}
        user_items = index.get("user_items") or {}
        catalog = index.get("catalog") or {}

        seed_watchers = item_users.get(seed_key) or set()
        # Com um único espectador não há "as mesmas pessoas também viram" — há
        # apenas uma pessoa, e isso é coincidência, não sinal.
        if len(seed_watchers) < 2:
            return []

        co_occurrence: Counter = Counter()
        for user_id in seed_watchers:
            for key in user_items.get(user_id, {}):
                if key == seed_key or key in excluded:
                    continue
                co_occurrence[key] += 1

        scored = []
        for key, shared_viewers in co_occurrence.items():
            if shared_viewers < min_co or key not in catalog:
                continue
            candidate_watchers = len(item_users.get(key) or ())
            if candidate_watchers <= 0:
                continue
            # Cosseno: divide pela popularidade dos dois lados, para que o
            # "filme que toda a gente viu" não seja recomendado a toda a gente.
            score = shared_viewers / sqrt(len(seed_watchers) * candidate_watchers)
            scored.append((score, shared_viewers, key))

        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))

        return [
            self._format_item(catalog[key], match_type="viewers", score=score, shared_viewers=shared_viewers)
            for score, shared_viewers, key in scored[:limit]
        ]

    def _similar_by_genre(self, index: Dict[str, Any], seed_key: str, excluded: Set[str],
                          limit: int) -> List[Dict[str, Any]]:
        """
        Plano B por conteúdo: obras que partilham géneros com a semente.

        Usa o índice de Jaccard (géneros em comum / géneros no total) para que
        um item com dois géneros iguais aos da semente ganhe a um item que tem
        vinte géneros e por acaso apanha um. Em caso de empate, decide a
        popularidade — entre dois "parecidos", o mais visto é a aposta melhor.
        """
        if limit <= 0:
            return []

        catalog = index.get("catalog") or {}
        item_users = index.get("item_users") or {}

        seed = catalog.get(seed_key) or {}
        seed_genres = {g for g in (seed.get("genres") or []) if g}
        if not seed_genres:
            return []

        # Com uma semente rica em géneros, um único género em comum diz muito
        # pouco (quase tudo é "Drama"); exigimos dois.
        min_shared = 2 if len(seed_genres) >= 2 else 1

        scored = []
        for key, item in catalog.items():
            if key == seed_key or key in excluded:
                continue
            genres = {g for g in (item.get("genres") or []) if g}
            shared = seed_genres & genres
            if len(shared) < min_shared:
                continue
            similarity = len(shared) / len(seed_genres | genres)
            same_type = 1 if item.get("media_type") == seed.get("media_type") else 0
            popularity = len(item_users.get(key) or ())
            scored.append((same_type, similarity, popularity, key, sorted(shared)))

        scored.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))

        return [
            self._format_item(catalog[key], match_type="genre", score=similarity, shared_genres=shared)
            for _same_type, similarity, _popularity, key, shared in scored[:limit]
        ]

    @staticmethod
    def _format_item(item: Dict[str, Any], match_type: str, score: float,
                     shared_viewers: int = 0, shared_genres: Optional[List[str]] = None) -> Dict[str, Any]:
        """Formata uma recomendação para a API (sem revelar QUEM viu o quê)."""
        return {
            "key": item["key"],
            "title": item["title"],
            "year": item["year"],
            "media_type": item["media_type"],
            "rating_key": item["rating_key"],
            "poster_url": item["poster_url"],
            "genres": list(item.get("genres") or [])[:3],
            "match_type": match_type,
            "shared_viewers": shared_viewers,
            "shared_genres": shared_genres or [],
            "score": round(float(score), 4),
        }
