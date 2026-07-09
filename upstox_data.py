"""
Upstox Market Data API — spot, option chains, candles, sentiment, OI.

Uses Upstox v2 + v3 REST APIs. Requires an analytics access token
(long-lived, no refresh needed) set as UPSTOX_ACCESS_TOKEN in .env.

NSE market hours: 9:15 - 15:30 IST. No data before 9:15.
"""

import os
import time
import logging
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_expiry_cache: dict[str, tuple[float, list[str]]] = {}
_EXPIRY_TTL = 3600

_BASE = "https://api.upstox.com/v2"
_BASE_V3 = "https://api.upstox.com/v3"
_session: requests.Session | None = None

INSTRUMENT_KEYS = {
    "NIFTY":     "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY":  "NSE_INDEX|Nifty Fin Service",
    "VIX":       "NSE_INDEX|India VIX",
}

STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}

LOT_SIZES = {"NIFTY": 75, "BANKNIFTY": 30, "FINNIFTY": 25}

_V3_MAX_DAYS = {1: 30, 5: 30, 15: 30, 30: 90, 60: 90}


# ══════════════════════════════════════════════════════════════════
#  SESSION
# ══════════════════════════════════════════════════════════════════

def _get_session() -> requests.Session:
    global _session
    if _session is None:
        token = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
        if not token:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN not set in environment")
        _session = requests.Session()
        _session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
    return _session


def reset_session() -> None:
    """Close and reset the session."""
    global _session
    if _session is not None:
        _session.close()
    _session = None


def is_available() -> bool:
    """Check if UPSTOX_ACCESS_TOKEN is set."""
    return bool(os.environ.get("UPSTOX_ACCESS_TOKEN", ""))


def _request(method: str, url: str, params: dict = None,
             payload: dict = None, timeout: int = 10) -> dict | None:
    """Base HTTP request with 401/429/5xx retry logic."""
    tag = url.split("upstox.com/")[-1]
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            s = _get_session()
            if method == "GET":
                r = s.get(url, params=params, timeout=timeout)
            elif method == "POST":
                r = s.post(url, json=payload, timeout=timeout)
            else:
                return None

            if r.status_code == 401 and attempt == 0:
                log.warning("Upstox 401 on %s — resetting session, retry", tag)
                reset_session()
                continue
            if r.status_code == 429 and attempt < max_retries:
                wait = min(2 ** attempt, 8)
                log.warning("Upstox 429 on %s — backoff %ds", tag, wait)
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503) and attempt < 1:
                log.warning("Upstox %d on %s — retry in 1s", r.status_code, tag)
                time.sleep(1)
                continue

            if r.status_code != 200:
                log.debug("Upstox %s %s: HTTP %d", method, tag, r.status_code)
                return None
            body = r.json()
            if body.get("status") != "success":
                log.debug("Upstox %s %s: status=%s", method, tag, body.get("status"))
                return None
            return body.get("data")
        except requests.RequestException as e:
            log.warning("Upstox %s request failed: %s", method, e)
            return None
    return None


def _get(url: str, params: dict = None, timeout: int = 10) -> dict | None:
    return _request("GET", url, params=params, timeout=timeout)


def _get_v3(path: str, params: dict = None, timeout: int = 10) -> dict | None:
    return _get(f"{_BASE_V3}{path}", params, timeout)


def _post(url: str, payload: dict, timeout: int = 10) -> dict | None:
    return _request("POST", url, payload=payload, timeout=timeout)


# ══════════════════════════════════════════════════════════════════
#  SPOT PRICE
# ══════════════════════════════════════════════════════════════════

