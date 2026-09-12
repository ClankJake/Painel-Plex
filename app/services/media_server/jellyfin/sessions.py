# app/services/media_server/jellyfin/sessions.py

"""Leitura e encerramento das sessões do Jellyfin.

Cumpre o mesmo contrato `SessionsProvider` que o Plex, para que o motor de
streams não saiba com qual dos dois está a falar.

Duas diferenças de vocabulário que vale a pena ter presentes:

* O Jellyfin conta tempo em **ticks** — 10 000 000 por segundo. O painel
  trabalha em milissegundos (herança do Plex), por isso a conversão é feita
  aqui, à entrada, e não espalhada por quem consome.
* O comando de paragem não leva um motivo. Para o utilizador perceber porque
  foi cortado, envia-se primeiro uma mensagem ao cliente e só depois o
  `Stop` — que é o mais próximo do `reason` do Plex.
"""

import logging
from typing import List, Optional, Tuple

from ....utils.identity import normalize_user_id
from ....utils.log_formatting import describe
from ..base import MediaSession
from .api_client import JellyfinApiError

logger = logging.getLogger(__name__)

# O Jellyfin mede tempo em ticks de 100 nanossegundos.
TICKS_POR_MILISSEGUNDO = 10_000


def ticks_para_ms(ticks) -> int:
    try:
        return int((ticks or 0) // TICKS_POR_MILISSEGUNDO)
    except (TypeError, ValueError):
        return 0


def plataforma_de(cliente: str, dispositivo: str, tipo: str) -> str:
    """Classe de ícone a partir do que o Jellyfin diz sobre o cliente."""
    texto = f"{cliente or ''} {dispositivo or ''} {tipo or ''}".lower()

    # ⚠️ A ORDEM IMPORTA, pela mesma razão que no Plex: as verificações são por
    # substring e 'chrome' está contido em 'chromecast'.
    # 🐛 Aqui estava também `or 'cast' in texto`, que apanhava por substring
    # qualquer cliente ou aparelho com essas quatro letras no nome (um
    # "Podcast", um aparelho chamado "Cast Room"). Um falso Chromecast fazia o
    # filtro de sessões duplicadas descartar a outra sessão do utilizador.
    if 'chromecast' in texto or 'google cast' in texto: return 'chromecast' 

    if 'chrome' in texto: return 'chrome'
    if 'safari' in texto: return 'safari'
    if 'firefox' in texto: return 'firefox'
    if 'edge' in texto: return 'msedge'
    if 'opera' in texto: return 'opera'

    if 'android' in texto: return 'android'
    if 'roku' in texto: return 'roku'
    if 'tvos' in texto or 'apple tv' in texto: return 'atv'
    if 'ios' in texto or 'iphone' in texto or 'ipad' in texto: return 'ios'
    if 'playstation' in texto or 'ps4' in texto or 'ps5' in texto: return 'playstation'
    if 'xbox' in texto: return 'xbox'
    if 'samsung' in texto or 'tizen' in texto: return 'samsung'
    if 'webos' in texto or texto.strip().startswith('lg'): return 'lg'
    if 'kodi' in texto: return 'kodi'
    if 'dlna' in texto: return 'dlna'

    if 'mac' in texto: return 'macos'
    if 'windows' in texto: return 'windows'
    if 'linux' in texto: return 'linux'

    # 🐛 Isto devolvia 'plex': um cliente do Jellyfin ficava com o logótipo do
    # PLEX ao lado do nome. O catálogo de ícones veio do painel original e não
    # tinha nenhum do Jellyfin — agora tem.
    if 'jellyfin' in texto or 'findroid' in texto or 'infuse' in texto: return 'jellyfin'

    return 'default'


class JellyfinSessionsProvider:
    """Cumpre o contrato `SessionsProvider` para o Jellyfin."""


    def __init__(self, connection):
        self.conn = connection

    # =========================================================================
    # LIGAÇÃO
    # =========================================================================

    def is_connected(self) -> bool:
        return bool(self.conn and self.conn.connected)

    def reconnect(self) -> Tuple[bool, str]:
        return self.conn.reload(from_job=True)

    def get_owner_id(self) -> Optional[str]:
        """No Jellyfin não há um "dono" isento como no Plex.

        Um administrador é apenas um utilizador com `IsAdministrator`, e o
        painel não deve isentá-lo dos limites automaticamente: quem administra
        o Jellyfin costuma ser a mesma pessoa que administra o painel, e nesse
        caso a isenção configura-se no perfil.
        """
        return None

    def user_thumb_source(self, raw_thumb: Optional[str]) -> Optional[str]:
        """O avatar é servido pelo próprio Jellyfin, com a chave de API."""
        if not raw_thumb or '/image/' in raw_thumb:
            return None
        return f"jellyfin:{raw_thumb}"

    # =========================================================================
    # LEITURA DAS SESSÕES
    # =========================================================================

    def list_sessions(self) -> List[MediaSession]:
        if not self.is_connected():
            return []

        # 🐛 Isto pedia `activeWithinSeconds=60`, um filtro que inventei sem
        # nada na API que o justificasse. Uma reprodução cujo cliente demore
        # mais do que isso a dar sinal de vida (acontece com o leitor integrado
        # a reproduzir diretamente, sem transcodificação) desaparecia da vista
        # do painel — e o que o painel não vê, não conta nem corta.
        #
        # Quem decide o que é uma sessão viva é o servidor. Ao painel basta o
        # `NowPlayingItem`: quem só está ligado, sem nada a tocar, não o traz.
        sessoes = self.conn.api.get('/Sessions') or []

        a_reproduzir = [s for s in sessoes if s.get('NowPlayingItem')]

        if len(sessoes) != len(a_reproduzir):
            logger.debug(
                "Jellyfin: %s sessão(ões) ligadas, %s a reproduzir.",
                len(sessoes), len(a_reproduzir),
            )

        return [self._traduzir(s) for s in a_reproduzir]

    def _traduzir(self, sessao: dict) -> MediaSession:
        item = sessao.get('NowPlayingItem') or {}
        estado_reproducao = sessao.get('PlayState') or {}

        posicao = ticks_para_ms(estado_reproducao.get('PositionTicks'))
        duracao = ticks_para_ms(item.get('RunTimeTicks'))

        progresso = 0.0
        if duracao and posicao:
            progresso = max(0.0, min(100.0, (posicao / duracao) * 100))

        tipo = str(item.get('Type', 'unknown')).lower()
        cliente = sessao.get('Client') or ''
        dispositivo = sessao.get('DeviceName') or ''

        return MediaSession(
            user_id=normalize_user_id(sessao.get('UserId')),
            username_fallback=sessao.get('UserName') or 'Desconhecido',
            # O Jellyfin não associa email às sessões; o painel usa o do perfil.
            user_email='',
            session_key=str(sessao.get('Id') or ''),
            playback_key=self._chave_da_reproducao(sessao, item),
            media_title=self._titulo_para_registo(item, tipo),
            title=self._titulo(item, tipo),
            subtitle=self._subtitulo(item, tipo),
            media_type='episode' if tipo == 'episode' else tipo,
            state='paused' if estado_reproducao.get('IsPaused') else 'playing',
            platform=plataforma_de(cliente, dispositivo, sessao.get('DeviceType')),
            player=f"{cliente} - {dispositivo}" if cliente and dispositivo else cliente or dispositivo or "Desconhecido",
            progress=round(progresso, 2),
            view_offset=posicao,
            duration=duracao,
            artwork_source=self._capa(item),
            stream_details=self._detalhes_do_stream(sessao),
            raw=sessao,
        )

    def _chave_da_reproducao(self, sessao, item) -> str:
        """O que distingue ESTA reprodução das seguintes no mesmo aparelho.

        🐛 O `Id` da sessão do Jellyfin é do aparelho: parar o filme e voltar a
        começá-lo devolve o MESMO id. Como o painel guardava "já cortei esta"
        por esse id, quem recomeçasse logo a seguir a um corte ficava sem ser
        incomodado durante toda a janela do anti-spam.

        O `PlaylistItemId` muda a cada reprodução quando existe; quando não
        existe, o item que está a tocar pelo menos separa filmes diferentes.
        """
        partes = [
            str(sessao.get('Id') or ''),
            str(sessao.get('PlaylistItemId') or ''),
            str(item.get('Id') or ''),
        ]
        return ':'.join(parte for parte in partes if parte)

    def _titulo(self, item, tipo) -> str:
        if tipo == 'episode':
            return item.get('SeriesName') or item.get('Name') or 'Desconhecido'
        return item.get('Name') or 'Desconhecido'

    def _subtitulo(self, item, tipo) -> str:
        if tipo != 'episode':
            return str(item.get('ProductionYear') or '')

        temporada = item.get('ParentIndexNumber')
        episodio = item.get('IndexNumber')
        if temporada is not None and episodio is not None:
            return f"S{int(temporada):02d} · E{int(episodio):02d} - {item.get('Name', '')}"
        return item.get('Name', '')

    def _titulo_para_registo(self, item, tipo) -> str:
        """O título composto que vai para os logs e para a auditoria."""
        nome = item.get('Name') or 'Desconhecido'
        if tipo != 'episode':
            return nome

        serie = item.get('SeriesName')
        if not serie:
            return nome

        titulo = serie
        temporada = item.get('ParentIndexNumber')
        episodio = item.get('IndexNumber')
        if temporada is not None and episodio is not None:
            try:
                titulo += f" S{int(temporada):02d}E{int(episodio):02d}"
            except (ValueError, TypeError):
                pass
        return f"{titulo} - {nome}" if nome else titulo

    def _capa(self, item) -> Optional[str]:
        """O identificador da imagem, no vocabulário do proxy deste backend.

        Prefere-se a capa da SÉRIE num episódio (é a que o painel mostra), com
        recurso ao próprio item quando não existe.
        """
        etiquetas = item.get('ImageTags') or {}
        if etiquetas.get('Primary') and item.get('Id'):
            return f"jellyfin:/Items/{item['Id']}/Images/Primary?tag={etiquetas['Primary']}"

        if item.get('SeriesPrimaryImageTag') and item.get('SeriesId'):
            return f"jellyfin:/Items/{item['SeriesId']}/Images/Primary?tag={item['SeriesPrimaryImageTag']}"

        if item.get('Id'):
            return f"jellyfin:/Items/{item['Id']}/Images/Primary"
        return None

    def _detalhes_do_stream(self, sessao) -> dict:
        transcode = sessao.get('TranscodingInfo') or {}
        a_transcodificar = bool(transcode)

        video_directo = transcode.get('IsVideoDirect')
        audio_directo = transcode.get('IsAudioDirect')

        return {
            "is_transcoding": a_transcodificar,
            "stream": "Transcode" if a_transcodificar else "Direct Play",
            "video_decision": "Direct Play" if (not a_transcodificar or video_directo) else "Transcode",
            "audio_decision": "Direct Play" if (not a_transcodificar or audio_directo) else "Transcode",
            "video_codec": str(transcode.get('VideoCodec') or 'N/A').upper(),
            "audio_codec": str(transcode.get('AudioCodec') or 'N/A').upper(),
            "container": str(transcode.get('Container') or 'N/A').upper(),
            "video_resolution": f"{transcode['Height']}p" if transcode.get('Height') else "N/A",
            "transcode_speed": transcode.get('TranscodingFramerate'),
            "transcode_progress": int(transcode['CompletionPercentage']) if transcode.get('CompletionPercentage') else None,
        }

    def deduplicate_sessions(self, sessions):
        """O Jellyfin não duplica uma reprodução: a lista vem intacta.

        🐛 Isto não é uma não-implementação, é a correção de um bug real. Quem
        apenas COMANDA outro aparelho (o "cast" do Jellyfin é controlo remoto de
        outro cliente) aparece em `/Sessions` sem `NowPlayingItem`, e essas
        sessões já são descartadas em `list_sessions`. O que resta são
        reproduções verdadeiras, cada uma no seu aparelho.

        Enquanto o motor de streams aplicava aqui o filtro de Cast do Plex — que
        funde duas sessões do mesmo utilizador com o mesmo título — um
        utilizador a ver a MESMA mídia em dois aparelhos contava como uma tela
        só, e o limite nunca era aplicado.
        """
        return list(sessions)

    # =========================================================================
    # ENCERRAMENTO
    # =========================================================================

    def terminate(self, session: MediaSession, reason: str) -> bool:
        if not session.session_key:
            return False

        # 🔍 Um cliente que não aceita comandos nunca vai obedecer. Dizê-lo é
        # melhor do que ficar a tentar em silêncio: é o caso do leitor
        # integrado da aplicação Android, que reproduz mas não escuta.
        bruta = session.raw if isinstance(session.raw, dict) else {}
        if bruta.get('SupportsMediaControl') is False:
            logger.warning(
                "A sessão %s (%s) declara não aceitar comandos de reprodução: a ordem de "
                "parar vai ser enviada na mesma, mas o cliente pode ignorá-la.",
                session.session_key, session.player,
            )

        # O comando de paragem do Jellyfin não leva motivo. Enviamos primeiro a
        # mensagem para que o utilizador perceba porque foi cortado — se ela
        # falhar, o corte faz-se na mesma: o motivo é um extra, não a operação.
        try:
            self.conn.api.post(
                f'/Sessions/{session.session_key}/Message',
                json={'Header': 'Painel', 'Text': str(reason), 'TimeoutMs': 8000},
            )
        except Exception as e:
            logger.debug(f"Não foi possível avisar a sessão {session.session_key}: {describe(e)}")

        try:
            self.conn.api.post(f'/Sessions/{session.session_key}/Playing/Stop')
            return True
        except JellyfinApiError as e:
            # A sessão já não existe: para efeitos práticos está encerrada.
            if e.status_code == 404:
                return True
            # 🐛 Isto devolvia True em TODOS os casos, incluindo quando o
            # servidor recusava a ordem. Quem chama trata True como "feito" e
            # não volta a tentar — a recusa desaparecia sem deixar rasto.
            logger.warning(
                "O Jellyfin recusou a ordem de parar a sessão %s: %s",
                session.session_key, describe(e),
            )
            return False
        except Exception as e:
            logger.warning(
                "Falha ao mandar parar a sessão %s: %s", session.session_key, describe(e)
            )
            return False

    def _aparelho_conhecido(self, device_id: str) -> Optional[str]:
        """O `Id` do aparelho tal como o servidor o LISTA, ou None.

        🐛 O `DeviceId` que vem na sessão é o que o CLIENTE diz ser, e nem
        sempre corresponde a um aparelho registado — o `DELETE /Devices` com um
        id que o servidor não conhece responde 400 com um corpo vazio, que não
        diz nada a quem lê o log. Confirmar na lista real transforma isso numa
        mensagem que se percebe, e evita pedidos condenados a falhar.
        """
        try:
            resposta = self.conn.api.get('/Devices')
        except Exception as e:
            logger.warning(f"Não foi possível listar os aparelhos do Jellyfin: {describe(e)}")
            return None

        itens = resposta.get('Items') if isinstance(resposta, dict) else resposta
        alvo = str(device_id).strip().lower()
        for aparelho in itens or []:
            identificador = str(aparelho.get('Id') or '')
            if identificador.strip().lower() == alvo:
                # Devolve-se a grafia do SERVIDOR, não a da sessão.
                return identificador
        return None

    def force_terminate(self, session: MediaSession, reason: str) -> bool:
        """Revoga o acesso do APARELHO, para clientes que ignoram a ordem de parar.

        Apagar o aparelho no Jellyfin invalida as credenciais dele: o pedido
        seguinte do leitor recebe 401 e a reprodução morre, obedeça o cliente ou
        não. Em troca, a pessoa tem de voltar a autenticar-se naquele aparelho —
        por isso só se chega aqui depois de a via educada ter falhado várias
        vezes e com autorização explícita do administrador.

        Devolve False quando não há como revogar. Não é uma falha a esconder: é
        o painel a dizer que, naquele cliente, o limite de telas não vai ser
        cumprido.
        """
        bruta = session.raw if isinstance(session.raw, dict) else {}
        device_id = bruta.get('DeviceId')

        if not device_id:
            logger.warning(
                "Não é possível forçar o fim da sessão %s: o Jellyfin não indicou o aparelho.",
                session.session_key,
            )
            return False

        alvo = self._aparelho_conhecido(device_id)
        if not alvo:
            logger.error(
                "O Jellyfin não reconhece o aparelho '%s' (sessão %s, cliente '%s'), por isso "
                "não há como revogar-lhe o acesso. O identificador vem da própria sessão — "
                "se não está na lista de aparelhos do servidor, este cliente não pode ser "
                "encerrado à força.",
                device_id, session.session_key, session.player,
            )
            return False

        try:
            self.conn.api.delete('/Devices', params={'id': alvo})
        except JellyfinApiError as e:
            logger.error(
                "O Jellyfin recusou revogar o aparelho %s (sessão %s): %s",
                alvo, session.session_key, describe(e),
            )
            return False
        except Exception as e:
            logger.error("Falha ao revogar o aparelho %s: %s", alvo, describe(e))
            return False

        logger.warning(
            "🔌 Acesso do aparelho %s revogado: o cliente '%s' ignorou repetidamente a ordem "
            "de parar a reprodução. O utilizador terá de autenticar-se de novo nesse aparelho.",
            alvo, session.player,
        )
        return True

    # =========================================================================
    # TEMPO REAL
    # =========================================================================

    def supports_realtime(self) -> bool:
        """Ainda não: o painel cai na verificação periódica.

        O Jellyfin tem um websocket (`/socket`) que permitiria o mesmo tempo
        real do Plex. Fica para quando o backend estiver validado contra um
        servidor real — dizer que não o suporta faz o motor de streams usar a
        verificação periódica, que funciona e é o comportamento seguro.
        """
        return False

    def is_listener_healthy(self) -> bool:
        return True

    def start_listener(self, on_change) -> None:
        return None

    def stop_listener(self) -> None:
        return None
