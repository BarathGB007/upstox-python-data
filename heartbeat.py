"""
Data Heartbeat — background LTP polling with WebSocket integration.

Daemon thread that maintains a live LTP cache for indices.
When WebSocket is connected, it receives ticks passively.
When WebSocket is down, it falls back to REST polling every N seconds.

Also provides tick history and spike detection.
"""

import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable

import upstox_data as ud

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


class DataHeartbeat:
    """
    Daemon thread that fetches LTP every N seconds into a shared cache.
    Read from this cache for instant access — no network call needed.
    """

    def __init__(self, symbols: list[str] | None = None, interval: int = 5):
        self.symbols = symbols or ["NIFTY", "BANKNIFTY"]
        self.interval = interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ltp_cache: dict = {}
        self._tick_history: dict = {}
        self._callbacks: list[Callable] = []
        self._websocket = None
        self._ws_mode = False

    def enable_websocket(self, ws) -> None:
        """Wire WebSocket tick callbacks into heartbeat."""
        self._websocket = ws
        self._ws_mode = True
        log.info("Heartbeat: WebSocket mode enabled")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="DataHeartbeat", daemon=True,
        )
        self._thread.start()
        log.info("Heartbeat started — fetching LTP every %ds for %s",
                 self.interval, self.symbols)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Heartbeat stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_ltp(self, symbol: str) -> dict:
        """Get latest LTP for a symbol from cache."""
        with self._lock:
            entry = self._ltp_cache.get(symbol)
            if entry is None:
                return {"ltp": 0, "timestamp": None, "age_seconds": 999,
                        "source": "NO_DATA", "stale": True}

            now = datetime.now(IST)
            age = (now - entry["timestamp"]).total_seconds()
            return {
                "ltp":         entry["ltp"],
                "prev_ltp":    entry.get("prev_ltp", entry["ltp"]),
                "timestamp":   entry["timestamp"].strftime("%H:%M:%S"),
                "age_seconds": round(age, 1),
                "source":      entry["source"],
                "stale":       age > 30,
            }

    def get_all_ltp(self) -> dict:
        """Get LTP for all tracked symbols."""
        return {sym: self.get_ltp(sym) for sym in self.symbols}

    def get_tick_history(self, symbol: str, minutes: int = 3) -> list[tuple]:
        """Get recent tick history as [(timestamp, ltp), ...]."""
        with self._lock:
            history = self._tick_history.get(symbol, [])
            cutoff = datetime.now(IST) - timedelta(minutes=minutes)
            return [(ts, ltp) for ts, ltp in history if ts > cutoff]

    def check_spike(self, symbol: str, threshold_pct: float = 0.5,
                    window_min: int = 3) -> dict:
        """Detect price spikes over a time window."""
        ticks = self.get_tick_history(symbol, window_min)
        if len(ticks) < 2:
            return {"spike": False, "move_pct": 0.0, "direction": "FLAT"}

        first_ltp = ticks[0][1]
        last_ltp = ticks[-1][1]
        if first_ltp <= 0:
            return {"spike": False, "move_pct": 0.0, "direction": "FLAT"}

        move_pct = round((last_ltp - first_ltp) / first_ltp * 100, 3)
        return {
            "spike":     abs(move_pct) >= threshold_pct,
            "move_pct":  move_pct,
            "direction": "UP" if move_pct > 0 else ("DOWN" if move_pct < 0 else "FLAT"),
            "first_ltp": first_ltp,
            "last_ltp":  last_ltp,
            "ticks":     len(ticks),
        }

    def register_callback(self, fn: Callable) -> None:
        """Register a callback: fn(symbol, {"ltp": ..., "prev_ltp": ..., "timestamp": ...})"""
        self._callbacks.append(fn)

    def on_ws_tick(self, tick: dict) -> None:
        """Called by WebSocket on every market data tick."""
        key = tick.get("instrument_key", "")
        ltp = tick.get("ltp", 0)
        if not key or not ltp:
            return

        now = datetime.now(IST)
        sym = self._key_to_symbol(key)
        if sym:
            with self._lock:
                prev_ltp = self._ltp_cache.get(sym, {}).get("ltp", ltp)
                self._ltp_cache[sym] = {
                    "ltp": ltp, "prev_ltp": prev_ltp,
                    "timestamp": now, "source": "WEBSOCKET",
                }
                if sym not in self._tick_history:
                    self._tick_history[sym] = []
                self._tick_history[sym].append((now, ltp))
                cutoff = now - timedelta(minutes=10)
                self._tick_history[sym] = [
                    (ts, p) for ts, p in self._tick_history[sym] if ts > cutoff
                ]

            for fn in self._callbacks:
                try:
                    fn(sym, {"ltp": ltp, "prev_ltp": prev_ltp, "timestamp": now})
                except Exception:
                    pass

    @staticmethod
    def _key_to_symbol(instrument_key: str) -> str | None:
        mapping = {
            "NSE_INDEX|Nifty 50": "NIFTY",
            "NSE_INDEX|Nifty Bank": "BANKNIFTY",
            "NSE_INDEX|Nifty Fin Service": "FINNIFTY",
        }
        return mapping.get(instrument_key)

    def _run(self):
        log_counter = 0
        while not self._stop.is_set():
            ws_active = (self._ws_mode and self._websocket
                         and not self._websocket.fallback_mode)
            if not ws_active:
                self._fetch_all()

            log_counter += 1
            if log_counter >= (60 // max(self.interval, 1)):
                parts = []
                with self._lock:
                    for sym in self.symbols:
                        entry = self._ltp_cache.get(sym)
                        if entry:
                            src = entry.get("source", "?")[:2]
                            parts.append(f"{sym} {entry['ltp']:,.2f}({src})")
                if parts:
                    log.info("LTP | %s", " | ".join(parts))
                log_counter = 0
            self._stop.wait(timeout=self.interval)

    def _fetch_all(self):
        for sym in self.symbols:
            try:
                ltp = ud.get_ltp(sym)
                if ltp is None or ltp <= 0:
                    continue

                now = datetime.now(IST)

                with self._lock:
                    prev_ltp = self._ltp_cache.get(sym, {}).get("ltp", ltp)
                    self._ltp_cache[sym] = {
                        "ltp":       ltp,
                        "prev_ltp":  prev_ltp,
                        "timestamp": now,
                        "source":    "REST",
                    }

                    if sym not in self._tick_history:
                        self._tick_history[sym] = []
                    self._tick_history[sym].append((now, ltp))

                    cutoff = now - timedelta(minutes=10)
                    self._tick_history[sym] = [
                        (ts, p) for ts, p in self._tick_history[sym] if ts > cutoff
                    ]

                for fn in self._callbacks:
                    try:
                        fn(sym, {"ltp": ltp, "prev_ltp": prev_ltp, "timestamp": now})
                    except Exception:
                        pass

            except Exception as e:
                log.debug("Heartbeat %s fetch failed: %s", sym, e)
