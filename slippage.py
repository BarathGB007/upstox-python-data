"""
Slippage model for paper trading.

Two modes:
1. Depth-based (preferred): BUY at ask, SELL at bid from real bid/ask depth.
2. Formula-based (fallback): multi-factor model when no depth available.
"""

import random


def compute_slippage(action: str, moneyness: float = 0.0, vix: float = 14.0,
                     hour: int = 10, symbol: str = "NIFTY",
                     is_exit: bool = False) -> float:
    """Compute formula-based slippage multiplier (fallback when no depth)."""
    base = 1.0

    if moneyness >= 4:
        base += 0.008
    elif moneyness >= 2:
        base += 0.004
    else:
        base += 0.002

    if vix >= 25:
        base += 0.006
    elif vix >= 18:
        base += 0.003

    if hour <= 9 or hour >= 15:
        base += 0.004
    elif hour >= 14:
        base += 0.002

    if symbol == "FINNIFTY":
        base += 0.005
    elif symbol == "BANKNIFTY":
        base += 0.001

    if is_exit:
        base += 0.002

    base += random.uniform(-0.001, 0.001)

    return max(base, 1.001)


def _apply_depth_slippage(price: float, action: str, depth: dict,
                          vix: float = 14.0, hour: int = 10) -> float:
    """Apply realistic slippage from bid/ask depth data."""
    bid = depth.get("bid", 0) or depth.get("best_bid", 0)
    ask = depth.get("ask", 0) or depth.get("best_ask", 0)

    if not bid or not ask or bid <= 0 or ask <= 0:
        return None

    if action == "BUY":
        fill_price = ask
    else:
        fill_price = bid

    spread = ask - bid
    if spread > 5.0:
        extra = (spread - 5.0) * 0.10
        if action == "BUY":
            fill_price += extra
        else:
            fill_price -= extra

    vix_jitter = 0.0
    if vix >= 25:
        vix_jitter = 0.003
    elif vix >= 18:
        vix_jitter = 0.001
    if hour <= 9 or hour >= 15:
        vix_jitter += 0.001

    if action == "BUY":
        fill_price *= (1 + vix_jitter)
    else:
        fill_price *= (1 - vix_jitter)

    fill_price += random.uniform(-0.05, 0.05)

    return round(max(fill_price, 0.05), 2)


def apply_slippage(price: float, action: str, *, depth: dict = None,
                   **kwargs) -> float:
    """Apply slippage. Uses depth if available, else formula."""
    if depth:
        result = _apply_depth_slippage(price, action, depth,
                                       vix=kwargs.get("vix", 14.0),
                                       hour=kwargs.get("hour", 10))
        if result is not None:
            return result

    mult = compute_slippage(action, **kwargs)
    if action == "BUY":
        return round(price * mult, 2)
    else:
        return round(price / mult, 2)
