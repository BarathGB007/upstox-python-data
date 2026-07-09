"""Get NIFTY and BANKNIFTY spot prices."""

import sys
sys.path.insert(0, "..")

from upstox_data import get_spot, get_ltp, get_vix

# Full spot data (OHLC + prev close + change %)
for symbol in ["NIFTY", "BANKNIFTY"]:
    spot = get_spot(symbol)
    if spot:
        print(f"\n{spot['symbol']}")
        print(f"  LTP:        {spot['ltp']:,.2f}")
        print(f"  Open:       {spot['open']:,.2f}")
        print(f"  High:       {spot['day_high']:,.2f}")
        print(f"  Low:        {spot['day_low']:,.2f}")
        print(f"  Prev Close: {spot['prev_close']:,.2f}")
        print(f"  Change:     {spot['change_pct']:+.2f}%")
    else:
        print(f"\n{symbol}: no data (market may be closed)")

# Lightweight LTP only
print(f"\nNIFTY LTP: {get_ltp('NIFTY')}")
print(f"India VIX: {get_vix()}")
