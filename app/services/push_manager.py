# app/services/push_manager.py

"""As notificações que chegam ao celular mesmo com o painel fechado.

O sino do painel só avisa quem está com ele aberto. Um pagamento confirmado às
três da manhã, ou um pedido de conteúdo novo à espera de aprovação, ficavam à
espera de alguém abrir a página. A notificação push é o mesmo aviso entregue
pelo sistema operacional — no Android, no iPhone com o painel adicionado à tela
de início, e no navegador do computador.

**Quem recebe o quê segue a regra que já existia**: `media_user_id=None` é o
administrador (a mesma convenção de `notifications`), e um `media_user_id`
preenchido é a pessoa dona daquele perfil.

⚠️ **Nada aqui pode derrubar o que o chamou.** Um serviço de push em baixo não
pode fazer um pagamento falhar nem um webhook do Seerr devolver erro: toda a
entrega é isolada, e o pior que acontece é um aviso no log.
"""

import logging

from ..config import load_or_create_config, save_app_config
from ..utils.log_sanitizer import mask_link
from . import web_push

logger = logging.getLogger(__name__)

# O que cabe numa notificação sem a tornar ilegível — e sem estourar o limite
# de 4 KB do corpo cifrado, que faria o serviço de push responder 413.
MAXIMO_DO_TITULO = 80
MAXIMO_DO_CORPO = 300

# Sem `sub` no JWT, alguns serviços de push recusam com 400. Quando não há
# `APP_BASE_URL` nem assunto configurado, este endereço identifica o software —
# é o que a norma pede (um contacto de quem envia), não um endereço de pessoa.
ASSUNTO_PADRAO = 'https://github.com/ClankJake/Painel-Plex'


def _cortar(texto, limite):
    texto = str(texto or '').strip()
    return texto if len(texto) <= limite else texto[:limite - 1] + '…'


