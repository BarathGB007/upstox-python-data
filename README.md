# Upstox Python Data Layer

Clean Python wrapper for Upstox market data — REST API + WebSocket ticks for NSE indices and F&O.

Built for my algo trading project. Sharing because half the sub is stuck at "how do I even get clean ticks."

## What's included

| File | What it does |
|------|-------------|
| `upstox_data.py` | REST API — spot, option chains, candles, VIX, FII/DII, PCR, max pain, OI |
| `upstox_websocket.py` | WebSocket — real-time LTP, bid/ask depth, option Greeks via protobuf |
| `heartbeat.py` | Background LTP poller with WebSocket integration + spike detection |
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

### WebSocket
- `MarketStreamer` — Real-time ticks with auto-reconnect
  - Modes: `ltpc` (LTP only), `full` (depth + OI), `option_greeks` (Greeks + IV)
  - Subscribe/unsubscribe/change mode on the fly

### Heartbeat
- `DataHeartbeat` — Background LTP cache with REST fallback
  - `get_ltp(symbol)` — Instant cached LTP
  - `check_spike(symbol)` — Detect price moves over time window
  - `register_callback(fn)` — Get notified on every tick

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

## Notes

- **Token:** Uses Upstox analytics token — long-lived, no OAuth refresh needed. Just paste in `.env`
- **Market hours:** NSE is 9:15 - 15:30 IST. No ticks before 9:15
- **Rate limits:** Built-in retry with exponential backoff for 429/5xx errors
- **v3 candle limits:** 1-15min candles go back 30 days, 30min up to 90 days
- **Daily candles** do NOT include today — use `get_spot()` for live price
- This is a data layer only — no order placement, no trading logic

## License

MIT — do whatever you want with it.
