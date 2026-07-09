"""Fetch market sentiment — VIX, PCR, FII/DII, Max Pain, OI."""

import sys
sys.path.insert(0, "..")

from upstox_data import (
    get_vix, get_nearest_expiry, get_pcr, get_max_pain,
    get_fii_activity, get_dii_activity, get_oi, get_oi_change,
    get_market_holidays,
)

# VIX
vix = get_vix()
print(f"India VIX: {vix}")

# FII / DII
fii = get_fii_activity()
dii = get_dii_activity()
if fii:
    print(f"\nFII: Buy {fii['buy_amount']:,.0f} Cr | Sell {fii['sell_amount']:,.0f} Cr | Net {fii['net_amount']:+,.0f} Cr")
if dii:
    print(f"DII: Buy {dii['buy_amount']:,.0f} Cr | Sell {dii['sell_amount']:,.0f} Cr | Net {dii['net_amount']:+,.0f} Cr")

# PCR + Max Pain (need expiry)
symbol = "NIFTY"
expiry = get_nearest_expiry(symbol)
if expiry:
    print(f"\n--- {symbol} expiry {expiry} ---")

    pcr = get_pcr(symbol, expiry)
    if pcr:
        print(f"PCR: {pcr['pcr']}")

    mp = get_max_pain(symbol, expiry)
    if mp:
        print(f"Max Pain: {mp['max_pain']}")

    # OI snapshot
    oi = get_oi(symbol, expiry)
    if oi:
        print(f"Total CE OI: {oi['total_calls']:,}")
        print(f"Total PE OI: {oi['total_puts']:,}")

    # OI change (1-day)
    oi_chg = get_oi_change(symbol, expiry)
    if oi_chg:
        print(f"CE OI change: {oi_chg['total_call_change']:+,}")
        print(f"PE OI change: {oi_chg['total_put_change']:+,}")

# Market holidays
print("\n--- Upcoming Holidays ---")
holidays = get_market_holidays()
for h in holidays[:5]:
    print(f"  {h['date']} — {h['description']}")
