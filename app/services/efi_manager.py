# app/services/efi_manager.py

import logging
from efipay import EfiPay

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

class EfiManager:
    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.config = None
        self.efi = None
        self.reload_credentials()

    def reload_credentials(self):
        """Recarrega a configuração e reinicia a instância da API da Efí."""
        self.config = load_or_create_config()
        
        client_id = self.config.get('EFI_CLIENT_ID')
        client_secret = self.config.get('EFI_CLIENT_SECRET')
        certificate = self.config.get('EFI_CERTIFICATE')
        sandbox = self.config.get('EFI_SANDBOX', True)
        
        if client_id and client_secret and certificate:
            credentials = {
                'client_id': client_id,
                'client_secret': client_secret,
                'certificate': certificate,
                'sandbox': sandbox
            }
            try:
                self.efi = EfiPay(credentials)
                logger.info("Credenciais da Efí recarregadas com sucesso.")
            except Exception as e:
                logger.error(f"Falha ao iniciar o EfiManager com as novas credenciais: {e}")
                self.efi = None
        else:
            logger.warning("Credenciais da Efí não estão completamente configuradas. O serviço de pagamento está desativado.")
            if not client_id: logger.warning("- Client ID não encontrado.")
            if not client_secret: logger.warning("- Client Secret não encontrado.")
            if not certificate: logger.warning("- Caminho do Certificado não encontrado.")
            self.efi = None


    def create_pix_charge(self, user_info, price, screens):
        """Cria uma cobrança PIX imediata para um utilizador."""
        if not self.efi:
            return {"success": False, "message": "O serviço de pagamento não está configurado corretamente."}
        
        # MELHORIA: Padroniza as descrições e adiciona o nome da aplicação.
        app_title = self.config.get("APP_TITLE", "Painel Plex")
        service_description = f"Renovação Plex - {screens} Tela(s)" if screens > 0 else "Renovação Plex - Plano Padrão"

        body = {
            "calendario": {
                "expiracao": 3600
            },
            "valor": {
                "original": f"{price:.2f}"
            },
            "chave": self.config.get("EFI_PIX_KEY"),
            "infoAdicionais": [
                {"nome": "Aplicação", "valor": app_title},
                {"nome": "Serviço", "valor": service_description},
                {"nome": "Utilizador", "valor": user_info.get('username')}
            ]
        }

        try:
            logger.info(f"A criar cobrança PIX para o utilizador '{user_info['username']}' no valor de {price:.2f} ({screens} tela(s)).")
            response = self.efi.pix_create_immediate_charge(body=body)
            
            if not isinstance(response, dict):
                error_message = f"A API da Efí retornou um erro inesperado, provavelmente relacionado ao certificado: {str(response)}"
                logger.error(error_message)
                return {"success": False, "message": "Erro de comunicação com o provedor de pagamento. Verifique o caminho do certificado e as permissões do ficheiro."}

            logger.debug(f"Resposta da API Efí (create_charge): {response}")
            txid = response.get('txid')

            if not txid:
                error_title = response.get('title', response.get('nome', 'Erro desconhecido'))
                error_detail = response.get('detail', response.get('mensagem', 'A API não retornou um TXID.'))
                if 'erros' in response:
                    error_messages = [err.get('mensagem', 'Erro não especificado.') for err in response['erros']]
                    error_detail = " | ".join(error_messages)
                
                full_error_message = f"{error_title}: {error_detail}"
                logger.error(f"Falha ao criar cobrança PIX: {full_error_message}. Resposta completa: {response}")
                return {"success": False, "message": full_error_message}
                
            # Para a Efí, podemos passar None para a referência externa, pois o txid já é o nosso identificador principal.
            self.data_manager.create_pix_payment(txid, user_info['username'], price, 'EFI', screens, external_reference=None)
            
            params = {'id': response['loc']['id']}
            qr_code_response = self.efi.pix_generate_qrcode(params=params)
            
            return {
                "success": True,
                "txid": txid,
                "pix_copy_paste": qr_code_response.get('qrcode'),
                "qr_code_image": qr_code_response.get('imagemQrcode')
            }
            
        except Exception as e:
            error_message = "Ocorreu um erro ao comunicar com o serviço de pagamentos."
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    error_detail = error_data.get('erros', [{}])[0].get('mensagem', '')
                    if error_detail:
                        error_message = error_detail
                    logger.error(f"Erro detalhado da API Efí: {error_data}")
                except:
                    pass
            
            logger.error(f"Erro ao criar cobrança PIX na Efí: {e}", exc_info=True)
            return {"success": False, "message": error_message}

    def detail_pix_charge(self, txid):
        """Consulta os detalhes de uma cobrança PIX específica na API da Efí."""
        if not self.efi:
            return {"success": False, "message": "O serviço de pagamento não está configurado corretamente."}
        
        params = {'txid': txid}
        
        try:
            response = self.efi.pix_detail_charge(params=params)
            
            if not isinstance(response, dict):
                error_message = f"A API da Efí retornou um erro inesperado ao consultar o estado da cobrança: {str(response)}"
                logger.error(error_message)
                return {"success": False, "message": "Erro ao consultar o estado do pagamento. Verifique as configurações do certificado."}
                
            return {"success": True, "data": response}
        except Exception as e:
            logger.error(f"Erro ao consultar cobrança PIX na Efí (TXID: {txid}): {e}")
            return {"success": False, "message": "Ocorreu um erro ao consultar o estado do pagamento."}