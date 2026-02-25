# app/services/pricing_manager.py

import logging
from datetime import datetime, timezone
from flask_babel import gettext as _

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

class PricingManager:
    """
    Serviço centralizado para toda a lógica de preços, planos e cupões.
    """

    def __init__(self, data_manager):
        self.data_manager = data_manager

    # --- CÁLCULO E VALIDAÇÃO DE PREÇOS ---

    def calculate_price(self, screens, coupon_code=None, plex_user_id=None):
        """
        Calcula o preço final de um plano, aplicando um cupão se for válido.
        """
        original_price = self._get_base_price(screens)
        if original_price is None:
            return {"success": False, "message": _("Preço para o plano selecionado não encontrado.")}

        if not coupon_code:
            return {
                "success": True, 
                "original_price": original_price, 
                "discounted_price": original_price, 
                "coupon_applied": False
            }

        # 1. Validação do Cupão
        is_valid, validation_result = self._validate_coupon(coupon_code, plex_user_id)
        if not is_valid:
            return {"success": False, "message": validation_result}

        # 2. Aplicação do Desconto
        coupon = validation_result
        discounted_price = self._apply_discount(original_price, coupon)

        return {
            "success": True,
            "original_price": original_price,
            "discounted_price": discounted_price,
            "coupon_applied": True,
            "message": _("Cupão aplicado com sucesso!")
        }

    # --- DISPONIBILIZAÇÃO DE PLANOS ---

    def get_available_plans(self, user_profile, is_public_request=False):
        """
        Determina os planos e preços disponíveis para um utilizador.
        """
        config = load_or_create_config()
        screen_prices = config.get("SCREEN_PRICES", {})
        renewal_price = config.get("RENEWAL_PRICE")
        current_screens = user_profile.get('screen_limit', 0)

        if is_public_request:
            return self._get_public_plans(current_screens, screen_prices, renewal_price)
        else:
            return self._get_private_plans(current_screens, screen_prices, renewal_price, user_profile, config)

    # --- MÉTODOS AUXILIARES (SRP) ---

    def _get_base_price(self, screens):
        """Obtém o preço base de um plano a partir da configuração."""
        config = load_or_create_config()
        screens_str = str(screens)
        price_str = config.get("SCREEN_PRICES", {}).get(screens_str) or config.get("RENEWAL_PRICE")
        
        if not price_str:
            return None
        try:
            return float(price_str)
        except (ValueError, TypeError):
            return None

    def _validate_coupon(self, coupon_code, plex_user_id):
        """
        Verifica se um cupão é válido para ser utilizado.
        Retorna um tuplo (bool, cupão_ou_mensagem_erro).
        """
        if plex_user_id and self.data_manager.has_user_used_coupon(plex_user_id, coupon_code):
            return False, _("Você já usou este cupão.")

        coupon = self.data_manager.get_coupon_by_code(coupon_code)
        if not coupon:
            return False, _("Cupão inválido ou não encontrado.")
            
        if not coupon.get('is_active'):
            return False, _("Este cupão não está mais ativo.")
            
        # CORREÇÃO: Utilizar timezone.utc para comparar corretamente com a data ISO guardada na BD
        expires_at_str = coupon.get('expires_at')
        if expires_at_str:
            try:
                # O SQLAlchemy pode retornar string ou datetime dependendo de como o driver SQLite atua.
                if isinstance(expires_at_str, str):
                    expires_at_dt = datetime.fromisoformat(expires_at_str)
                else:
                    expires_at_dt = expires_at_str
                    
                # Se for naive, adiciona o fuso horário UTC para comparação
                if expires_at_dt.tzinfo is None:
                    expires_at_dt = expires_at_dt.replace(tzinfo=timezone.utc)
                    
                if datetime.now(timezone.utc) > expires_at_dt:
                    return False, _("Este cupão expirou.")
            except (ValueError, TypeError):
                logger.warning(f"Erro ao analisar a data de expiração do cupão '{coupon_code}'.")

        if coupon.get('use_count', 0) >= coupon.get('max_uses', 1):
            return False, _("Este cupão já atingiu o seu limite de utilizações.")
            
        return True, coupon

    def _apply_discount(self, original_price, coupon):
        """Calcula a matemática do desconto."""
        discounted_price = original_price
        
        if coupon['discount_type'] == 'percentage':
            discounted_price = original_price * (1 - coupon['value'] / 100)
        elif coupon['discount_type'] == 'fixed':
            discounted_price = original_price - coupon['value']

        # O preço nunca pode ser negativo
        return max(0.0, discounted_price)

    def _get_public_plans(self, current_screens, screen_prices, renewal_price):
        """Filtra os planos para requisições de links públicos (apenas renovação exata)."""
        price_for_current_plan = screen_prices.get(str(current_screens), renewal_price)
        if price_for_current_plan and float(price_for_current_plan) > 0:
            return {str(current_screens): price_for_current_plan}
        return {}

    def _get_private_plans(self, current_screens, screen_prices, renewal_price, user_profile, config):
        """Filtra os planos para utilizadores com sessão iniciada (permite upgrade/downgrade)."""
        valid_screen_prices = {k: v for k, v in screen_prices.items() if v and float(v) > 0}
        
        expiration_date_str = user_profile.get('expiration_date')
        days_left = 999
        
        # CORREÇÃO: Utilizar timezone.utc para alinhar com o formato guardado pela aplicação
        if expiration_date_str:
            try:
                expiration_date = datetime.fromisoformat(expiration_date_str).date()
                days_left = (expiration_date - datetime.now(timezone.utc).date()).days
            except (ValueError, TypeError): 
                pass
        
        renewal_window_days = int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7))
        can_downgrade = days_left <= renewal_window_days
        
        available_prices = {}
        for screens, price in valid_screen_prices.items():
            # Só permite downgrade (ex: passar de 2 para 1 tela) se estiver perto de expirar.
            # Caso contrário, só permite upgrades.
            if can_downgrade or int(screens) >= current_screens:
                available_prices[screens] = price
        
        # Fallback: Adiciona o preço padrão se não houver opções multipantalla ativadas
        if not available_prices and renewal_price and float(renewal_price) > 0:
            available_prices["0"] = renewal_price
            
        return available_prices
