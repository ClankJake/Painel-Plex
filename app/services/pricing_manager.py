# app/services/pricing_manager.py

import logging
from datetime import datetime
from flask_babel import gettext as _

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

class PricingManager:
    """
    Serviço centralizado para toda a lógica de preços, planos e cupões.
    """

    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.config = load_or_create_config()

    def _get_base_price(self, screens):
        """Obtém o preço base de um plano a partir da configuração."""
        screens_str = str(screens)
        price_str = self.config.get("SCREEN_PRICES", {}).get(screens_str) or self.config.get("RENEWAL_PRICE")
        
        if not price_str:
            return None
        try:
            return float(price_str)
        except (ValueError, TypeError):
            return None

    def calculate_price(self, screens, coupon_code=None, plex_user_id=None):
        """
        Calcula o preço final de um plano, aplicando um cupão se for válido.

        Retorna um dicionário com:
        - success (bool)
        - message (str)
        - original_price (float)
        - discounted_price (float)
        - coupon_applied (bool)
        """
        original_price = self._get_base_price(screens)
        if original_price is None:
            return {"success": False, "message": _("Preço para o plano selecionado não encontrado.")}

        if not coupon_code:
            return {"success": True, "original_price": original_price, "discounted_price": original_price, "coupon_applied": False}

        # Validação do cupão
        if plex_user_id and self.data_manager.has_user_used_coupon(plex_user_id, coupon_code):
            return {"success": False, "message": _("Você já usou este cupão.")}

        coupon = self.data_manager.get_coupon_by_code(coupon_code)
        if not coupon:
            return {"success": False, "message": _("Cupão inválido ou não encontrado.")}
        if not coupon['is_active']:
            return {"success": False, "message": _("Este cupão não está mais ativo.")}
        if coupon['expires_at'] and datetime.utcnow() > coupon['expires_at']:
            return {"success": False, "message": _("Este cupão expirou.")}
        if coupon['use_count'] >= coupon['max_uses']:
            return {"success": False, "message": _("Este cupão já atingiu o seu limite de utilizações.")}
            
        # Cálculo do desconto
        discounted_price = original_price
        if coupon['discount_type'] == 'percentage':
            discounted_price = original_price * (1 - coupon['value'] / 100)
        elif coupon['discount_type'] == 'fixed':
            discounted_price = original_price - coupon['value']

        discounted_price = max(0, discounted_price)

        return {
            "success": True,
            "original_price": original_price,
            "discounted_price": discounted_price,
            "coupon_applied": True,
            "message": _("Cupão aplicado com sucesso!")
        }

    def get_available_plans(self, user_profile, is_public_request=False):
        """
        Determina os planos e preços disponíveis para um utilizador.
        """
        self.config = load_or_create_config() # Garante que a config está atualizada
        screen_prices = self.config.get("SCREEN_PRICES", {})
        renewal_price = self.config.get("RENEWAL_PRICE")
        current_screens = user_profile.get('screen_limit', 0)

        available_prices = {}

        if is_public_request:
            # Em links públicos, só permite renovar o plano atual
            price_for_current_plan = screen_prices.get(str(current_screens), renewal_price)
            if price_for_current_plan and float(price_for_current_plan) > 0:
                available_prices = {str(current_screens): price_for_current_plan}
        else:
            # Na página da conta, mostra mais opções
            valid_screen_prices = {k: v for k, v in screen_prices.items() if v and float(v) > 0}
            
            expiration_date_str = user_profile.get('expiration_date')
            days_left = 999
            if expiration_date_str:
                try:
                    expiration_date = datetime.fromisoformat(expiration_date_str).date()
                    days_left = (expiration_date - datetime.today().date()).days
                except ValueError: pass
            
            renewal_window_days = int(self.config.get("DAYS_TO_NOTIFY_EXPIRATION", 7))
            can_downgrade = days_left <= renewal_window_days
            
            for screens, price in valid_screen_prices.items():
                if can_downgrade or int(screens) >= current_screens:
                    available_prices[screens] = price
            
            # Adiciona o preço padrão se nenhum preço por tela for aplicável
            if not available_prices and renewal_price and float(renewal_price) > 0:
                available_prices["0"] = renewal_price
        
        return available_prices
