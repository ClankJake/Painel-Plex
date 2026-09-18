# app/utils/periodo_de_teste.py

"""Em que ponto do período de teste está esta pessoa.

⚠️ **São DUAS perguntas parecidas, e trocá-las dá respostas opostas:**

- `teste_a_decorrer()` — a data de fim ainda não passou. É o que o painel
  sempre chamou `is_on_trial`, e é por ela que a página de utilizadores conta a
  aba "Em teste";
- `estado_do_teste()` — que TIPO de conta é esta: um teste a decorrer, um teste
  já terminado, ou nem sequer um teste. ⚠️ Quem já tem `expiration_date` **não
  é um teste**, tenha lá o `trial_end_date` que tiver: passou a assinante e o
  que ficou para trás é história. É a mesma regra que o `trial_sweep_job` já
  aplica ao decidir em quem não toca, e a mesma que faz
  `add_days_to_subscription` limpar os dados do teste ao dar um vencimento.

📌 A leitura vivia copiada em três rotas de `api/users.py`, cada uma com o seu
`try/except` à volta do mesmo `fromisoformat`. Uma cópia nova é o que faz
divergirem no primeiro ajuste.

⚠️ E as datas destas colunas são guardadas SEM FUSO, em UTC: lê-las com o fuso
do sistema é o mesmo engano que já custou três horas em cada data de vencimento
(ver `_momento_do_vencimento`, em `api/users.py`).
"""

from datetime import datetime, timezone


def _campo(perfil, nome):
    """O valor de um campo, venha o perfil como dicionário ou como modelo.

    As rotas de administração trabalham com o dicionário do `DataManager` e a
    página de pagamento com o `UserProfile` do SQLAlchemy — e quem pergunta
    "isto é um teste?" não tem de saber de qual dos dois se trata.
    """
    if perfil is None:
        return None
    if isinstance(perfil, dict):
        return perfil.get(nome)
    return getattr(perfil, nome, None)


def fim_do_teste(perfil):
    """Quando o período de teste acaba, ou `None` se não houver (ou não se ler).

    Devolve sempre uma data COM fuso: o que está gravado é UTC sem sufixo, e
    compará-la com um `datetime.now(timezone.utc)` sem a marcar levanta
    `TypeError`.
    """
    bruto = _campo(perfil, 'trial_end_date')
    if not bruto:
        return None
    try:
        fim = datetime.fromisoformat(str(bruto))
    except (ValueError, TypeError):
        return None
    return fim if fim.tzinfo else fim.replace(tzinfo=timezone.utc)


def teste_a_decorrer(perfil):
    """O período de teste existe e ainda não acabou."""
    fim = fim_do_teste(perfil)
    return bool(fim and fim > datetime.now(timezone.utc))


def estado_do_teste(perfil):
    """`'a_decorrer'`, `'terminado'`, ou `None` quando isto não é um teste."""
    if _campo(perfil, 'expiration_date'):
        return None
    if not fim_do_teste(perfil):
        return None
    return 'a_decorrer' if teste_a_decorrer(perfil) else 'terminado'
