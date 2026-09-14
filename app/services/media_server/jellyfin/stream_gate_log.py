# app/services/media_server/jellyfin/stream_gate_log.py

"""Os bloqueios do plugin StreamLimiter, lidos do log do Jellyfin.

O plugin recusa o pedido HTTP da mídia antes de servir um byte — é por isso que
funciona onde a ordem de parar falha (ver `stream_limit.py`). Mas quem bloqueia
é ELE, dentro do processo do Jellyfin: o painel não participa, e por isso não
sabia nada desses cortes. A "Auditoria de Cortes" mostrava só os seus.

🛡️ **O log é a única fonte.** O plugin não expõe nenhuma rota de eventos: a API
dele é `POST /StreamLimit/SetUserStreamLimit` e `GET /StreamLimit/GetAllStreamLimits`,
ambas sobre limites, nenhuma sobre o que já aconteceu. O que resta é a linha que
ele escreve:

    [2026-09-12 20:23:28.756 -03:00] [INF] [97] Jellyfin.Plugin.StreamLimit.Gate.StreamGateFilter: Stream gate denied playback negotiation. User: 44874bdd-d626-4037-b45f-671fa20ca85b, device: "7e0fa1c8...", limit: 1

⚠️ **A hora vem no fuso do SERVIDOR**, com o deslocamento à frente
(`-03:00`). Guardar o "20:23" sem o converter punha o corte três horas no
futuro para quem lê o painel — e, pior, à frente da marca de água, que faria a
importação seguinte ignorar tudo o que viesse depois.

⚠️ **Só se lê o que ainda não se leu.** O ficheiro do dia cresce e é pedido de
poucos em poucos minutos: pede-se a partir do ponto onde a leitura anterior
parou (`Range`). Se o servidor ignorar o pedido parcial e mandar o ficheiro
inteiro, corta-se aqui — o resultado é o mesmo, muda só o que atravessa a rede.
E o ponto onde se retoma é sempre um FIM DE LINHA: retomar a meio de uma linha
dava uma linha partida que nunca seria reconhecida.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ....extensions import cache
from ....utils.log_formatting import describe
from .identity import chave_de

logger = logging.getLogger(__name__)

# A razão com que estes cortes ficam na auditoria. É vocabulário do painel — a
# interface traduz o código para texto (ver `dashboard_modules/formatters.js`) —
# e é também a marca de água: o último registo com esta razão diz até onde a
# importação anterior chegou.
RAZAO_DO_CORTE = 'plugin_limit_blocked'

# O nome da classe do plugin que escreve a linha. É o que a distingue de todo o
# resto do log, e é estável: faz parte do namespace do próprio plugin.
ORIGEM = 'StreamGateFilter'

# A linha que interessa. `denied` é exigido de propósito: se um dia o filtro
# passar a registar também o que DEIXA passar, não se pode tomar uma coisa pela
# outra — e não reconhecer uma linha nova é melhor do que inventar um corte.
MARCA_DE_RECUSA = 'denied'

CABECALHO = re.compile(r'^\[(?P<quando>[^\]]+)\]')
UTILIZADOR = re.compile(r'[Uu]ser:\s*"?(?P<valor>[0-9a-fA-F-]{32,36})"?')
APARELHO = re.compile(r'[Dd]evice:\s*"(?P<valor>[^"]*)"')
LIMITE = re.compile(r'[Ll]imit:\s*(?P<valor>\d+)')

# Quanto se traz de uma vez quando não há ponto de partida (primeira leitura, ou
# o ficheiro rodou): só o fim interessa, o resto é história que já passou.
PRIMEIRA_LEITURA_BYTES = 256 * 1024

# Onde a leitura anterior parou. É cache de propósito: perdê-la faz reler o
# ficheiro, não duplicar cortes — quem impede a repetição é a marca de água da
# auditoria, que vem da base de dados.
CHAVE_POSICAO = 'jellyfin_stream_gate_posicao'
POSICAO_VALIDA_SEGUNDOS = 7 * 24 * 3600


class BloqueioDoServidor:
    """Um corte que o servidor deu sozinho, já traduzido."""

    __slots__ = ('quando', 'user_id', 'aparelho', 'limite')

    def __init__(self, quando: datetime, user_id: str, aparelho: str, limite: int):
        self.quando = quando
        self.user_id = user_id
        self.aparelho = aparelho
        self.limite = limite

    def __repr__(self):
        return f"<BloqueioDoServidor {self.user_id} {self.quando.isoformat()} limite={self.limite}>"


def _para_utc(texto: str) -> Optional[datetime]:
    """A hora do log (no fuso do servidor) em UTC.

    O formato do Serilog que o Jellyfin usa é `2026-09-12 20:23:28.756 -03:00`.
    Sem deslocamento, assume-se UTC — é o melhor palpite possível e não desfaz
    a ordem das linhas entre si.
    """
    texto = (texto or '').strip()
    for formato in ('%Y-%m-%d %H:%M:%S.%f %z', '%Y-%m-%d %H:%M:%S %z',
                    '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            momento = datetime.strptime(texto, formato)
        except ValueError:
            continue
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=timezone.utc)
        return momento.astimezone(timezone.utc)

    logger.debug(f"Data ilegível numa linha do log do Jellyfin: {texto!r}")
    return None


class JellyfinStreamGateLog:
    """Lê do log do servidor os bloqueios que o plugin deu."""

    def __init__(self, connection):
        self.conn = connection

    def ler_bloqueios(self) -> List[BloqueioDoServidor]:
        """Os bloqueios que apareceram desde a última leitura.

        Nunca levanta: um log ilegível não pode partir o job que o lê. Devolve
        uma lista vazia e deixa a explicação no log do painel.
        """
        if not self.conn.connected:
            return []

        try:
            ficheiro = self._ficheiro_atual()
            if not ficheiro:
                return []

            dados, posicao = self._ler_novidades(ficheiro)
            if not dados:
                return []

            bloqueios, consumidos = self._interpretar(dados)
            self._guardar_posicao(ficheiro['Name'], posicao + consumidos)
            return bloqueios
        except Exception as e:
            logger.warning(f"Não foi possível ler os bloqueios no log do Jellyfin: {describe(e)}")
            return []

    # =========================================================================
    # O FICHEIRO
    # =========================================================================

    def _ficheiro_atual(self) -> Optional[Dict[str, Any]]:
        """O log mais recente do servidor.

        O Jellyfin roda o ficheiro todos os dias (`log_20260912.log`), por isso
        o alvo muda sozinho — e quando muda, a posição guardada deixa de servir.
        """
        ficheiros = self.conn.api.get('/System/Logs') or []
        candidatos = [f for f in ficheiros if isinstance(f, dict) and f.get('Name')]
        if not candidatos:
            return None

        return max(candidatos, key=lambda f: (f.get('DateModified') or '', f.get('Name') or ''))

    def _ler_novidades(self, ficheiro: Dict[str, Any]) -> Tuple[bytes, int]:
        """Os bytes ainda não lidos, e a posição em que começam."""
        posicao = self._posicao_guardada(ficheiro['Name'])
        tamanho = int(ficheiro.get('Size') or 0)

        if posicao is None:
            # Primeira leitura deste ficheiro: só o fim interessa. Importar o
            # dia inteiro encheria a auditoria de cortes antigos de uma vez.
            posicao = max(0, tamanho - PRIMEIRA_LEITURA_BYTES)
        elif tamanho and tamanho < posicao:
            # O ficheiro encolheu: foi rodado ou truncado por baixo de nós.
            logger.debug("O log do Jellyfin encolheu: a leitura recomeça do início.")
            posicao = 0
        elif tamanho and tamanho == posicao:
            return b'', posicao

        dados, parcial = self.conn.api.get_bytes(
            '/System/Logs/Log', params={'name': ficheiro['Name']},
            headers={'Range': f'bytes={posicao}-'} if posicao else None,
        )
        if dados is None:
            return b'', posicao

        # O servidor pode ignorar o `Range` e mandar tudo: o corte é o mesmo,
        # feito aqui em vez de lá.
        if not parcial and posicao:
            dados = dados[posicao:]

        return dados, posicao

    # =========================================================================
    # AS LINHAS
    # =========================================================================

    def _interpretar(self, dados: bytes) -> Tuple[List[BloqueioDoServidor], int]:
        """Os bloqueios encontrados, e quantos bytes ficaram consumidos.

        Só se consome até ao último fim de linha: o resto é uma linha ainda a
        ser escrita, que fica para a leitura seguinte inteira.
        """
        fim = dados.rfind(b'\n')
        if fim < 0:
            return [], 0

        texto = dados[:fim + 1].decode('utf-8', errors='replace')
        bloqueios = []
        for linha in texto.splitlines():
            if ORIGEM not in linha:
                continue
            bloqueio = self._traduzir(linha)
            if bloqueio is not None:
                bloqueios.append(bloqueio)

        return bloqueios, fim + 1

    def _traduzir(self, linha: str) -> Optional[BloqueioDoServidor]:
        """Uma linha do plugin como um corte, ou None se não for um corte."""
        if MARCA_DE_RECUSA not in linha.lower():
            return None

        cabecalho = CABECALHO.match(linha)
        utilizador = UTILIZADOR.search(linha)
        if not cabecalho or not utilizador:
            # Vale a pena o aviso: é uma linha do plugin que mudou de forma, e
            # a partir daqui os cortes deixariam de aparecer em silêncio.
            logger.debug(f"Linha do StreamGateFilter que não foi possível ler: {linha[:200]!r}")
            return None

        quando = _para_utc(cabecalho.group('quando'))
        if quando is None:
            return None

        aparelho = APARELHO.search(linha)
        limite = LIMITE.search(linha)
        return BloqueioDoServidor(
            quando=quando,
            user_id=chave_de(utilizador.group('valor')),
            aparelho=(aparelho.group('valor') if aparelho else '').strip(),
            limite=int(limite.group('valor')) if limite else 0,
        )

    # =========================================================================
    # OS APARELHOS
    # =========================================================================

    def nomes_de_aparelhos(self) -> Dict[str, str]:
        """`id do aparelho` → o nome que a pessoa reconhece.

        O log identifica o aparelho pelo id que o CLIENTE declara, que não diz
        nada a quem lê a auditoria. Quando o servidor não o conhece, fica vazio:
        um id de cinquenta caracteres no lugar do nome é pior do que nada.
        """
        try:
            resposta = self.conn.api.get('/Devices') or {}
        except Exception as e:
            logger.debug(f"Não foi possível listar os aparelhos do Jellyfin: {describe(e)}")
            return {}

        itens = resposta.get('Items') if isinstance(resposta, dict) else resposta
        nomes = {}
        for bruto in itens or []:
            identificador = chave_de(bruto.get('Id'))
            if not identificador:
                continue
            nomes[identificador] = (bruto.get('CustomName') or bruto.get('Name')
                                    or bruto.get('AppName') or '')
        return nomes

    # =========================================================================
    # A POSIÇÃO
    # =========================================================================

    def _chave(self) -> str:
        return f"{CHAVE_POSICAO}_{self.conn.api.base_url}"

    def _posicao_guardada(self, nome: str) -> Optional[int]:
        guardado = cache.get(self._chave())
        if isinstance(guardado, dict) and guardado.get('nome') == nome:
            return int(guardado.get('posicao') or 0)
        return None

    def _guardar_posicao(self, nome: str, posicao: int) -> None:
        cache.set(self._chave(), {'nome': nome, 'posicao': int(posicao)},
                  timeout=POSICAO_VALIDA_SEGUNDOS)
