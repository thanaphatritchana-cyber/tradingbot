"""Non-trading readiness checks for paper and live IBKR operation."""

import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import Settings
from .main import create_broker, live_config_fingerprint
from .notify import LineNotifier
from .security import ensure_allowed_user
from .storage import Store


LIVE_CONFIRMATION = "I_UNDERSTAND_LIVE_ORDERS"


def validate_paper_track_record(cfg: Settings, summary) -> list[str]:
    errors = []
    if summary.total_trades < cfg.min_paper_closed_trades_for_live:
        errors.append(
            f"Paper track record requires {cfg.min_paper_closed_trades_for_live} closed trades; "
            f"found {summary.total_trades}"
        )
    if summary.total_profit <= 0:
        errors.append(
            f"Paper net profit after fees must be positive; found {summary.total_profit:.2f}"
        )
    win_rate = summary.total_wins / summary.total_trades if summary.total_trades else 0.0
    if win_rate < cfg.min_paper_win_rate_for_live:
        errors.append(
            f"Paper win rate must be at least {cfg.min_paper_win_rate_for_live:.0%}; "
            f"found {win_rate:.1%}"
        )
    return errors


def validate_settings(cfg: Settings, mode: str) -> list[str]:
    errors: list[str] = []
    if cfg.broker.strip().lower() not in {"ibkr", "interactivebrokers", "interactive_brokers"}:
        errors.append("BROKER must be ibkr")
    if not cfg.allowed_os_user.strip():
        errors.append("ALLOWED_OS_USER is required")
    if not cfg.line_channel_access_token.strip() or not cfg.line_target_id.strip():
        errors.append("LINE notification credentials are required")
    if not cfg.ibkr_account.strip():
        errors.append("IBKR_ACCOUNT is required")
    if not 0 < cfg.risk_per_trade <= 0.01:
        errors.append("RISK_PER_TRADE must be greater than 0 and at most 1%")
    if not 0 < cfg.max_position_pct <= 0.10:
        errors.append("MAX_POSITION_PCT must be greater than 0 and at most 10%")
    if cfg.max_order_notional <= 0:
        errors.append("MAX_ORDER_NOTIONAL must be greater than 0")
    if cfg.max_total_exposure < cfg.max_order_notional:
        errors.append("MAX_TOTAL_EXPOSURE must be at least MAX_ORDER_NOTIONAL")
    if not 0 < cfg.stop_loss_pct < cfg.take_profit_pct:
        errors.append("STOP_LOSS_PCT must be positive and lower than TAKE_PROFIT_PCT")
    if not 0.03 <= cfg.take_profit_pct <= cfg.take_profit_max_pct <= 0.10:
        errors.append("Take-profit range must be between 3% and 10%")
    if mode == "paper":
        if not cfg.ibkr_paper:
            errors.append("Paper preflight requires IBKR_PAPER=true")
        if cfg.ibkr_port not in {7497, 4002}:
            errors.append("Paper mode must use TWS 7497 or IB Gateway 4002")
    if mode == "live":
        if cfg.ibkr_paper:
            errors.append("Live preflight requires IBKR_PAPER=false")
        if cfg.ibkr_read_only:
            errors.append("Live preflight requires IBKR_READ_ONLY=false")
        if cfg.kill_switch:
            errors.append("Live preflight requires KILL_SWITCH=false")
        if cfg.ibkr_port not in {7496, 4001}:
            errors.append("Live mode must use TWS 7496 or IB Gateway 4001")
        if cfg.ibkr_account.upper().startswith("DU"):
            errors.append("A DU paper account cannot pass live preflight")
        if cfg.live_trading_confirm != LIVE_CONFIRMATION:
            errors.append(f"LIVE_TRADING_CONFIRM must equal {LIVE_CONFIRMATION}")
        if not cfg.ibkr_native_bracket:
            errors.append("Live preflight requires IBKR_NATIVE_BRACKET=true")
        if cfg.ibkr_market_data_type != 1:
            errors.append("Live preflight requires IBKR_MARKET_DATA_TYPE=1")
    return errors


