import logging
import signal as os_signal
import time
from datetime import datetime, timezone
import yfinance as yf
from .broker import AlpacaBroker, LocalPaperBroker
from .config import Settings
from .notify import LineNotifier
from .storage import Store
from .strategy import analyze


def run() -> None:
    cfg = Settings()
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("trading-bot")
    store, notifier = Store(cfg.database_url), LineNotifier(cfg.line_channel_access_token, cfg.line_target_id)
    broker = AlpacaBroker(cfg.alpaca_api_key, cfg.alpaca_secret_key, cfg.alpaca_paper, store) if cfg.broker == "alpaca" else LocalPaperBroker(store)
    running = True
    def stop(*_):
        nonlocal running
        running = False
    os_signal.signal(os_signal.SIGINT, stop); os_signal.signal(os_signal.SIGTERM, stop)
    notifier.send("🤖 Trading bot started (paper mode)" if cfg.broker == "local" or cfg.alpaca_paper else "⚠️ Trading bot started (LIVE mode)")
    while running:
        for symbol in cfg.symbol_list:
            try:
                data = yf.download(symbol, period=f"{cfg.lookback_days}d", interval=cfg.interval, auto_adjust=True, progress=False)
                if getattr(data.columns, "nlevels", 1) > 1: data.columns = data.columns.get_level_values(0)
                sig = analyze(data, cfg.min_signal_samples)
                log.info("%s side=%s probability=%.3f samples=%d", symbol, sig.side, sig.probability, sig.samples)
                position_qty, avg_price = store.position(symbol)
                if position_qty > 0 and (sig.price <= avg_price * (1 - cfg.stop_loss_pct) or sig.price >= avg_price * (1 + cfg.take_profit_pct)):
                    reason = "STOP LOSS" if sig.price <= avg_price * (1 - cfg.stop_loss_pct) else "TAKE PROFIT"
                    order = broker.sell(symbol, position_qty, sig.price)
                    notifier.send(f"🟠 SELL {symbol} ({reason})\nจำนวน: {position_qty:g}\nราคาอ้างอิง: {sig.price:.2f}\nต้นทุน: {avg_price:.2f}")
                    log.info("exit order=%s", order)
                    continue
                last = store.last_trade(symbol)
                cooling = last and (datetime.now(timezone.utc) - datetime.fromisoformat(last[0])).total_seconds() < cfg.cooldown_minutes * 60
                if cfg.kill_switch or cooling or position_qty > 0 or sig.side != "buy" or sig.probability < cfg.min_win_probability:
                    continue
                risk_cash = cfg.starting_cash * cfg.risk_per_trade
                cap_cash = cfg.starting_cash * cfg.max_position_pct
                qty = max(0, int(min(risk_cash / (sig.price * cfg.stop_loss_pct), cap_cash / sig.price)))
                if qty < 1: continue
                order = broker.buy(symbol, qty, sig.price, sig.probability)
                notifier.send(f"✅ BUY {symbol}\nจำนวน: {qty}\nราคาอ้างอิง: {sig.price:.2f}\nความน่าจะเป็นขั้นต่ำ: {sig.probability:.1%}\n{sig.reason}")
                log.info("order=%s", order)
            except Exception:
                log.exception("cycle failed for %s", symbol)
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    run()