class PushManager:
    """Guarda as chaves VAPID e entrega notificações aos aparelhos subscritos."""

    def __init__(self, data_manager=None):
        self.data_manager = data_manager
        self.reload_credentials()

    # --- Configuração ------------------------------------------------------

    def reload_credentials(self):
        """Relê o config. Chamado pela recarga seletiva de `save_settings`."""
        config = load_or_create_config()
        self.ligado = bool(config.get('PUSH_ENABLED'))
        self.chave_publica = (config.get('PUSH_VAPID_PUBLIC_KEY') or '').strip()
        self._chave_privada = (config.get('PUSH_VAPID_PRIVATE_KEY') or '').strip()
        self.assunto = (config.get('PUSH_VAPID_SUBJECT') or '').strip()
        self.avisar_administrador = {
            'pagamento': bool(config.get('PUSH_ADMIN_PAYMENTS', True)),
            'pedido': bool(config.get('PUSH_ADMIN_MEDIA_REQUESTS', True)),
        }

    @property
    def disponivel(self):
        """Ligado, com um par de chaves utilizável e com onde as guardar."""
        return bool(
            self.ligado and self.chave_publica and self._chave_privada
            and self.data_manager is not None
        )

    def _assunto_do_jwt(self):
        if self.assunto:
            return self.assunto
        base = (load_or_create_config().get('APP_BASE_URL') or '').strip().rstrip('/')
        return base or ASSUNTO_PADRAO

    def garantir_chaves(self):
        """Cria o par VAPID na primeira vez, e devolve a chave pública.

        ⚠️ **Só gera quando NÃO HÁ nada utilizável.** A chave pública fica
        registada no serviço de push no momento em que cada aparelho subscreve:
        trocá-la invalida de uma vez todas as subscrições já feitas, e o sintoma
        é toda a gente deixar de receber notificações sem ninguém dar por isso.
        Por isso um par já existente nunca é substituído — nem sequer para o
        "corrigir".
        """
        if self.chave_publica and web_push.chave_privada_valida(self._chave_privada):
            # ⚠️ Um par DESEMPARELHADO (uma edição manual do config.json, um
            # restauro a meio) responde 403 em cada envio e mais nada. Vale a
            # pena repor a pública a partir da privada, que é a que os
            # aparelhos já subscritos não conseguem ver.
            correspondente = web_push.publica_de(self._chave_privada)
            if correspondente != self.chave_publica:
                logger.warning(
                    "As chaves VAPID no config.json não são do mesmo par. "
                    "A chave pública foi reposta a partir da privada — os "
                    "aparelhos subscritos antes disto vão ter de ser religados."
                )
                self._guardar_chaves(correspondente, self._chave_privada)
            return self.chave_publica

        par = web_push.gerar_par_de_chaves()
        self._guardar_chaves(par['publica'], par['privada'])
        logger.info("Par de chaves VAPID criado: as notificações push já podem ser ligadas.")
        return self.chave_publica

    def _guardar_chaves(self, publica, privada):
        config = load_or_create_config()
        config['PUSH_VAPID_PUBLIC_KEY'] = publica
        config['PUSH_VAPID_PRIVATE_KEY'] = privada
        save_app_config(config)
        self.chave_publica = publica
        self._chave_privada = privada

    # --- Subscrições -------------------------------------------------------

    def registar(self, media_user_id, subscricao, device_label=None):
        """Guarda o aparelho que acabou de aceitar receber notificações."""
        endpoint = (subscricao or {}).get('endpoint')
        chaves = (subscricao or {}).get('keys') or {}
        p256dh, auth = chaves.get('p256dh'), chaves.get('auth')
        if not (endpoint and p256dh and auth):
            raise ValueError("Subscrição incompleta: faltam o endereço ou as chaves.")

        return self.data_manager.registar_push_subscription(
            media_user_id, endpoint, p256dh, auth, device_label=device_label)

    def remover(self, endpoint):
        return self.data_manager.remover_push_subscription(endpoint)

    def aparelhos_de(self, media_user_id=None):
        return self.data_manager.get_push_subscriptions(media_user_id)

    # --- Entrega -----------------------------------------------------------

    def enviar(self, media_user_id, titulo, corpo, url=None, tag=None, urgencia='normal'):
        """Entrega uma notificação a todos os aparelhos de uma pessoa.

        `media_user_id=None` entrega aos aparelhos do administrador.

        Devolve quantos aparelhos aceitaram a entrega. Nunca levanta: quem
        chama está a meio de confirmar um pagamento ou de responder a um
        webhook, e uma notificação é sempre menos importante do que isso.
        """
        if not self.disponivel:
            return 0

        try:
            aparelhos = self.data_manager.get_push_subscriptions(media_user_id)
        except Exception as e:
            logger.warning(f"Não foi possível ler as subscrições push: {e}")
            return 0

        return self.entregar(aparelhos, titulo, corpo, url=url, tag=tag, urgencia=urgencia)

    def enviar_ao_administrador(self, assunto, titulo, corpo, url=None, tag=None):
        """O aviso que é do dono do painel — se ele o quiser para este assunto.

        `assunto` é 'pagamento' ou 'pedido': o administrador pode desligar cada
        um nas Configurações sem perder o outro. Um assunto desconhecido passa,
        para que um aviso novo nunca fique calado por esquecimento.
        """
        if not self.avisar_administrador.get(assunto, True):
            return 0
        return self.enviar(None, titulo, corpo, url=url, tag=tag)

    def entregar(self, aparelhos, titulo, corpo, url=None, tag=None, urgencia='normal'):
        """Entrega a uma lista de aparelhos já em mão.

        Existe separada de `enviar` para quem já precisou de saber ANTES se há
        aparelhos — o envio em massa, que anuncia o total de destinatários e não
        pode contar quem não tem por onde receber.
        """
        if not self.disponivel or not aparelhos:
            return 0
        payload = {
            'title': _cortar(titulo, MAXIMO_DO_TITULO),
            'body': _cortar(corpo, MAXIMO_DO_CORPO),
        }
        if url:
            payload['url'] = url
        if tag:
            # Notificações com a mesma etiqueta substituem-se no aparelho, em
            # vez de se empilharem: cinco pagamentos seguidos deixam um aviso,
            # não cinco linhas iguais na barra de notificações.
            payload['tag'] = tag

        privada, assunto = self._chave_privada, self._assunto_do_jwt()
        entregues = 0
        for aparelho in aparelhos:
            if self._entregar(aparelho, payload, privada, assunto, urgencia):
                entregues += 1
        return entregues

    def _entregar(self, aparelho, payload, privada, assunto, urgencia):
        endpoint = aparelho.get('endpoint')
        try:
            web_push.enviar(aparelho, payload, privada, assunto, urgencia=urgencia)
        except web_push.PushExpirado:
            # 🐛 Uma subscrição morta (navegador desinstalado, permissão
            # revogada) responde 410 para sempre. Deixá-la na tabela era um
            # erro no log por cada notificação, a cada pagamento, sem nada a
            # fazer sobre ele. Apaga-se: a pessoa volta a ligar quando quiser.
            logger.info(
                f"Subscrição push removida — o aparelho já não existe "
                f"({mask_link(endpoint)})."
            )
            try:
                self.data_manager.remover_push_subscription(endpoint)
            except Exception as e:
                logger.warning(f"Falha ao remover a subscrição push expirada: {e}")
            return False
        except Exception as e:
            logger.warning(f"Notificação push não entregue a {mask_link(endpoint)}: {e}")
            return False

        try:
            self.data_manager.marcar_push_entregue(endpoint)
        except Exception as e:
            # A entrega ACONTECEU; falhar a anotar a data é irrelevante.
            logger.debug(f"Não foi possível anotar a entrega do push: {e}")
        return True