def run() -> int:
    parser = argparse.ArgumentParser(description="TradingBot readiness checks (never places orders)")
    parser.add_argument("--mode", choices=("paper", "live"), default="paper")
    parser.add_argument("--send-line-test", action="store_true")
    parser.add_argument("--check-order-permission", action="store_true")
    args = parser.parse_args()

    cfg = Settings()
    ensure_allowed_user(cfg.allowed_os_user)
    errors = validate_settings(cfg, args.mode)
    if args.check_order_permission and cfg.ibkr_read_only:
        errors.append("Order-permission check requires IBKR_READ_ONLY=false")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    store = Store(cfg.database_url)
    if args.mode == "live":
        store.set_state("live_preflight_fingerprint", "")
        report_date = datetime.now(ZoneInfo(cfg.daily_report_timezone)).date()
        track_errors = validate_paper_track_record(
            cfg, store.trading_summary(report_date, cfg.daily_report_timezone)
        )
        if track_errors:
            for error in track_errors:
                print(f"FAIL: {error}")
            return 1
    probe_key = "last_preflight_utc"
    store.set_state(probe_key, datetime.now(timezone.utc).isoformat())
    print("PASS: SQL Server read/write")

    broker = create_broker(cfg, store)
    try:
        print(f"PASS: IBKR account {broker.account} connected read_only={broker.read_only}")
        available_funds = broker.available_funds()
        if available_funds <= 0:
            raise RuntimeError(f"IBKR AvailableFunds is not positive: {available_funds:.2f}")
        print(f"PASS: IBKR AvailableFunds={available_funds:.2f}")
        if args.mode == "live":
            open_orders = broker.account_open_orders()
            if open_orders:
                raise RuntimeError(
                    f"Live preflight requires no existing open orders; found {len(open_orders)}"
                )
            account_positions = broker.account_positions()
            if account_positions:
                raise RuntimeError(
                    f"Live preflight requires the entire account to be flat; "
                    f"found {len(account_positions)} position(s)"
                )
        last_prices: dict[str, float] = {}
        for symbol in cfg.symbol_list:
            qty, _ = broker.position(symbol)
            if args.mode == "live" and abs(qty) > 1e-9:
                raise RuntimeError(
                    f"Live preflight requires a flat account for configured symbols; "
                    f"{symbol} position={qty:g}"
                )
            history = broker.history(symbol, min(cfg.lookback_days, 30), cfg.interval)
            if history is None or history.empty:
                raise RuntimeError(f"No IBKR historical data for {symbol}")
            last_prices[symbol] = float(history["Close"].iloc[-1])
            print(f"PASS: {symbol} contract/data rows={len(history)} position={qty:g}")
            if args.mode == "live":
                if not broker.market_is_open(symbol):
                    raise RuntimeError(f"{symbol} regular market is not open")
                quote = broker.current_quote(symbol)
                if quote["market_data_type"] != 1:
                    raise RuntimeError(
                        f"{symbol} did not return live market data type 1"
                    )
                if quote["age_seconds"] > cfg.max_market_data_age_seconds:
                    raise RuntimeError(
                        f"{symbol} quote is stale ({quote['age_seconds']:.1f}s)"
                    )
                print(
                    f"PASS: {symbol} live quote age={quote['age_seconds']:.1f}s"
                )
        if args.check_order_permission or args.mode == "live":
            first_symbol = cfg.symbol_list[0]
            state = broker.what_if_buy(first_symbol, last_prices[first_symbol], 1)
            warning = str(state.warningText or "").strip()
            if warning:
                raise RuntimeError(f"IBKR What-If warning: {warning}")
            print("PASS: IBKR What-If order permission (order not transmitted)")
    finally:
        broker.close()

    if args.send_line_test:
        notifier = LineNotifier(cfg.line_channel_access_token, cfg.line_target_id)
        if not notifier.send(f"✅ TradingBot {args.mode.upper()} preflight ผ่าน (ไม่มีการส่งออเดอร์)"):
            print("FAIL: LINE test message")
            return 1
        print("PASS: LINE notification")
    if args.mode == "live":
        store.set_state("live_preflight_fingerprint", live_config_fingerprint(cfg))
    print(f"READY: {args.mode.upper()} preflight passed; no order was submitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