def get_spot(symbol: str) -> dict | None:
    """
    Spot quote for an index (NIFTY, BANKNIFTY, FINNIFTY).
    Returns: symbol, ltp, open, day_high, day_low, prev_close, change_pct, vix.
    """
    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return None

    data = _get_v3("/market-quote/ohlc", {"instrument_key": key, "interval": "1d"})
    if data:
        entry = list(data.values())[0]
        ltp = float(entry.get("last_price", 0))
        live = entry.get("live_ohlc") or {}
        prev_ohlc = entry.get("prev_ohlc") or {}
        prev = round(float(prev_ohlc.get("close", 0) or 0), 2)
        if not prev:
            v2 = _get(f"{_BASE}/market-quote/quotes", {"instrument_key": key})
            if v2:
                v2e = list(v2.values())[0]
                nc = float(v2e.get("net_change", 0) or 0)
                prev = round(ltp - nc, 2) if nc else round(ltp, 2)
            else:
                prev = round(ltp, 2)
        chg = round((ltp - prev) / prev * 100, 2) if prev else 0.0
        vix = get_vix()
        return {
            "symbol":     symbol,
            "ltp":        round(ltp, 2),
            "open":       round(float(live.get("open", ltp)), 2),
            "day_high":   round(float(live.get("high", ltp)), 2),
            "day_low":    round(float(live.get("low", ltp)), 2),
            "prev_close": prev,
            "change_pct": chg,
            "vix":        vix,
            "source":     "UPSTOX_V3",
        }

    data = _get(f"{_BASE}/market-quote/quotes", {"instrument_key": key})
    if not data:
        return None

    entry = list(data.values())[0]
    ohlc = entry.get("ohlc", {})
    ltp = float(entry.get("last_price", 0))
    net_change = float(entry.get("net_change", 0) or 0)
    prev = round(ltp - net_change, 2)
    chg = round(net_change / prev * 100, 2) if prev else 0.0
    vix = get_vix()

    return {
        "symbol":     symbol,
        "ltp":        round(ltp, 2),
        "open":       round(float(ohlc.get("open", ltp)), 2),
        "day_high":   round(float(ohlc.get("high", ltp)), 2),
        "day_low":    round(float(ohlc.get("low", ltp)), 2),
        "prev_close": prev,
        "change_pct": chg,
        "vix":        vix,
        "source":     "UPSTOX",
    }


def get_ltp(symbol: str) -> float | None:
    """Lightweight LTP-only fetch."""
    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return None

    data = _get_v3("/market-quote/ltp", {"instrument_key": key}, timeout=5)
    if data:
        entry = list(data.values())[0]
        return round(float(entry.get("last_price", 0)), 2)

    data = _get(f"{_BASE}/market-quote/ltp", {"instrument_key": key}, timeout=5)
    if not data:
        return None
    return round(float(list(data.values())[0].get("last_price", 0)), 2)


def get_vix() -> float:
    """Current India VIX value. Returns 15.0 as fallback."""
    key = INSTRUMENT_KEYS["VIX"]
    data = _get_v3("/market-quote/ltp", {"instrument_key": key}, timeout=5)
    if not data:
        data = _get(f"{_BASE}/market-quote/ltp", {"instrument_key": key}, timeout=5)
    if data:
        try:
            return round(float(list(data.values())[0]["last_price"]), 2)
        except (KeyError, IndexError, TypeError):
            pass
    return 15.0


# ══════════════════════════════════════════════════════════════════
#  LTP DETAIL + OPTION GREEKS (v3)
# ══════════════════════════════════════════════════════════════════

def get_ltp_detail(instrument_key: str) -> dict | None:
    """
    Rich LTP via v3 — returns last_price, ltq, volume, cp.
    Takes raw instrument_key (e.g. 'NSE_FO|51834'), not symbol name.
    """
    data = _get_v3("/market-quote/ltp", {"instrument_key": instrument_key}, timeout=5)
    if not data:
        return None
    try:
        entry = list(data.values())[0]
        return {
            "last_price": round(float(entry.get("last_price", 0)), 2),
            "ltq": int(entry.get("ltq", 0)),
            "volume": int(entry.get("volume", 0)),
            "cp": round(float(entry.get("cp", 0)), 2),
        }
    except (KeyError, IndexError, TypeError):
        return None


def get_greeks(instrument_keys: list[str]) -> dict | None:
    """
    Option Greeks via v3 — delta, gamma, theta, vega, IV, OI.
    Up to 50 instrument keys per call.
    """
    if not instrument_keys:
        return None
    if len(instrument_keys) > 50:
        instrument_keys = instrument_keys[:50]

    keys_str = ",".join(instrument_keys)
    data = _get_v3("/market-quote/option-greek", {"instrument_key": keys_str}, timeout=10)
    if not data:
        return None

    result = {}
    for raw_key, entry in data.items():
        key = raw_key.replace(":", "|", 1)
        result[key] = {
            "last_price": round(float(entry.get("last_price", 0)), 2),
            "iv": round(float(entry.get("iv", 0)), 4),
            "delta": round(float(entry.get("delta", 0)), 4),
            "gamma": round(float(entry.get("gamma", 0)), 6),
            "theta": round(float(entry.get("theta", 0)), 4),
            "vega": round(float(entry.get("vega", 0)), 4),
            "oi": int(entry.get("oi", 0)),
            "volume": int(entry.get("volume", 0)),
        }

    return result if result else None


