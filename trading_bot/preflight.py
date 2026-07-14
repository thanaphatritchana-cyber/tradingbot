"""Non-trading readiness checks for paper and live IBKR operation."""

import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import Settings
from .costs import estimate_profitability
from .main import (
    _what_if_round_trip_commission, create_broker, live_config_fingerprint,
)
from .notify import LineNotifier
from .security import ensure_allowed_user
from .storage import Store


LIVE_CONFIRMATION = "I_UNDERSTAND_LIVE_ORDERS"
LIVE_TAX_CONFIRMATION = "I_CONFIRMED_TAX_RATE"
LIVE_COST_CONFIRMATION = "I_VERIFIED_TRADING_COSTS"


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
    gross_loss = float(getattr(summary, "total_gross_loss", 0) or 0)
    gross_profit = float(getattr(summary, "total_gross_profit", 0) or 0)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    if profit_factor < cfg.min_paper_profit_factor_for_live:
        errors.append(
            f"Paper profit factor must be at least "
            f"{cfg.min_paper_profit_factor_for_live:.2f}; found {profit_factor:.2f}"
        )
    active_days = int(getattr(summary, "active_trading_days", 0) or 0)
    if active_days < cfg.min_paper_trading_days_for_live:
        errors.append(
            f"Paper track record requires {cfg.min_paper_trading_days_for_live} active "
            f"trading days; found {active_days}"
        )
    max_drawdown = float(getattr(summary, "max_drawdown", 0) or 0)
    drawdown_limit = cfg.starting_cash * cfg.max_paper_drawdown_pct_for_live
    if max_drawdown > drawdown_limit:
        errors.append(
            f"Paper max drawdown {max_drawdown:.2f} exceeds {drawdown_limit:.2f}"
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
    if cfg.ibkr_host.strip().lower() not in {"127.0.0.1", "localhost", "::1"}:
        errors.append("IBKR_HOST must be loopback-only")
    if cfg.max_order_notional <= 0:
        errors.append("MAX_ORDER_NOTIONAL must be greater than 0")
    if cfg.max_total_exposure < cfg.max_order_notional:
        errors.append("MAX_TOTAL_EXPOSURE must be at least MAX_ORDER_NOTIONAL")
    if cfg.max_total_exposure > cfg.max_order_notional * cfg.max_concurrent_positions:
        errors.append("MAX_TOTAL_EXPOSURE cannot exceed order cap times concurrent positions")
    if not 0 < cfg.stop_loss_pct < cfg.take_profit_pct:
        errors.append("STOP_LOSS_PCT must be positive and lower than TAKE_PROFIT_PCT")
    if not 0.03 <= cfg.take_profit_pct <= cfg.take_profit_max_pct <= 0.10:
        errors.append("Take-profit range must be between 3% and 10%")
    if not 0 < cfg.trailing_stop_pct < cfg.take_profit_pct:
        errors.append("TRAILING_STOP_PCT must be positive and lower than TAKE_PROFIT_PCT")
    if cfg.min_atr_pct >= cfg.max_atr_pct:
        errors.append("MIN_ATR_PCT must be lower than MAX_ATR_PCT")
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
        if cfg.live_tax_confirm != LIVE_TAX_CONFIRMATION:
            errors.append(f"LIVE_TAX_CONFIRM must equal {LIVE_TAX_CONFIRMATION}")
        if cfg.live_cost_model_confirm != LIVE_COST_CONFIRMATION:
            errors.append(
                f"LIVE_COST_MODEL_CONFIRM must equal {LIVE_COST_CONFIRMATION}"
            )
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

    store = Store(
        cfg.database_url, cfg.estimated_exchange_fee_rate,
        cfg.estimated_fx_cost_rate, cfg.tax_rate,
    )
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

    try:
        broker = create_broker(cfg, store)
    except Exception as exc:
        print(f"FAIL: IBKR connection: {type(exc).__name__}: {exc}")
        return 1
    try:
        print(f"PASS: IBKR account {broker.account} connected read_only={broker.read_only}")
        available_funds = broker.available_funds()
        if available_funds <= 0:
            raise RuntimeError(f"IBKR AvailableFunds is not positive: {available_funds:.2f}")
        print(f"PASS: IBKR AvailableFunds={available_funds:.2f}")
        net_liquidation = broker.net_liquidation()
        print(
            f"PASS: IBKR NetLiquidation={net_liquidation:.2f}; "
            f"configured sizing ceiling={cfg.starting_cash:.2f}"
        )
        daily_pnl = broker.account_daily_pnl()
        print(f"PASS: IBKR account DailyPnL={daily_pnl:.2f}")
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
        affordable = [
            symbol for symbol, price in last_prices.items()
            if price <= cfg.max_order_notional
        ]
        if affordable:
            print(
                "PASS: whole-share affordability under MAX_ORDER_NOTIONAL: "
                + ", ".join(affordable)
            )
        else:
            affordability_message = (
                "no configured symbol can buy one whole share under "
                f"MAX_ORDER_NOTIONAL={cfg.max_order_notional:.2f}"
            )
            if args.mode == "live":
                raise RuntimeError(f"Live preflight failed: {affordability_message}")
            print(f"WARNING: {affordability_message}; the bot will not place BUY orders")
        if args.check_order_permission or args.mode == "live":
            symbols_to_check = affordable or [cfg.symbol_list[0]]
            maximum_budget = min(cfg.starting_cash * 0.20, cfg.max_order_notional)
            for checked_symbol in symbols_to_check:
                price = last_prices[checked_symbol]
                qty = max(1, int(maximum_budget / price))
                state = broker.what_if_buy(checked_symbol, price, qty)
                warning = str(state.warningText or "").strip()
                if warning:
                    raise RuntimeError(
                        f"IBKR What-If warning for {checked_symbol}: {warning}"
                    )
                round_trip_commission = _what_if_round_trip_commission(
                    state, cfg.what_if_commission_buffer_pct,
                )
                estimate = estimate_profitability(
                    0.9501, qty * price, cfg.stop_loss_pct,
                    cfg.take_profit_max_pct, round_trip_commission,
                    cfg.estimated_exchange_fee_rate,
                    cfg.estimated_fx_cost_rate, cfg.tax_rate,
                    cfg.min_net_profit_cost_multiple,
                )
                if not estimate.should_trade:
                    message = (
                        f"{checked_symbol} best-tier Expected Net "
                        f"{estimate.net_profit:.2f} does not exceed required "
                        f"{estimate.required_net_profit:.2f}"
                    )
                    if args.mode == "live":
                        raise RuntimeError(message)
                    print(f"WARNING: {message}; runtime BUY will be skipped")
                print(
                    f"PASS: IBKR What-If {checked_symbol} commission "
                    f"round-trip+buffer={round_trip_commission:.2f} (not transmitted)"
                )
    except Exception as exc:
        print(f"FAIL: IBKR readiness: {type(exc).__name__}: {exc}")
        return 1
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
