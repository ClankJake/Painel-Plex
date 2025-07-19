# app/services/mercado_pago_manager.py

import logging
import mercadopago
import uuid
from flask import url_for
from datetime import datetime, timedelta, timezone
from flask_babel import gettext as _

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

class MercadoPagoManager:
    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.config = None
        self.sdk = None
        self.reload_credentials()

    def reload_credentials(self):
        """Recarrega a configuração e reinicia a instância da API do Mercado Pago."""
        self.config = load_or_create_config()
        access_token = self.config.get("MERCADOPAGO_ACCESS_TOKEN")
        if access_token:
            self.sdk = mercadopago.SDK(access_token)
            logger.info("Credenciais do Mercado Pago recarregadas com sucesso.")
        else:
            self.sdk = None
            logger.warning("Access Token do Mercado Pago não configurado. O serviço de pagamento está desativado.")

    def check_status(self):
        """Verifica se o serviço do Mercado Pago está configurado e ativo."""
        config = load_or_create_config()
        if not config.get("MERCADOPAGO_ENABLED"):
            return {"status": "DISABLED", "message": _("Desativado na configuração.")}
        if self.sdk:
            return {"status": "ONLINE", "message": _("Ativo e configurado.")}
        else:
            return {"status": "OFFLINE", "message": _("Ativado, mas falha na configuração (verifique o Access Token).")}

    def create_pix_payment(self, user_info, price, screens):
        """
        Cria uma cobrança PIX no Mercado Pago com detalhes adicionais para maior
        segurança e taxa de aprovação, utilizando o SDK oficial.
        """
        if not self.sdk:
            return {"success": False, "message": "Credenciais do Mercado Pago não configuradas."}

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
            "payer": {
                "email": user_info.get('email'),
                "first_name": user_info.get('name', user_info.get('username')),
                "last_name": " "
            },
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

        idempotency_key = str(uuid.uuid4())
        request_options = mercadopago.config.RequestOptions()
        request_options.custom_headers = {
            'x-idempotency-key': idempotency_key
        }

        try:
            logger.info(f"A criar cobrança PIX no Mercado Pago para o utilizador '{user_info['username']}' com a referência externa: {external_reference}.")
            payment_response = self.sdk.payment().create(payment_data, request_options)
            payment = payment_response.get("response")
            
            if payment_response.get("status") == 201 and payment:
                txid = str(payment['id'])
                self.data_manager.create_pix_payment(txid, user_info['username'], price, 'MERCADOPAGO', screens, external_reference)
                
                return {
                    "success": True,
                    "payment_id": txid,
                    "pix_copy_paste": payment['point_of_interaction']['transaction_data']['qr_code'],
                    "qr_code_image": f"data:image/png;base64,{payment['point_of_interaction']['transaction_data']['qr_code_base64']}"
                }
            else:
                error_message = payment.get('message', 'Falha ao criar cobrança PIX no Mercado Pago.')
                logger.error(f"Falha ao criar cobrança PIX no Mercado Pago: {payment_response}")
                return {"success": False, "message": error_message}
        except Exception as e:
            logger.error(f"Erro ao criar cobrança PIX no Mercado Pago: {e}", exc_info=True)
            return {"success": False, "message": "Ocorreu um erro ao comunicar com o serviço de pagamentos."}

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
