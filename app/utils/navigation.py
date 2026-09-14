# app/utils/navigation.py

"""Para onde vai quem não é administrador.

As estatísticas eram a casa de toda a gente, e estavam escritas à mão em cinco
sítios (o `before_request`, três redireccionamentos de login e o logótipo). Mas
só existem onde há de onde as tirar — hoje, do Tautulli, que só fala com o
Plex. Num painel ligado ao Jellyfin, ou num painel Plex sem o Tautulli
configurado, a casa é a página da conta.
"""

from .estatisticas import estatisticas_disponiveis


def endpoint_inicial_do_utilizador() -> str:
    """O endpoint Flask para onde mandar um utilizador comum."""
    if estatisticas_disponiveis():
        return 'main.statistics_page'
    return 'main.account_page'
