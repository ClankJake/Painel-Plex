# tests/test_resumo_financeiro.py

"""Os "próximos vencimentos" do resumo financeiro, e o que vence HOJE.

🐛 `_, last_day = calendar.monthrange(...)` deixava o `_` do gettext a valer um
INTEIRO para o resto da função. A linha que monta o texto dos dias restantes
chama `_("Hoje")` quando faltam zero dias, e aí levantava
`TypeError: 'int' object is not callable`.

⚠️ **E o erro não chegava a aparecer**: o `except (ValueError, TypeError):
continue` logo abaixo engolia-o, e a pessoa era simplesmente saltada. O sintoma
não era um erro no log — era quem vence hoje DESAPARECER da lista, mesmo por
baixo do comentário que diz que esses devem aparecer sempre no topo.
"""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


def _resumo(data_manager):
    agora = datetime.now(timezone.utc)
    return data_manager.get_financial_summary(agora.year, agora.month)


def _nomes(resumo):
    return [u['username'] for u in resumo['upcoming_expirations']]


class TestQuemVenceHoje:
    def test_aparece_na_lista(self, app_context, db_session, data_manager):
        data_manager.set_user_profile('u1', {
            'username': 'ana',
            'expiration_date': datetime.now(timezone.utc).isoformat(),
        })

        assert 'ana' in _nomes(_resumo(data_manager))

    def test_o_texto_dos_dias_diz_hoje(self, app_context, db_session, data_manager):
        data_manager.set_user_profile('u1', {
            'username': 'ana',
            'expiration_date': datetime.now(timezone.utc).isoformat(),
        })

        ana = next(u for u in _resumo(data_manager)['upcoming_expirations'] if u['username'] == 'ana')

        assert ana['days_left'] == 0
        assert ana['days_left_text'] == "Hoje"

    def test_vem_no_topo_da_lista(self, app_context, db_session, data_manager):
        # É o que o comentário do código promete — e era exatamente quem
        # desaparecia.
        agora = datetime.now(timezone.utc)
        data_manager.set_user_profile('u1', {
            'username': 'bruno', 'expiration_date': (agora + timedelta(days=3)).isoformat(),
        })
        data_manager.set_user_profile('u2', {
            'username': 'ana', 'expiration_date': agora.isoformat(),
        })

        assert _nomes(_resumo(data_manager))[0] == 'ana'


class TestQuemVenceDaquiAUnsDias:
    def test_continua_a_aparecer(self, app_context, db_session, data_manager):
        # Este caminho nunca chegou a tocar no `_`: é a metade que já funcionava,
        # e serve para provar que a correção não a estragou.
        data_manager.set_user_profile('u1', {
            'username': 'bruno',
            'expiration_date': (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        })

        bruno = next(u for u in _resumo(data_manager)['upcoming_expirations'] if u['username'] == 'bruno')

        assert bruno['days_left'] == 3
        assert '3' in bruno['days_left_text']