# ══════════════════════════════════════════════════════════════════
#  EXPIRY DATES
# ══════════════════════════════════════════════════════════════════

def get_expiry_dates(symbol: str) -> list[str]:
    """
    Fetch valid expiry dates for a symbol.
    Returns sorted list of YYYY-MM-DD strings (future dates only).
    Cached for 1 hour.
    """
    now = time.time()
    cached = _expiry_cache.get(symbol)
    if cached and (now - cached[0]) < _EXPIRY_TTL:
        return cached[1]

    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return []

    data = _get(f"{_BASE}/option/contract", {"instrument_key": key}, timeout=10)
    if not data:
        return cached[1] if cached else []

    today = date.today()
    expiries = set()
    for contract in data:
        exp_str = contract.get("expiry")
        if not exp_str:
            continue
        try:
            exp_date = date.fromisoformat(exp_str[:10])
            if exp_date >= today:
                expiries.add(exp_str[:10])
        except ValueError:
            continue

    result = sorted(expiries)
    _expiry_cache[symbol] = (now, result)
    log.info("%s expiries: %s", symbol, result[:5])
    return result


def get_nearest_expiry(symbol: str) -> str | None:
    """Get the nearest valid expiry date for a symbol."""
    expiries = get_expiry_dates(symbol)
    return expiries[0] if expiries else None


# ══════════════════════════════════════════════════════════════════
#  OPTION CHAIN
# ══════════════════════════════════════════════════════════════════

def get_option_chain(symbol: str, expiry_date: str,
                     num_strikes: int = 10) -> dict | None:
    """
    Full option chain with IV, Greeks, OI, bid/ask.

    Args:
        symbol: NIFTY, BANKNIFTY, or FINNIFTY
        expiry_date: YYYY-MM-DD
        num_strikes: strikes on each side of ATM

    Returns dict with: symbol, spot, atm_strike, expiry, dte, lot_size,
    max_pain, total_ce_oi, total_pe_oi, pcr, chain[].
    """
    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return None

    data = _get(
        f"{_BASE}/option/chain",
        {"instrument_key": key, "expiry_date": expiry_date},
        timeout=10,
    )
    if not data:
        return None

    raw = data if isinstance(data, list) else []
    if not raw:
        return None

    spot = float(raw[0].get("underlying_spot_price", 0) or 0)
    if spot <= 0:
        spot_data = get_spot(symbol)
        spot = spot_data["ltp"] if spot_data else 0
    if spot <= 0:
        return None

    step = STRIKE_STEP.get(symbol, 50)
    lot_size = LOT_SIZES.get(symbol, 50)
    atm = round(spot / step) * step

    exp_dt = date.fromisoformat(expiry_date)
    dte = (exp_dt - date.today()).days

    chain = []
    total_ce_oi = 0
    total_pe_oi = 0

    for row in sorted(raw, key=lambda r: r.get("strike_price", 0)):
        strike = int(row.get("strike_price", 0))
        if abs(strike - atm) > step * num_strikes:
            continue

        ce = row.get("call_options", {})
        pe = row.get("put_options", {})
        ce_ikey = ce.get("instrument_key", "")
        pe_ikey = pe.get("instrument_key", "")
        ce_md = ce.get("market_data", {})
        pe_md = pe.get("market_data", {})
        ce_gr = ce.get("option_greeks", {})
        pe_gr = pe.get("option_greeks", {})

        ce_ltp = float(ce_md.get("ltp", 0) or 0)
        pe_ltp = float(pe_md.get("ltp", 0) or 0)
        ce_oi = int(ce_md.get("oi", 0) or 0)
        pe_oi = int(pe_md.get("oi", 0) or 0)
        total_ce_oi += ce_oi
        total_pe_oi += pe_oi

        ce_bid = float(ce_md.get("bid_price", 0) or 0) or ce_ltp * 0.995
        ce_ask = float(ce_md.get("ask_price", 0) or 0) or ce_ltp * 1.005
        pe_bid = float(pe_md.get("bid_price", 0) or 0) or pe_ltp * 0.995
        pe_ask = float(pe_md.get("ask_price", 0) or 0) or pe_ltp * 1.005

        ce_side = _build_option_side(ce_ltp, ce_bid, ce_ask, ce_oi, ce_md, ce_gr, 0.5)
        pe_side = _build_option_side(pe_ltp, pe_bid, pe_ask, pe_oi, pe_md, pe_gr, -0.5)
        ce_side["instrument_key"] = ce_ikey
        pe_side["instrument_key"] = pe_ikey

        chain.append({
            "strike": strike,
            "is_atm": strike == atm,
            "CE": ce_side,
            "PE": pe_side,
        })

    if not chain:
        return None

    pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 1.0
    max_pain = _calc_max_pain(chain)

    return {
        "symbol":      symbol,
        "spot":        round(spot, 2),
        "atm_strike":  atm,
        "expiry":      expiry_date,
        "dte":         dte,
        "lot_size":    lot_size,
        "max_pain":    max_pain,
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "pcr":         pcr,
        "chain":       chain,
        "source":      "UPSTOX",
    }


