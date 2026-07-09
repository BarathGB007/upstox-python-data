"""
Trading costs calculator — NSE F&O charges as of April 2026.

Computes brokerage, STT, exchange txn, SEBI fee, stamp duty, GST
for a round-trip options trade. All rates in one place for easy updates.
"""

BROKERAGE_PER_ORDER   = 20.0        # Flat Rs 20 per executed order
STT_OPTIONS_SELL_PCT  = 0.15        # 0.15% on sell-side premium
EXCHANGE_TXN_PCT      = 0.03503     # Rs 35.03 per lakh = 0.03503%
SEBI_FEE_PCT          = 0.0001      # Rs 10 per crore = 0.0001%
STAMP_DUTY_BUY_PCT    = 0.003       # 0.003% on buy-side premium
GST_PCT               = 18.0        # 18% on (brokerage + exchange txn)


def compute_trade_costs(entry_price: float, exit_price: float,
                        total_qty: int, action: str = "BUY") -> dict:
    """
    Compute all charges for a completed round-trip options trade.

    Args:
        entry_price: per-unit entry price
        exit_price:  per-unit exit price
        total_qty:   total quantity (lots x lot_size)
        action:      "BUY" = bought first then sold, "SELL" = sold first then bought back
    """
    if action == "BUY":
        buy_premium  = entry_price * total_qty
        sell_premium = exit_price * total_qty
    else:
        sell_premium = entry_price * total_qty
        buy_premium  = exit_price * total_qty

    brokerage = BROKERAGE_PER_ORDER * 2
    stt = sell_premium * STT_OPTIONS_SELL_PCT / 100
    exchange_txn = (buy_premium + sell_premium) * EXCHANGE_TXN_PCT / 100
    sebi_fee = (buy_premium + sell_premium) * SEBI_FEE_PCT / 100
    stamp_duty = buy_premium * STAMP_DUTY_BUY_PCT / 100
    gst = (brokerage + exchange_txn) * GST_PCT / 100

    total = brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst

    return {
        "brokerage":    round(brokerage, 2),
        "stt":          round(stt, 2),
        "exchange_txn": round(exchange_txn, 2),
        "sebi_fee":     round(sebi_fee, 2),
        "stamp_duty":   round(stamp_duty, 2),
        "gst":          round(gst, 2),
        "total":        round(total, 2),
    }


def total_costs(entry_price: float, exit_price: float,
                total_qty: int, action: str = "BUY") -> float:
    """Shortcut — returns just the total cost as a float."""
    return compute_trade_costs(entry_price, exit_price, total_qty, action)["total"]
