"""Permissões dos ficheiros que guardam segredos.

🛡️ O painel nunca escolheu as permissões do que escreve: o `config.json`, as
bases de dados SQLite e os ZIPs de backup nasciam com o que o umask do processo
ditasse — tipicamente 0644, legível por qualquer conta do host ou de outro
contentor que monte o mesmo volume.

O que está nesses ficheiros não é pouco: o `config.json` guarda em texto puro a
SECRET_KEY, o token do Plex, a chave de API do Jellyfin (que é de
administrador do servidor de média) e as credenciais dos três gateways de
pagamento; o ZIP de backup leva o `config.json` INTEIRO mais as bases de dados.
Um desses ficheiros lido por quem não devia é o painel todo.

⚠️ **Falhar aqui não pode derrubar nada.** Em sistemas de ficheiros que não têm
permissões POSIX (um volume montado de Windows, por exemplo) o `chmod` levanta
ou não tem efeito — e um painel que se recusa a arrancar por causa disso é
muito pior do que um ficheiro com permissões largas. Por isso avisa-se e
segue-se em frente.
"""

import logging
import os
import stat

logger = logging.getLogger(__name__)

# Dono lê e escreve; mais ninguém vê nada.
MODO_PRIVADO = 0o600


def proteger_ficheiro(caminho, modo=MODO_PRIVADO):
    """Restringe as permissões de um ficheiro. Devolve True se ficou aplicado.

    Silenciosa quando o ficheiro não existe: quem chama isto a seguir a uma
    escrita não tem de se preocupar com o caso em que ela falhou.
    """
    if not caminho or not os.path.isfile(caminho):
        return False

    try:
        atual = stat.S_IMODE(os.stat(caminho).st_mode)
        if atual == modo:
            return True
        os.chmod(caminho, modo)
        return True
    except OSError as e:
        logger.warning(
            f"Não foi possível restringir as permissões de '{caminho}': {e}. "
            "Verifique manualmente quem consegue ler este ficheiro."
        )
        return False


def proteger_ficheiros(*caminhos, modo=MODO_PRIVADO):
    """`proteger_ficheiro` para vários caminhos. Devolve quantos ficaram aplicados."""
    return sum(1 for caminho in caminhos if proteger_ficheiro(caminho, modo))


def proteger_base_de_dados(caminho):
    """Protege um ficheiro SQLite e os companheiros que o modo WAL cria.

    ⚠️ **O `-wal` não é um detalhe.** Em WAL, as escritas mais recentes vivem
    nele e não no ficheiro principal: proteger só o `.db` deixava à vista tudo
    o que ainda não tinha sido integrado. O `-shm` é o índice partilhado desse
    diário.
    """
    return proteger_ficheiros(
        caminho, f"{caminho}-wal", f"{caminho}-shm", modo=MODO_PRIVADO
    )