def _build_option_side(ltp, bid, ask, oi, md, greeks, default_delta) -> dict:
    return {
        "ltp":        round(ltp, 2),
        "bid":        round(bid, 2),
        "ask":        round(ask, 2),
        "spread_pct": round((ask - bid) / ltp * 100, 2) if ltp > 0 else 0,
        "iv":         round(float(greeks.get("iv", 0) or 0), 2),
        "oi":         oi,
        "volume":     int(md.get("volume", 0) or 0),
        "delta":      round(float(greeks.get("delta", default_delta) or default_delta), 4),
        "theta":      round(float(greeks.get("theta", 0) or 0), 4),
        "vega":       round(float(greeks.get("vega", 0) or 0), 4),
    }


def _calc_max_pain(chain: list) -> int:
    """Strike where total option-writer pain is minimized."""
    strikes = [s["strike"] for s in chain]
    if not strikes:
        return 0
    best_strike, min_pain = strikes[0], float("inf")
    for test in strikes:
        pain = 0
        for s in chain:
            pain += s["CE"]["oi"] * max(0, s["strike"] - test)
            pain += s["PE"]["oi"] * max(0, test - s["strike"])
        if pain < min_pain:
            min_pain = pain
            best_strike = test
    return best_strike


# ══════════════════════════════════════════════════════════════════
#  HISTORICAL CANDLES
# ══════════════════════════════════════════════════════════════════

def _parse_candles(candles: list, include_oi: bool = False) -> pd.DataFrame:
    """Convert Upstox candle array to DataFrame."""
    rows = []
    for c in candles:
        row = {
            "date":   pd.Timestamp(c[0]),
            "open":   float(c[1]),
            "high":   float(c[2]),
            "low":    float(c[3]),
            "close":  float(c[4]),
            "volume": int(c[5]),
        }
        if include_oi and len(c) > 6:
            row["oi"] = int(c[6])
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("date").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="last")]
    return df


def fetch_daily_candles(symbol: str, days: int = 365) -> pd.DataFrame | None:
    """
    Daily OHLCV candles via v3 API. Up to ~10 years available.
    Does NOT include today — use get_spot() for live price.
    """
    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return None

    end = date.today()
    start = end - timedelta(days=days + 30)

    data = _get(
        f"{_BASE_V3}/historical-candle/{key}/days/1/{end.isoformat()}/{start.isoformat()}",
        timeout=15,
    )
    if not data:
        return None

    candles = data.get("candles", [])
    if not candles:
        return None

    df = _parse_candles(candles)
    df = df.tail(days)

    log.info("Daily %s: %d candles (%s to %s)",
             symbol, len(df), df.index[0].date(), df.index[-1].date())
    return df


def fetch_intraday_candles(symbol: str, days: int = 20,
                           interval_min: int = 15) -> pd.DataFrame | None:
    """
    Intraday OHLCV candles via v3 API. Merges historical + today.

    Supported intervals: 1, 5, 15, 30 minutes.
    Max depth: 1-15min = 30 days, 30min = 90 days.
    """
    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return None
    if interval_min not in (1, 5, 15, 30):
        return None

    max_days = _V3_MAX_DAYS.get(interval_min, 30)
    days = min(days, max_days)

    end = date.today()
    start = end - timedelta(days=days + 5)
    all_candles = []

    hist_data = _get(
        f"{_BASE_V3}/historical-candle/{key}/minutes/{interval_min}/{end.isoformat()}/{start.isoformat()}",
        timeout=15,
    )
    if hist_data:
        all_candles.extend(hist_data.get("candles", []))

    intra_data = _get(
        f"{_BASE_V3}/historical-candle/intraday/{key}/minutes/{interval_min}",
        timeout=10,
    )
    if intra_data:
        all_candles.extend(intra_data.get("candles", []))

    if not all_candles:
        return None

    df = _parse_candles(all_candles)

    bars_per_day = 375 // interval_min
    df = df.tail(days * bars_per_day)

    log.info("%dm %s: %d bars (%s to %s)",
             interval_min, symbol, len(df), df.index[0].date(), df.index[-1].date())
    return df


