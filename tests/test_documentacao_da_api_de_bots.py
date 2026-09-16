# tests/test_documentacao_da_api_de_bots.py
"""A documentação da API de bots é escrita à mão, e por isso mente sozinha.

`docs/api-convites-bot.md` tem uma tabela de campos e uma lista de rotas que
ninguém regenera: o dia em que um campo mudar de nome, ou uma rota de caminho,
ela continua a dizer o que dizia — e quem escreve um bot a partir dela leva com
um 400 sem perceber porquê. É a mesma armadilha que o
`test_assistente_por_servidor.py` fecha entre o `setup.js` e o `setup.html`.

Uma diferença em relação a esse: aqui a documentação é um CONTRATO PÚBLICO.
Tirar um campo sem o tirar da documentação parte integrações que já existem lá
fora, onde este repositório não chega.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DOC = (RAIZ / 'docs/api-convites-bot.md').read_text(encoding='utf-8')
ROTAS = (RAIZ / 'app/blueprints/api/invites.py').read_text(encoding='utf-8')


def _campos_da_tabela():
    """Os nomes na primeira coluna da tabela de PARÂMETROS.

    Só dessa tabela: o documento tem outras — a dos códigos de erro, por
    exemplo — e varrer o ficheiro inteiro fazia o `erro` de uma RESPOSTA passar
    por um campo do PEDIDO.
    """
    seccao = re.search(r'^### Parâmetros\n(.*?)^###', DOC, re.M | re.S)
    assert seccao, "a secção '### Parâmetros' desapareceu da documentação"
    return set(re.findall(r'^\|\s*`([a-z_]+)`\s*\|', seccao.group(1), re.M))


def _campos_do_esquema():
    from app.blueprints.api.schemas import CreateInviteBotSchema

    campos = getattr(CreateInviteBotSchema, 'model_fields', None)
    if campos is None:                      # Pydantic v1
        campos = CreateInviteBotSchema.__fields__
    return set(campos)


class TestATabelaDeParametros:
    def test_ha_campos_para_comparar(self):
        assert len(_campos_do_esquema()) >= 5

    def test_todo_o_campo_aceite_esta_documentado(self):
        em_falta = sorted(_campos_do_esquema() - _campos_da_tabela())
        assert em_falta == [], (
            f"o esquema aceita estes campos e a documentação não os menciona: {em_falta}"
        )

    def test_a_documentacao_nao_promete_campos_que_nao_existem(self):
        """
        O pior dos dois erros: quem escreve um bot a partir da documentação
        manda um campo que o Pydantic descarta em silêncio, e depois não
        percebe porque é que o convite não saiu como pedido.
        """
        inventados = sorted(_campos_da_tabela() - _campos_do_esquema())
        assert inventados == [], (
            f"a documentação promete campos que o esquema não aceita: {inventados}"
        )


class TestOsCaminhosDocumentados:
    """Uma rota documentada tem de existir, e com o mesmo método."""

    def _documentadas(self):
        # Os blocos ```\nGET /api/...\n``` e as linhas soltas do mesmo formato.
        # A query string não faz parte do caminho.
        return {
            (metodo, caminho.split('?')[0])
            for metodo, caminho in re.findall(
                r'^(GET|POST|DELETE|PUT|PATCH) (/api/invites/\S+)', DOC, re.M)
        }

    def _registadas(self):
        registadas = set()
        for caminho, metodos in re.findall(
            r"@invites_api_bp\.route\('([^']+)',\s*methods=\[([^\]]+)\]\)", ROTAS
        ):
            for metodo in re.findall(r"'([A-Z]+)'", metodos):
                registadas.add((metodo, f"/api/invites{caminho}"))
        return registadas

    def test_ha_rotas_para_comparar(self):
        assert len(self._documentadas()) >= 4

    @pytest.mark.parametrize('metodo, caminho', sorted({
        ('POST', '/api/invites/bot/create'),
        ('GET', '/api/invites/bot/invite/{code}'),
        ('DELETE', '/api/invites/bot/invite/{code}'),
        ('GET', '/api/invites/bot/invites'),
    }))
    def test_a_rota_esta_na_documentacao(self, metodo, caminho):
        assert (metodo, caminho) in self._documentadas()

    def test_toda_a_rota_documentada_existe_mesmo(self):
        # `{code}` na documentação é `<string:code>` no Flask.
        def normalizar(caminho):
            caminho = re.sub(r'\{[a-z_]+\}', '{}', caminho)
            caminho = re.sub(r'<[^>]+>', '{}', caminho)
            return caminho.split('?')[0]

        registadas = {(m, normalizar(c)) for m, c in self._registadas()}
        em_falta = sorted(
            (m, c) for m, c in self._documentadas()
            if (m, normalizar(c)) not in registadas
        )
        assert em_falta == [], (
            f"a documentação descreve rotas que não existem: {em_falta}"
        )


class TestOsMotivosDeRecusa:
    """As chaves de `erro` são contrato: um bot decide por elas."""

    def test_as_duas_chaves_estao_documentadas(self):
        from app.services.media_server.invitations import CONFLITO, PEDIDO_INVALIDO

        for chave in (CONFLITO, PEDIDO_INVALIDO):
            assert f'`{chave}`' in DOC, f"o motivo '{chave}' não está na documentação"
