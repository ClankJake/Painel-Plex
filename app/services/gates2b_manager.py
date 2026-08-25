# app/services/gates2b_manager.py

import logging
import requests
import uuid
import time
from flask import url_for
from flask_babel import gettext as _
from datetime import datetime, timedelta, timezone

from ..config import load_or_create_config
from ..utils.log_sanitizer import mask_token

logger = logging.getLogger(__name__)

class Gates2bManager:
    """Gerencia a comunicação com a API do gateway Gates2b (api.gates2b.com)."""

    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.config = None
        self.base_url = "https://api.gates2b.com"
        self.auth_token = None
        self.reload_credentials()

    def reload_credentials(self):
        """Recarrega a configuração e reinicia a instância da API."""
        self.config = load_or_create_config()

        # 1. Verifica PRIMEIRO se a integração está ativada.
        #
        # 🐛 CORREÇÃO: antes o método olhava apenas para a existência do token, pelo
        # que uma instalação com a Gates2b DESATIVADA — mas com um token guardado de
        # uma configuração anterior — continuava a registar "Credenciais da Gates2b
        # recarregadas com sucesso" a cada arranque e a cada gravação de definições.
        # Isso dava a impressão de que o gateway estava ativo quando não estava, e
        # poluía os logs com ruído sobre um serviço que não está em uso.
        is_enabled = self.config.get("GATES2B_ENABLED", False)
        if isinstance(is_enabled, str):
            is_enabled = is_enabled.lower() in ['true', '1', 't', 'y', 'yes']

        if not is_enabled:
            self.auth_token = None
            logger.debug("Gates2b está desativado nas configurações. Inicialização ignorada.")
            return

        # 2. Só depois de confirmar que está ativo é que validamos as credenciais.
        self.auth_token = self.config.get('GATES2B_AUTH_TOKEN')
        if self.auth_token:
            logger.info("Credenciais da Gates2b recarregadas com sucesso.")
        else:
            logger.warning("Gates2b está ativado, mas o Token de Autorização não está configurado.")

    def check_status(self):
        """
        Verifica se o serviço da Gates2b está configurado e ativo.

        🐛 CORREÇÃO: esta função lia 'self.config', que é um SNAPSHOT carregado na
        última vez que 'reload_credentials()' correu. Como a recarga de credenciais
        é seletiva (só acontece quando as chaves da Gates2b mudam), desativar o
        gateway sem tocar no token deixava o snapshot desatualizado — e o painel
        continuava a mostrar "ONLINE" para um gateway já desligado.
        Passamos a ler a configuração fresca do disco, como já faziam a Efí e o
        Mercado Pago.
        """
        config = load_or_create_config()
        if not config.get("GATES2B_ENABLED"):
            return {"status": "DISABLED", "message": _("Desativado na configuração.")}
        # O token também vem da configuração atual, pela mesma razão.
        if config.get("GATES2B_AUTH_TOKEN"):
            return {"status": "ONLINE", "message": _("Ativo e configurado.")}
        else:
            return {"status": "OFFLINE", "message": _("Ativado, mas falha na configuração (verifique o Token de Autorização).")}

    def test_connection(self, auth_token):
        """Testa a conexão com a Gates2b usando um token de autorização."""
        if not auth_token:
            return {'success': False, 'message': _('O Token de Autorização é obrigatório.')}
        
        # A Gates2b tem um endpoint dedicado à validação da chave, que devolve
        # também a data de expiração. É preferível a listar pagamentos: não
        # depende de haver transações na conta e é explícito quanto à validade.
        endpoint = f"{self.base_url}/api-key/validate"
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        }

        try:
            logger.info("A testar a conexão com a Gates2b...")
            response = requests.get(endpoint, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json() or {}
            info = data.get('response', {}) if isinstance(data, dict) else {}

            if info.get('valid') is False:
                return {'success': False, 'message': info.get('message') or _('A chave de API não é válida.')}

            expires_at = info.get('expires_at')
            if expires_at:
                logger.info(f"Conexão com a Gates2b bem-sucedida. A chave expira em {expires_at}.")
                return {'success': True, 'message': _('Conexão bem-sucedida! A chave expira em %(date)s.', date=expires_at)}

            logger.info("Conexão com a Gates2b bem-sucedida.")
            return {'success': True, 'message': _('Conexão com a Gates2b bem-sucedida!')}
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.warning("Falha no teste de conexão com a Gates2b: Token inválido.")
                return {'success': False, 'message': _('Falha na autenticação: O Token de Autorização parece ser inválido.')}
            error_text = e.response.text
            logger.error(f"Erro HTTP ao testar a conexão com a Gates2b: {e}. Resposta: {error_text}")
            return {'success': False, 'message': f"Erro do gateway: {e.response.status_code}"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de comunicação ao testar a conexão com a Gates2b: {e}", exc_info=True)
            return {'success': False, 'message': _("Falha na conexão: Verifique a URL e a sua conexão de rede.")}

    def _build_webhook_url(self):
        """
        URL público do webhook, construído a partir do URL Base da aplicação.
        Enviar o webhookUrl em cada cobrança torna a integração mais robusta: deixa
        de depender de o administrador ter colado o endereço certo no painel do
        gateway.
        """
        base = (self.config.get("APP_BASE_URL") or "").strip().rstrip('/')
        if not base:
            logger.warning("URL Base da Aplicação não configurado: a cobrança será criada sem webhookUrl.")
            return None
        if 'localhost' in base or '127.0.0.1' in base:
            logger.error(
                "ALERTA CRÍTICO: a 'URL Base da Aplicação' aponta para um endereço local (%s). "
                "A Gates2b não conseguirá notificar o pagamento e as renovações NÃO serão automáticas.", base
            )
        return f"{base}/api/payments/webhook/gates2b"

    def create_pix_charge(self, user_info, price, screens, coupon_code=None):
        """Cria uma cobrança PIX na Gates2b."""
        if not self.auth_token:
            self.reload_credentials()
            if not self.auth_token:
                return {"success": False, "message": "O serviço de pagamento Gates2b não está configurado corretamente."}
        
        # 💰 Valor mínimo da Gates2b: pedidos abaixo do mínimo configurado na conta
        # devolvem HTTP 400. Validamos antes de chamar a API para dar ao utilizador
        # uma mensagem clara em vez de um erro cru do gateway. O padrão documentado
        # é R$ 3,00 — nenhuma conta tem taxa inferior a esse valor.
        minimo = float(self.config.get("GATES2B_MIN_AMOUNT", 3.0) or 3.0)
        if float(price) < minimo:
            logger.warning(f"Cobrança Gates2b recusada localmente: R$ {float(price):.2f} é inferior ao mínimo de R$ {minimo:.2f}.")
            return {
                "success": False,
                "message": _("O valor mínimo aceite pelo gateway é de R$ %(min).2f. Ajuste o preço do plano ou o desconto aplicado.", min=minimo)
            }

        endpoint = f"{self.base_url}/charge"
        
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }
        
        service_description = f"Renovacao Plex - {screens} Telas" if screens > 0 else "Renovacao Plex - Plano Padrao"
        
        expire_at = datetime.now(timezone.utc) + timedelta(minutes=20)

        # Referência própria: liga a cobrança ao nosso registo e serve de chave de
        # idempotência — repetir o mesmo pedido (ex: após um timeout de rede, em que
        # a cobrança pode já ter sido criada) devolve a existente em vez de gerar
        # uma segunda cobrança ao mesmo cliente.
        external_reference = str(uuid.uuid4())

        # 📌 Payload do endpoint /charge (o antigo /payments foi descontinuado em
        # 01/09/2026). O /charge suporta vários métodos de pagamento; aqui pedimos
        # explicitamente apenas PIX.
        payload = {
            "grossAmount": f"{float(price):.2f}",   # a API espera STRING, não número
            "currency": "BRL",
            "paymentMethod": "PIX",
            "externalReference": external_reference,
            "description": f"Pagamento para {user_info.get('username')} - {service_description}",
            "expiresAt": expire_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "attemptIdempotencyKey": external_reference,
            "customerMeta": {
                "name": user_info.get('name') or user_info.get('username'),
                "email": user_info.get('email'),
            },
        }

        # O /charge aceita o URL do webhook por cobrança. Enviá-lo aqui garante que
        # a notificação chega mesmo que o URL não esteja configurado no painel do
        # gateway — uma causa comum de "paguei e não renovou".
        webhook_url = self._build_webhook_url()
        if webhook_url:
            payload["webhookUrl"] = webhook_url

        try:
            logger.info(f"A criar cobrança PIX na Gates2b para o utilizador '{user_info['username']}' no valor de {price:.2f}.")
            response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json() or {}

            # ⚠️ A resposta real difere do exemplo da documentação. Na prática a API
            # devolve o objeto da TENTATIVA (attempt), com esta forma:
            #   id           -> uuid da tentativa
            #   chargeId     -> id da COBRANÇA (ULID, ex: '01M0TAX...')
            #   checkoutMeta -> NO TOPO (não aninhado em 'attempt')
            #     .qr_text   -> código copia-e-cola
            #     .qr_image  -> base64 SEM o prefixo 'data:'
            #     .emv       -> txid do PIX
            #   charge       -> objeto da cobrança aninhado
            #
            # A documentação mostra outra forma (attempt.checkoutMeta.pix.brCode).
            # Suportamos AS DUAS, para a integração não voltar a partir se a API
            # mudar de formato ou se as contas antigas responderem de outra maneira.
            charge_obj = data.get("charge") or {}
            attempt = data.get("attempt") or {}

            # checkoutMeta pode estar no topo ou dentro de 'attempt'
            checkout = data.get("checkoutMeta") or attempt.get("checkoutMeta") or {}
            pix = checkout.get("pix") or {}

            # Identificador da cobrança: preferimos sempre o ID DA COBRANÇA (não o
            # da tentativa), porque é ele que aparece no checkoutUrl e que as
            # notificações referem.
            charge_id = (
                data.get("chargeId")
                or charge_obj.get("id")
                or (data.get("id") if str(data.get("id", "")).startswith("chg_") else None)
                or data.get("id")
            )

            # Código copia-e-cola: 'qr_text' (real) ou 'pix.brCode' (documentado)
            pix_copy_paste = checkout.get("qr_text") or pix.get("brCode")

            # Imagem: 'qr_image' (real, base64 puro) ou 'pix.qrCodeImage' (documentado, já com prefixo)
            qr_image = checkout.get("qr_image") or pix.get("qrCodeImage")

            if charge_id and pix_copy_paste:
                # Guardamos o ID DA COBRANÇA ('chg_...') como identificador interno:
                # é ele que identifica o recurso na API e o que as notificações
                # referem. O txId do PIX é apenas informativo.
                logger.info(f"Cobrança Gates2b criada com sucesso. Charge ID: {mask_token(charge_id)}")
                self.data_manager.create_pix_payment(
                    txid=charge_id,
                    plex_user_id=user_info['plex_user_id'],
                    username=user_info['username'],
                    value=price,
                    provider='GATES2B',
                    screens=screens,
                    external_reference=external_reference,
                    coupon_code=coupon_code
                )

                # O 'qrCodeImage' já vem com o prefixo 'data:image/png;base64,'.
                # Só o acrescentamos se vier em base64 puro, para não duplicar.
                if qr_image and not str(qr_image).startswith('data:'):
                    qr_image = f"data:image/png;base64,{qr_image}"

                return {
                    "success": True,
                    "txid": charge_id,
                    "pix_copy_paste": pix_copy_paste,
                    "qr_code_image": qr_image
                }
            else:
                error_message = data.get("message") or "Resposta da Gates2b sem os dados do PIX."
                logger.error(f"Falha ao criar cobrança PIX na Gates2b: {data}")
                return {"success": False, "message": error_message}

        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            logger.error(f"Erro HTTP ao comunicar com a Gates2b: {e}. Resposta: {error_text}")
            return {"success": False, "message": f"Erro do gateway de pagamento: {error_text}"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de comunicação ao criar cobrança na Gates2b: {e}", exc_info=True)
            return {"success": False, "message": "Ocorreu um erro ao comunicar com o serviço de pagamentos Gates2b."}

    def detail_pix_charge(self, txid):
        """
        Verifica o estado de uma cobrança PIX. Para a Gates2b, a confirmação
        depende primariamente do webhook. Esta função evita fazer polling
        na API para prevenir erros de 'não encontrado' no log.
        """
        if not self.auth_token:
            return {"success": False, "message": "O serviço de pagamento Gates2b não está configurado."}

        payment = self.data_manager.get_pix_payment(txid)
        if not payment:
            logger.warning(f"Pagamento Gates2b com TXID {mask_token(txid)} não encontrado na base de dados local durante a consulta de estado.")
            return {"success": False, "message": "Pagamento não encontrado localmente."}

        current_status = payment.get('status')
        if current_status == 'CONCLUIDA':
            return {"success": True, "data": {"status": "Pagamento realizado"}}
        else:
            return {"success": True, "data": {"status": "WAITING_PAYMENT"}}
