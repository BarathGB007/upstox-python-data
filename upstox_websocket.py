"""
Upstox WebSocket — real-time market data ticks.

MarketStreamer provides tick-by-tick LTP, bid/ask depth, and option Greeks
for NSE indices and F&O instruments via Upstox's protobuf WebSocket.

Requires: upstox-python-sdk (pip install upstox-python-sdk)
Uses the analytics access token — no OAuth refresh needed for market data.
"""

import os
import threading
import time
import logging
from typing import Callable

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)


class MarketStreamer:
    """Real-time market data via Upstox MarketDataStreamerV3."""

    def __init__(self, on_tick: Callable[[dict], None],
                 on_disconnect: Callable[[], None] = None):
        self._on_tick = on_tick
        self._on_disconnect = on_disconnect
        self._streamer = None
        self._connected = False
        self._fallback = False
        self._fail_count = 0
        self._max_fails = 3
        self._subscriptions: dict[str, str] = {}
        self._pending_subs: list[tuple[list[str], str]] = []
        self._lock = threading.Lock()
        self._last_tick_time: float = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def fallback_mode(self) -> bool:
        return self._fallback

    @property
    def subscriptions(self) -> dict[str, str]:
        with self._lock:
            return dict(self._subscriptions)

    def start(self) -> None:
        """Connect to Upstox WebSocket."""
        try:
            from upstox_client import Configuration, ApiClient, MarketDataStreamerV3

            token = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
            if not token:
                log.warning("No UPSTOX_ACCESS_TOKEN — WebSocket disabled")
                self._fallback = True
                return

            cfg = Configuration()
            cfg.access_token = token
            client = ApiClient(configuration=cfg)

            self._streamer = MarketDataStreamerV3(api_client=client)
            self._streamer.auto_reconnect(True, interval=5, retry_count=50)

            self._streamer.on(MarketDataStreamerV3.Event["OPEN"], self._on_open)
            self._streamer.on(MarketDataStreamerV3.Event["MESSAGE"], self._on_message)
            self._streamer.on(MarketDataStreamerV3.Event["ERROR"], self._on_error)
            self._streamer.on(MarketDataStreamerV3.Event["CLOSE"], self._on_close)
            self._streamer.on(MarketDataStreamerV3.Event["RECONNECTING"],
                              self._on_reconnecting)
            self._streamer.on(MarketDataStreamerV3.Event["AUTO_RECONNECT_STOPPED"],
                              self._on_reconnect_stopped)

            self._streamer.connect()
            log.info("WebSocket connecting...")
        except ImportError:
            log.warning("upstox-python-sdk not installed — WebSocket disabled")
            self._fallback = True
        except Exception as e:
            log.warning("WebSocket start failed: %s", e)
            self._fallback = True

    def stop(self) -> None:
        """Disconnect gracefully."""
        if self._streamer:
            try:
                self._streamer.disconnect()
            except Exception:
                pass
        self._connected = False
        log.info("WebSocket stopped")

    def subscribe(self, instrument_keys: list[str], mode: str = "ltpc") -> None:
        """Subscribe to instruments. Modes: ltpc, full, option_greeks.
        If not yet connected, queues for delivery on connect."""
        if not self._streamer or not instrument_keys:
            return
        if not self._connected:
            with self._lock:
                self._pending_subs.append((list(instrument_keys), mode))
                for key in instrument_keys:
                    self._subscriptions[key] = mode
            log.info("WS queued %d keys in %s mode (waiting for connection)",
                     len(instrument_keys), mode)
            return
        try:
            self._streamer.subscribe(instrument_keys, mode)
            with self._lock:
                for key in instrument_keys:
                    self._subscriptions[key] = mode
            log.info("WS subscribed %d keys in %s mode: %s",
                     len(instrument_keys), mode, instrument_keys)
        except Exception as e:
            log.warning("WS subscribe failed: %s", e)

    def unsubscribe(self, instrument_keys: list[str]) -> None:
        """Unsubscribe from instruments."""
        if not self._streamer or not instrument_keys:
            return
        try:
            self._streamer.unsubscribe(instrument_keys)
            with self._lock:
                for key in instrument_keys:
                    self._subscriptions.pop(key, None)
            log.info("WS unsubscribed %d keys", len(instrument_keys))
        except Exception as e:
            log.warning("WS unsubscribe failed: %s", e)

    def change_mode(self, instrument_keys: list[str], new_mode: str) -> None:
        """Change subscription mode for instruments."""
        if not self._streamer or not instrument_keys:
            return
        try:
            self._streamer.change_mode(instrument_keys, new_mode)
            with self._lock:
                for key in instrument_keys:
                    if key in self._subscriptions:
                        self._subscriptions[key] = new_mode
            log.info("WS mode changed to %s for %d keys", new_mode, len(instrument_keys))
        except Exception as e:
            log.warning("WS change_mode failed: %s", e)

    def seconds_since_last_tick(self) -> float:
        if self._last_tick_time == 0:
            return 0
        return time.time() - self._last_tick_time

    # ── SDK callbacks ────────────────────────────────────────────

    def _on_open(self, *args) -> None:
        self._connected = True
        self._fallback = False
        self._fail_count = 0
        log.info("WebSocket connected")
        with self._lock:
            pending = list(self._pending_subs)
            self._pending_subs.clear()
            existing = dict(self._subscriptions)
        for keys, mode in pending:
            try:
                self._streamer.subscribe(keys, mode)
                log.info("WS flushed %d queued keys in %s mode", len(keys), mode)
            except Exception as e:
                log.warning("WS flush subscribe failed: %s", e)
        if not pending and existing:
            by_mode: dict[str, list[str]] = {}
            for key, mode in existing.items():
                by_mode.setdefault(mode, []).append(key)
            for mode, keys in by_mode.items():
                try:
                    self._streamer.subscribe(keys, mode)
                    log.info("WS re-subscribed %d keys in %s mode (reconnect)",
                             len(keys), mode)
                except Exception as e:
                    log.warning("WS re-subscribe failed: %s", e)

    def _on_message(self, data: dict) -> None:
        self._last_tick_time = time.time()
        try:
            ticks = self._parse_ticks(data)
            for tick in ticks:
                self._on_tick(tick)
        except Exception as e:
            log.debug("WS parse error: %s", e)

    def _on_error(self, error, *args) -> None:
        log.warning("WebSocket error: %s", error)

    def _on_close(self, *args) -> None:
        self._connected = False
        self._fail_count += 1
        log.warning("WebSocket closed (fail_count=%d)", self._fail_count)
        if self._on_disconnect:
            self._on_disconnect()

    def _on_reconnecting(self, *args) -> None:
        log.info("WebSocket reconnecting...")

    def _on_reconnect_stopped(self, reason, *args) -> None:
        self._connected = False
        self._fallback = True
        log.warning("WebSocket auto-reconnect stopped: %s — falling back to REST",
                     reason)
        if self._on_disconnect:
            self._on_disconnect()

    # ── Tick parsing ─────────────────────────────────────────────

    @staticmethod
    def _parse_ticks(data: dict) -> list[dict]:
        """Parse SDK protobuf-decoded dict into normalized tick list."""
        ticks = []
        feeds = data.get("feeds", {})
        for raw_key, feed in feeds.items():
            key = raw_key.replace(":", "|", 1)
            tick = {"instrument_key": key}

            ff = feed.get("ff", feed.get("fullFeed", feed.get("ltpc", {})))

            ltpc = ff.get("ltpc") or feed.get("ltpc", {})
            if ltpc:
                tick["ltp"] = float(ltpc.get("ltp", 0))
                tick["ltq"] = int(ltpc.get("ltq", 0))
                tick["cp"] = float(ltpc.get("cp", 0))

            market_ff = ff.get("marketFF", {})
            if market_ff:
                tick["ltp"] = float(market_ff.get("ltpc", {}).get("ltp", tick.get("ltp", 0)))
                tick["volume"] = int(market_ff.get("ltq", 0))
                tick["oi"] = int(market_ff.get("oi", 0))

                depth_info = market_ff.get("marketDepth", {})
                bids = depth_info.get("buy", [])
                asks = depth_info.get("sell", [])

                if not bids and not asks:
                    bid_ask = market_ff.get("marketLevel", {}).get("bidAskQuote", [])
                    if bid_ask:
                        bids = [{"price": q.get("bidP", 0), "quantity": q.get("bidQ", 0)} for q in bid_ask]
                        asks = [{"price": q.get("askP", 0), "quantity": q.get("askQ", 0)} for q in bid_ask]

                if bids:
                    tick["bid"] = float(bids[0].get("price", 0))
                    tick["bid_qty"] = int(bids[0].get("quantity", 0))
                    tick["total_bid_qty"] = sum(int(b.get("quantity", 0)) for b in bids)
                if asks:
                    tick["ask"] = float(asks[0].get("price", 0))
                    tick["ask_qty"] = int(asks[0].get("quantity", 0))
                    tick["total_ask_qty"] = sum(int(a.get("quantity", 0)) for a in asks)
                tick["depth_buy"] = bids
                tick["depth_sell"] = asks

            greeks = ff.get("optionGreeks", {}) or market_ff.get("optionGreeks", {})
            if greeks:
                tick["iv"] = float(greeks.get("iv", 0))
                tick["delta"] = float(greeks.get("delta", 0))
                tick["gamma"] = float(greeks.get("gamma", 0))
                tick["theta"] = float(greeks.get("theta", 0))
                tick["vega"] = float(greeks.get("vega", 0))

            if "ltp" in tick:
                ticks.append(tick)

        return ticks
