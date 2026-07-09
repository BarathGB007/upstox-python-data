# Upstox Python Data Layer

Clean Python wrapper for Upstox market data — REST API + WebSocket ticks for NSE indices and F&O.

Built for my algo trading project. Sharing because half the sub is stuck at "how do I even get clean ticks."

## What's included

| File | What it does |
|------|-------------|
| `upstox_data.py` | REST API — spot, option chains, candles, VIX, FII/DII, PCR, max pain, OI |
| `upstox_websocket.py` | WebSocket — real-time LTP, bid/ask depth, option Greeks via protobuf |
| `heartbeat.py` | Background LTP poller with WebSocket integration + spike detection |
| `paper_broker.py` | Simulated broker — realistic slippage, NSE costs, position tracking, spreads |
| `slippage.py` | Slippage model — depth-based (bid/ask) + formula fallback |
| `costs.py` | NSE F&O charge calculator — brokerage, STT, exchange txn, SEBI, stamp, GST |
| `examples/` | Ready-to-run scripts for each feature |

## Setup

1. Create an [Upstox Developer](https://account.upstox.com/developer/apps) account
2. Get your **analytics access token** (long-lived, no refresh needed for market data)
3. Copy `.env.example` to `.env` and paste your token
4. Install dependencies:

```bash
pip install -r requirements.txt
```

## Quick start

```python
from upstox_data import get_spot, get_option_chain, get_nearest_expiry

# Spot price
spot = get_spot("NIFTY")
print(f"NIFTY: {spot['ltp']:,.2f} ({spot['change_pct']:+.2f}%)")

# Option chain
expiry = get_nearest_expiry("NIFTY")
chain = get_option_chain("NIFTY", expiry)
for row in chain["chain"]:
    if row["is_atm"]:
        print(f"ATM {row['strike']}: CE={row['CE']['ltp']} PE={row['PE']['ltp']}")
```

## Examples

```bash
cd examples
python spot_price.py       # NIFTY/BANKNIFTY spot + VIX
python option_chain.py     # Full chain with IV, Greeks, OI
python live_ticks.py       # WebSocket real-time ticks
python candles.py          # Daily + intraday OHLCV candles
python sentiment.py        # VIX, PCR, FII/DII, max pain, OI
python paper_trading.py    # Simulated order with slippage + costs
```

## Available functions

### Spot & LTP
- `get_spot(symbol)` — Full quote: LTP, OHLC, prev close, change %
- `get_ltp(symbol)` — Lightweight LTP only
- `get_vix()` — India VIX value
- `get_ltp_detail(instrument_key)` — Rich LTP for any instrument key
- `get_market_depth(instrument_key)` — 5 bid / 5 ask depth

### Options
- `get_option_chain(symbol, expiry, num_strikes)` — Chain with IV, Greeks, OI, bid/ask, max pain, PCR
- `get_greeks(instrument_keys)` — Delta, gamma, theta, vega, IV for up to 50 contracts
- `get_expiry_dates(symbol)` — All valid expiry dates
- `get_nearest_expiry(symbol)` — Nearest expiry

### Historical candles
- `fetch_daily_candles(symbol, days)` — Daily OHLCV, up to 10 years
- `fetch_intraday_candles(symbol, days, interval_min)` — 1/5/15/30 min candles
- `fetch_vix_history(days)` — Daily VIX close values

### Sentiment
- `get_pcr(symbol, expiry)` — Put-call ratio with intraday insights
- `get_max_pain(symbol, expiry)` — Max pain strike with insights
- `get_oi(symbol, expiry)` — Open interest per strike
- `get_oi_change(symbol, expiry)` — OI change (buildup/unwinding)
- `get_fii_activity()` — FII buy/sell/net in crores
- `get_dii_activity()` — DII buy/sell/net in crores
- `get_market_holidays()` — NSE holidays for the year

### Margin & Charges (no order placed)
- `get_order_margin(symbol, expiry, legs)` — Real margin for single or multi-leg orders
- `get_brokerage(instrument_key, qty, product, txn_type, price)` — Brokerage + STT + stamp duty + all charges

### WebSocket
- `MarketStreamer` — Real-time ticks with auto-reconnect
  - Modes: `ltpc` (LTP only), `full` (depth + OI), `option_greeks` (Greeks + IV)
  - Subscribe/unsubscribe/change mode on the fly

### Heartbeat
- `DataHeartbeat` — Background LTP cache with REST fallback
  - `get_ltp(symbol)` — Instant cached LTP
  - `check_spike(symbol)` — Detect price moves over time window
  - `register_callback(fn)` — Get notified on every tick

### Paper Broker
- `PaperBroker(capital, max_lots, max_positions)` — Simulated order execution
  - `place_order(...)` — Fill at slipped price with margin validation
  - `place_spread(...)` — Multi-leg spread with atomic rollback
  - `close_position(id, reason)` — Close with exit slippage + full cost breakdown
  - `update_price(id, ltp)` — Update MFE/MAE tracking from live ticks
  - `set_sl_target(id, sl_pct, target_pct)` — Attach stop loss + target
  - `get_portfolio()` — Full snapshot: positions, P&L, capital, lot usage
  - Positions persist to `data/positions.json` across restarts

## Supported symbols

| Symbol | Instrument Key | Notes |
|--------|---------------|-------|
| NIFTY | `NSE_INDEX\|Nifty 50` | Weekly expiry |
| BANKNIFTY | `NSE_INDEX\|Nifty Bank` | Weekly expiry |
| FINNIFTY | `NSE_INDEX\|Nifty Fin Service` | Monthly expiry |
| VIX | `NSE_INDEX\|India VIX` | No options |

## Adding stocks

The code works with any NSE instrument — just add the instrument key to the `INSTRUMENT_KEYS` dict in `upstox_data.py`:

```python
INSTRUMENT_KEYS = {
    "NIFTY":     "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    # Add stocks:
    "RELIANCE":  "NSE_EQ|INE002A01018",
    "TCS":       "NSE_EQ|INE467B01029",
    "HDFCBANK":  "NSE_EQ|INE040A01034",
    "INFY":      "NSE_EQ|INE009A01021",
    "SBIN":      "NSE_EQ|INE062A01020",
}
```

**Finding instrument keys:** The format is `NSE_EQ|<ISIN>` for stocks. You can find the ISIN on the [NSE website](https://www.nseindia.com) — search the stock and look for ISIN in the details.

For stock F&O options, also add the strike step and lot size:

```python
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "RELIANCE": 20, "TCS": 50}
LOT_SIZES = {"NIFTY": 75, "BANKNIFTY": 30, "RELIANCE": 250, "TCS": 150}
```

Then use the same functions:

```python
spot = get_spot("RELIANCE")
chain = get_option_chain("RELIANCE", "2026-07-31")
df = fetch_daily_candles("RELIANCE", days=90)
```

For WebSocket ticks on stocks:

```python
ws.subscribe(["NSE_EQ|INE002A01018"], mode="full")  # RELIANCE live ticks
```

## Integrating with your algo

This is a data layer — plug it into whatever strategy you're building. Some ideas:

### Signal generation
```python
from upstox_data import fetch_intraday_candles, get_spot
import pandas as pd

# Compute RSI on live 5-min candles
df = fetch_intraday_candles("NIFTY", days=1, interval_min=5)
delta = df["close"].diff()
gain = delta.where(delta > 0, 0).rolling(14).mean()
loss = -delta.where(delta < 0, 0).rolling(14).mean()
rsi = 100 - (100 / (1 + gain / loss))
print(f"Current RSI(14): {rsi.iloc[-1]:.1f}")
```

### Entry/exit with WebSocket ticks
```python
from upstox_websocket import MarketStreamer
from upstox_data import INSTRUMENT_KEYS

def strategy_tick(tick):
    ltp = tick.get("ltp", 0)
    # Your logic here — crossover, breakout, mean reversion, etc.
    if ltp > your_entry_level:
        place_order(...)  # Use your own broker's order API

ws = MarketStreamer(on_tick=strategy_tick)
ws.start()
ws.subscribe([INSTRUMENT_KEYS["NIFTY"]], mode="full")
```

### Option strategy screening
```python
from upstox_data import get_option_chain, get_nearest_expiry

chain = get_option_chain("NIFTY", get_nearest_expiry("NIFTY"))
for row in chain["chain"]:
    ce, pe = row["CE"], row["PE"]
    # Find high IV strikes for selling
    if ce["iv"] > 15 and ce["oi"] > 1_000_000:
        print(f"CE {row['strike']}: IV={ce['iv']}% OI={ce['oi']:,} spread={ce['spread_pct']:.1f}%")
    # Find cheap PE hedges
    if pe["ltp"] < 10 and pe["delta"] > -0.15:
        print(f"Cheap PE hedge: {row['strike']} @ {pe['ltp']}")
```

### Sentiment-based filters
```python
from upstox_data import get_vix, get_fii_activity, get_pcr, get_nearest_expiry

vix = get_vix()
fii = get_fii_activity()
pcr = get_pcr("NIFTY", get_nearest_expiry("NIFTY"))

# Simple regime filter
if vix > 20 and fii["net_amount"] < -1000:
    print("High vol + FII selling — avoid naked longs")
elif vix < 13 and pcr["pcr"] > 1.2:
    print("Low vol + high PCR — bullish setup")
```

### Spike detection with heartbeat
```python
from heartbeat import DataHeartbeat

hb = DataHeartbeat(symbols=["NIFTY", "BANKNIFTY"], interval=5)
hb.start()

# Check for sudden moves
spike = hb.check_spike("NIFTY", threshold_pct=0.3, window_min=2)
if spike["spike"]:
    print(f"NIFTY spike {spike['direction']} {spike['move_pct']:.2f}% in 2 min")
```

### Backtesting with historical candles
```python
from upstox_data import fetch_daily_candles

df = fetch_daily_candles("BANKNIFTY", days=365)
# Compute your indicators, run your strategy, track P&L
df["sma_20"] = df["close"].rolling(20).mean()
df["sma_50"] = df["close"].rolling(50).mean()
df["signal"] = (df["sma_20"] > df["sma_50"]).astype(int)
crossovers = df["signal"].diff().abs().sum()
print(f"Golden/death crosses in 1 year: {int(crossovers)}")
```

### Realistic paper trading
```python
from paper_broker import PaperBroker
from upstox_data import get_spot, get_option_chain, get_nearest_expiry
from upstox_websocket import MarketStreamer

broker = PaperBroker(capital=500_000, max_lots=5)

# Place an order with realistic fill
chain = get_option_chain("NIFTY", get_nearest_expiry("NIFTY"))
atm = next(r for r in chain["chain"] if r["is_atm"])
result = broker.place_order(
    symbol="NIFTY", expiry=chain["expiry"],
    strike=atm["strike"], option_type="CE", action="BUY", quantity=1,
    option_ltp_hint=atm["CE"]["ltp"],
    depth_hint={"bid": atm["CE"]["bid"], "ask": atm["CE"]["ask"]},  # real bid/ask
)

# Stream ticks to update position MFE/MAE
def on_tick(tick):
    for pos_id, pos in broker.positions.items():
        if tick.get("instrument_key") == pos.get("instrument_key"):
            broker.update_price(pos_id, tick["ltp"])
            # Auto SL/target check
            sl_info = broker.get_sl_targets().get(pos_id)
            if sl_info and tick["ltp"] <= sl_info["stop_loss"]:
                broker.close_position(pos_id, "stop loss hit")

ws = MarketStreamer(on_tick=on_tick)
ws.start()
```

**Why this beats LTP-based paper trading:**
- **Fills at bid/ask** — BUY fills at ask, SELL at bid, not mid-price
- **Slippage model** — wider for OTM, high VIX, open/close, illiquid symbols
- **Full NSE costs** — brokerage, STT, exchange txn, SEBI, stamp duty, GST
- **MFE/MAE tracking** — know your max favorable/adverse excursion per trade
- **Spread support** — multi-leg orders with atomic rollback on partial fills
- **Margin validation** — per-trade and total capital limits enforced
- **Position persistence** — survives restarts via JSON state file

## Notes

- **Token:** Uses Upstox analytics token — long-lived, no OAuth refresh needed. Just paste in `.env`
- **Market hours:** NSE is 9:15 - 15:30 IST. No ticks before 9:15
- **Rate limits:** Built-in retry with exponential backoff for 429/5xx errors
- **v3 candle limits:** 1-15min candles go back 30 days, 30min up to 90 days
- **Daily candles** do NOT include today — use `get_spot()` for live price
- Includes paper broker for simulated trading — no real orders are placed

## License

MIT — do whatever you want with it.
