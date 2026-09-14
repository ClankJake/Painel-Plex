# tests/test_credenciais_do_jellyfin.py

"""A URL e a chave do Jellyfin, na página de Configurações.

🐛 **Os campos eram editáveis e não se conseguiam gravar.** Estavam desenhados
no cartão das Conexões e fora de `fields_to_update`: o `save_settings` só
escreve os campos dessa lista, por isso gravar descartava-os em silêncio — e o
`jellyfin_changed` da recarga seletiva nunca podia ser verdade, o que deixava o
painel a falar com o servidor antigo até ao próximo reinício. Os do Plex não
estão na lista porque têm tratamento próprio (chegam em minúsculas, do
assistente); os do Jellyfin não tinham nenhum.

🛡️ **E a chave descia em claro para o navegador.** Era a única credencial fora
de `sensitive_keys` — dá acesso de administrador a todo o servidor de média.
"""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def configurada(config_file):
    return config_file(IS_CONFIGURED=True, ADMIN_USER="dono", ADMIN_USER_ID="1")


def _autenticar(client):
    with client.session_transaction() as sessao:
        sessao["user_details"] = {"id": "1", "username": "dono", "email": "a@b.test", "role": "admin"}
        sessao["_user_id"] = "1"
        sessao["_fresh"] = True


class TestGravar:
    def test_a_url_e_a_chave_ficam_gravadas(self, client, configurada, db_session):
        from app.config import load_or_create_config

        _autenticar(client)
        resposta = client.post('/api/system/settings', json={
            'JELLYFIN_URL': 'http://jellyfin.local:8096',
            'JELLYFIN_API_KEY': 'chave-nova',
        })

        assert resposta.status_code == 200
        config = load_or_create_config()
        assert config['JELLYFIN_URL'] == 'http://jellyfin.local:8096'
        assert config['JELLYFIN_API_KEY'] == 'chave-nova'

    def test_gravar_sem_mexer_na_chave_nao_a_apaga(self, client, configurada, config_file, db_session):
        # A interface não reenvia um campo de palavra-passe que não foi tocado
        # (manda o marcador de asteriscos e omite-o): gravar outra coisa não
        # pode deixar o painel sem credencial.
        from app.config import load_or_create_config

        config_file(JELLYFIN_API_KEY='chave-antiga')
        _autenticar(client)

        client.post('/api/system/settings', json={'JELLYFIN_URL': 'http://outro:8096'})

        assert load_or_create_config()['JELLYFIN_API_KEY'] == 'chave-antiga'

    def test_o_marcador_de_escondido_nunca_e_gravado(self, client, configurada, config_file, db_session):
        # Segunda tranca, do lado do servidor: o `{is_set: ...}` que a leitura
        # devolve não pode voltar para dentro do config.json.
        from app.config import load_or_create_config

        config_file(JELLYFIN_API_KEY='chave-antiga')
        _autenticar(client)

        client.post('/api/system/settings', json={
            'JELLYFIN_API_KEY': {'is_set': True, 'length': 12},
        })

        assert load_or_create_config()['JELLYFIN_API_KEY'] == 'chave-antiga'


class TestNaoViajaEmClaro:
    def test_a_chave_nao_desce_para_o_navegador(self, client, configurada, config_file, db_session):
        config_file(JELLYFIN_API_KEY='chave-secreta')
        _autenticar(client)

        dados = client.get('/api/system/settings').get_json()

        assert dados['JELLYFIN_API_KEY'] == {'is_set': True, 'length': len('chave-secreta')}
        assert 'chave-secreta' not in str(dados)

    def test_a_url_desce_na_mesma(self, client, configurada, config_file, db_session):
        # Só a CHAVE é segredo: sem a URL a página não tem o que mostrar.
        config_file(JELLYFIN_URL='http://jellyfin.local:8096')
        _autenticar(client)

        dados = client.get('/api/system/settings').get_json()

        assert dados['JELLYFIN_URL'] == 'http://jellyfin.local:8096'
