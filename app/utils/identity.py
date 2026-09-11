# app/utils/identity.py

"""A identidade de um utilizador no servidor de média.

O Plex identifica as contas por um inteiro (`123456`); o Jellyfin por um GUID
(`38c3a1f0e4b24d...`). Para que o painel possa administrar qualquer um dos
dois, a identidade passou a ser **texto** em toda a aplicação — incluindo na
base de dados.

Isto tem uma consequência que não é óbvia e que é a origem provável de
qualquer bug nesta área: no SQLite, uma coluna de texto **não** encontra a
linha quando a consulta é feita com um inteiro. `WHERE media_user_id = 123`
não casa com `'123'`. Como o ID chega ao painel de sítios diferentes e em
formatos diferentes — da API do Plex vem inteiro, de um URL vem string, da
sessão vem string, de um webhook vem o que o gateway quiser — tem de haver um
único sítio onde isso é normalizado.

Esse sítio é o `DataManager`: **todos** os seus métodos normalizam o ID que
recebem antes de tocar na base de dados. Use `normalize_user_id` também em
qualquer comparação entre um ID vindo do servidor e um ID vindo do painel.
"""

from typing import Any, Iterable, List, Optional


def normalize_user_id(valor: Any) -> Optional[str]:
    """Devolve o ID como texto limpo, ou None se não houver ID nenhum.

    Aceita inteiros (Plex), strings (Jellyfin, URLs, sessões) e None. Um valor
    vazio ou só com espaços conta como ausência de ID — devolver `''` daria
    consultas que não encontram nada e perfis gravados com chave vazia.
    """
    if valor is None:
        return None

    texto = str(valor).strip()
    return texto or None


def normalize_user_ids(valores: Optional[Iterable[Any]]) -> List[str]:
    """Normaliza uma coleção de IDs, descartando os vazios e os repetidos.

    A ordem é preservada: há listagens no painel que dependem dela.
    """
    if not valores:
        return []

    vistos = set()
    resultado = []
    for valor in valores:
        normalizado = normalize_user_id(valor)
        if normalizado is not None and normalizado not in vistos:
            vistos.add(normalizado)
            resultado.append(normalizado)
    return resultado


def same_user(a: Any, b: Any) -> bool:
    """Os dois valores referem-se ao mesmo utilizador?

    Compara sempre como texto normalizado, o que evita o erro clássico de
    comparar o `id` inteiro devolvido pela API do Plex com o ID em texto que
    está guardado no perfil.
    """
    a_norm = normalize_user_id(a)
    return a_norm is not None and a_norm == normalize_user_id(b)
