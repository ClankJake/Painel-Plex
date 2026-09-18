# app/services/atualizacoes.py

"""A última release publicada no GitHub, para a aba "Sobre".

⚠️ **É a única coisa do painel que depende de um serviço de fora sem ser
configurada por ninguém**, e isso obriga a três regras:

- 🛡️ **o endereço é FIXO** (`versao.REPOSITORIO`). O painel faz este pedido de
  DENTRO da rede onde corre; um endereço que viesse do config.json fazia de uma
  sessão de administrador tomada uma forma de o apontar a um serviço interno —
  é o mesmo SSRF que a lista de serviços de push e a de hosts de imagem já
  existem para fechar;
- ⚠️ **falhar é normal e não é um erro do painel.** Sem rede, atrás de um
  proxy, ou com o teto de pedidos do GitHub esgotado (60 por hora por endereço,
  porque não há token), isto devolve `None` — e quem chama diz "não foi
  possível verificar", que é diferente de "está atualizado";
- ⚠️ **a falha NÃO fica em cache.** Guardar um "não sei" durante seis horas
  deixava o painel a dizer que não consegue verificar muito depois de a rede
  ter voltado. É a mesma regra da deteção dos plugins do Jellyfin: não saber
  não é o mesmo que não existir.
"""

import logging
import time

import requests

from .. import versao as versao_do_painel

logger = logging.getLogger(__name__)

URL_DA_API = f"https://api.github.com/repos/{versao_do_painel.REPOSITORIO}/releases/latest"

# ⚠️ Isto corre DENTRO de um pedido HTTP, num painel com um worker gevent. O
# timeout é curto de propósito: a aba tem de abrir mesmo com o GitHub lento, e
# quem espera é quem clicou.
TEMPO_LIMITE = 8

# Seis horas. Uma release não sai de hora a hora, e o teto do GitHub é por
# ENDEREÇO: num painel atrás do mesmo IP que outros, gastá-lo a verificar
# versões seria gastá-lo para nada.
VALIDADE_DA_CACHE = 6 * 3600

# A cache vive no PROCESSO e não em disco: o painel corre com um worker de
# propósito, e o que aqui se guarda não vale um ficheiro a mais dentro do ZIP
# de backup.
_cache = {"em": 0.0, "release": None}


def _pedir():
    """Fala com o GitHub. Devolve o dicionário da release, ou `None`."""
    resposta = requests.get(
        URL_DA_API,
        timeout=TEMPO_LIMITE,
        headers={
            # A API do GitHub recusa pedidos sem User-Agent, e a versão vai lá
            # para um dia se poder ver quantos painéis antigos ainda perguntam.
            "User-Agent": f"Painel-Plex/{versao_do_painel.VERSAO}",
            "Accept": "application/vnd.github+json",
        },
    )
    resposta.raise_for_status()
    dados = resposta.json()

    etiqueta = dados.get("tag_name") or dados.get("name") or ""
    if not etiqueta:
        # Uma resposta sem etiqueta não diz que versão é: é o mesmo que não ter
        # resposta nenhuma, e tratá-la como tal evita anunciar um vazio.
        logger.warning("A última release do GitHub veio sem tag_name.")
        return None

    return {
        "versao": versao_do_painel.normalizar(etiqueta),
        "etiqueta": str(etiqueta),
        "nome": str(dados.get("name") or etiqueta),
        # 🛡️ O endereço é CONFIRMADO, não copiado: é um valor que vem de fora e
        # vai parar a um `href` na página de um administrador. Se não for uma
        # página do GitHub, usa-se a do repositório, que é sempre verdadeira.
        "url": _endereco_seguro(dados.get("html_url")),
        "publicada_em": str(dados.get("published_at") or ""),
    }


def _endereco_seguro(url):
    url = str(url or "")
    if url.startswith("https://github.com/"):
        return url
    return versao_do_painel.URL_DAS_RELEASES


def ultima_release(forcar=False):
    """A última release publicada, ou `None` quando não se conseguiu saber.

    `forcar` salta a cache — é o que o botão "Verificar de novo" da aba usa.
    """
    agora = time.time()
    if not forcar and _cache["release"] and (agora - _cache["em"]) < VALIDADE_DA_CACHE:
        return _cache["release"]

    try:
        release = _pedir()
    except Exception as e:
        # ⚠️ Um WARNING e não um ERROR: não haver rede de saída é uma forma
        # legítima de correr este painel, e um ERROR por cada visita à aba
        # "Sobre" empurrava para fora do log o que interessa.
        logger.warning(f"Não foi possível consultar as releases no GitHub: {e}")
        return None

    if release:
        _cache.update({"em": agora, "release": release})
    return release


def limpar_cache():
    """Esquece o que foi lido. Existe para os testes não se contaminarem."""
    _cache.update({"em": 0.0, "release": None})