def fetch_vix_history(days: int = 365) -> pd.DataFrame | None:
    """Daily India VIX close values. Up to ~10 years."""
    key = INSTRUMENT_KEYS["VIX"]
    end = date.today()
    start = end - timedelta(days=days + 30)

    data = _get(
        f"{_BASE_V3}/historical-candle/{key}/days/1/{end.isoformat()}/{start.isoformat()}",
        timeout=15,
    )
    if not data:
        return None

    candles = data.get("candles", [])
    if not candles:
        return None

    rows = []
    for c in candles:
        rows.append({
            "date": pd.Timestamp(c[0][:10]),
            "vix":  float(c[4]),
        })

    df = pd.DataFrame(rows).set_index("date").sort_index()
    df = df.tail(days)

    log.info("VIX history: %d days", len(df))
    return df


# ══════════════════════════════════════════════════════════════════
#  FII / DII DATA
# ══════════════════════════════════════════════════════════════════

def get_fii_activity(segment: str = "NSE_EQ|CASH") -> dict | None:
    """
    FII (Foreign Institutional Investor) activity.
    Amounts are in crores INR.
    """
    data = _get(f"{_BASE}/market/fii", {"data_type": segment, "interval": "1D"})
    if not data:
        return None

    records = data.get(segment, [])
    if not records:
        return None

    latest = records[-1]
    buy = float(latest.get("buy_amount", 0) or 0)
    sell = float(latest.get("sell_amount", 0) or 0)

    return {
        "buy_amount":  round(buy, 2),
        "sell_amount": round(sell, 2),
        "net_amount":  round(buy - sell, 2),
        "timestamp":   latest.get("time_stamp"),
        "segment":     segment,
        "source":      "UPSTOX",
    }


def get_dii_activity() -> dict | None:
    """
    DII (Domestic Institutional Investor) activity — cash market only.
    Amounts are in crores INR.
    """
    segment = "NSE_EQ|CASH"
    data = _get(f"{_BASE}/market/dii", {"data_type": segment, "interval": "1D"})
    if not data:
        return None

    records = data.get(segment, [])
    if not records:
        return None

    latest = records[-1]
    buy = float(latest.get("buy_amount", 0) or 0)
    sell = float(latest.get("sell_amount", 0) or 0)

    return {
        "buy_amount":  round(buy, 2),
        "sell_amount": round(sell, 2),
        "net_amount":  round(buy - sell, 2),
        "timestamp":   latest.get("time_stamp"),
        "source":      "UPSTOX",
    }


# ══════════════════════════════════════════════════════════════════
#  PCR (Put-Call Ratio)
# ══════════════════════════════════════════════════════════════════

def get_pcr(symbol: str, expiry_date: str,
            data_date: str | None = None,
            bucket_interval: int = 15) -> dict | None:
    """
    Put-Call Ratio with intraday insights.
    Returns: pcr, spot_price, insights (list of {pcr, spot_price, time}).
    """
    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return None

    if data_date is None:
        data_date = date.today().isoformat()

    data = _get(f"{_BASE}/market/pcr", {
        "instrument_key": key,
        "expiry": expiry_date,
        "date": data_date,
        "bucket_interval": bucket_interval,
    })
    if not data:
        return None

    return {
        "pcr":         round(float(data.get("pcr", 0) or 0), 4),
        "spot_price":  round(float(data.get("spot_closing_price", 0) or 0), 2),
        "expiry":      data.get("expiry_date", expiry_date),
        "insights":    data.get("insights", []),
        "source":      "UPSTOX",
    }


# ══════════════════════════════════════════════════════════════════
#  MAX PAIN
# ══════════════════════════════════════════════════════════════════

