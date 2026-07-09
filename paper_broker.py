"""
Paper Trading Broker — simulated order execution with realistic slippage and costs.

Fills at bid/ask (not LTP), applies NSE F&O charges, tracks positions with
MFE/MAE, supports spreads with cascade close, persists state to disk.

All prices come from Upstox market data. No real orders are placed.

Usage:
    from paper_broker import PaperBroker

    broker = PaperBroker(capital=500_000)
    result = broker.place_order(
        symbol="NIFTY", expiry="2026-07-15", strike=24000,
        option_type="CE", action="BUY", quantity=1,
        option_ltp_hint=150.0,
    )
    print(result)  # {"status": "SUCCESS", "position_id": "P0001_NIFTY24000CE", ...}

    portfolio = broker.get_portfolio()
    broker.close_position(result["position_id"], reason="target hit")
"""

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from costs import compute_trade_costs
from slippage import apply_slippage

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

LOT_SIZES = {"NIFTY": 75, "BANKNIFTY": 30, "FINNIFTY": 25}
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}
_FREEZE_QTY = {"NIFTY": 1800, "BANKNIFTY": 900, "FINNIFTY": 1800}


def _now_ist() -> datetime:
    return datetime.now(IST)


class PaperBroker:
    """
    Simulated broker for paper trading NSE F&O options.

    Args:
        capital: starting capital in INR
        max_lots: maximum total BUY lots across all positions (default 5)
        max_positions: maximum concurrent positions (default 7)
        per_trade_pct: max % of capital per trade (default 20%)
        persist_dir: directory for positions.json (default ./data/)
    """

    def __init__(self, capital: float, max_lots: int = 5,
                 max_positions: int = 7, per_trade_pct: float = 20.0,
                 persist_dir: str | Path = None):
        self._capital = float(capital)
        self._start_capital = float(capital)
        self._max_lots = max_lots
        self._max_positions = max_positions
        self._per_trade_pct = per_trade_pct
        self._positions: dict[str, dict] = {}
        self._closed_trades: list[dict] = []
        self._sl_targets: dict[str, dict] = {}
        self._order_counter = 0
        self._lock = threading.RLock()

        self._heartbeat = None
        self._persist_dir = Path(persist_dir) if persist_dir else Path("data")
        self._positions_file = self._persist_dir / "positions.json"
        self._load_positions()

        log.info("Paper broker | Capital: INR %s | Max lots: %d",
                 f"{capital:,.0f}", max_lots)

    def set_heartbeat(self, heartbeat) -> None:
        """Connect a DataHeartbeat for automatic bid/ask depth on order fills."""
        self._heartbeat = heartbeat
        log.info("Paper broker: heartbeat connected — using live bid/ask for fills")

    # ── Properties ───────────────────────────────────────────────

    @property
    def positions(self) -> dict:
        with self._lock:
            return dict(self._positions)

    @property
    def closed_trades(self) -> list:
        with self._lock:
            return list(self._closed_trades)

    @property
    def start_capital(self) -> float:
        return self._start_capital

    @property
    def available_capital(self) -> float:
        with self._lock:
            return self._capital

    # ── PLACE ORDER ──────────────────────────────────────────────

    def place_order(self, symbol: str, expiry: str, strike: int,
                    option_type: str, action: str, quantity: int,
                    option_ltp_hint: float, *,
                    order_type: str = "MARKET",
                    reason: str = "",
                    strategy_name: str = "directional",
                    limit_price: float = None,
                    depth_hint: dict = None,
                    instrument_key: str = "",
                    vix: float = 14.0) -> dict:
        """
        Place a simulated order.

        Args:
            symbol: NIFTY, BANKNIFTY, FINNIFTY
            expiry: YYYY-MM-DD
            strike: option strike price
            option_type: CE or PE
            action: BUY or SELL
            quantity: number of lots
            option_ltp_hint: current option LTP (required for fill price)
            depth_hint: optional {"bid": x, "ask": y} for realistic fills
            vix: current VIX for slippage model
        """
        with self._lock:
            return self._place_order_locked(
                symbol, expiry, strike, option_type, action, quantity,
                order_type, reason, strategy_name, option_ltp_hint,
                limit_price, depth_hint, instrument_key, vix,
            )

    def _place_order_locked(self, symbol, expiry, strike, option_type,
                            action, quantity, order_type, reason,
                            strategy_name, option_ltp_hint,
                            limit_price, depth_hint, instrument_key, vix) -> dict:
        now = _now_ist()
        lot_size = LOT_SIZES.get(symbol, 50)
        total_qty = quantity * lot_size

        if not depth_hint and self._heartbeat and instrument_key:
            ws_depth = self._heartbeat.get_depth(instrument_key)
            if ws_depth:
                depth_hint = {"bid": ws_depth["bid"], "ask": ws_depth["ask"]}
                log.info("Auto depth from WS | %s bid=%.2f ask=%.2f (age=%.1fs)",
                         instrument_key, ws_depth["bid"], ws_depth["ask"],
                         ws_depth["age_seconds"])

        # Validations
        freeze = _FREEZE_QTY.get(symbol, 1800)
        if total_qty > freeze:
            return self._reject(f"Qty {total_qty} exceeds freeze limit {freeze}")

        if len(self._positions) >= self._max_positions:
            return self._reject(f"Max {self._max_positions} positions reached")

        buy_lots = sum(p["quantity"] for p in self._positions.values()
                       if p["action"] == "BUY")
        new_lots = buy_lots + (quantity if action == "BUY" else 0)
        if new_lots > self._max_lots:
            return self._reject(
                f"Total BUY lots {new_lots} exceeds max {self._max_lots}")

        # Price resolution
        raw_price = option_ltp_hint or limit_price
        if not raw_price or raw_price <= 0:
            return self._reject("No price available (option_ltp_hint and limit_price both missing)")

        step = STRIKE_STEP.get(symbol, 50)
        premium_pct = raw_price / strike * 100 if strike else 0
        if premium_pct >= 2.0:
            moneyness_steps = 0
        elif premium_pct >= 0.5:
            moneyness_steps = 2
        else:
            moneyness_steps = 5

        entry_price = apply_slippage(
            raw_price, action,
            depth=depth_hint,
            moneyness=moneyness_steps,
            vix=vix,
            hour=now.hour,
            symbol=symbol,
        )

        # Duplicate check
        dup = self._check_no_duplicate(symbol, strike, option_type, action)
        if dup:
            return self._reject(dup)

        # Margin check
        premium = entry_price * total_qty
        margin = premium
        per_trade_limit = self._start_capital * self._per_trade_pct / 100

        if margin > per_trade_limit:
            return self._reject(
                f"Margin INR {margin:,.0f} exceeds per-trade limit INR {per_trade_limit:,.0f}")

        if margin > self._capital:
            return self._reject(
                f"Margin INR {margin:,.0f} exceeds available capital INR {self._capital:,.0f}")

        # Execute
        self._order_counter += 1
        pos_id = f"P{self._order_counter:04d}_{symbol}{strike}{option_type}"
        self._capital -= margin

        position = {
            "position_id":   pos_id,
            "symbol":        symbol,
            "expiry":        expiry,
            "strike":        strike,
            "option_type":   option_type,
            "action":        action,
            "quantity":      quantity,
            "lot_size":      lot_size,
            "total_qty":     total_qty,
            "entry_price":   round(entry_price, 2),
            "current_price": round(entry_price, 2),
            "margin_used":   round(margin, 2),
            "unrealized_pnl": 0.0,
            "strategy":      strategy_name,
            "reason":        reason,
            "entry_time":    now.strftime("%H:%M:%S"),
            "entry_date":    str(now.date()),
            "order_type":    order_type,
            "max_price":     round(entry_price, 2),
            "min_price":     round(entry_price, 2),
            "mfe_time":      now.strftime("%H:%M:%S"),
            "mae_time":      now.strftime("%H:%M:%S"),
            "spread_pair_id": None,
            "instrument_key": instrument_key or "",
        }

        self._positions[pos_id] = position
        self._save_positions()

        slip_pct = abs(entry_price - raw_price) / raw_price * 100 if raw_price else 0
        log.info("ORDER FILLED | %s %dx %s %d%s @ INR %.2f | margin=INR %s | %s",
                 action, quantity, symbol, strike, option_type,
                 entry_price, f"{margin:,.0f}", pos_id)
        log.info("SLIPPAGE | %s raw=%.2f fill=%.2f (%+.2f%%)",
                 pos_id, raw_price, entry_price, slip_pct)

        return {
            "status":       "SUCCESS",
            "position_id":  pos_id,
            "entry_price":  round(entry_price, 2),
            "margin_used":  round(margin, 2),
            "quantity":     quantity,
            "total_qty":    total_qty,
            "message":      f"Paper order filled: {action} {quantity}x {symbol} {strike}{option_type}",
        }

    # ── PLACE SPREAD ─────────────────────────────────────────────

    def place_spread(self, symbol: str, expiry: str,
                     legs: list[dict], reason: str = "",
                     strategy: str = "spread", vix: float = 14.0) -> dict:
        """
        Place a multi-leg spread. Rolls back if any leg fails.

        Args:
            legs: list of dicts with: strike, option_type, action, quantity,
                  ltp (or limit_price), optional _depth_hint
        """
        results = []
        for leg in legs:
            r = self.place_order(
                symbol=symbol, expiry=expiry,
                strike=leg["strike"],
                option_type=leg["option_type"],
                action=leg["action"],
                quantity=leg.get("quantity", 1),
                option_ltp_hint=leg.get("ltp", 0),
                limit_price=leg.get("limit_price"),
                reason=reason,
                strategy_name=strategy,
                depth_hint=leg.get("_depth_hint"),
                instrument_key=leg.get("_instrument_key", ""),
                vix=vix,
            )
            results.append(r)

        placed = sum(1 for r in results if r["status"] == "SUCCESS")

        if 0 < placed < len(legs):
            log.warning("SPREAD PARTIAL FILL — rolling back %d orphan legs", placed)
            for r in results:
                if r["status"] == "SUCCESS":
                    self.close_position(r["position_id"], "ROLLBACK: spread partial fill")
            return {
                "status": "REJECTED",
                "reason": "Spread partial fill — all legs rolled back.",
                "legs_placed": 0,
                "leg_results": results,
            }

        # Link spread legs
        success_ids = [r["position_id"] for r in results if r["status"] == "SUCCESS"]
        if len(success_ids) == 2:
            with self._lock:
                id_a, id_b = success_ids
                if id_a in self._positions and id_b in self._positions:
                    self._positions[id_a]["spread_pair_id"] = id_b
                    self._positions[id_b]["spread_pair_id"] = id_a
                    self._save_positions()

        return {
            "status": "SUCCESS" if placed == len(legs) else "REJECTED",
            "legs_placed": placed,
            "leg_results": results,
        }

    # ── CLOSE POSITION ───────────────────────────────────────────

    def close_position(self, position_id: str, reason: str, *,
                       exit_price_override: float = None) -> dict:
        """Close a position. Uses current_price or override for exit."""
        with self._lock:
            return self._close_locked(position_id, reason, exit_price_override)

    def _close_locked(self, position_id, reason, exit_price_override) -> dict:
        pos = self._positions.get(position_id)
        if not pos:
            return {"status": "ERROR", "message": f"Position {position_id} not found"}

        now = _now_ist()
        raw_exit = exit_price_override or pos.get("current_price", pos["entry_price"])

        reverse_action = "SELL" if pos["action"] == "BUY" else "BUY"

        depth = None
        inst_key = pos.get("instrument_key")
        if self._heartbeat and inst_key:
            ws_depth = self._heartbeat.get_depth(inst_key)
            if ws_depth:
                depth = {"bid": ws_depth["bid"], "ask": ws_depth["ask"]}

        exit_price = apply_slippage(
            raw_exit, reverse_action,
            depth=depth,
            symbol=pos["symbol"],
            hour=now.hour,
            is_exit=True,
        )

        # P&L
        if pos["action"] == "BUY":
            pnl_per_unit = exit_price - pos["entry_price"]
        else:
            pnl_per_unit = pos["entry_price"] - exit_price

        gross_pnl = round(pnl_per_unit * pos["total_qty"], 2)
        costs = compute_trade_costs(
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            total_qty=pos["total_qty"],
            action=pos["action"],
        )
        realized_pnl = round(gross_pnl - costs["total"], 2)

        # Restore capital
        self._capital += pos["margin_used"] + realized_pnl

        # MFE / MAE
        max_p = pos.get("max_price", pos["entry_price"])
        min_p = pos.get("min_price", pos["entry_price"])
        if pos["action"] == "BUY":
            mfe = round((max_p - pos["entry_price"]) * pos["total_qty"], 2)
            mae = round((pos["entry_price"] - min_p) * pos["total_qty"], 2)
        else:
            mfe = round((pos["entry_price"] - min_p) * pos["total_qty"], 2)
            mae = round((max_p - pos["entry_price"]) * pos["total_qty"], 2)

        # Hold time
        hold_min = 0
        try:
            e_dt = datetime.strptime(pos.get("entry_time", ""), "%H:%M:%S")
            x_dt = datetime.strptime(now.strftime("%H:%M:%S"), "%H:%M:%S")
            hold_min = round((x_dt - e_dt).total_seconds() / 60, 1)
        except (ValueError, TypeError):
            pass

        # Record
        closed = {
            **pos,
            "exit_price":    round(exit_price, 2),
            "gross_pnl":     gross_pnl,
            "costs":         costs,
            "pnl":           realized_pnl,
            "mfe":           mfe,
            "mae":           mae,
            "hold_minutes":  hold_min,
            "exit_time":     now.strftime("%H:%M:%S"),
            "close_date":    str(now.date()),
            "close_reason":  reason,
        }
        self._closed_trades.append(closed)

        # Cascade close paired spread leg
        pair_id = pos.get("spread_pair_id")
        del self._positions[position_id]
        self._sl_targets.pop(position_id, None)
        self._save_positions()

        arrow = "+" if realized_pnl >= 0 else ""
        log.info("CLOSED | %s %dx %s %d%s | entry=%.2f exit=%.2f | "
                 "Gross=INR %s Costs=INR %s Net=INR %s%s | MFE=%s MAE=%s | %s",
                 pos["action"], pos["quantity"], pos["symbol"],
                 pos["strike"], pos["option_type"],
                 pos["entry_price"], exit_price,
                 f"{gross_pnl:,.2f}", f"{costs['total']:,.2f}",
                 arrow, f"{realized_pnl:,.2f}",
                 f"{mfe:,.2f}", f"{mae:,.2f}", reason)

        if pair_id and pair_id in self._positions:
            log.info("SPREAD CASCADE | closing paired leg %s", pair_id)
            self._close_locked(pair_id, f"SPREAD_LEG_CLOSED: {reason}", None)

        return {
            "status":       "CLOSED",
            "position_id":  position_id,
            "entry_price":  pos["entry_price"],
            "exit_price":   round(exit_price, 2),
            "gross_pnl":    gross_pnl,
            "costs":        costs,
            "realized_pnl": realized_pnl,
            "mfe":          mfe,
            "mae":          mae,
            "hold_minutes": hold_min,
            "reason":       reason,
        }

    # ── SET SL / TARGET ──────────────────────────────────────────

    def set_sl_target(self, position_id: str,
                      stop_loss_pct: float, target_pct: float) -> dict:
        """Set stop loss and target percentages for a position."""
        with self._lock:
            pos = self._positions.get(position_id)
            if not pos:
                return {"status": "ERROR", "message": f"Position {position_id} not found"}

            entry = pos["entry_price"]
            if pos["action"] == "BUY":
                sl_price = round(entry * (1 - stop_loss_pct / 100), 2)
                target_price = round(entry * (1 + target_pct / 100), 2)
            else:
                sl_price = round(entry * (1 + stop_loss_pct / 100), 2)
                target_price = round(entry * (1 - target_pct / 100), 2)

            self._sl_targets[position_id] = {
                "stop_loss": sl_price,
                "target": target_price,
                "sl_pct": stop_loss_pct,
                "target_pct": target_pct,
            }

            return {"status": "SET", "stop_loss": sl_price, "target": target_price}

    def get_sl_targets(self) -> dict:
        """Return all SL/target settings."""
        with self._lock:
            return dict(self._sl_targets)

    # ── UPDATE PRICE ─────────────────────────────────────────────

    def update_price(self, position_id: str, ltp: float) -> None:
        """Update current price + MFE/MAE for a position (call from heartbeat/tick)."""
        with self._lock:
            pos = self._positions.get(position_id)
            if not pos or not ltp or ltp <= 0:
                return

            pos["current_price"] = round(ltp, 2)
            now_str = _now_ist().strftime("%H:%M:%S")

            if ltp > pos.get("max_price", ltp):
                pos["max_price"] = ltp
                if pos["action"] == "BUY":
                    pos["mfe_time"] = now_str
                else:
                    pos["mae_time"] = now_str
            else:
                pos["max_price"] = max(pos.get("max_price", ltp), ltp)

            if ltp < pos.get("min_price", ltp):
                pos["min_price"] = ltp
                if pos["action"] == "SELL":
                    pos["mfe_time"] = now_str
                else:
                    pos["mae_time"] = now_str
            else:
                pos["min_price"] = min(pos.get("min_price", ltp), ltp)

            if pos["action"] == "BUY":
                pos["unrealized_pnl"] = round((ltp - pos["entry_price"]) * pos["total_qty"], 2)
            else:
                pos["unrealized_pnl"] = round((pos["entry_price"] - ltp) * pos["total_qty"], 2)

    # ── GET PORTFOLIO ────────────────────────────────────────────

    def get_portfolio(self) -> dict:
        """Get full portfolio snapshot."""
        from datetime import date
        with self._lock:
            positions_out = []
            total_unrealized = 0.0

            for pos in self._positions.values():
                if pos["action"] == "BUY":
                    unrealized = (pos["current_price"] - pos["entry_price"]) * pos["total_qty"]
                else:
                    unrealized = (pos["entry_price"] - pos["current_price"]) * pos["total_qty"]

                exp_str = pos.get("expiry", "")
                try:
                    pos_dte = (date.fromisoformat(exp_str) - date.today()).days if exp_str else None
                except ValueError:
                    pos_dte = None

                positions_out.append({
                    **pos,
                    "unrealized_pnl": round(unrealized, 2),
                    "position_dte": pos_dte,
                    "sl_target": self._sl_targets.get(pos["position_id"]),
                })
                total_unrealized += unrealized

            today_str = str(_now_ist().date())
            today_closed = [t for t in self._closed_trades
                            if t.get("close_date") == today_str]
            realized_today = sum(t["pnl"] for t in today_closed)
            buy_lots = sum(p["quantity"] for p in self._positions.values()
                           if p["action"] == "BUY")

            return {
                "mode":              "PAPER",
                "open_positions":    positions_out,
                "position_count":    len(positions_out),
                "buy_lots_used":     buy_lots,
                "max_lots":          self._max_lots,
                "max_positions":     self._max_positions,
                "available_capital": round(self._capital, 2),
                "start_capital":     self._start_capital,
                "unrealized_pnl":    round(total_unrealized, 2),
                "realized_pnl_today": round(realized_today, 2),
                "total_pnl_today":   round(realized_today + total_unrealized, 2),
                "total_trades_today": len(today_closed),
            }

    # ── HELPERS ──────────────────────────────────────────────────

    def _reject(self, reason: str) -> dict:
        log.warning("ORDER REJECTED | %s", reason)
        return {"status": "REJECTED", "message": reason}

    def _check_no_duplicate(self, symbol, strike, option_type, action) -> str | None:
        for pos in self._positions.values():
            if (pos["symbol"] == symbol and pos["strike"] == strike
                    and pos["option_type"] == option_type
                    and pos["action"] == action):
                return (f"Duplicate: already holding {action} {symbol} "
                        f"{strike}{option_type} ({pos['position_id']})")
        return None

    # ── PERSISTENCE ──────────────────────────────────────────────

    def _save_positions(self):
        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            realized = sum(t.get("pnl", 0) for t in self._closed_trades)
            with self._lock:
                data = {
                    "positions": dict(self._positions),
                    "sl_targets": dict(self._sl_targets),
                    "order_counter": self._order_counter,
                    "realized_pnl_today": round(realized, 2),
                    "realized_date": str(_now_ist().date()),
                }
            with open(self._positions_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            log.error("Failed to save positions: %s", e)

    def _load_positions(self):
        if not self._positions_file.exists():
            return
        try:
            with open(self._positions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._positions = data.get("positions", {})
            self._sl_targets = data.get("sl_targets", {})
            self._order_counter = data.get("order_counter", 0)
            from datetime import date
            saved_date = data.get("realized_date")
            if saved_date == date.today().isoformat():
                prior_pnl = data.get("realized_pnl_today", 0)
                if prior_pnl:
                    self._closed_trades.append({
                        "pnl": prior_pnl,
                        "close_reason": "PRIOR_SESSION",
                        "close_date": saved_date,
                    })
            if self._positions:
                margin_held = sum(p.get("margin_used", 0) for p in self._positions.values())
                self._capital -= margin_held
                log.info("Loaded %d positions (margin held: INR %s)",
                         len(self._positions), f"{margin_held:,.0f}")
        except Exception as e:
            log.error("Failed to load positions: %s", e)
