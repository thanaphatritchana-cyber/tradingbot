"""One-share Paper-only round-trip validation during US regular market hours."""

from datetime import datetime, time as clock_time
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from .broker import InteractiveBrokersBroker
from .config import Settings
from .notify import LineNotifier
from .security import ensure_allowed_user
from .storage import Store


ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "paper-roundtrip.log"


def validate_paper_safety(cfg: Settings) -> list[str]:
    errors = []
    if not cfg.ibkr_paper:
        errors.append("IBKR_PAPER must be true")
    if not cfg.ibkr_account.upper().startswith("DU"):
        errors.append("IBKR_ACCOUNT must be a DU paper account")
    if cfg.ibkr_port not in {7497, 4002}:
        errors.append("IBKR_PORT must be a paper port (7497 or 4002)")
    if cfg.ibkr_read_only:
        errors.append("IBKR_READ_ONLY must be false")
    if not cfg.kill_switch:
        errors.append("KILL_SWITCH must remain true for the isolated test")
    if cfg.max_order_notional < 500:
        errors.append("MAX_ORDER_NOTIONAL is too low for one AAPL share")
    return errors


def run() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )
    log = logging.getLogger("trading-bot.paper-roundtrip")
    cfg = Settings()
    ensure_allowed_user(cfg.allowed_os_user)
    errors = validate_paper_safety(cfg)
    if errors:
        raise RuntimeError("; ".join(errors))

    eastern_now = datetime.now(ZoneInfo("America/New_York"))
    if eastern_now.weekday() >= 5 or not clock_time(9, 35) <= eastern_now.time() <= clock_time(15, 45):
        raise RuntimeError(f"Refusing outside US regular test window: {eastern_now.isoformat()}")

    store = Store(cfg.database_url)
    notifier = LineNotifier(cfg.line_channel_access_token, cfg.line_target_id, store=store)
    state_key = f"paper_roundtrip_{eastern_now.date().isoformat()}"
    if store.get_state(state_key) == "completed":
        log.info("Paper round-trip already completed today")
        return 0

    symbol = cfg.symbol_list[0]
    initial_qty = 0.0
    broker = None
    try:
        broker = InteractiveBrokersBroker(
            cfg.ibkr_host, cfg.ibkr_port, cfg.ibkr_client_id + 200,
            cfg.ibkr_account, cfg.ibkr_exchange, cfg.ibkr_currency,
            cfg.ibkr_primary_exchange, False, max(60, cfg.ibkr_order_timeout_seconds),
            cfg.ibkr_market_data_type, True, cfg.ibkr_entry_limit_offset_pct, store,
        )
        initial_qty, _ = broker.position(symbol)
        if initial_qty != 0:
            raise RuntimeError(f"Refusing test: existing {symbol} position is {initial_qty:g}")
        quote = broker.current_quote(symbol)
        reference = quote["ask"] or quote["market"] or quote["last"]
        if reference <= 0 or reference > cfg.max_order_notional:
            raise RuntimeError(
                f"One {symbol} share at {reference:.2f} exceeds MAX_ORDER_NOTIONAL"
            )

        notifier.send(f"🧪 เริ่ม Paper round-trip {symbol} 1 หุ้น (ไม่มีเงินจริง)")
        buy = broker.buy(
            symbol, 1, reference, 1.0,
            reference * (1 + cfg.ibkr_entry_limit_offset_pct) * (1 - cfg.stop_loss_pct),
            reference * (1 + cfg.ibkr_entry_limit_offset_pct) * (1 + cfg.take_profit_pct),
        )
        notifier.send(
            f"✅ PAPER BUY Fill {symbol}\n"
            f"จำนวน: {float(buy['filled_qty']):g}\nราคา: {float(buy['avg_fill_price']):.2f}"
        )
        position_qty, _ = broker.position(symbol)
        if position_qty < 1:
            raise RuntimeError(f"Paper BUY filled but position is only {position_qty:g}")

        protection = broker.protective_orders(symbol)
        if len(protection) != 2:
            raise RuntimeError(f"Expected 2 active protective orders, found {len(protection)}")
        cancelled = broker.cancel_protective_orders(symbol)
        if any(status not in {"Cancelled", "ApiCancelled"} for status in cancelled):
            raise RuntimeError(f"Protective cancellation failed: {cancelled}")
        notifier.send("✅ Native bracket ผ่าน: Stop Loss + Take Profit ถูกสร้างและยกเลิกได้")

        sell = broker.sell(symbol, position_qty, reference)
        notifier.send(
            f"✅ PAPER SELL Fill {symbol}\n"
            f"จำนวน: {float(sell['filled_qty']):g}\nราคา: {float(sell['avg_fill_price']):.2f}"
        )
        final_qty, _ = broker.position(symbol)
        if final_qty != initial_qty:
            raise RuntimeError(f"Paper round-trip did not flatten position: {final_qty:g}")
        store.set_state(state_key, "completed")
        notifier.send("✅ Paper round-trip ผ่านครบ: Fill, SQL, LINE และ Position กลับเป็น 0")
        log.info("Paper round-trip completed successfully")
        return 0
    except Exception as exc:
        log.exception("Paper round-trip failed")
        try:
            if broker is not None:
                current_qty, _ = broker.position(symbol)
                if current_qty > initial_qty:
                    broker.cancel_protective_orders(symbol)
                    broker.sell(symbol, current_qty - initial_qty, 0)
                    log.warning("Emergency Paper flatten completed")
        except Exception:
            log.exception("Emergency Paper flatten failed")
        notifier.send(f"❌ Paper round-trip ล้มเหลว: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if broker is not None:
            broker.close()


if __name__ == "__main__":
    raise SystemExit(run())
