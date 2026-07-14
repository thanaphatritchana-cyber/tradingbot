import logging
import os
import signal as os_signal
import hashlib
import json
import math
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import yfinance as yf
from .broker import AlpacaBroker, InteractiveBrokersBroker, LocalPaperBroker
from .config import Settings
from .costs import estimate_profitability
from .notify import LineNotifier
from .security import SingleInstance, ensure_allowed_user
from .storage import Store
from .strategy import analyze, profit_target_pct


def _latest_reportable_date(now: datetime, report_time: str, timezone_name: str) -> date:
    try:
        hour, minute = (int(part) for part in report_time.split(":"))
        scheduled = clock_time(hour, minute)
    except (TypeError, ValueError):
        raise ValueError("DAILY_REPORT_TIME must use HH:MM format")
    local_now = now.astimezone(ZoneInfo(timezone_name))
    return local_now.date() if local_now.time() >= scheduled else local_now.date() - timedelta(days=1)


def _signed(value: float) -> str:
    return f"{value:+,.2f}"


def _win_rate(wins: int, trades: int) -> float:
    return wins / trades if trades else 0.0


def _what_if_round_trip_commission(state, buffer_pct: float) -> float:
    one_way = float("nan")
    for field in ("commission", "maxCommission", "minCommission"):
        try:
            candidate = float(getattr(state, field, float("nan")))
        except (TypeError, ValueError):
            continue
        if math.isfinite(candidate) and 0 <= candidate < 1e20:
            one_way = candidate
            break
    if not math.isfinite(one_way):
        raise RuntimeError("IBKR What-If returned invalid commission")
    return one_way * 2 * (1 + max(0.0, buffer_pct))


def _order_value(order, name: str, default):
    value = order.get(name) if isinstance(order, dict) else getattr(order, name, None)
    return default if value is None or value == "" else value


def _confidence_allocation_pct(probability: float) -> float:
    if probability < 0.70:
        return 0.0
    if probability < 0.80:
        return 0.05
    if probability < 0.90:
        return 0.10
    if probability <= 0.95:
        return 0.15
    return 0.20


def _confidence_order_budget(
    cfg: Settings, probability: float, portfolio_value: float | None = None,
) -> float:
    portfolio_base = (
        cfg.starting_cash if portfolio_value is None else max(0.0, portfolio_value)
    )
    return min(
        portfolio_base * _confidence_allocation_pct(probability),
        cfg.max_order_notional,
    )


def _calculate_order_qty(
    cfg: Settings, price: float, probability: float,
    portfolio_value: float | None = None,
) -> int:
    if price <= 0:
        return 0
    return max(0, int(_confidence_order_budget(cfg, probability, portfolio_value) / price))


def _risk_block_reason(
    cfg: Settings, summary, exposure: float, next_notional: float,
    available_funds: float = float("inf"), open_positions: int = 0,
    broker_daily_pnl: float | None = None,
    estimated_entry_fee: float = 0,
    portfolio_value: float | None = None,
) -> str | None:
    daily_loss_limit = _daily_loss_limit(cfg, portfolio_value)
    effective_daily_pnl = summary.daily_profit
    if broker_daily_pnl is not None:
        effective_daily_pnl = min(effective_daily_pnl, broker_daily_pnl)
    if effective_daily_pnl <= -daily_loss_limit:
        return f"daily net loss {effective_daily_pnl:.2f} reached limit {-daily_loss_limit:.2f}"
    if summary.daily_buys >= cfg.max_orders_per_day:
        return f"daily order limit reached ({summary.daily_buys}/{cfg.max_orders_per_day})"
    if summary.daily_consecutive_losses >= cfg.max_consecutive_losses:
        return (
            f"consecutive loss limit reached "
            f"({summary.daily_consecutive_losses}/{cfg.max_consecutive_losses})"
        )
    if open_positions >= cfg.max_concurrent_positions:
        return (
            f"concurrent position limit reached "
            f"({open_positions}/{cfg.max_concurrent_positions})"
        )
    if exposure + next_notional > cfg.max_total_exposure + 1e-9:
        return (
            f"aggregate exposure would be {exposure + next_notional:.2f}, "
            f"above {cfg.max_total_exposure:.2f}"
        )
    required_cash = next_notional + estimated_entry_fee
    if required_cash > available_funds + 1e-9:
        return (
            f"order cash requirement {required_cash:.2f} exceeds available funds "
            f"{available_funds:.2f}"
        )
    return None


