# app/services/referral_manager.py

import logging
import secrets
import string
from datetime import datetime, timezone

from flask_babel import gettext as _

from ..config import load_or_create_config

logger = logging.getLogger(__name__)

# Alfabeto sem caracteres ambíguos (0/O, 1/I/L) — os códigos são partilhados
# por voz/WhatsApp e escritos à mão, por isso evitamos confusões.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


class ReferralManager:
    """
    Sistema "Indique e Ganhe": cada utilizador tem um código próprio; quando
    alguém se regista com esse código e efetua o PRIMEIRO pagamento, quem indicou
    recebe automaticamente a recompensa configurada pelo administrador.

    Duas modalidades de recompensa (escolhidas nas Configurações):
      • 'days'   → dias grátis somados diretamente ao vencimento de quem indicou.
      • 'credit' → crédito em dinheiro, abatido automaticamente na próxima renovação.
    """

    def __init__(self, data_manager, subscription_manager=None, notifier_manager=None):
        self.data_manager = data_manager
        self.subscription_manager = subscription_manager
        self.notifier_manager = notifier_manager

    # ------------------------------------------------------------------
    # CÓDIGOS
    # ------------------------------------------------------------------

    def _generate_unique_code(self, max_attempts=10):
        """Gera um código curto e único. Em caso de colisão, tenta novamente."""
        for _attempt in range(max_attempts):
            code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if not self.data_manager.get_user_profile_by_referral_code(code):
                return code
        # Fallback praticamente impossível de atingir, mas nunca devolvemos None.
        return f"{secrets.token_hex(6).upper()}"

    def get_or_create_code(self, plex_user_id):
        """
        Devolve o código de referência do utilizador, criando-o na primeira vez
        que é pedido (geração preguiçosa — não precisamos de gerar códigos para
        utilizadores que nunca vão usar a funcionalidade).
        """
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile:
            return None

        existing = profile.get('referral_code')
        if existing:
            return existing

        code = self._generate_unique_code()
        profile['referral_code'] = code
        self.data_manager.set_user_profile(plex_user_id, profile)
        logger.info(f"[Referral] Código '{code}' gerado para o utilizador ID {plex_user_id}.")
        return code

    # ------------------------------------------------------------------
    # REGISTO DA INDICAÇÃO
    # ------------------------------------------------------------------

    def register_referral(self, new_user_plex_id, referral_code):
        """
        Associa um utilizador recém-criado a quem o indicou. Não paga nada ainda —
        a recompensa só é atribuída quando o indicado efetuar o primeiro pagamento
        (ver 'reward_referrer_on_payment'), para evitar que se criem contas falsas
        só para gerar recompensas.
        """
        config = load_or_create_config()
        if not config.get("REFERRAL_ENABLED", False):
            return {"success": False, "message": _("O sistema de indicações está desativado.")}

        if not referral_code:
            return {"success": False, "message": _("Código de indicação em falta.")}

        referrer = self.data_manager.get_user_profile_by_referral_code(str(referral_code).strip().upper())
        if not referrer:
            return {"success": False, "message": _("Código de indicação inválido.")}

        referrer_id = referrer.get('plex_user_id')

        # 🛡️ Auto-indicação: sem isto, qualquer pessoa ganharia recompensas
        # indicando-se a si própria numa segunda conta.
        if str(referrer_id) == str(new_user_plex_id):
            logger.warning(f"[Referral] Tentativa de auto-indicação bloqueada (ID {new_user_plex_id}).")
            return {"success": False, "message": _("Não pode usar o seu próprio código de indicação.")}

        profile = self.data_manager.get_user_profile(new_user_plex_id)
        if not profile:
            return {"success": False, "message": _("Usuário não encontrado.")}

        # Só se aceita uma indicação por utilizador, e apenas se ainda não tiver uma.
        if profile.get('referred_by'):
            return {"success": False, "message": _("Este usuário já foi indicado por alguém.")}

        profile['referred_by'] = referrer_id
        profile['referral_rewarded'] = False
        self.data_manager.set_user_profile(new_user_plex_id, profile)

        logger.info(f"[Referral] Utilizador ID {new_user_plex_id} foi indicado por '{referrer.get('username')}' (ID {referrer_id}).")
        return {"success": True, "referrer_username": referrer.get('username')}

    # ------------------------------------------------------------------
    # RECOMPENSA
    # ------------------------------------------------------------------

    def reward_referrer_on_payment(self, paying_user_plex_id):
        """
        Chamado quando um pagamento é confirmado. Se este utilizador foi indicado
        por alguém e a recompensa ainda não foi paga, atribui-a a quem o indicou.

        Falha sempre em silêncio (devolvendo um dicionário) em vez de lançar: um
        problema no sistema de indicações nunca pode impedir a confirmação de um
        pagamento legítimo.
        """
        try:
            config = load_or_create_config()
            if not config.get("REFERRAL_ENABLED", False):
                return {"success": False, "rewarded": False}

            profile = self.data_manager.get_user_profile(paying_user_plex_id)
            if not profile:
                return {"success": False, "rewarded": False}

            referrer_id = profile.get('referred_by')
            if not referrer_id:
                return {"success": True, "rewarded": False}

            # 🛡️ Só a PRIMEIRA compra do indicado gera recompensa.
            if profile.get('referral_rewarded'):
                return {"success": True, "rewarded": False, "message": "Recompensa já atribuída anteriormente."}

            referrer_profile = self.data_manager.get_user_profile(referrer_id)
            if not referrer_profile:
                logger.warning(f"[Referral] Quem indicou (ID {referrer_id}) já não tem perfil. Indicação ignorada.")
                return {"success": False, "rewarded": False}

            reward_type = config.get("REFERRAL_REWARD_TYPE", "days")
            reward_desc = ""

            if reward_type == "credit":
                amount = float(config.get("REFERRAL_REWARD_CREDIT", 0) or 0)
                if amount <= 0:
                    return {"success": True, "rewarded": False}
                current_credit = float(referrer_profile.get('referral_credit') or 0)
                referrer_profile['referral_credit'] = round(current_credit + amount, 2)
                self.data_manager.set_user_profile(referrer_id, referrer_profile)
                reward_desc = f"R$ {amount:.2f}"
                logger.info(f"[Referral] Crédito de {reward_desc} atribuído a '{referrer_profile.get('username')}'.")

            else:  # 'days' (padrão)
                days = int(config.get("REFERRAL_REWARD_DAYS", 0) or 0)
                if days <= 0:
                    return {"success": True, "rewarded": False}
                if not self.subscription_manager:
                    logger.error("[Referral] SubscriptionManager indisponível: não foi possível somar dias.")
                    return {"success": False, "rewarded": False}
                self.subscription_manager.add_days_to_subscription(referrer_id, days)
                reward_desc = _("%(days)d dia(s) grátis", days=days)
                logger.info(f"[Referral] {days} dia(s) atribuídos a '{referrer_profile.get('username')}'.")

            # Marca no INDICADO que a recompensa já foi paga (idempotência).
            profile['referral_rewarded'] = True
            self.data_manager.set_user_profile(paying_user_plex_id, profile)

            # Notifica quem indicou.
            try:
                self.data_manager.create_notification(
                    message=_("A sua indicação de %(username)s foi confirmada! Recebeu %(reward)s.",
                              username=profile.get('username', ''), reward=reward_desc),
                    category='success', link="/account", user_plex_id=referrer_id
                )
            except Exception as e:
                logger.warning(f"[Referral] Não foi possível notificar quem indicou: {e}")

            return {"success": True, "rewarded": True, "reward": reward_desc, "referrer_id": referrer_id}

        except Exception as e:
            logger.error(f"[Referral] Falha ao processar a recompensa de indicação: {e}", exc_info=True)
            return {"success": False, "rewarded": False}

    # ------------------------------------------------------------------
    # CONSULTA
    # ------------------------------------------------------------------

    def get_referral_stats(self, plex_user_id):
        """Resumo do programa de indicações para o painel do utilizador."""
        config = load_or_create_config()
        profile = self.data_manager.get_user_profile(plex_user_id) or {}

        referred = self.data_manager.get_users_referred_by(plex_user_id)
        confirmed = [r for r in referred if r.get('referral_rewarded')]

        return {
            "enabled": bool(config.get("REFERRAL_ENABLED", False)),
            "code": profile.get('referral_code'),
            "reward_type": config.get("REFERRAL_REWARD_TYPE", "days"),
            "reward_days": int(config.get("REFERRAL_REWARD_DAYS", 0) or 0),
            "reward_credit": float(config.get("REFERRAL_REWARD_CREDIT", 0) or 0),
            "total_referred": len(referred),
            "total_confirmed": len(confirmed),
            "pending": len(referred) - len(confirmed),
            "current_credit": float(profile.get('referral_credit') or 0),
            "referred_users": [
                {
                    "username": r.get('username'),
                    "confirmed": bool(r.get('referral_rewarded')),
                }
                for r in referred
            ],
        }

    def consume_credit(self, plex_user_id, amount):
        """
        Abate crédito de indicações do saldo do utilizador (usado ao aplicar o
        desconto numa renovação). Devolve o valor efetivamente consumido, que
        nunca excede o saldo disponível.
        """
        profile = self.data_manager.get_user_profile(plex_user_id)
        if not profile:
            return 0.0
        available = float(profile.get('referral_credit') or 0)
        used = min(available, max(0.0, float(amount)))
        if used > 0:
            profile['referral_credit'] = round(available - used, 2)
            self.data_manager.set_user_profile(plex_user_id, profile)
            logger.info(f"[Referral] Crédito de R$ {used:.2f} usado pelo utilizador ID {plex_user_id}.")
        return used
