# app/services/branding.py

"""A logo personalizada do painel: validar, guardar e apagar.

⚠️ **Fica em `CONFIG_DIR/branding/`, não em `app/static/`.** O `static` vive
dentro da imagem Docker e desaparece na atualização seguinte; o `config/` é o
volume que o utilizador monta, e é o único sítio onde um ficheiro enviado
sobrevive.

🛡️ **Só formatos de imagem RASTER, e a validação é pelo CONTEÚDO.** A extensão
do ficheiro é o que quem envia diz que ele é; os primeiros bytes são o que ele
é mesmo. E o SVG é recusado de propósito: é XML, aceita `<script>` dentro, e
esta imagem aparece no cabeçalho de TODAS as páginas do painel — incluindo as
públicas. Um SVG hostil seria XSS armazenado em todo o lado, enviado pela
própria interface de administração.
"""

import logging
import os
from typing import Optional, Tuple

from flask_babel import gettext as _

from ..config import CONFIG_DIR, load_or_create_config, save_app_config

logger = logging.getLogger(__name__)

PASTA = 'branding'

# 2 MB dá para qualquer logo de cabeçalho com folga. O limite existe para o
# volume do utilizador não encher com um envio enganado.
TAMANHO_MAXIMO = 2 * 1024 * 1024

# Assinatura dos primeiros bytes → extensão que o painel usa ao gravar.
# A ordem não importa: cada assinatura é única.
ASSINATURAS = (
    (b'\x89PNG\r\n\x1a\n', 'png', 'image/png'),
    (b'\xff\xd8\xff', 'jpg', 'image/jpeg'),
    (b'GIF87a', 'gif', 'image/gif'),
    (b'GIF89a', 'gif', 'image/gif'),
)


def _pasta() -> str:
    caminho = os.path.join(CONFIG_DIR, PASTA)
    os.makedirs(caminho, exist_ok=True)
    return caminho


def _tipo_real(dados: bytes) -> Optional[Tuple[str, str]]:
    """(extensão, content-type) a partir dos PRIMEIROS BYTES. None se não for imagem."""
    for assinatura, extensao, tipo in ASSINATURAS:
        if dados.startswith(assinatura):
            return extensao, tipo

    # O WEBP não tem assinatura no início: é um contentor RIFF com 'WEBP' no
    # byte 8. Sem esta verificação à parte, um .webp válido seria recusado.
    if dados[:4] == b'RIFF' and dados[8:12] == b'WEBP':
        return 'webp', 'image/webp'
    return None


def logo_atual() -> Optional[str]:
    """O caminho do ficheiro da logo, ou None se não houver (ou tiver sumido)."""
    nome = (load_or_create_config().get('APP_LOGO_FILE') or '').strip()
    if not nome:
        return None

    # 🛡️ O nome é sempre gerado por nós, mas isto é o que o transforma num
    # caminho: `os.path.basename` impede que um config.json editado à mão (ou
    # restaurado de um backup adulterado) leia um ficheiro fora da pasta.
    caminho = os.path.join(_pasta(), os.path.basename(nome))
    return caminho if os.path.isfile(caminho) else None


def guardar_logo(dados: bytes) -> dict:
    """Valida e grava a logo enviada. Devolve a resposta pronta para a API."""
    if not dados:
        return {"success": False, "message": _("Nenhum arquivo enviado."), "status": 400}

    if len(dados) > TAMANHO_MAXIMO:
        return {
            "success": False,
            "message": _("A imagem tem mais de %(limite)s MB.", limite=TAMANHO_MAXIMO // (1024 * 1024)),
            "status": 400,
        }

    tipo = _tipo_real(dados)
    if not tipo:
        return {
            "success": False,
            # A mensagem nomeia o SVG porque é o engano provável: é uma imagem,
            # e é recusado à mesma.
            "message": _("Formato não suportado. Use PNG, JPG, GIF ou WEBP — "
                         "o SVG não é aceite por motivos de segurança."),
            "status": 400,
        }

    extensao, _tipo_mime = tipo
    anterior = logo_atual()
    nome = f"logo.{extensao}"

    try:
        with open(os.path.join(_pasta(), nome), 'wb') as ficheiro:
            ficheiro.write(dados)
    except OSError as e:
        logger.error(f"Não foi possível gravar a logo: {e}", exc_info=True)
        return {"success": False, "message": _("Não foi possível gravar a imagem."), "status": 500}

    config = load_or_create_config()
    config['APP_LOGO_FILE'] = nome
    save_app_config(config)

    # Uma logo nova noutro formato deixaria a antiga na pasta para sempre.
    if anterior and os.path.basename(anterior) != nome:
        _apagar(anterior)

    logger.info(f"Logo do painel atualizada ({nome}, {len(dados)} bytes).")
    return {"success": True, "message": _("Logo atualizada."), "status": 200}


def remover_logo() -> dict:
    """Volta ao símbolo padrão do painel."""
    caminho = logo_atual()
    if caminho:
        _apagar(caminho)

    config = load_or_create_config()
    if config.get('APP_LOGO_FILE'):
        config['APP_LOGO_FILE'] = ''
        save_app_config(config)

    logger.info("Logo do painel removida: volta o símbolo padrão.")
    return {"success": True, "message": _("Logo removida."), "status": 200}


def _apagar(caminho: str) -> None:
    try:
        os.remove(caminho)
    except OSError as e:
        # Não é motivo para falhar: o que manda é o config, e esse já mudou.
        logger.warning(f"Não foi possível apagar o ficheiro da logo {caminho}: {e}")
