"""Stream real-time ticks via WebSocket."""

import sys
import time
sys.path.insert(0, "..")

from upstox_data import INSTRUMENT_KEYS
from upstox_websocket import MarketStreamer

def on_tick(tick):
    key = tick["instrument_key"]
    ltp = tick.get("ltp", 0)

    # Identify symbol
    symbol = "UNKNOWN"
    for name, ikey in INSTRUMENT_KEYS.items():
        if ikey == key:
            symbol = name
            break

    parts = [f"{symbol} LTP={ltp:,.2f}"]
    if "bid" in tick:
        parts.append(f"bid={tick['bid']:.2f}")
    if "ask" in tick:
        parts.append(f"ask={tick['ask']:.2f}")
    if "oi" in tick:
        parts.append(f"OI={tick['oi']:,}")

    print(" | ".join(parts))

def on_disconnect():
    print("WebSocket disconnected!")

# Create and start streamer
ws = MarketStreamer(on_tick=on_tick, on_disconnect=on_disconnect)
ws.start()

# Subscribe to NIFTY and BANKNIFTY indices
keys = [INSTRUMENT_KEYS["NIFTY"], INSTRUMENT_KEYS["BANKNIFTY"]]
ws.subscribe(keys, mode="ltpc")  # ltpc = LTP + last traded qty + close price

# For full depth (bid/ask/OI), use mode="full":
# ws.subscribe(keys, mode="full")

# For option Greeks (IV, delta, gamma, theta, vega), use mode="option_greeks":
# ws.subscribe(["NSE_FO|51834"], mode="option_greeks")

print("Streaming... Press Ctrl+C to stop.\n")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    ws.stop()
    print("\nStopped.")
