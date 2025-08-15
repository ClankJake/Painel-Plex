# app/services/bpix_manager.py

import logging
import requests
import uuid
from flask import url_for
from flask_babel import gettext as _
from datetime import datetime, timedelta

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

class BpixManager:
    """Gerencia a comunicação com a API do gateway BPIX (api.bpix.app)."""

    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.config = None
        self.base_url = "https://api.bpix.app"
        self.auth_token = None
        self.reload_credentials()

    def reload_credentials(self):
        """Recarrega a configuração e reinicia a instância da API."""
        self.config = load_or_create_config()
        self.auth_token = self.config.get('BPIX_AUTH_TOKEN')
        if self.auth_token:
            logger.info("Credenciais da BPIX recarregadas com sucesso.")
        else:
            logger.warning("Credenciais da BPIX não estão completamente configuradas.")

    def check_status(self):
        """Verifica se o serviço da BPIX está configurado e ativo."""
        if not self.config.get("BPIX_ENABLED"):
            return {"status": "DISABLED", "message": _("Desativado na configuração.")}
        if self.auth_token:
            return {"status": "ONLINE", "message": _("Ativo e configurado.")}
        else:
            return {"status": "OFFLINE", "message": _("Ativado, mas falha na configuração (verifique o Token de Autorização).")}

    def create_pix_charge(self, user_info, price, screens):
        """Cria uma cobrança PIX na BPIX."""
        self.reload_credentials()
        if not self.auth_token:
            return {"success": False, "message": "O serviço de pagamento BPIX não está configurado corretamente."}
        
        endpoint = f"{self.base_url}/payments"
        
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }
        
        service_description = f"Renovacao Plex - {screens} Telas" if screens > 0 else "Renovacao Plex - Plano Padrao"
        
        expire_at = datetime.utcnow() + timedelta(minutes=20)

        # CORREÇÃO: Adiciona a URL do webhook ao criar a cobrança
        webhook_url = None
        app_base_url = self.config.get("APP_BASE_URL")
        if app_base_url:
            webhook_url = f"{app_base_url.strip('/')}/api/payments/webhook/bpix"
        else:
            logger.warning("APP_BASE_URL não está configurada. A URL do webhook para a BPIX não será enviada.")

        payload = {
            "amount": float(price),
            "clientMode": "fillDataNow",
            "expire_at": expire_at.isoformat() + "Z",
            "description": f"Pagamento para {user_info.get('username')} - {service_description}",
            "external_reference": str(uuid.uuid4()), # Mantemos uma referência interna única
            "name_client": user_info.get('name', user_info.get('username')),
            "email": user_info.get('email'),
            "webhook_url": webhook_url
        }
        
        # Remove a chave do webhook se a URL não estiver disponível
        if not payload["webhook_url"]:
            del payload["webhook_url"]

        try:
            logger.info(f"A criar cobrança PIX na BPIX para o utilizador '{user_info['username']}' no valor de {price:.2f}.")
            response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            txid = data.get("transaction_pix_id")
            lookup_id = data.get("id")

            if txid and lookup_id:
                self.data_manager.create_pix_payment(
                    txid=txid,
                    username=user_info['username'],
                    value=price,
                    provider='BPIX',
                    screens=screens,
                    external_reference=str(lookup_id)
                )
                
                qr_image_base64 = data.get('qr_image')
                qr_code_image_url = f"data:image/png;base64,{qr_image_base64}" if qr_image_base64 else None

                return {
                    "success": True,
                    "txid": txid,
                    "pix_copy_paste": data.get('qr_text'),
                    "qr_code_image": qr_code_image_url
                }
            else:
                error_message = data.get("message", "Erro desconhecido ao criar cobrança na BPIX. IDs não encontrados na resposta.")
                logger.error(f"Falha ao criar cobrança PIX na BPIX: {data}")
                return {"success": False, "message": error_message}

        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            logger.error(f"Erro HTTP ao comunicar com a BPIX: {e}. Resposta: {error_text}")
            return {"success": False, "message": f"Erro do gateway de pagamento: {error_text}"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de comunicação ao criar cobrança na BPIX: {e}", exc_info=True)
            return {"success": False, "message": "Ocorreu um erro ao comunicar com o serviço de pagamentos BPIX."}

    def detail_pix_charge(self, txid):
        """Consulta os detalhes de uma cobrança PIX na API da BPIX."""
        self.reload_credentials()
        if not self.auth_token:
            return {"success": False, "message": "O serviço de pagamento BPIX não está configurado."}

        payment = self.data_manager.get_pix_payment(txid)
        if not payment:
            logger.warning(f"Pagamento BPIX com TXID {txid} não encontrado na base de dados local.")
            return {"success": False, "message": "Pagamento não encontrado localmente."}
        
        lookup_id = payment.get('external_reference')
        if not lookup_id:
            logger.error(f"ID de consulta (lookup_id) não encontrado para o pagamento BPIX com TXID {txid}.")
            return {"success": False, "message": "ID de consulta interno em falta."}

        endpoint = f"{self.base_url}/payments/{lookup_id}"
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }

        try:
            logger.info(f"Consultando status do pagamento BPIX com ID de consulta: {lookup_id} (TXID: {txid})")
            response = requests.get(endpoint, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            return {"success": True, "data": data}
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Pagamento BPIX com ID de consulta {lookup_id} não encontrado na API.")
                return {"success": False, "message": "Pagamento não encontrado no gateway."}
            error_text = e.response.text
            logger.error(f"Erro HTTP ao consultar pagamento BPIX (ID de consulta: {lookup_id}): {e}. Resposta: {error_text}")
            return {"success": False, "message": f"Erro do gateway de pagamento: {error_text}"}
        except Exception as e:
            logger.error(f"Erro ao consultar cobrança PIX na BPIX (ID de consulta: {lookup_id}): {e}")
            return {"success": False, "message": "Ocorreu um erro ao consultar o estado do pagamento."}
