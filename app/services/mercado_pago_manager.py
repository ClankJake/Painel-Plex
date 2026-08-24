# app/services/mercado_pago_manager.py

import logging
import hmac
import hashlib
import mercadopago
import uuid
from flask import url_for
from datetime import datetime, timedelta, timezone
from flask_babel import gettext as _

from ..config import load_or_create_config
from ..utils.log_sanitizer import mask_token

logger = logging.getLogger(__name__)


def _build_payer(user_info):
    """
    Monta o bloco 'payer' do pagamento.

    🐛 Antes era enviado 'last_name': " " (um espaço). O Mercado Pago usa os dados
    do pagador na análise antifraude, e um apelido em branco é um sinal negativo
    que pode baixar a taxa de aprovação. Aqui dividimos o nome real quando existe,
    e omitimos os campos quando não há informação — omitir é melhor do que enviar
    lixo.
    """
    payer = {"email": user_info.get('email')}

    nome_completo = (user_info.get('name') or user_info.get('username') or '').strip()
    if nome_completo:
        partes = nome_completo.split()
        payer["first_name"] = partes[0]
        if len(partes) > 1:
            payer["last_name"] = " ".join(partes[1:])

    return payer


def _extract_mp_error(payload):
    """
    Extrai a mensagem de erro mais útil da resposta do Mercado Pago.

    A API devolve os detalhes em 'cause' (uma lista de {code, description}) e um
    resumo em 'message'. Preferimos a descrição da causa, que é o que realmente
    explica o problema a quem está a configurar o gateway.
    """
    if not isinstance(payload, dict):
        return None

    causas = payload.get('cause')
    if isinstance(causas, list) and causas:
        descricoes = [
            str(c.get('description') or c.get('code'))
            for c in causas if isinstance(c, dict)
        ]
        if descricoes:
            return " | ".join(d for d in descricoes if d)

    return payload.get('message')


