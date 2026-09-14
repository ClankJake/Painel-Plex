# app/utils/image_proxy.py

"""Construção dos URLs que passam pelo proxy de imagens do painel.

O proxy (`/image/?source=...`) recebe um payload em base64 com a forma
`<prefixo>:<caminho>`. Quem decide o prefixo é o backend do servidor de média
(ver `MediaServerBackend.IMAGE_SOURCES`); esta função trata apenas da parte
mecânica, que era copiada em três sítios.
"""

import base64
from typing import Optional

from flask import url_for


def proxied_image_url(source_payload: Optional[str]) -> Optional[str]:
    """Transforma `"plex:/library/..."` no URL do proxy.

    Devolve None quando não há payload, para quem chama poder simplesmente
    omitir a imagem.
    """
    if not source_payload:
        return None

    b64 = base64.urlsafe_b64encode(source_payload.encode('utf-8')).decode('utf-8')
    try:
        return url_for('image.proxy_image', source=b64)
    except RuntimeError:
        # Fora de um contexto de pedido (um job do agendador, a tarefa de fundo
        # dos sockets): o caminho é fixo, por isso monta-se à mão.
        return f"/image/?source={b64}"
