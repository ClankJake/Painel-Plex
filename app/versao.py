# app/versao.py

"""A versão deste painel, e como se compara com outra.

⚠️ **O painel não sabia dizer que versão era.** As entregas são feitas por
RELEASES do GitHub (`v22.3`, `v22.2`, ...) e nada dentro do código o registava:
quem abria uma página não tinha como saber o que estava a correr, e quem
respondia a um problema tinha de adivinhar pela data da imagem.

Esta constante é a única fonte da verdade, e é atualizada À MÃO ao publicar uma
release — como o `package.json`, que diz a mesma coisa para o lado do frontend.
Um teste compara os dois (`tests/test_aba_sobre.py`): duas versões que divergem
são piores do que nenhuma, porque uma delas está a mentir.
"""

VERSAO = "22.3"

# O repositório de onde saem as releases. Fica AQUI e não no config.json de
# propósito: é ele que diz a que endereço o painel vai bater de dentro da rede,
# e pô-lo na interface faria de uma sessão de administrador tomada uma forma de
# apontar o painel a outro servidor — a mesma razão da `ALLOWED_IMAGE_HOSTS` e
# da lista de serviços de push.
REPOSITORIO = "ClankJake/Painel-Plex"

URL_DAS_RELEASES = f"https://github.com/{REPOSITORIO}/releases"


def normalizar(versao):
    """`v22.3` -> `22.3`. As tags do repositório levam o 'v', a constante não."""
    return str(versao or "").strip().lstrip("vV").strip()


def _partes(versao):
    """Os números de uma versão, para se poderem comparar como números.

    ⚠️ **Comparar versões como TEXTO dá a resposta errada exatamente quando
    ela importa**: `'22.10' < '22.3'` é verdade em ordem alfabética, por isso o
    painel diria "está atualizado" no dia em que saísse a 22.10. O que não for
    número (um `-beta`, um `rc1`) é ignorado para efeito de ordem — não há
    forma de o ordenar sem inventar regras que ninguém aqui escreveu.
    """
    numeros = []
    for pedaco in normalizar(versao).split("."):
        digitos = ""
        for caractere in pedaco:
            if not caractere.isdigit():
                break
            digitos += caractere
        numeros.append(int(digitos) if digitos else 0)
    return numeros


def comparar(uma, outra):
    """-1 se `uma` for anterior, 0 se iguais, 1 se posterior."""
    a, b = _partes(uma), _partes(outra)
    # ⚠️ `22.3` e `22.3.0` são a MESMA versão: quem tem menos pedaços é
    # completado com zeros, senão a de três pedaços aparecia sempre à frente.
    tamanho = max(len(a), len(b))
    a += [0] * (tamanho - len(a))
    b += [0] * (tamanho - len(b))
    return (a > b) - (a < b)


def ha_atualizacao(instalada, publicada):
    """Há uma versão nova para instalar?

    ⚠️ Uma versão publicada que não se consegue ler (uma tag com outro formato,
    um campo vazio) NÃO é uma atualização. Anunciar uma que não existe manda
    alguém procurar o que não há; ficar calado só adia uma boa notícia.
    """
    if not normalizar(publicada) or not normalizar(instalada):
        return False
    return comparar(publicada, instalada) > 0
