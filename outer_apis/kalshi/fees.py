"""Cálculo de fees reales de Kalshi, compartido por LimitArb y MarketArb.

Fórmula pública (taker, la única confirmada de forma consistente en
fuentes públicas — help.kalshi.com no publica el PDF completo del fee
schedule por categoría en texto plano, solo el link al PDF):

    fee = ceil_to_cent(TAKER_FEE_MULTIPLIER * contracts * price * (1 - price))

Redondea POR ORDEN, no por contrato (Kalshi confirmado). El multiplier
estándar es 0.07; hay indicios de multipliers más altos ("premium") para
categorías como cripto, pero el valor exacto no está confirmado en fuentes
públicas confiables -> fail-closed: se aplica un multiplier conservador
más alto para esas categorías en vez de asumir 0.07 y subestimar el costo
real (TODO: reemplazar por el valor exacto del fee schedule oficial de
Kalshi cuando se consiga, el PDF está en kalshi.com/fee-schedule).

Los descuentos de "maker" (para órdenes límite que quedan resting en el
book) varían por serie según Kalshi y no hay una cifra fija confiable
públicamente -> fail-closed: NO se asume descuento de maker, se cobra la
misma fee que a una orden taker/market en ambas patas. Esto subestima el
retorno neto real (nunca lo infla), que es la dirección segura para
decisiones de capital real.
"""

from decimal import Decimal, InvalidOperation, ROUND_CEILING

TAKER_FEE_MULTIPLIER = 0.07

# Multiplier conservador para categorías "premium" con fee más alto
# reportado mencionado en fuentes públicas pero sin cifra exacta
# confirmada. TODO: reemplazar por el valor real del fee schedule.
PREMIUM_FEE_MULTIPLIER = 0.10
PREMIUM_CATEGORIES = {"btc", "eth", "sol", "xrp", "doge", "bnb"}


def _multiplier_for(category: str | None) -> float:
    if category in PREMIUM_CATEGORIES:
        return PREMIUM_FEE_MULTIPLIER
    return TAKER_FEE_MULTIPLIER


def order_fee(
    price: float, contracts: float = 1, category: str | None = None,
    *, fee_multiplier: float | None = None,
) -> float | None:
    """Fee total de una orden de `contracts` contratos a `price` (0-1).

    Fail-closed: precio ausente/None o fuera de [0,1] -> None (el llamador
    debe tratar esto como "no hay dato de costo confiable", no como fee 0).
    """
    if price is None:
        return None
    try:
        decimal_price = Decimal(str(price))
        decimal_contracts = Decimal(str(contracts))
    except (InvalidOperation, ValueError):
        return None
    if (
        not decimal_price.is_finite() or not decimal_contracts.is_finite()
        or not (Decimal("0") <= decimal_price <= Decimal("1"))
        or decimal_contracts <= 0
    ):
        return None

    if fee_multiplier is not None:
        try:
            multiplier = Decimal(str(fee_multiplier))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not multiplier.is_finite() or not Decimal("0") < multiplier <= Decimal("1"):
            return None
    else:
        multiplier = Decimal(str(_multiplier_for(category)))
    raw_fee = multiplier * decimal_contracts * decimal_price * (Decimal("1") - decimal_price)
    # round up al centavo más cercano (Kalshi redondea a favor de la casa)
    return float((raw_fee * Decimal("100")).to_integral_value(rounding=ROUND_CEILING) / Decimal("100"))


def leg_fee_fraction(price: float, category: str | None = None) -> float | None:
    """Fee de una orden de 1 contrato, expresada como fracción de precio
    (0-1) en vez de dólares nominales de $1 de payout -- es lo que
    consumen las estrategias, que trabajan en la misma escala 0-1 que los
    precios de mercado (payout de 1 contrato Kalshi = $1)."""
    return order_fee(price, contracts=1, category=category)
