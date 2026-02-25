# app/services/efi_manager.py

import logging
from efipay import EfiPay
from flask_babel import gettext as _

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

class EfiManager:
    """
    Gere a integração com a API de pagamentos da Efí (antiga Gerencianet)
    para geração e consulta de cobranças PIX.
    """
    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.config = None
        self.efi = None
        self.reload_credentials()

    def reload_credentials(self):
        """Recarrega as configurações e reinicializa o cliente da API Efí."""
        self.config = load_or_create_config()
        
        client_id = self.config.get('EFI_CLIENT_ID')
        client_secret = self.config.get('EFI_CLIENT_SECRET')
        certificate = self.config.get('EFI_CERTIFICATE')
        sandbox = self.config.get('EFI_SANDBOX', True)
        
        if all([client_id, client_secret, certificate]):
            credentials = {
                'client_id': client_id,
                'client_secret': client_secret,
                'certificate': certificate,
                'sandbox': sandbox
            }
            try:
                self.efi = EfiPay(credentials)
                logger.info("Credenciais da Efí carregadas e cliente inicializado com sucesso.")
            except Exception as e:
                logger.error(f"Falha ao inicializar o cliente Efí com as credenciais fornecidas: {e}")
                self.efi = None
        else:
            logger.warning("Credenciais da Efí incompletas. O serviço PIX Efí está desativado.")
            self.efi = None

    def check_status(self):
        """Verifica o status da configuração da Efí."""
        config = load_or_create_config()
        if not config.get("EFI_ENABLED"):
            return {"status": "DISABLED", "message": _("Desativado nas configurações do sistema.")}
        
        if self.efi:
            return {"status": "ONLINE", "message": _("Serviço ativo e credenciais configuradas.")}
        
        return {"status": "OFFLINE", "message": _("Serviço ativado, mas as credenciais são inválidas ou estão em falta.")}

    def configure_webhook(self):
        """Configura a URL de Webhook na API da Efí para receber notificações de pagamento."""
        if not self.efi:
            logger.warning("Configuração de webhook ignorada: Cliente Efí não inicializado.")
            return

        pix_key = self.config.get("EFI_PIX_KEY")
        app_base_url = self.config.get("APP_BASE_URL", "").rstrip('/')
        use_mtls = self.config.get("EFI_USE_MTLS", True)
        hmac_secret = self.config.get("EFI_WEBHOOK_HMAC_SECRET")

        if not pix_key or not app_base_url:
            logger.warning("Configuração de webhook ignorada: 'Chave PIX' ou 'URL Base da Aplicação' ausentes.")
            return
        
        if "localhost" in app_base_url or "127.0.0.1" in app_base_url:
            logger.critical(
                "ALERTA CRÍTICO: A 'URL Base da Aplicação' está configurada como local (%s). "
                "A Efí não conseguirá enviar notificações de pagamento. Configure um domínio público acessível.",
                app_base_url
            )
            return

        try:
            # Constrói a URL do Webhook manualmente para evitar problemas de contexto com url_for
            base_webhook_url = f"{app_base_url}/api/payments/webhook/efi"
            headers = {}
            
            if use_mtls:
                headers['x-skip-mtls-checking'] = 'false'
                webhook_url = f"{base_webhook_url}?ignorar="
            else:
                headers['x-skip-mtls-checking'] = 'true'
                if hmac_secret:
                    webhook_url = f"{base_webhook_url}?hmac={hmac_secret}&ignorar="
                else:
                    logger.error("Configuração Insegura: mTLS está desativado mas nenhum HMAC Secret foi definido. Webhook não configurado.")
                    return

            params = {'chave': pix_key}
            body = {'webhookUrl': webhook_url}
            
            logger.info(f"A registar Webhook na Efí para a chave '{pix_key}'. URL destino: {webhook_url}")
            self.efi.pix_config_webhook(params=params, body=body, headers=headers)
            logger.info("Webhook da Efí configurado com sucesso.")
            
        except Exception as e:
            logger.error(f"Falha ao tentar registar o Webhook na Efí para a chave '{pix_key}': {e}", exc_info=True)


    def create_pix_charge(self, user_info, price, screens, coupon_code=None):
        """Gera uma cobrança PIX imediata (QR Code e Copia/Cola)."""
        if not self.efi:
            return {"success": False, "message": _("O provedor de pagamentos Efí não está disponível no momento.")}
        
        app_title = self.config.get("APP_TITLE", "Painel Plex")
        username = user_info.get('username', 'Desconhecido')
        service_desc = f"Renovação - {screens} Tela(s)" if screens > 0 else "Renovação - Plano Padrão"

        body = {
            "calendario": {
                "expiracao": 1200  # 20 minutos de validade
            },
            "valor": {
                "original": f"{price:.2f}"
            },
            "chave": self.config.get("EFI_PIX_KEY"),
            "solicitacaoPagador": f"{app_title}: {username}",
            "infoAdicionais": [
                {"nome": "Servico", "valor": service_desc},
                {"nome": "Utilizador", "valor": username}
            ]
        }
        
        try:
            logger.info(f"Efí: A solicitar nova cobrança PIX de R$ {price:.2f} para o utilizador '{username}'.")
            response = self.efi.pix_create_immediate_charge(body=body)
            
            # Validação defensiva da resposta
            if not isinstance(response, dict):
                logger.error(f"Efí: Resposta inválida da API (esperado dicionário, recebido {type(response)}). Possível erro de certificado SSL.")
                return {"success": False, "message": _("Erro interno ao comunicar com o servidor de pagamentos. Verifique o certificado SSL.")}

            txid = response.get('txid')

            if not txid:
                # Extrai informações detalhadas de erro se o txid não estiver presente
                error_title = response.get('title', response.get('nome', 'Erro Desconhecido'))
                error_detail = response.get('detail', response.get('mensagem', 'Nenhum TXID retornado pela API.'))
                
                if 'erros' in response and isinstance(response['erros'], list):
                    erros_lista = [err.get('mensagem', '') for err in response['erros']]
                    error_detail += " | Detalhes: " + " - ".join(erros_lista)
                
                logger.error(f"Efí: A API recusou a criação da cobrança. Motivo: {error_title} - {error_detail}. Resposta bruta: {response}")
                return {"success": False, "message": f"{error_title}: {error_detail}"}
                
            # Salva o pagamento localmente como 'ATIVA'
            self.data_manager.create_pix_payment(
                txid=txid,
                plex_user_id=user_info.get('plex_user_id'),
                username=username,
                value=price,
                provider='EFI',
                screens=screens,
                external_reference=None,
                coupon_code=coupon_code
            )
            
            # Solicita a imagem do QR Code usando o locator ID
            loc_id = response.get('loc', {}).get('id')
            qr_code_response = self.efi.pix_generate_qrcode(params={'id': loc_id})
            
            logger.info(f"Efí: Cobrança criada com sucesso. TXID: {txid}")
            
            return {
                "success": True,
                "txid": txid,
                "pix_copy_paste": qr_code_response.get('qrcode'),
                "qr_code_image": qr_code_response.get('imagemQrcode')
            }
            
        except Exception as e:
            error_message = _("Ocorreu uma falha de comunicação com o sistema bancário. Tente novamente mais tarde.")
            
            # Tenta extrair a mensagem de erro formatada pela biblioteca efipay (geralmente uma exceção personalizada ou com atributo response)
            try:
                if hasattr(e, 'response') and getattr(e, 'response') is not None:
                    error_data = e.response.json()
                    logger.error(f"Efí Exceção HTTP Detalhada: {error_data}")
                    
                    if 'erros' in error_data and isinstance(error_data['erros'], list) and len(error_data['erros']) > 0:
                        error_message = error_data['erros'][0].get('mensagem', error_message)
            except Exception:
                pass # Falha silenciosa na extração de erro, mantém a mensagem padrão
            
            logger.error(f"Efí: Erro não tratado durante a criação da cobrança PIX: {e}", exc_info=True)
            return {"success": False, "message": error_message}

    def detail_pix_charge(self, txid):
        """
        Consulta o status atual de uma cobrança PIX na API da Efí pelo seu TXID.
        """
        if not self.efi:
            return {"success": False, "message": "Cliente Efí não inicializado."}
        
        try:
            response = self.efi.pix_detail_charge(params={'txid': txid})
            
            if not isinstance(response, dict):
                logger.error(f"Efí: Resposta inválida ao consultar detalhes do TXID {txid}.")
                return {"success": False, "message": "Resposta inválida da API da Efí."}
                
            return {"success": True, "data": response}
            
        except Exception as e:
            logger.error(f"Efí: Falha ao tentar consultar o status do TXID {txid}: {e}")
            return {"success": False, "message": str(e)}
