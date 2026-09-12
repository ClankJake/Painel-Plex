# app/services/media_server/jellyfin/identity.py

"""Comparar dois identificadores do Jellyfin.

⚠️ **O mesmo GUID aparece com e sem hífenes, conforme quem o devolve.** O
`Id` de um utilizador vem sem (`38c3a1f0e4b2...`), mas os campos declarados
como `format: uuid` no OpenAPI — o `LastUserId` de um aparelho, por exemplo —
vêm com (`38c3a1f0-e4b2-...`). Comparar os dois com `==` dá sempre falso, e o
sintoma não é um erro: é uma lista que fica vazia, ou um filtro que não filtra.

Isto NÃO pertence ao `app/utils/identity.py`: lá a identidade é texto e serve
servidores que nem GUIDs usam (o Plex identifica por inteiro). Tirar hífenes é
conhecimento do Jellyfin, e fica dentro do Jellyfin.
"""

from typing import Any


def chave_de(identificador: Any) -> str:
    """A forma em que dois identificadores do Jellyfin se podem comparar."""
    return str(identificador or '').replace('-', '').strip().lower()


def mesma_conta(a: Any, b: Any) -> bool:
    """Os dois identificam o mesmo utilizador? Vazio nunca bate com vazio."""
    chave_a, chave_b = chave_de(a), chave_de(b)
    return bool(chave_a) and chave_a == chave_b