def get_max_pain(symbol: str, expiry_date: str,
                 data_date: str | None = None,
                 bucket_interval: int = 15) -> dict | None:
    """
    Max Pain with intraday insights.
    Returns: max_pain (strike), spot_price, insights.
    """
    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return None

    if data_date is None:
        data_date = date.today().isoformat()

    data = _get(f"{_BASE}/market/max-pain", {
        "instrument_key": key,
        "expiry": expiry_date,
        "date": data_date,
        "bucket_interval": bucket_interval,
    })
    if not data:
        return None

    return {
        "max_pain":    int(float(data.get("max_pain", 0) or 0)),
        "spot_price":  round(float(data.get("spot_closing_price", 0) or 0), 2),
        "expiry":      data.get("expiry_date", expiry_date),
        "insights":    data.get("insights", []),
        "source":      "UPSTOX",
    }


# ══════════════════════════════════════════════════════════════════
#  OI (Open Interest)
# ══════════════════════════════════════════════════════════════════

def get_oi(symbol: str, expiry_date: str,
           data_date: str | None = None) -> dict | None:
    """
    Open Interest per strike.
    Returns: total_puts, total_calls, pcr, spot_price, strikes.
    """
    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return None

    if data_date is None:
        data_date = date.today().isoformat()

    data = _get(f"{_BASE}/market/oi", {
        "instrument_key": key,
        "expiry": expiry_date,
        "date": data_date,
    })
    if not data:
        return None

    total_puts = int(data.get("total_puts", 0) or 0)
    total_calls = int(data.get("total_calls", 0) or 0)
    pcr = round(total_puts / total_calls, 4) if total_calls > 0 else 1.0

    return {
        "total_puts":  total_puts,
        "total_calls": total_calls,
        "pcr":         pcr,
        "spot_price":  round(float(data.get("spot_closing_price", 0) or 0), 2),
        "expiry":      expiry_date,
        "strikes":     data.get("call_put_oi_data_list", []),
        "source":      "UPSTOX",
    }


def get_oi_change(symbol: str, expiry_date: str,
                  data_date: str | None = None,
                  interval_days: int = 1) -> dict | None:
    """
    Change in Open Interest per strike over N days.
    Positive = buildup, negative = unwinding.
    """
    key = INSTRUMENT_KEYS.get(symbol)
    if not key:
        return None

    if data_date is None:
        data_date = date.today().isoformat()

    data = _get(f"{_BASE}/market/change-oi", {
        "instrument_key": key,
        "expiry": expiry_date,
        "date": data_date,
        "interval": interval_days,
    })
    if not data:
        return None

    return {
        "total_put_change":  int(data.get("total_put_change_oi", 0) or 0),
        "total_call_change": int(data.get("total_call_change_oi", 0) or 0),
        "spot_price":        round(float(data.get("spot_closing_price", 0) or 0), 2),
        "expiry":            expiry_date,
        "strikes":           data.get("call_put_oi_data_list", []),
        "source":            "UPSTOX",
    }


# ══════════════════════════════════════════════════════════════════
#  MARKET HOLIDAYS & DEPTH
# ══════════════════════════════════════════════════════════════════

def get_market_holidays() -> list[dict]:
    """NSE trading holidays for the current year."""
    data = _get(f"{_BASE}/market/holidays")
    if not data:
        return []

    holidays = []
    for h in data:
        holidays.append({
            "date":         h.get("date", ""),
            "description":  h.get("description", ""),
            "holiday_type": h.get("holiday_type", ""),
        })
    return holidays


def get_market_depth(instrument_key: str) -> dict | None:
    """Get market depth (5 bid / 5 ask) via full quote endpoint."""
    data = _get(f"{_BASE}/market-quote/quotes",
                {"instrument_key": instrument_key}, timeout=10)
    if not data:
        return None
    entry = list(data.values())[0] if data else None
    if not entry:
        return None
    depth = entry.get("depth", {})
    return {
        "ltp": float(entry.get("last_price", 0)),
        "bid": depth.get("buy", []),
        "ask": depth.get("sell", []),
        "total_bid_qty": sum(b.get("quantity", 0) for b in depth.get("buy", [])),
        "total_ask_qty": sum(a.get("quantity", 0) for a in depth.get("sell", [])),
        "best_bid": float(depth.get("buy", [{}])[0].get("price", 0)) if depth.get("buy") else 0,
        "best_ask": float(depth.get("sell", [{}])[0].get("price", 0)) if depth.get("sell") else 0,
        "oi": int(entry.get("oi", 0)),
        "volume": int(entry.get("volume", 0)),
    }
