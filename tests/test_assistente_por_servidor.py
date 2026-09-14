# tests/test_assistente_por_servidor.py

"""O assistente de instalação, num painel que pode ser de dois servidores.

🐛 **O cartão do Tautulli aparecia sempre.** Ele só fala com o Plex, por isso
num assistente de Jellyfin pedia credenciais de um serviço que nunca ia ser
usado — e quem as preenchesse ficava convencido de que tinha ligado alguma
coisa. É a mesma armadilha da página de Conexões, resolvida lá pela capacidade
`estatisticas_externas`; aqui não há backend construído a quem perguntar, por
isso quem esconde é o JavaScript, no momento da escolha.

⚠️ **E o passo 2 ficava com o texto do OUTRO servidor**: quem experimentasse o
Jellyfin e voltasse ao Plex via "Conta de Administrador" por cima de uma lista
de servidores.

O comportamento vive no navegador e não há aqui um. O que se pode prender — e é
onde os enganos aconteceram — é o CONTRATO entre o template e o script: os
ganchos que um oferece e o outro procura.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
TEMPLATE = (RAIZ / 'app/templates/setup.html').read_text(encoding='utf-8')
SCRIPT = (RAIZ / 'app/static/js/setup.js').read_text(encoding='utf-8')


def _chave_do_dataset(atributo):
    """`data-i18n-select-admin` -> `selectAdmin`, como o browser a entrega.

    O `setup.js` corta os 4 primeiros caracteres da chave do `dataset` e passa
    a seguinte a minúscula — é essa a forma que ele procura.
    """
    camel = re.sub(r'-([a-z])', lambda m: m.group(1).upper(), atributo)
    return camel[4].lower() + camel[5:]


class TestOCartaoDoTautulli:
    def test_tem_o_gancho_que_o_script_procura(self):
        assert 'id="setup-card-tautulli"' in TEMPLATE

    def test_o_script_esconde_o_cartao_fora_do_plex(self):
        assert "getElementById('setup-card-tautulli')" in SCRIPT
        # Escondido sempre que o tipo não for 'plex'.
        assert re.search(
            r"setup-card-tautulli'\)\?\.classList\.toggle\('hidden',\s*tipo\s*!==\s*'plex'\)",
            SCRIPT,
        )

    def test_as_credenciais_nao_sao_enviadas_num_painel_jellyfin(self):
        # Mandá-las vazias apagava o que estivesse num config.json restaurado.
        assert re.search(r"if \(!ehJellyfin\(\)\) \{\s*\n\s*setupData\.TAUTULLI_URL", SCRIPT)


class TestOTextoDoPasso2:
    def test_o_template_nao_presume_um_servidor(self):
        # O título é reescrito pela escolha; deixá-lo a dizer "Plex" era o que
        # aparecia a quem nunca chegou a escolher.
        titulo = re.search(r'id="step2-title"[^>]*>\{\{ _\(\'([^\']+)\'', TEMPLATE)
        assert titulo, "o título do passo 2 mudou de forma"
        assert 'Plex' not in titulo.group(1)
        assert 'Jellyfin' not in titulo.group(1)

    def test_a_escolha_repoe_o_texto_dos_DOIS_lados(self):
        # Só repor num deles é o bug: quem voltasse ao Plex ficava com o texto
        # do Jellyfin.
        assert 'i18n.selectServerTitle' in SCRIPT
        assert 'i18n.selectAdminTitle' in SCRIPT

    def test_o_script_corre_uma_vez_no_arranque(self):
        # Sem isto, o que está visível é o que o HTML tiver escrito à mão — e
        # era assim que o cartão e o título ficavam a depender de duas
        # verdades diferentes.
        assert re.search(
            r'selecionarTipoDeServidor\(setupData\.media_server_type\);', SCRIPT)


class TestTrocarDeServidorAMeio:
    def test_apaga_o_que_ficou_do_outro(self):
        # Quem experimentasse um servidor e mudasse de ideias levava consigo o
        # nome do administrador do primeiro.
        bloco = re.search(r'if \(mudou\) \{(.*?)\n        \}', SCRIPT, re.S)
        assert bloco, "o bloco que limpa o estado desapareceu"
        for campo in ('plex_url', 'plex_token', 'jellyfin_url',
                      'jellyfin_api_key', 'admin_user', 'admin_user_id'):
            assert f'setupData.{campo} = null' in bloco.group(1), campo


class TestAMarcaNoEcraDeBoasVindas:
    def test_nao_diz_plex_antes_de_a_pessoa_escolher(self):
        # É o ecrã ANTES da escolha, e metade das instalações são de Jellyfin.
        # Só conta o que a pessoa LÊ: os comentários do template são internos.
        passo0 = TEMPLATE.split('data-step="0"', 1)[1].split('data-step="1"', 1)[0]
        visivel = ' '.join(re.findall(r"_\(\s*'([^']+)'", passo0))

        assert 'Plex' not in visivel
        assert 'Jellyfin' not in visivel
        assert visivel, "o passo 0 deixou de ter texto traduzido — o teste ficaria vazio"


class TestOContratoEntreOTemplateEOScript:
    """Toda a chave que o script procura tem de existir no HTML.

    ⚠️ É a armadilha que já deu `undefined` por extenso na página de convite:
    o script pede uma chave, o template não a tem, e a pessoa lê o buraco.
    """

    def test_todas_as_chaves_i18n_usadas_existem(self):
        oferecidas = {
            _chave_do_dataset(a) for a in re.findall(r'\bdata-(i18n-[a-z0-9-]+)=', TEMPLATE)
        }
        usadas = set(re.findall(r'\bi18n\.([A-Za-z0-9_]+)', SCRIPT))

        em_falta = sorted(usadas - oferecidas)
        assert em_falta == [], f"o setup.js pede chaves que o setup.html não define: {em_falta}"

    def test_todas_as_urls_usadas_existem(self):
        oferecidas = {
            _chave_do_dataset(a) for a in re.findall(r'\bdata-(urls-[a-z0-9-]+)=', TEMPLATE)
        }
        usadas = set(re.findall(r'\burls\.([A-Za-z0-9_]+)', SCRIPT))

        em_falta = sorted(usadas - oferecidas)
        assert em_falta == [], f"o setup.js pede URLs que o setup.html não define: {em_falta}"
