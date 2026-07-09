"""Fetch historical daily and intraday candles."""

import sys
sys.path.insert(0, "..")

from upstox_data import fetch_daily_candles, fetch_intraday_candles, fetch_vix_history

# Daily candles — last 30 days
print("=== NIFTY Daily (last 30 days) ===")
df = fetch_daily_candles("NIFTY", days=30)
if df is not None:
    print(df.tail(10).to_string())
    print(f"\nTotal bars: {len(df)}")
else:
    print("No data")

# Intraday 15-min candles — last 5 days
print("\n=== BANKNIFTY 15-min (last 5 days) ===")
df = fetch_intraday_candles("BANKNIFTY", days=5, interval_min=15)
if df is not None:
    print(df.tail(10).to_string())
    print(f"\nTotal bars: {len(df)}")
else:
    print("No data")

# Intraday 5-min candles — today only
print("\n=== NIFTY 5-min (today) ===")
df = fetch_intraday_candles("NIFTY", days=1, interval_min=5)
if df is not None:
    print(df.to_string())
else:
    print("No data (market may not have opened yet)")

# VIX history — last 90 days
print("\n=== India VIX (last 90 days) ===")
vix = fetch_vix_history(days=90)
if vix is not None:
    print(f"Current: {vix['vix'].iloc[-1]:.2f}")
    print(f"90-day avg: {vix['vix'].mean():.2f}")
    print(f"90-day high: {vix['vix'].max():.2f}")
    print(f"90-day low: {vix['vix'].min():.2f}")
else:
    print("No data")
