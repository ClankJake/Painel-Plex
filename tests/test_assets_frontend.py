# tests/test_assets_frontend.py
"""Os assets que os templates pedem têm de ser gerados pelo build.

🐛 REGRESSÃO REPORTADA: o navegador acusava `io is not defined` e
`Chart is not defined` fora do Docker. Os caminhos das bibliotecas estavam
escritos em DOIS sítios — o `Dockerfile`, que as copiava para a imagem, e mais
nenhum. Quem corria o painel localmente só tinha o `build:css`, e as
bibliotecas nunca chegavam a `app/static/dist/`.

O `copy:vendor` fecha isso, mas a armadilha de fundo é a duplicação: uma lista
que é preciso lembrar de atualizar noutro sítio acaba sempre por divergir. Este
teste lê o que os templates pedem e o que o build gera, e compara — para que a
próxima biblioteca que alguém acrescente ao HTML e esqueça no package.json
falhe aqui, e não no navegador de quem instalou.
"""

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
TEMPLATES = RAIZ / 'app' / 'templates'

# O `build:css` do Tailwind gera este, e não passa pelo `copy:vendor`.
GERADOS_PELO_CSS = {'output.css'}


def _pedidos_pelos_templates():
    """Tudo o que um `<script src=".../dist/X">` ou `<link href=...>` pede."""
    pedidos = {}
    for template in sorted(TEMPLATES.rglob('*.html')):
        for nome in re.findall(r"filename='dist/([^']+)'", template.read_text(encoding='utf-8')):
            pedidos.setdefault(nome, []).append(template.relative_to(RAIZ).as_posix())
    return pedidos


def _copiados_pelo_build():
    scripts = json.loads((RAIZ / 'package.json').read_text(encoding='utf-8'))['scripts']
    caminhos = re.findall(r'node_modules/\S+', scripts.get('copy:vendor', ''))
    return {caminho.rsplit('/', 1)[-1] for caminho in caminhos}


def test_todo_o_asset_pedido_e_gerado():
    pedidos = _pedidos_pelos_templates()
    disponiveis = _copiados_pelo_build() | GERADOS_PELO_CSS

    em_falta = {nome: onde for nome, onde in pedidos.items() if nome not in disponiveis}

    assert not em_falta, (
        "Estes ficheiros são pedidos pelos templates mas nada os põe em "
        f"app/static/dist/: {em_falta}. Acrescente-os ao 'copy:vendor' do "
        "package.json — senão o navegador vai acusar que a biblioteca não existe."
    )


def test_o_build_corre_o_copy_e_o_css():
    # `npm run build` é o comando único que a documentação manda correr.
    scripts = json.loads((RAIZ / 'package.json').read_text(encoding='utf-8'))['scripts']

    assert 'copy:vendor' in scripts['build']
    assert 'build:css' in scripts['build']


def test_os_caminhos_do_copy_vendor_existem_no_pacote_instalado():
    """Sem `npm install` feito, não há o que verificar — o teste salta."""
    scripts = json.loads((RAIZ / 'package.json').read_text(encoding='utf-8'))['scripts']
    caminhos = re.findall(r'node_modules/\S+', scripts.get('copy:vendor', ''))

    if not (RAIZ / 'node_modules').is_dir():
        pytest.skip("node_modules não está instalado")

    em_falta = [c for c in caminhos if not (RAIZ / c).is_file()]

    assert not em_falta, (
        f"O 'copy:vendor' aponta para ficheiros que não existem: {em_falta}. "
        "Um deles mudou de sítio na biblioteca, e o `cp` falha o build inteiro."
    )


def test_o_dockerfile_nao_repete_os_caminhos_das_bibliotecas():
    """
    🐛 Era daqui que vinha a divergência: o Dockerfile sabia os caminhos, o
    package.json não. Agora quem os sabe é só o package.json, e o Dockerfile
    copia a pasta já construída.
    """
    dockerfile = (RAIZ / 'Dockerfile').read_text(encoding='utf-8')

    repetidos = [
        linha.strip() for linha in dockerfile.splitlines()
        if linha.startswith('COPY') and 'node_modules/' in linha
    ]

    assert not repetidos, (
        f"O Dockerfile voltou a copiar bibliotecas por caminho: {repetidos}. "
        "Isso duplica a lista do 'copy:vendor' e as duas vão divergir."
    )
