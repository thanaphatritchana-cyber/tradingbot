import logging
import os
import signal as os_signal
import hashlib
import json
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import yfinance as yf
from .broker import AlpacaBroker, InteractiveBrokersBroker, LocalPaperBroker
from .config import Settings
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


def _order_value(order, name: str, default):
    value = order.get(name) if isinstance(order, dict) else getattr(order, name, None)
    return default if value is None or value == "" else value


def _calculate_order_qty(cfg: Settings, price: float) -> int:
    if price <= 0:
        return 0
    risk_cash = cfg.starting_cash * cfg.risk_per_trade
    cap_cash = min(cfg.starting_cash * cfg.max_position_pct, cfg.max_order_notional)
    return max(0, int(min(risk_cash / (price * cfg.stop_loss_pct), cap_cash / price)))


def _risk_block_reason(
    cfg: Settings, summary, exposure: float, next_notional: float,
    available_funds: float = float("inf"),
) -> str | None:
    if summary.daily_profit <= -cfg.max_daily_loss:
        return f"daily net loss {summary.daily_profit:.2f} reached limit {-cfg.max_daily_loss:.2f}"
    if summary.daily_buys >= cfg.max_orders_per_day:
        return f"daily order limit reached ({summary.daily_buys}/{cfg.max_orders_per_day})"
    if summary.daily_consecutive_losses >= cfg.max_consecutive_losses:
        return (
            f"consecutive loss limit reached "
            f"({summary.daily_consecutive_losses}/{cfg.max_consecutive_losses})"
        )
    if exposure + next_notional > cfg.max_total_exposure + 1e-9:
        return (
            f"aggregate exposure would be {exposure + next_notional:.2f}, "
            f"above {cfg.max_total_exposure:.2f}"
        )
    if next_notional > available_funds + 1e-9:
        return (
            f"order notional {next_notional:.2f} exceeds available funds "
            f"{available_funds:.2f}"
        )
    return None


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
        "starting_cash": cfg.starting_cash,
        "risk_per_trade": cfg.risk_per_trade,
        "max_position_pct": cfg.max_position_pct,
        "max_order_notional": cfg.max_order_notional,
        "max_total_exposure": cfg.max_total_exposure,
        "max_daily_loss": cfg.max_daily_loss,
        "max_orders_per_day": cfg.max_orders_per_day,
        "max_consecutive_losses": cfg.max_consecutive_losses,
        "max_consecutive_cycle_errors": cfg.max_consecutive_cycle_errors,
        "min_paper_closed_trades_for_live": cfg.min_paper_closed_trades_for_live,
        "min_paper_win_rate_for_live": cfg.min_paper_win_rate_for_live,
        "stop_loss_pct": cfg.stop_loss_pct,
        "take_profit_pct": cfg.take_profit_pct,
        "take_profit_max_pct": cfg.take_profit_max_pct,
        "take_profit_atr_multiplier": cfg.take_profit_atr_multiplier,
        "native_bracket": cfg.ibkr_native_bracket,
        "entry_limit_offset_pct": cfg.ibkr_entry_limit_offset_pct,
        "market_data_type": cfg.ibkr_market_data_type,
        "max_market_data_age_seconds": cfg.max_market_data_age_seconds,
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
        f"ค่าธรรมเนียมและกำไรสุทธิ {report_date:%d/%m/%Y}\n"
        f"กำไรสุทธิวันนี้: {_signed(summary.daily_profit)} {currency}\n"
        f"ค่าธรรมเนียมวันนี้: {summary.daily_fees:,.2f} {currency}\n"
        f"กำไรสุทธิสะสม: {_signed(summary.total_profit)} {currency}\n"
        f"ค่าธรรมเนียมสะสม: {summary.total_fees:,.2f} {currency}"
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
    with SingleInstance(lock_path):
        stop_path.unlink(missing_ok=True)
        pid_path.write_text(str(os.getpid()), encoding="ascii")
        try:
            _run(cfg, stop_path)
        finally:
            pid_path.unlink(missing_ok=True)
            stop_path.unlink(missing_ok=True)


def _run(cfg: Settings, stop_path: Path) -> None:
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("trading-bot")
    store = Store(cfg.database_url)
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
        unknown_orders = [
            order for order in broker.account_open_orders()
            if not order["order_ref"].startswith("TradingBot:")
        ]
        if unknown_orders:
            broker.close()
            raise RuntimeError(
                f"Live startup blocked by {len(unknown_orders)} non-TradingBot open order(s)"
            )
    notifier.send("🤖 Trading bot started (paper mode)" if paper_mode else "⚠️ Trading bot started (LIVE mode)")
    while running:
        for symbol in cfg.symbol_list:
            try:
                data = broker.history(symbol, cfg.lookback_days, cfg.interval)
                if data is None:
                    data = yf.download(symbol, period=f"{cfg.lookback_days}d", interval=cfg.interval, auto_adjust=True, progress=False)
                if getattr(data.columns, "nlevels", 1) > 1: data.columns = data.columns.get_level_values(0)
                if data.empty or "Close" not in data:
                    log.warning("no market data for %s; skipping", symbol)
                    continue
                sig = analyze(data, cfg.min_signal_samples)
                log.info("%s side=%s probability=%.3f samples=%d", symbol, sig.side, sig.probability, sig.samples)
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
                            cfg.stop_loss_pct, position_target_pct,
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
                error_counts[symbol] = 0
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
                if cfg.kill_switch or cooling or position_qty > 0 or sig.side != "buy" or sig.probability <= cfg.min_win_probability:
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
                qty = _calculate_order_qty(cfg, order_reference)
                if qty < 1: continue
                report_date = datetime.now(ZoneInfo(cfg.daily_report_timezone)).date()
                risk_reason = _risk_block_reason(
                    cfg,
                    store.trading_summary(report_date, cfg.daily_report_timezone),
                    broker.account_exposure(),
                    qty * order_reference,
                    broker.available_funds(),
                )
                if risk_reason:
                    risk_key = f"risk_block:{report_date.isoformat()}:{symbol}"
                    if store.get_state(risk_key) != risk_reason:
                        notifier.send(f"RISK LIMIT BLOCKED BUY {symbol}\n{risk_reason}")
                        store.set_state(risk_key, risk_reason)
                    continue
                target_pct = calculated_target_pct
                entry_price_basis = order_reference * (
                    (1 + cfg.ibkr_entry_limit_offset_pct)
                    if native_ibkr_protection else 1
                )
                order = broker.buy(
                    symbol, qty, order_reference, sig.probability,
                    entry_price_basis * (1 - cfg.stop_loss_pct),
                    entry_price_basis * (1 + target_pct),
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
                    f"Profit target: {target_pct:.2%} "
                    f"(allowed range {cfg.take_profit_pct:.0%}-{cfg.take_profit_max_pct:.0%})\n"
                    f"Take-profit price: {float(_order_value(order, 'take_profit_price', entry_price_basis * (1 + target_pct))):.2f}"
                )
                log.info("order=%s", order)
            except Exception as exc:
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
            wait_seconds = min(1, remaining)
            broker.wait(wait_seconds)
            for execution in broker.sync_executions():
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