class MercadoPagoManager:
    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.config = None
        self.sdk = None
        self.reload_credentials()

    def reload_credentials(self):
        """Recarrega a configuração e reinicia a instância da API do Mercado Pago."""
        try:
            self.config = load_or_create_config()
            
            # 1. Verifica primeiro se a integração está ativada nas configurações
            is_enabled = self.config.get("MERCADOPAGO_ENABLED", False)
            
            # Converte para booleano caso venha como string
            if isinstance(is_enabled, str):
                is_enabled = is_enabled.lower() in ['true', '1', 't', 'y', 'yes']

            if not is_enabled:
                self.sdk = None
                logger.debug("Mercado Pago está desativado nas configurações. Inicialização ignorada.")
                return # Pára a execução aqui, evitando o Warning desnecessário

            # 2. Se estiver ativado, aí sim tenta buscar o token
            access_token = self.config.get("MERCADOPAGO_ACCESS_TOKEN")
            
            # Valida estritamente se o token existe e é uma String válida
            if access_token and isinstance(access_token, str) and access_token.strip():
                self.sdk = mercadopago.SDK(access_token.strip())
                logger.info("Credenciais do Mercado Pago recarregadas e ativadas com sucesso.")
            else:
                self.sdk = None
                logger.warning("Mercado Pago está ATIVADO, mas o Access Token está vazio ou inválido. O serviço não funcionará.")
                
        except Exception as e:
            self.sdk = None
            logger.error(f"Erro interno ao inicializar o SDK do Mercado Pago: {e}")

    def check_status(self):
        """Verifica se o serviço do Mercado Pago está configurado e ativo."""
        config = load_or_create_config()
        is_enabled = config.get("MERCADOPAGO_ENABLED", False)
        
        if isinstance(is_enabled, str):
            is_enabled = is_enabled.lower() in ['true', '1', 't', 'y', 'yes']

        if not is_enabled:
            return {"status": "DISABLED", "message": _("Desativado na configuração.")}
        if self.sdk:
            return {"status": "ONLINE", "message": _("Ativo e configurado.")}
        else:
            return {"status": "OFFLINE", "message": _("Ativado, mas falha na configuração (verifique o Access Token).")}

    def create_pix_payment(self, user_info, price, screens, coupon_code=None):
        """
        Cria uma cobrança PIX no Mercado Pago com detalhes adicionais para maior
        segurança e taxa de aprovação, utilizando o SDK oficial.
        """
        if not self.sdk:
            return {"success": False, "message": "Credenciais do Mercado Pago não configuradas."}

        # 💰 Valor mínimo: o Mercado Pago recusa transações abaixo do mínimo
        # (bastante baixo para PIX, mas relevante com upgrades pro-rata, onde a
        # diferença a pagar pode ser de cêntimos). Validar aqui dá uma mensagem
        # clara em vez de um erro cru da API.
        minimo = float(self.config.get("MERCADOPAGO_MIN_AMOUNT", 1.0) or 0)
        if minimo > 0 and float(price) < minimo:
            logger.warning(f"Cobrança Mercado Pago recusada localmente: R$ {float(price):.2f} < mínimo de R$ {minimo:.2f}.")
            return {
                "success": False,
                "message": f"O valor mínimo aceite pelo Mercado Pago é de R$ {minimo:.2f}."
            }

        external_reference = str(uuid.uuid4())
        
        item_title = f"Renovação Plex - {screens} Tela(s)" if screens > 0 else "Renovação Plex - Plano Padrão"
        item_description = f"Assinatura de acesso ao servidor Plex para o utilizador {user_info.get('username')}."
        
        payment_description = f"Serviço: {item_title} | Utilizador: {user_info.get('username')}"

        app_title = self.config.get("APP_TITLE", "PainelPlex")
        statement_descriptor = ''.join(filter(str.isalnum, app_title))[:22]

        expiration_time = datetime.now(timezone.utc) + timedelta(minutes=20)
        date_of_expiration_iso = expiration_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        payment_data = {
            "transaction_amount": float(price),
            "payment_method_id": "pix",
            "description": payment_description,
            "date_of_expiration": date_of_expiration_iso,
            "payer": _build_payer(user_info),
            "additional_info": {
                "items": [
                    {
                        "id": f"plex-renewal-{screens}-screens",
                        "title": item_title,
                        "description": item_description,
                        "category_id": "services",
                        "quantity": 1,
                        "unit_price": float(price)
                    }
                ]
            },
            "external_reference": external_reference,
            "statement_descriptor": statement_descriptor,
            "notification_url": f"{self.config.get('APP_BASE_URL', '').rstrip('/')}{url_for('payments_api.mercadopago_webhook')}"
        }

        # 🐛 IDEMPOTÊNCIA REAL: antes era gerado um uuid4 NOVO a cada chamada, o que
        # anula por completo o propósito do cabeçalho. Se a criação falhasse por
        # timeout (a cobrança podia já ter sido criada do lado do Mercado Pago) e o
        # pedido fosse repetido, uma chave diferente criava uma SEGUNDA cobrança —
        # o cliente podia acabar a pagar duas vezes.
        #
        # A chave passa a derivar do próprio pedido: repetir o mesmo pedido devolve
        # a cobrança já existente em vez de criar outra.
        request_options = mercadopago.config.RequestOptions()
        request_options.custom_headers = {
            'x-idempotency-key': external_reference
        }

        try:
            logger.info(f"A criar cobrança PIX no Mercado Pago para o utilizador '{user_info['username']}' com a referência externa: {external_reference}.")
            payment_response = self.sdk.payment().create(payment_data, request_options)
            payment = payment_response.get("response")
            
            if payment_response.get("status") == 201 and payment:
                txid = str(payment['id'])
                self.data_manager.create_pix_payment(
                    txid=txid,
                    plex_user_id=user_info['plex_user_id'],
                    username=user_info['username'],
                    value=price,
                    provider='MERCADOPAGO',
                    screens=screens,
                    external_reference=external_reference,
                    coupon_code=coupon_code
                )
                
                return {
                    "success": True,
                    "payment_id": txid,
                    "pix_copy_paste": payment['point_of_interaction']['transaction_data']['qr_code'],
                    "qr_code_image": f"data:image/png;base64,{payment['point_of_interaction']['transaction_data']['qr_code_base64']}"
                }
            else:
                # 🐛 O Mercado Pago devolve o motivo real em 'cause' (ex: conta sem PIX
                # ativo, CPF inválido, valor abaixo do mínimo). Antes ficava tudo
                # reduzido a uma mensagem genérica, o que tornava o diagnóstico quase
                # impossível a partir dos logs.
                error_message = _extract_mp_error(payment) or 'Falha ao criar cobrança PIX no Mercado Pago.'
                logger.error(f"Falha ao criar cobrança PIX no Mercado Pago: {payment_response}")
                return {"success": False, "message": error_message}
        except Exception as e:
            logger.error(f"Erro ao criar cobrança PIX no Mercado Pago: {e}", exc_info=True)
            return {"success": False, "message": f"Erro ao comunicar com o Mercado Pago: {e}"}


    def validate_webhook_signature(self, x_signature, x_request_id, data_id):
        """
        Valida a assinatura HMAC-SHA256 de uma notificação do Mercado Pago.

        Porque isto importa: a rota do webhook é pública e está isenta de rate
        limit. Sem validação, qualquer pessoa pode enviar notificações forjadas e,
        embora o pagamento seja sempre reconfirmado na API antes de ser aceite
        (o que impede fraudes), cada pedido falso gera uma chamada à API do
        Mercado Pago — um vetor fácil para esgotar o rate limit da conta.

        Formato do cabeçalho 'x-signature':
            ts=1704908010,v1=<hash>

        Manifesto assinado (a ordem e os separadores são obrigatórios):
            id:<data.id>;request-id:<x-request-id>;ts:<ts>;

        Devolve (True, None) quando é válida ou quando a validação está desativada
        (sem segredo configurado), e (False, motivo) quando falha.
        """
        secret = (self.config.get("MERCADOPAGO_WEBHOOK_SECRET") or "").strip()
        if not secret:
            # Sem segredo configurado a validação fica desligada, para não quebrar
            # instalações existentes que ainda não a configuraram. A proteção real
            # continua a ser a reconfirmação do pagamento na API.
            return True, None

        if not x_signature:
            return False, "Cabeçalho 'x-signature' em falta."

        ts = None
        received_hash = None
        for parte in x_signature.split(','):
            chave, _, valor = parte.strip().partition('=')
            if chave == 'ts':
                ts = valor.strip()
            elif chave == 'v1':
                received_hash = valor.strip()

        if not ts or not received_hash:
            return False, "Formato inesperado do cabeçalho 'x-signature'."

        manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
        expected = hmac.new(secret.encode('utf-8'), manifest.encode('utf-8'), hashlib.sha256).hexdigest()

        # compare_digest evita fugas de informação através do tempo de comparação.
        if not hmac.compare_digest(expected, received_hash):
            return False, "Assinatura inválida."

        return True, None

    def get_payment_details(self, payment_id):
        """Consulta os detalhes de um pagamento no Mercado Pago."""
        if not self.sdk:
            return {"success": False, "message": "O serviço de pagamento não está configurado corretamente."}
        
        try:
            logger.info(f"A consultar estado do pagamento no Mercado Pago (ID: {payment_id}).")
            payment_info = self.sdk.payment().get(payment_id)
            if payment_info.get("status") == 200:
                return {"success": True, "data": payment_info.get("response")}
            else:
                logger.error(f"Erro ao consultar pagamento no Mercado Pago: {payment_info}")
                return {"success": False, "message": "Falha ao consultar pagamento."}
        except Exception as e:
            logger.error(f"Erro ao consultar pagamento no Mercado Pago (ID: {payment_id}): {e}", exc_info=True)
            return {"success": False, "message": "Ocorreu um erro ao consultar o estado do pagamento."}
