# app/services/pricing_manager.py

import logging
import math
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

    def calculate_price(self, screens, coupon_code=None, plex_user_id=None, apply_referral_credit=False):
        """
        Calcula o preço final de um plano, aplicando um cupão se for válido e,
        opcionalmente, o crédito de indicações do utilizador.

        ⚠️ NOTA IMPORTANTE: este método é PURO — apenas calcula, nunca debita nada.
        O crédito só é efetivamente consumido quando o pagamento é confirmado
        (ver o processamento do webhook). Se debitássemos aqui, um utilizador que
        gerasse um PIX e nunca o pagasse perderia o crédito sem receber nada.

        'apply_referral_credit' é opt-in para não alterar o comportamento de quem
        já chama este método (ex: a pré-visualização de preço com cupão).
        """
        original_price = self._get_base_price(screens)
        if original_price is None:
            return {"success": False, "message": _("Preço para o plano selecionado não encontrado.")}

        result = {
            "success": True,
            "original_price": original_price,
            "discounted_price": original_price,
            "coupon_applied": False,
            "referral_credit_available": 0.0,
            "referral_credit_applied": 0.0,
        }

        # 1. Cupão (se houver)
        if coupon_code:
            is_valid, validation_result = self._validate_coupon(coupon_code, plex_user_id)
            if not is_valid:
                return {"success": False, "message": validation_result}

            coupon = validation_result
            result["discounted_price"] = self._apply_discount(original_price, coupon)
            result["coupon_applied"] = True
            result["message"] = _("Cupão aplicado com sucesso!")

        # 2. Crédito de indicações (aplicado DEPOIS do cupão, sobre o valor já com desconto)
        if apply_referral_credit and plex_user_id:
            credit_info = self._calculate_referral_credit(result["discounted_price"], plex_user_id)
            result["referral_credit_available"] = credit_info["available"]
            result["referral_credit_applied"] = credit_info["applied"]
            result["discounted_price"] = credit_info["final_price"]

        return result

    def _calculate_referral_credit(self, current_price, plex_user_id):
        """
        Determina quanto do crédito de indicações pode ser abatido do preço atual.
        Nunca debita — só calcula. O crédito aplicado está sempre limitado tanto
        pelo saldo disponível como pelo próprio preço (nunca gera valor negativo
        nem "troco").

        Nota: o saldo é abatido independentemente do tipo de recompensa que
        estiver configurado NESTE momento. O crédito só se acumula no modo
        'credit', mas se o administrador mudar depois para 'dias grátis' o
        dinheiro que os utilizadores já ganharam continua a ser deles — antes
        ficava invisível e impossível de gastar.
        """
        empty = {"available": 0.0, "applied": 0.0, "final_price": current_price}

        config = load_or_create_config()
        if not config.get("REFERRAL_ENABLED", False):
            return empty

        try:
            profile = self.data_manager.get_user_profile(plex_user_id)
        except Exception:
            return empty

        if not profile:
            return empty

        balance = round(float(profile.get('referral_credit') or 0), 2)
        if balance <= 0:
            return empty

        # 🛡️ Desconta o crédito já comprometido em cobranças abertas: sem isto,
        # gerar duas cobranças ao mesmo tempo dava o desconto completo nas duas e
        # o utilizador gastava o mesmo saldo a dobrar.
        try:
            reserved = round(float(self.data_manager.get_reserved_referral_credit(plex_user_id) or 0), 2)
        except Exception:
            reserved = 0.0

        available = round(max(0.0, balance - reserved), 2)
        if available <= 0:
            return empty

        applied = round(min(available, current_price), 2)
        final_price = round(max(0.0, current_price - applied), 2)

        return {"available": available, "applied": applied, "final_price": final_price}

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

    def requires_proration_for_upgrade(self, user_profile):
        """
        Indica se, para este utilizador, subir de plano AGORA exige usar o
        pro-rata (em vez de uma renovação a preço cheio).

        Retorna False quando o pro-rata está desativado ou quando o utilizador já
        está na janela de renovação — nesses casos a renovação normal é permitida
        e o upgrade acontece junto com ela, como sempre funcionou.

        É um método puro (sem estado partilhado entre pedidos), por isso é seguro
        chamá-lo a partir de vários pedidos em simultâneo.
        """
        config = load_or_create_config()
        if not config.get("PRORATION_ENABLED", False):
            return False

        expiration_str = user_profile.get('expiration_date')
        if not expiration_str:
            return False

        try:
            expiration = datetime.fromisoformat(expiration_str)
            if expiration.tzinfo is None:
                expiration = expiration.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return False

        days_left = (expiration - datetime.now(timezone.utc)).days
        renewal_window = int(config.get("DAYS_TO_NOTIFY_EXPIRATION", 7))
        # Dentro da janela de renovação, a renovação normal já é o caminho natural.
        return days_left > renewal_window

    def calculate_upgrade_proration(self, plex_user_id, new_screens):
        """
        Calcula quanto custa fazer um UPGRADE de plano a meio do ciclo (pro-rata):
        o utilizador paga apenas a DIFERENÇA de preço pelos dias que ainda faltam,
        e o vencimento NÃO se altera.

        Exemplo: tem 1 ecrã (R$20), quer 3 (R$30), faltam 12 dias:
            (30 - 20) / 30 * 12 = R$ 4,00

        Devolve sempre um dicionário com 'eligible' a indicar se a operação é
        possível — nunca lança exceção, para que o fluxo normal de renovação
        continue a funcionar mesmo que algo aqui falhe.
        """
        config = load_or_create_config()
        result = {
            "eligible": False,
            "reason": None,
            "current_screens": 0,
            "new_screens": new_screens,
            "days_remaining": 0,
            "amount": 0.0,
            "is_free": False,
        }

        if not config.get("PRORATION_ENABLED", False):
            result["reason"] = _("O upgrade proporcional está desativado.")
            return result

        try:
            profile = self.data_manager.get_user_profile(plex_user_id)
        except Exception:
            profile = None

        if not profile:
            result["reason"] = _("Perfil não encontrado.")
            return result

        current_screens = int(profile.get('screen_limit') or 0)
        result["current_screens"] = current_screens

        # 🛡️ Só UPGRADE. Downgrade a meio do ciclo continua bloqueado: devolver
        # dinheiro ou crédito por telas já disponíveis seria um vetor de abuso
        # (subir o plano, usar, descer e pedir de volta).
        if int(new_screens) <= current_screens:
            result["reason"] = _("O upgrade proporcional aplica-se apenas ao aumentar o número de telas.")
            return result

        # Subscrição tem de estar ativa e com dias por usar.
        expiration_str = profile.get('expiration_date')
        if not expiration_str:
            result["reason"] = _("Sem data de vencimento definida.")
            return result

        try:
            expiration = datetime.fromisoformat(expiration_str)
            if expiration.tzinfo is None:
                expiration = expiration.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            result["reason"] = _("Data de vencimento inválida.")
            return result

        # 🐛 Usar '.days' diretamente trunca a fração: uma subscrição que vence
        # daqui a 1 dia e 20 horas dá '.days == 1', mas uma que vence daqui a 20
        # horas dá '.days == 0' e seria tratada como JÁ EXPIRADA. Arredondamos
        # para cima, para que qualquer tempo restante conte como pelo menos 1 dia.
        delta = expiration - datetime.now(timezone.utc)
        total_seconds = delta.total_seconds()
        days_remaining = math.ceil(total_seconds / 86400) if total_seconds > 0 else 0
        result["days_remaining"] = days_remaining

        if days_remaining <= 0:
            # Já venceu: não há nada a "aproveitar", o utilizador deve renovar
            # normalmente escolhendo o plano novo.
            result["reason"] = _("A subscrição já expirou. Faça uma renovação normal com o novo plano.")
            return result

        # Se falta pouco tempo, não vale a pena cobrar uma fração: o utilizador
        # está prestes a renovar já com o plano novo.
        min_days = int(config.get("PRORATION_MIN_DAYS", 3) or 0)
        if days_remaining < min_days:
            result["reason"] = _("Faltam poucos dias para o vencimento. Escolha o novo plano na renovação.")
            return result

        current_price = self._get_base_price(str(current_screens))
        new_price = self._get_base_price(str(new_screens))
        if current_price is None or new_price is None:
            result["reason"] = _("Preço não encontrado para um dos planos.")
            return result

        # Base de 30 dias: simples, previsível e fácil de explicar ao utilizador.
        # Usar os dias reais de cada mês mudaria o valor em cêntimos e tornaria a
        # conta difícil de justificar no suporte.
        daily_difference = (new_price - current_price) / 30
        amount = round(daily_difference * days_remaining, 2)

        # Abaixo de um mínimo, cobrar não compensa (taxas de PIX/gateway podem
        # ultrapassar o próprio valor). O administrador decide se nesses casos o
        # upgrade é oferecido gratuitamente ou simplesmente recusado.
        min_charge = float(config.get("PRORATION_MIN_CHARGE", 5.0) or 0)
        if amount < min_charge:
            if config.get("PRORATION_FREE_BELOW_MINIMUM", True):
                result.update({"eligible": True, "amount": 0.0, "is_free": True})
                return result
            result["reason"] = _("O valor proporcional é inferior ao mínimo cobrável.")
            return result

        result.update({"eligible": True, "amount": amount})
        return result

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
        """Filtra os planos para requisições de links públicos (apenas renovação exata ou fallback inteligente)."""
        price_for_current_plan = screen_prices.get(str(current_screens))
        
        # 1. Se tem preço exato para a tela atual, retorna
        if price_for_current_plan and float(price_for_current_plan) > 0:
            return {str(current_screens): price_for_current_plan}
            
        # 2. Se não tem preço para tela, usa o fallback geral
        if renewal_price and float(renewal_price) > 0:
            return {str(current_screens): renewal_price}
            
        # 3. Fallback Inteligente: O admin apagou o fallback e o usuário tem telas '0' ou não cadastradas.
        # Pega o plano mais barato disponível.
        valid_screen_prices = {int(k): float(v) for k, v in screen_prices.items() if v and float(v) > 0}
        
        if valid_screen_prices:
            # Encontra a chave (número de telas) do menor preço
            min_screens = min(valid_screen_prices, key=valid_screen_prices.get)
            min_price = valid_screen_prices[min_screens]
            logger.info(f"Link público: Nenhum preço exato para {current_screens} telas. Usando o menor preço disponível ({min_screens} telas: R$ {min_price}).")
            # Devolvemos a chave exata para que a criação da cobrança bata com o dicionário
            return {str(min_screens): str(min_price)}

        # Se chegar aqui, o admin não configurou nenhum preço no painel.
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
        
        # 🔒 UPGRADE A MEIO DO CICLO: quando o pro-rata está ativo, um upgrade pago
        # a preço cheio é SEMPRE mais vantajoso para o utilizador do que pagar a
        # diferença — ele recebe os dias que restam do ciclo atual já com o plano
        # superior, sem pagar por eles (ex: 25 dias restantes = R$8,33 não cobrados).
        # Na prática isso tornaria o pro-rata inútil, porque ninguém o escolheria.
        #
        # Por isso, com o pro-rata ativo, o upgrade por renovação normal só fica
        # disponível perto do vencimento — o mesmo tratamento que já se dá ao
        # downgrade. Quem quiser subir de plano antes disso usa o pro-rata.
        proration_on = bool(config.get("PRORATION_ENABLED", False))
        block_full_price_upgrade = proration_on and not can_downgrade

        available_prices = {}
        for screens, price in valid_screen_prices.items():
            n = int(screens)
            # Downgrade: só perto de expirar.
            if n < current_screens and not can_downgrade:
                continue
            # NOTA: os planos superiores CONTINUAM na lista mesmo quando o upgrade a
            # preço cheio está bloqueado — senão o utilizador não teria como sequer
            # chegar ao pro-rata. O bloqueio efetivo é feito no momento de gerar a
            # cobrança (ver 'proration_required' abaixo e a validação em payments.py).
            available_prices[screens] = price
        
        # Fallback: Adiciona o preço padrão se não houver opções multipantalla ativadas
        if not available_prices and renewal_price and float(renewal_price) > 0:
            available_prices["0"] = renewal_price

        return available_prices
