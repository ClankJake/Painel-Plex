# app/utils/estatisticas.py

"""Há estatísticas de visualização para mostrar?

São duas perguntas parecidas e não é a mesma:

- `media_server.capabilities.estatisticas` — **pode?** É uma característica do
  servidor, fixa. É ela que mantém o cartão do Tautulli nas Conexões: escondê-lo
  quando o Tautulli não está configurado deixava o administrador sem forma
  nenhuma de o configurar.
- `estatisticas_disponiveis()` — **tem?** É o estado de agora: o servidor
  suporta-as E a fonte está ligada. É por esta que se escondem o pódio, o XP,
  as conquistas, as recomendações e o Wrapped.

Confundi-las dá os dois erros opostos: um painel Plex sem Tautulli com um menu
cheio de páginas vazias, ou um painel sem maneira de ligar o Tautulli.
"""

from typing import Any


def estatisticas_disponiveis() -> bool:
    """A resposta para o servidor de média deste painel."""
    from ..extensions import media_server

    return _disponiveis(media_server)


def _disponiveis(media_server: Any) -> bool:
    """Separada para poder ser respondida sobre um backend qualquer.

    Um backend a meio de ser construído — ou um duplo de teste incompleto — não
    pode partir a página: na dúvida, não há estatísticas.
    """
    try:
        pergunta = getattr(media_server, 'estatisticas_disponiveis', None)
        if callable(pergunta):
            return bool(pergunta())
        return bool(media_server.capabilities.estatisticas)
    except AttributeError:
        return False
