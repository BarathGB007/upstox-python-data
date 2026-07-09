"""Fetch NIFTY option chain with IV, Greeks, OI, and bid/ask."""

import sys
sys.path.insert(0, "..")

from upstox_data import get_option_chain, get_nearest_expiry

symbol = "NIFTY"
expiry = get_nearest_expiry(symbol)
if not expiry:
    print("No expiry dates found")
    sys.exit(1)

print(f"Fetching {symbol} chain for expiry {expiry}...\n")

chain = get_option_chain(symbol, expiry, num_strikes=5)
if not chain:
    print("No chain data")
    sys.exit(1)

print(f"Spot: {chain['spot']:,.2f}  |  ATM: {chain['atm_strike']}  |  DTE: {chain['dte']}")
print(f"Max Pain: {chain['max_pain']}  |  PCR: {chain['pcr']}")
print(f"Lot Size: {chain['lot_size']}")
print()

header = f"{'Strike':>8}  {'CE LTP':>8} {'CE IV':>6} {'CE OI':>10}  |  {'PE LTP':>8} {'PE IV':>6} {'PE OI':>10}"
print(header)
print("-" * len(header))

for row in chain["chain"]:
    atm_marker = " *" if row["is_atm"] else "  "
    ce = row["CE"]
    pe = row["PE"]
    print(f"{row['strike']:>8}{atm_marker} {ce['ltp']:>7.2f} {ce['iv']:>5.1f}% {ce['oi']:>10,}  |  "
          f"{pe['ltp']:>7.2f} {pe['iv']:>5.1f}% {pe['oi']:>10,}")
