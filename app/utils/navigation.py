# app/utils/navigation.py

"""Para onde vai quem não é administrador.

As estatísticas eram a casa de toda a gente, e estavam escritas à mão em cinco
sítios (o `before_request`, três redireccionamentos de login e o logótipo). Mas
só existem onde o servidor as suporta — hoje vêm do Tautulli, que só fala com o
Plex. Num painel ligado ao Jellyfin, a casa é a página da conta.
"""


def endpoint_inicial_do_utilizador() -> str:
    """O endpoint Flask para onde mandar um utilizador comum."""
    from ..extensions import media_server

    try:
        if media_server and media_server.capabilities.estatisticas:
            return 'main.statistics_page'
    except AttributeError:
        # Um backend a meio de ser construído (ou um duplo de teste sem
        # capacidades): a página da conta existe sempre.
        pass
    return 'main.account_page'
