"""
Paper trading example — place simulated orders with realistic slippage and costs.

Run: python examples/paper_trading.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from upstox_data import get_spot, get_option_chain, get_nearest_expiry
from paper_broker import PaperBroker

broker = PaperBroker(capital=500_000, max_lots=5, max_positions=7)

print("=" * 60)
print("PAPER TRADING DEMO")
print("=" * 60)

# 1. Get market data
spot = get_spot("NIFTY")
print(f"\nNIFTY spot: {spot['ltp']:,.2f}")

expiry = get_nearest_expiry("NIFTY")
print(f"Nearest expiry: {expiry}")

chain = get_option_chain("NIFTY", expiry, num_strikes=5)
atm_row = next((r for r in chain["chain"] if r["is_atm"]), None)

if not atm_row:
    print("No ATM strike found")
    sys.exit(1)

strike = atm_row["strike"]
ce_ltp = atm_row["CE"]["ltp"]
pe_ltp = atm_row["PE"]["ltp"]

print(f"ATM strike: {strike}")
print(f"  CE LTP: {ce_ltp:.2f}  |  PE LTP: {pe_ltp:.2f}")

# 2. Place a BUY order
print(f"\n{'─' * 60}")
print("Placing BUY order: 1 lot NIFTY CE...")
result = broker.place_order(
    symbol="NIFTY",
    expiry=expiry,
    strike=strike,
    option_type="CE",
    action="BUY",
    quantity=1,
    option_ltp_hint=ce_ltp,
    reason="demo trade",
)
print(f"  Status: {result['status']}")
if result["status"] == "SUCCESS":
    print(f"  Position: {result['position_id']}")
    print(f"  Fill price: {result['entry_price']:.2f} (LTP was {ce_ltp:.2f})")
    print(f"  Margin used: INR {result['margin_used']:,.2f}")

    # 3. Set stop loss and target
    sl_result = broker.set_sl_target(result["position_id"], stop_loss_pct=20, target_pct=30)
    print(f"\n  SL: {sl_result['stop_loss']:.2f}  |  Target: {sl_result['target']:.2f}")

    # 4. Simulate price update
    new_price = ce_ltp * 1.05
    broker.update_price(result["position_id"], new_price)
    print(f"\n  Price updated to {new_price:.2f}")

    # 5. Check portfolio
    portfolio = broker.get_portfolio()
    print(f"\n{'─' * 60}")
    print("PORTFOLIO:")
    print(f"  Open positions: {portfolio['position_count']}")
    print(f"  Unrealized P&L: INR {portfolio['unrealized_pnl']:,.2f}")
    print(f"  Available capital: INR {portfolio['available_capital']:,.2f}")

    # 6. Close the position
    print(f"\n{'─' * 60}")
    print("Closing position...")
    close = broker.close_position(result["position_id"], "demo exit")
    print(f"  Exit price: {close['exit_price']:.2f}")
    print(f"  Gross P&L: INR {close['gross_pnl']:,.2f}")
    print(f"  Costs: INR {close['costs']['total']:,.2f}")
    print(f"    Brokerage: {close['costs']['brokerage']:.2f}")
    print(f"    STT:       {close['costs']['stt']:.2f}")
    print(f"    GST:       {close['costs']['gst']:.2f}")
    print(f"  Net P&L: INR {close['realized_pnl']:,.2f}")
    print(f"  MFE: INR {close['mfe']:,.2f}  |  MAE: INR {close['mae']:,.2f}")
    print(f"  Hold time: {close['hold_minutes']} min")

# 7. Final portfolio
final = broker.get_portfolio()
print(f"\n{'─' * 60}")
print("FINAL STATE:")
print(f"  Capital: INR {final['available_capital']:,.2f}")
print(f"  Realized P&L today: INR {final['realized_pnl_today']:,.2f}")
print(f"  Trades today: {final['total_trades_today']}")
print("=" * 60)