def _daily_loss_limit(cfg: Settings, portfolio_value: float | None = None) -> float:
    loss_base = max(0.0, portfolio_value) if portfolio_value is not None else cfg.starting_cash
    return min(cfg.max_daily_loss, loss_base * cfg.max_daily_loss_pct)


def _bot_portfolio_value(cfg: Settings, summary, broker_net_liquidation: float) -> float:
    """Grow sizing only from this bot's closed, after-cost results."""
    recorded_equity = max(0.0, cfg.starting_cash + summary.total_profit)
    return min(recorded_equity, max(0.0, broker_net_liquidation))


def live_config_fingerprint(cfg: Settings) -> str:
    critical = {
        "broker": cfg.broker.strip().lower(),
        "host": cfg.ibkr_host,
        "port": cfg.ibkr_port,
        "client_id": cfg.ibkr_client_id,
        "account": cfg.ibkr_account,
        "exchange": cfg.ibkr_exchange,
        "currency": cfg.ibkr_currency,
        "symbols": cfg.symbol_list,
        "interval": cfg.interval,
        "lookback_days": cfg.lookback_days,
        "poll_seconds": cfg.poll_seconds,
        "min_win_probability": cfg.min_win_probability,
        "min_signal_samples": cfg.min_signal_samples,
        "signal_horizon_bars": cfg.signal_horizon_bars,
        "estimated_round_trip_commission": cfg.estimated_round_trip_commission,
        "what_if_commission_buffer_pct": cfg.what_if_commission_buffer_pct,
        "estimated_exchange_fee_rate": cfg.estimated_exchange_fee_rate,
        "estimated_fx_cost_rate": cfg.estimated_fx_cost_rate,
        "tax_rate": cfg.tax_rate,
        "min_net_profit_cost_multiple": cfg.min_net_profit_cost_multiple,
        "min_volume_ratio": cfg.min_volume_ratio,
        "min_atr_pct": cfg.min_atr_pct,
        "max_atr_pct": cfg.max_atr_pct,
        "starting_cash": cfg.starting_cash,
        "max_order_notional": cfg.max_order_notional,
        "max_total_exposure": cfg.max_total_exposure,
        "max_daily_loss": cfg.max_daily_loss,
        "max_daily_loss_pct": cfg.max_daily_loss_pct,
        "max_orders_per_day": cfg.max_orders_per_day,
        "max_concurrent_positions": cfg.max_concurrent_positions,
        "max_consecutive_losses": cfg.max_consecutive_losses,
        "max_consecutive_cycle_errors": cfg.max_consecutive_cycle_errors,
        "min_paper_closed_trades_for_live": cfg.min_paper_closed_trades_for_live,
        "min_paper_win_rate_for_live": cfg.min_paper_win_rate_for_live,
        "min_paper_profit_factor_for_live": cfg.min_paper_profit_factor_for_live,
        "min_paper_trading_days_for_live": cfg.min_paper_trading_days_for_live,
        "max_paper_drawdown_pct_for_live": cfg.max_paper_drawdown_pct_for_live,
        "stop_loss_pct": cfg.stop_loss_pct,
        "trailing_stop_pct": cfg.trailing_stop_pct,
        "take_profit_pct": cfg.take_profit_pct,
        "take_profit_max_pct": cfg.take_profit_max_pct,
        "take_profit_atr_multiplier": cfg.take_profit_atr_multiplier,
        "cooldown_minutes": cfg.cooldown_minutes,
        "daily_report_timezone": cfg.daily_report_timezone,
        "watchdog_stale_seconds": cfg.watchdog_stale_seconds,
        "live_preflight_valid_hours": cfg.live_preflight_valid_hours,
        "native_bracket": cfg.ibkr_native_bracket,
        "entry_limit_offset_pct": cfg.ibkr_entry_limit_offset_pct,
        "market_data_type": cfg.ibkr_market_data_type,
        "max_market_data_age_seconds": cfg.max_market_data_age_seconds,
        "live_tax_confirm": cfg.live_tax_confirm,
        "live_cost_model_confirm": cfg.live_cost_model_confirm,
    }
    payload = json.dumps(critical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_live_preflight(cfg: Settings, store: Store) -> None:
    live_orders_enabled = (
        cfg.broker.strip().lower() in {"ibkr", "interactivebrokers", "interactive_brokers"}
        and not cfg.ibkr_paper
        and not cfg.ibkr_read_only
        and not cfg.kill_switch
    )
    if not live_orders_enabled:
        return
    if store.get_state("live_preflight_fingerprint") != live_config_fingerprint(cfg):
        raise RuntimeError(
            "Live configuration has not passed preflight. Run "
            "python -m trading_bot.preflight --mode live"
        )
    preflight_time = store.get_state("last_preflight_utc")
    try:
        preflight_age = datetime.now(timezone.utc) - datetime.fromisoformat(preflight_time or "")
    except ValueError as exc:
        raise RuntimeError("Live preflight timestamp is missing or invalid") from exc
    if preflight_age > timedelta(hours=cfg.live_preflight_valid_hours):
        raise RuntimeError(
            f"Live preflight expired after {cfg.live_preflight_valid_hours} hours; run it again"
        )


def send_daily_report(store: Store, notifier: LineNotifier, cfg: Settings, now: datetime | None = None) -> bool:
    if not notifier.enabled:
        return False
    report_date = _latest_reportable_date(
        now or datetime.now(timezone.utc), cfg.daily_report_time, cfg.daily_report_timezone
    )
    state_key = "last_daily_report_date"
    if store.get_state(state_key) == report_date.isoformat():
        return False

    summary = store.trading_summary(report_date, cfg.daily_report_timezone)
    currency = cfg.daily_report_currency.upper()
    target = f" / {cfg.daily_profit_target:+,.2f}" if cfg.daily_profit_target else ""
    balance = cfg.starting_cash + summary.total_profit
    notifier.send(
        f"ผลประกอบการสุทธิ {report_date:%d/%m/%Y}\n"
        f"Gross Profit: {_signed(summary.daily_gross_profit)} {currency}\n"
        f"Commission: -{summary.daily_commission:,.2f} {currency}\n"
        f"Exchange Fee (estimated): -{summary.daily_exchange_fee:,.2f} {currency}\n"
        f"FX Cost (estimated): -{summary.daily_fx_cost:,.2f} {currency}\n"
        f"Estimated Tax ({cfg.tax_rate:.0%}): -{summary.daily_estimated_tax:,.2f} {currency}\n"
        f"Net Profit: {_signed(summary.daily_profit)} {currency}\n"
        "────────────\n"
        f"Gross Profit สะสม: {_signed(summary.total_gross_result)} {currency}\n"
        f"Commission สะสม: -{summary.total_commission:,.2f} {currency}\n"
        f"Exchange Fee สะสม: -{summary.total_exchange_fee:,.2f} {currency}\n"
        f"FX Cost สะสม: -{summary.total_fx_cost:,.2f} {currency}\n"
        f"Estimated Tax สะสม: -{summary.total_estimated_tax:,.2f} {currency}\n"
        f"Net Profit สะสม: {_signed(summary.total_profit)} {currency}"
    )
    notifier.send(
        f"📊 สรุปรายวัน {report_date:%d/%m/%Y}\n"
        f"วันนี้: {_signed(summary.daily_profit)}{target} {currency} • {summary.daily_trades} ไม้\n"
        f"Win Rate วันนี้: {_win_rate(summary.daily_wins, summary.daily_trades):.1%} "
        f"• {summary.daily_wins}W / {summary.daily_losses}L\n"
        "────────────\n"
        f"ยอดสะสม: {_signed(summary.total_profit)} {currency} • {summary.total_trades} ไม้\n"
        f"Win Rate สะสม: {_win_rate(summary.total_wins, summary.total_trades):.1%} "
        f"• {summary.total_wins}W / {summary.total_losses}L\n"
        f"ยอดคงเหลือ: {balance:,.2f} {currency}"
    )
    store.set_state(state_key, report_date.isoformat())
    return True


def create_broker(cfg: Settings, store: Store):
    broker_name = cfg.broker.strip().lower()
    if broker_name == "local":
        return LocalPaperBroker(store)
    if broker_name == "alpaca":
        return AlpacaBroker(cfg.alpaca_api_key, cfg.alpaca_secret_key, cfg.alpaca_paper, store)
    if broker_name in {"ibkr", "interactivebrokers", "interactive_brokers"}:
        if cfg.ibkr_host.strip().lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("IBKR_HOST must be loopback-only for this single-user deployment")
        if cfg.ibkr_paper and cfg.ibkr_port in {7496, 4001}:
            raise ValueError("IBKR_PAPER=true cannot use a standard IBKR live port")
        if not cfg.ibkr_paper and cfg.ibkr_port in {7497, 4002}:
            raise ValueError("IBKR_PAPER=false cannot use a standard IBKR paper port")
        if not cfg.ibkr_paper and cfg.ibkr_account.upper().startswith("DU"):
            raise ValueError("A DU paper account cannot be used with IBKR_PAPER=false")
        if (
            not cfg.ibkr_paper
            and not cfg.ibkr_read_only
            and cfg.live_trading_confirm != "I_UNDERSTAND_LIVE_ORDERS"
        ):
            raise ValueError(
                "Live order transmission requires "
                "LIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_ORDERS"
            )
        return InteractiveBrokersBroker(
            cfg.ibkr_host,
            cfg.ibkr_port,
            cfg.ibkr_client_id,
            cfg.ibkr_account,
            cfg.ibkr_exchange,
            cfg.ibkr_currency,
            cfg.ibkr_primary_exchange,
            cfg.ibkr_read_only,
            cfg.ibkr_order_timeout_seconds,
            cfg.ibkr_market_data_type,
            cfg.max_market_data_age_seconds,
            cfg.ibkr_native_bracket,
            cfg.ibkr_entry_limit_offset_pct,
            store,
        )
    raise ValueError("BROKER must be one of: local, alpaca, ibkr")


def run() -> None:
    cfg = Settings()
    ensure_allowed_user(cfg.allowed_os_user)
    root = Path(__file__).resolve().parent.parent
    lock_path = root / ".trading_bot.lock"
    pid_path = root / ".trading_bot.pid"
    stop_path = root / ".trading_bot.stop"
    expected_path = root / ".trading_bot.expected"
    heartbeat_path = root / ".trading_bot.heartbeat"
    with SingleInstance(lock_path):
        stop_path.unlink(missing_ok=True)
        expected_path.write_text("running", encoding="ascii")
        heartbeat_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")
        pid_path.write_text(str(os.getpid()), encoding="ascii")
        completed_normally = False
        try:
            _run(cfg, stop_path, heartbeat_path)
            completed_normally = True
        finally:
            pid_path.unlink(missing_ok=True)
            stop_path.unlink(missing_ok=True)
            heartbeat_path.unlink(missing_ok=True)
            if completed_normally:
                expected_path.unlink(missing_ok=True)


def _run(cfg: Settings, stop_path: Path, heartbeat_path: Path) -> None:
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("trading-bot")
    store = Store(
        cfg.database_url, cfg.estimated_exchange_fee_rate,
        cfg.estimated_fx_cost_rate, cfg.tax_rate,
    )
    require_live_preflight(cfg, store)
    notifier = LineNotifier(cfg.line_channel_access_token, cfg.line_target_id, store=store)
    broker = create_broker(cfg, store)
    running = True
    error_counts = {symbol: 0 for symbol in cfg.symbol_list}
    def stop(*_):
        nonlocal running
        running = False
    os_signal.signal(os_signal.SIGINT, stop); os_signal.signal(os_signal.SIGTERM, stop)
    broker_name = cfg.broker.strip().lower()
    paper_mode = (
        broker_name == "local"
        or (broker_name == "alpaca" and cfg.alpaca_paper)
        or (broker_name in {"ibkr", "interactivebrokers", "interactive_brokers"} and cfg.ibkr_paper)
    )
    if not paper_mode:
        startup_orders = broker.account_open_orders()
        unknown_orders = [
            order for order in startup_orders
            if not order["order_ref"].startswith("TradingBot:")
        ]
        if unknown_orders:
            broker.close()
            raise RuntimeError(
                f"Live startup blocked by {len(unknown_orders)} non-TradingBot open order(s)"
            )
        pending_entries = [
            order for order in startup_orders
            if order["order_ref"].endswith(":ENTRY")
        ]
        if pending_entries:
            broker.close()
            raise RuntimeError(
                f"Live startup blocked by {len(pending_entries)} pending entry order(s)"
            )
        unconfigured_positions = [
            item for item in broker.account_positions()
            if item["symbol"].upper() not in cfg.symbol_list
        ]
        if unconfigured_positions:
            broker.close()
            raise RuntimeError(
                "Live startup blocked by position(s) outside configured symbols: "
                + ", ".join(item["symbol"] for item in unconfigured_positions)
            )
    notifier.send("🤖 Trading bot started (paper mode)" if paper_mode else "⚠️ Trading bot started (LIVE mode)")
    while running:
        heartbeat_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")
        if not paper_mode:
            try:
                runtime_unknown_orders = [
                    order for order in broker.account_open_orders()
                    if not order["order_ref"].startswith("TradingBot:")
                ]
                runtime_unconfigured_positions = [
                    item for item in broker.account_positions()
                    if item["symbol"].upper() not in cfg.symbol_list
                ]
            except Exception as exc:
                log.exception("account-integrity monitoring failed")
                notifier.send(
                    "CRITICAL: account-integrity monitoring failed; TradingBot is stopping\n"
                    f"{type(exc).__name__}: {exc}"
                )
                running = False
                break
            if runtime_unknown_orders or runtime_unconfigured_positions:
                notifier.send(
                    "CRITICAL: unexpected account activity detected; TradingBot is stopping\n"
                    f"Unknown orders: {len(runtime_unknown_orders)}\n"
                    f"Unconfigured positions: {len(runtime_unconfigured_positions)}"
                )
                running = False
                break
        for symbol in cfg.symbol_list:
            cycle_failed = False
            try:
                data = broker.history(symbol, cfg.lookback_days, cfg.interval)
                if data is None:
                    data = yf.download(symbol, period=f"{cfg.lookback_days}d", interval=cfg.interval, auto_adjust=True, progress=False)
                if getattr(data.columns, "nlevels", 1) > 1: data.columns = data.columns.get_level_values(0)
                if data.empty or "Close" not in data:
                    log.warning("no market data for %s; skipping", symbol)
                    continue
                report_date = datetime.now(ZoneInfo(cfg.daily_report_timezone)).date()
                current_summary = store.trading_summary(
                    report_date, cfg.daily_report_timezone,
                )
                portfolio_value = _bot_portfolio_value(
                    cfg, current_summary, broker.net_liquidation(),
                )
                sig = analyze(
                    data,
                    cfg.min_signal_samples,
                    cfg.signal_horizon_bars,
                    cfg.stop_loss_pct,
                    cfg.take_profit_pct,
                    portfolio_value,
                    cfg.max_order_notional,
                    cfg.estimated_round_trip_commission,
                    cfg.estimated_exchange_fee_rate,
                    cfg.estimated_fx_cost_rate,
                    cfg.tax_rate,
                    cfg.min_net_profit_cost_multiple,
                    cfg.min_volume_ratio,
                    cfg.min_atr_pct,
                    cfg.max_atr_pct,
                    broker_name not in {
                        "ibkr", "interactivebrokers", "interactive_brokers",
                    },
                )
                log.info("%s side=%s probability=%.3f samples=%d", symbol, sig.side, sig.probability, sig.samples)
                if (
                    sig.side == "hold"
                    and sig.probability >= cfg.min_win_probability
                    and sig.expected_trading_cost > 0
                    and sig.expected_net_profit <= sig.required_net_profit
                ):
                    report_date = datetime.now(ZoneInfo(cfg.daily_report_timezone)).date()
                    skip_key = f"profitability_skip:{report_date.isoformat()}:{symbol}"
                    skip_value = (
                        f"{sig.expected_net_profit:.4f}:{sig.required_net_profit:.4f}"
                    )
                    if store.get_state(skip_key) != skip_value:
                        notifier.send(
                            f"SKIP BUY {symbol} — กำไรสุทธิไม่เพียงพอ\n"
                            f"Expected Gross Profit: {_signed(sig.expected_gross_profit)} "
                            f"{cfg.daily_report_currency.upper()}\n"
                            f"Trading Cost: -{sig.expected_trading_cost:,.2f} "
                            f"{cfg.daily_report_currency.upper()}\n"
                            f"Estimated Tax ({cfg.tax_rate:.0%}): -{sig.estimated_tax:,.2f} "
                            f"{cfg.daily_report_currency.upper()}\n"
                            f"Expected Net Profit: {_signed(sig.expected_net_profit)} "
                            f"{cfg.daily_report_currency.upper()}\n"
                            f"Required: > {sig.required_net_profit:,.2f} "
                            f"({cfg.min_net_profit_cost_multiple:g}x Trading Cost)"
                        )
                        store.set_state(skip_key, skip_value)
                position_qty, avg_price = broker.position(symbol)
                target_state_key = f"take_profit_pct:{symbol}"
                calculated_target_pct = profit_target_pct(
                    data,
                    cfg.take_profit_pct,
                    cfg.take_profit_max_pct,
                    cfg.take_profit_atr_multiplier,
                )
                stored_target = store.get_state(target_state_key)
                try:
                    stored_target_pct = float(stored_target) if stored_target else 0.0
                except ValueError:
                    stored_target_pct = 0.0
                position_target_pct = (
                    stored_target_pct
                    if cfg.take_profit_pct <= stored_target_pct <= cfg.take_profit_max_pct
                    else calculated_target_pct
                )
                native_ibkr_protection = (
                    broker_name in {"ibkr", "interactivebrokers", "interactive_brokers"}
                    and cfg.ibkr_native_bracket
                )
                if position_qty < -1e-9:
                    raise RuntimeError(f"Unsupported SHORT position detected: {symbol} {position_qty:g}")
                if position_qty > 0 and native_ibkr_protection:
                    try:
                        protection = broker.ensure_protective_orders(
                            symbol, position_qty, avg_price,
                            cfg.stop_loss_pct, position_target_pct, cfg.trailing_stop_pct,
                        )
                    except Exception as exc:
                        notifier.send(
                            f"CRITICAL: {symbol} position is not confirmed protected\n"
                            f"TradingBot is stopping\n{type(exc).__name__}: {exc}"
                        )
                        running = False
                        raise
                    if protection.get("repaired"):
                        notifier.send(
                            f"PROTECTION REPAIRED {symbol}\n"
                            f"Position: {position_qty:g}\n"
                            f"Stop: {protection['stop_loss_price']:.2f}\n"
                            f"Take profit: {protection['take_profit_price']:.2f}"
                        )
                if (
                    position_qty > 0
                    and not native_ibkr_protection
                    and (sig.price <= avg_price * (1 - cfg.stop_loss_pct)
                         or sig.price >= avg_price * (1 + position_target_pct))
                ):
                    reason = "STOP LOSS" if sig.price <= avg_price * (1 - cfg.stop_loss_pct) else "TAKE PROFIT"
                    if cfg.kill_switch:
                        log.warning("kill switch blocked %s SELL for %s", reason, symbol)
                        continue
                    order = broker.sell(symbol, position_qty, sig.price)
                    filled_qty = float(_order_value(order, "filled_qty", position_qty))
                    fill_price = float(_order_value(order, "avg_fill_price", sig.price))
                    fee = float(_order_value(order, "commission", 0))
                    realized = float(_order_value(order, "realized_pnl", 0))
                    notifier.send(
                        f"ค่าธรรมเนียม: {fee:.2f} {cfg.daily_report_currency.upper()}\n"
                        f"กำไร/ขาดทุนที่ IBKR รายงาน: {_signed(realized)} "
                        f"{cfg.daily_report_currency.upper()}"
                    )
                    notifier.send(f"🟠 SELL {symbol} ({reason})\nจำนวนที่ Fill: {filled_qty:g}\nราคา Fill: {fill_price:.2f}\nต้นทุน: {avg_price:.2f}")
                    store.set_state(target_state_key, "0")
                    log.info("exit order=%s", order)
                    continue
                last = store.last_trade(symbol)
                cooling = last and (datetime.now(timezone.utc) - datetime.fromisoformat(last[0])).total_seconds() < cfg.cooldown_minutes * 60
                if cfg.kill_switch or cooling or position_qty > 0 or sig.side != "buy" or sig.probability < cfg.min_win_probability:
                    continue
                order_reference = sig.price
                if broker_name in {"ibkr", "interactivebrokers", "interactive_brokers"}:
                    if not broker.market_is_open(symbol):
                        log.info("market is closed for %s; BUY skipped", symbol)
                        continue
                    quote = broker.current_quote(symbol)
                    if not cfg.ibkr_paper:
                        if quote["market_data_type"] != 1:
                            raise RuntimeError(
                                f"LIVE order blocked: {symbol} market data type is "
                                f"{quote['market_data_type']}, expected 1"
                            )
                        if quote["age_seconds"] > cfg.max_market_data_age_seconds:
                            raise RuntimeError(
                                f"LIVE order blocked: {symbol} quote is "
                                f"{quote['age_seconds']:.1f}s old"
                            )
                    order_reference = quote["ask"] or quote["market"] or quote["last"]
                entry_price_basis = order_reference * (
                    (1 + cfg.ibkr_entry_limit_offset_pct)
                    if native_ibkr_protection else 1
                )
                order_budget = _confidence_order_budget(
                    cfg, sig.probability, portfolio_value,
                )
                qty = _calculate_order_qty(
                    cfg, entry_price_basis, sig.probability, portfolio_value,
                )
                if qty < 1:
                    log.info(
                        "%s BUY skipped: confidence budget %.2f cannot buy one whole share at %.2f",
                        symbol, order_budget, order_reference,
                    )
                    budget_key = (
                        f"order_budget_block:{datetime.now(ZoneInfo(cfg.daily_report_timezone)).date()}:"
                        f"{symbol}:{order_budget:.2f}"
                    )
                    if store.get_state(budget_key) != "sent":
                        notifier.send(
                            f"ORDER SIZE BLOCKED {symbol}\n"
                            f"AI Confidence: {sig.probability:.1%}\n"
                            f"Budget: {order_budget:.2f} {cfg.daily_report_currency.upper()}\n"
                            f"One whole share costs about {order_reference:.2f}; no order was sent"
                        )
                        store.set_state(budget_key, "sent")
                    continue
                order_notional = qty * entry_price_basis
                round_trip_commission = cfg.estimated_round_trip_commission
                estimated_entry_commission = round_trip_commission / 2
                if broker_name in {
                    "ibkr", "interactivebrokers", "interactive_brokers",
                }:
                    state = broker.what_if_buy(symbol, entry_price_basis, qty)
                    warning = str(getattr(state, "warningText", "") or "").strip()
                    if warning:
                        raise RuntimeError(f"IBKR What-If warning for {symbol}: {warning}")
                    round_trip_commission = _what_if_round_trip_commission(
                        state, cfg.what_if_commission_buffer_pct,
                    )
                    estimated_entry_commission = round_trip_commission / 2
                profitability = estimate_profitability(
                    sig.probability, order_notional, cfg.stop_loss_pct,
                    calculated_target_pct, round_trip_commission,
                    cfg.estimated_exchange_fee_rate,
                    cfg.estimated_fx_cost_rate, cfg.tax_rate,
                    cfg.min_net_profit_cost_multiple,
                )
                if not profitability.should_trade:
                    skip_key = f"profitability_skip:{report_date.isoformat()}:{symbol}"
                    skip_value = (
                        f"{profitability.net_profit:.4f}:"
                        f"{profitability.required_net_profit:.4f}"
                    )
                    if store.get_state(skip_key) != skip_value:
                        notifier.send(
                            f"SKIP BUY {symbol} — Expected Net Profit ไม่ผ่านเกณฑ์\n"
                            f"Expected Gross: {_signed(profitability.gross_profit)} "
                            f"{cfg.daily_report_currency.upper()}\n"
                            f"Commission + buffer: -{profitability.commission:,.2f}\n"
                            f"Exchange Fee: -{profitability.exchange_fee:,.2f}\n"
                            f"FX Cost: -{profitability.fx_cost:,.2f}\n"
                            f"Estimated Tax: -{profitability.estimated_tax:,.2f}\n"
                            f"Expected Net: {_signed(profitability.net_profit)}\n"
                            f"Required: > {profitability.required_net_profit:,.2f} "
                            f"({cfg.min_net_profit_cost_multiple:g}x cost)"
                        )
                        store.set_state(skip_key, skip_value)
                    continue
                report_date = datetime.now(ZoneInfo(cfg.daily_report_timezone)).date()
                risk_reason = _risk_block_reason(
                    cfg,
                    store.trading_summary(report_date, cfg.daily_report_timezone),
                    broker.account_exposure(),
                    order_notional,
                    broker.available_funds(),
                    len(broker.account_positions()),
                    broker.account_daily_pnl(),
                    estimated_entry_commission,
                    portfolio_value,
                )
                if risk_reason:
                    risk_key = f"risk_block:{report_date.isoformat()}:{symbol}"
                    if store.get_state(risk_key) != risk_reason:
                        notifier.send(f"RISK LIMIT BLOCKED BUY {symbol}\n{risk_reason}")
                        store.set_state(risk_key, risk_reason)
                    continue
                target_pct = calculated_target_pct
                order = broker.buy(
                    symbol, qty, order_reference, sig.probability,
                    entry_price_basis * (1 - cfg.stop_loss_pct),
                    entry_price_basis * (1 + target_pct),
                    cfg.trailing_stop_pct,
                )
                store.set_state(target_state_key, f"{target_pct:.8f}")
                filled_qty = float(_order_value(order, "filled_qty", qty))
                fill_price = float(_order_value(order, "avg_fill_price", sig.price))
                notifier.send(
                    f"ค่าธรรมเนียม: {float(_order_value(order, 'commission', 0)):.2f} "
                    f"{cfg.daily_report_currency.upper()}"
                )
                notifier.send(f"✅ BUY {symbol}\nจำนวนที่ Fill: {filled_qty:g}\nราคา Fill: {fill_price:.2f}\nความน่าจะเป็นขั้นต่ำ: {sig.probability:.1%}\n{sig.reason}")
                notifier.send(
                    f"AI allocation: {_confidence_allocation_pct(sig.probability):.0%} of portfolio "
                    f"(budget capped at {order_budget:.2f} {cfg.daily_report_currency.upper()})\n"
                    f"Stop loss: {cfg.stop_loss_pct:.2%} | "
                    f"Trailing stop: {cfg.trailing_stop_pct:.2%}\n"
                    f"Profit target: {target_pct:.2%}\n"
                    f"Take-profit price: {float(_order_value(order, 'take_profit_price', entry_price_basis * (1 + target_pct))):.2f}"
                )
                log.info("order=%s", order)
            except Exception as exc:
                cycle_failed = True
                log.exception("cycle failed for %s", symbol)
                error_counts[symbol] = error_counts.get(symbol, 0) + 1
                error_key = f"cycle_error:{symbol}"
                error_text = f"{type(exc).__name__}: {exc}"
                if store.get_state(error_key) != error_text:
                    notifier.send(f"TradingBot error {symbol}\n{error_text}")
                    store.set_state(error_key, error_text)
                if error_counts[symbol] >= cfg.max_consecutive_cycle_errors:
                    notifier.send(
                        f"TradingBot stopped after {error_counts[symbol]} consecutive "
                        f"errors for {symbol}"
                    )
                    running = False
            finally:
                if not cycle_failed:
                    error_counts[symbol] = 0
            if not running:
                break
        try:
            notifier.flush_pending()
            if send_daily_report(store, notifier, cfg):
                log.info("daily report sent")
        except Exception:
            log.exception("daily report failed")
        remaining = cfg.poll_seconds
        while running and remaining > 0 and not stop_path.exists():
            heartbeat_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")
            wait_seconds = min(1, remaining)
            try:
                broker.wait(wait_seconds)
                monitored_daily_pnl = broker.account_daily_pnl()
                monitored_loss_limit = _daily_loss_limit(
                    cfg, _bot_portfolio_value(
                        cfg,
                        store.trading_summary(
                            datetime.now(ZoneInfo(cfg.daily_report_timezone)).date(),
                            cfg.daily_report_timezone,
                        ),
                        broker.net_liquidation(),
                    ),
                )
                if (
                    monitored_daily_pnl is not None
                    and monitored_daily_pnl <= -monitored_loss_limit
                ):
                    notifier.send(
                        f"CRITICAL: account DailyPnL {monitored_daily_pnl:.2f} reached "
                        f"the {-monitored_loss_limit:.2f} limit; flattening positions"
                    )
                    if broker_name in {"ibkr", "interactivebrokers", "interactive_brokers"}:
                        for item in broker.account_positions():
                            symbol = item["symbol"].upper()
                            if symbol not in cfg.symbol_list or item["qty"] <= 0:
                                continue
                            if not broker.market_is_open(symbol):
                                raise RuntimeError(
                                    f"Daily-loss limit reached while {symbol} market is closed; "
                                    "protective orders were left active"
                                )
                            broker.cancel_protective_orders(symbol)
                            current_qty, _ = broker.position(symbol)
                            if current_qty > 0:
                                broker.sell(symbol, current_qty, 0)
                    running = False
                    break
                executions = broker.sync_executions()
            except Exception as exc:
                log.exception("broker monitoring failed; stopping TradingBot")
                notifier.send(
                    f"CRITICAL: broker monitoring failed; TradingBot is stopping\n"
                    f"{type(exc).__name__}: {exc}"
                )
                running = False
                break
            for execution in executions:
                realized = execution.get("realized_pnl")
                details = (
                    f"ค่าธรรมเนียม: {execution.get('commission', 0):.2f} "
                    f"{cfg.daily_report_currency.upper()}"
                )
                if realized is not None:
                    details += (
                        f"\nกำไร/ขาดทุนที่ IBKR รายงาน: {_signed(float(realized))} "
                        f"{cfg.daily_report_currency.upper()}"
                    )
                notifier.send(details)
                notifier.send(
                    f"🔄 IBKR FILL {execution['side'].upper()} {execution['symbol']}\n"
                    f"จำนวน: {execution['qty']:g}\nราคา: {execution['price']:.2f}"
                )
            remaining -= wait_seconds
        if stop_path.exists():
            log.info("stop requested")
            running = False
    broker.close()


if __name__ == "__main__":
    run()
