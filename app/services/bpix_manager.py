# app/services/bpix_manager.py

import logging
import requests
import uuid
import time
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

    def test_connection(self, auth_token):
        """Testa a conexão com a BPIX usando um token de autorização."""
        if not auth_token:
            return {'success': False, 'message': _('O Token de Autorização é obrigatório.')}
        
        endpoint = f"{self.base_url}/payments"
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        }
        params = {"limit": 1} 

        try:
            logger.info("A testar a conexão com a BPIX...")
            response = requests.get(endpoint, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            logger.info("Conexão com a BPIX bem-sucedida.")
            return {'success': True, 'message': _('Conexão com a BPIX bem-sucedida!')}
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.warning("Falha no teste de conexão com a BPIX: Token inválido.")
                return {'success': False, 'message': _('Falha na autenticação: O Token de Autorização parece ser inválido.')}
            error_text = e.response.text
            logger.error(f"Erro HTTP ao testar a conexão com a BPIX: {e}. Resposta: {error_text}")
            return {'success': False, 'message': f"Erro do gateway: {e.response.status_code}"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de comunicação ao testar a conexão com a BPIX: {e}", exc_info=True)
            return {'success': False, 'message': _("Falha na conexão: Verifique a URL e a sua conexão de rede.")}

    def create_pix_charge(self, user_info, price, screens, coupon_code=None):
        """Cria uma cobrança PIX na BPIX."""
        if not self.auth_token:
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

        payload = {
            "amount": float(price),
            "clientMode": "fillDataNow",
            "expire_at": expire_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "description": f"Pagamento para {user_info.get('username')} - {service_description}",
            "name_client": user_info.get('name', user_info.get('username')),
            "email": user_info.get('email')
        }

        try:
            logger.info(f"A criar cobrança PIX na BPIX para o utilizador '{user_info['username']}' no valor de {price:.2f}.")
            response = requests.post(endpoint, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            txid = data.get("transaction_pix_id")
            lookup_id = data.get("id")

            if txid and lookup_id:
                logger.info(f"Cobrança BPIX criada com sucesso. TXID: {txid}, Lookup ID: {lookup_id}")
                self.data_manager.create_pix_payment(
                    txid=txid,
                    plex_user_id=user_info['plex_user_id'],
                    username=user_info['username'],
                    value=price,
                    provider='BPIX',
                    screens=screens,
                    external_reference=str(lookup_id),
                    coupon_code=coupon_code
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
        """
        Verifica o estado de uma cobrança PIX. Para a BPIX, a confirmação
        depende primariamente do webhook. Esta função evita fazer polling
        na API para prevenir erros de 'não encontrado' no log.
        """
        if not self.auth_token:
            return {"success": False, "message": "O serviço de pagamento BPIX não está configurado."}

        payment = self.data_manager.get_pix_payment(txid)
        if not payment:
            logger.warning(f"Pagamento BPIX com TXID {txid} não encontrado na base de dados local durante a consulta de estado.")
            return {"success": False, "message": "Pagamento não encontrado localmente."}

        current_status = payment.get('status')
        if current_status == 'CONCLUIDA':
            return {"success": True, "data": {"status": "Pagamento realizado"}}
        else:
            return {"success": True, "data": {"status": "WAITING_PAYMENT"}}
