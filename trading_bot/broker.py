from abc import ABC, abstractmethod
from datetime import datetime, timezone
import logging
import math
import time
from zoneinfo import ZoneInfo

from .storage import Store


def _within_liquid_hours(liquid_hours: str, timezone_name: str, now: datetime) -> bool:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    for session in liquid_hours.split(";"):
        if not session or "CLOSED" in session.upper() or ":" not in session:
            continue
        session_date, windows = session.split(":", 1)
        for window in windows.split(","):
            if "-" not in window:
                continue
            start_token, end_token = window.split("-", 1)
            start_date, start_time = session_date, start_token
            if ":" in end_token:
                end_date, end_time = end_token.split(":", 1)
            else:
                end_date, end_time = session_date, end_token
            try:
                start = datetime.strptime(start_date + start_time, "%Y%m%d%H%M").replace(
                    tzinfo=local_now.tzinfo
                )
                end = datetime.strptime(end_date + end_time, "%Y%m%d%H%M").replace(
                    tzinfo=local_now.tzinfo
                )
            except ValueError:
                continue
            if start <= local_now <= end:
                return True
    return False


class Broker(ABC):
    @abstractmethod
    def buy(
        self, symbol: str, qty: float, price: float, probability: float,
        stop_loss_price: float | None = None, take_profit_price: float | None = None,
        trailing_stop_pct: float | None = None,
    ): ...
    @abstractmethod
    def sell(self, symbol: str, qty: float, price: float, probability: float = 0): ...

    def position(self, symbol: str) -> tuple[float, float]:
        return self.store.position(symbol)

    def close(self) -> None:
        pass

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def history(self, symbol: str, lookback_days: int, interval: str):
        return None

    def sync_executions(self) -> list[dict]:
        return []

    def account_positions(self) -> list[dict]:
        return []

    def account_exposure(self) -> float:
        return sum(abs(item["qty"] * item["avg_cost"]) for item in self.account_positions())

    def account_open_orders(self) -> list[dict]:
        return []

    def available_funds(self) -> float:
        return float("inf")

    def net_liquidation(self) -> float:
        return float("inf")

    def account_daily_pnl(self) -> float | None:
        return None

    def market_is_open(self, symbol: str) -> bool:
        return True


class LocalPaperBroker(Broker):
    def __init__(self, store: Store): self.store = store
    def buy(self, symbol, qty, price, probability, stop_loss_price=None, take_profit_price=None, trailing_stop_pct=None):
        self.store.record(symbol, "buy", qty, price, probability)
        return {"id": "local", "status": "filled", "symbol": symbol, "qty": qty}
    def sell(self, symbol, qty, price, probability=0):
        self.store.record(symbol, "sell", qty, price, probability)
        return {"id": "local", "status": "filled", "symbol": symbol, "qty": qty}


class AlpacaBroker(Broker):
    def __init__(self, key: str, secret: str, paper: bool, store: Store):
        from alpaca.trading.client import TradingClient
        self.client = TradingClient(key, secret, paper=paper)
        self.store = store

    def buy(self, symbol, qty, price, probability, stop_loss_price=None, take_profit_price=None, trailing_stop_pct=None):
        result = self._order(symbol, qty, "buy")
        self.store.record(symbol, "buy", qty, price, probability, "submitted")
        return result

    def sell(self, symbol, qty, price, probability=0):
        result = self._order(symbol, qty, "sell")
        self.store.record(symbol, "sell", qty, price, probability, "submitted")
        return result

    def _order(self, symbol, qty, side):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        req = MarketOrderRequest(symbol=symbol, qty=qty, side=order_side, time_in_force=TimeInForce.DAY)
        return self.client.submit_order(order_data=req)


class InteractiveBrokersBroker(Broker):
    """Synchronous IBKR TWS/IB Gateway adapter backed by ib_async."""

    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        account: str,
        exchange: str,
        currency: str,
        primary_exchange: str,
        read_only: bool,
        order_timeout_seconds: int,
        market_data_type: int,
        max_market_data_age_seconds: int,
        native_bracket: bool,
        entry_limit_offset_pct: float,
        store: Store,
        trade_purpose: str = "strategy",
    ):
        from ib_async import IB

        self.log = logging.getLogger("trading-bot.ibkr")
        self.host, self.port, self.client_id = host, port, client_id
        self.account = account.strip()
        self.exchange, self.currency = exchange, currency
        self.primary_exchange = primary_exchange
        self.read_only = read_only
        self.order_timeout_seconds = order_timeout_seconds
        self.market_data_type = market_data_type
        self.max_market_data_age_seconds = max_market_data_age_seconds
        self.native_bracket = native_bracket
        self.entry_limit_offset_pct = entry_limit_offset_pct
        self.store = store
        if trade_purpose not in {"strategy", "connectivity_test"}:
            raise ValueError("trade_purpose must be strategy or connectivity_test")
        self.trade_purpose = trade_purpose
        self.order_ref_prefix = (
            "TradingBotTest" if trade_purpose == "connectivity_test" else "TradingBot"
        )
        self.ib = IB()
        self._executions_loaded = False
        self._pnl_subscription = None
        self._connect()

    @property
    def _order_prefix(self) -> str:
        return getattr(self, "order_ref_prefix", "TradingBot")

    def _connect(self) -> None:
        if self.ib.isConnected():
            return
        self.ib.connect(
            self.host,
            self.port,
            clientId=self.client_id,
            account=self.account,
            readonly=self.read_only,
            timeout=10,
        )
        accounts = self.ib.managedAccounts()
        if self.account:
            if self.account not in accounts:
                self.ib.disconnect()
                raise RuntimeError(f"IBKR account {self.account!r} is not available to this login")
        elif len(accounts) == 1:
            self.account = accounts[0]
        elif len(accounts) > 1:
            self.ib.disconnect()
            raise RuntimeError("IBKR_ACCOUNT is required when the login manages multiple accounts")
        else:
            self.ib.disconnect()
            raise RuntimeError("IBKR returned no managed account")
        self.log.info(
            "connected host=%s port=%s client_id=%s account=%s read_only=%s",
            self.host, self.port, self.client_id, self.account, self.read_only,
        )
        self.ib.reqMarketDataType(self.market_data_type)
        self._executions_loaded = False
        self._pnl_subscription = None

    def _ensure_connected(self) -> None:
        if not self.ib.isConnected():
            self.log.warning("IBKR disconnected; reconnecting")
            self._connect()

    def _stock(self, symbol: str):
        from ib_async import Stock

        contract = Stock(symbol, self.exchange, self.currency)
        if self.primary_exchange:
            contract.primaryExchange = self.primary_exchange
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(
                f"IBKR could not resolve {symbol!r}; check IBKR_EXCHANGE, "
                "IBKR_CURRENCY and IBKR_PRIMARY_EXCHANGE"
            )
        return qualified[0]

    def position(self, symbol: str) -> tuple[float, float]:
        self._ensure_connected()
        contract = self._stock(symbol)
        matches = [
            item for item in self.ib.positions(self.account)
            if item.contract.conId == contract.conId
        ]
        if not matches:
            return 0.0, 0.0
        return sum(float(item.position) for item in matches), float(matches[0].avgCost)

    def history(self, symbol: str, lookback_days: int, interval: str):
        import pandas as pd

        self._ensure_connected()
        contract = self._stock(symbol)
        bar_sizes = {
            "1m": "1 min", "2m": "2 mins", "5m": "5 mins",
            "15m": "15 mins", "30m": "30 mins", "60m": "1 hour",
            "1h": "1 hour", "1d": "1 day",
        }
        try:
            bar_size = bar_sizes[interval.lower()]
        except KeyError as exc:
            raise ValueError(f"Unsupported IBKR interval: {interval}") from exc
        duration = (
            f"{max(1, lookback_days)} D"
            if lookback_days <= 365
            else f"{max(1, round(lookback_days / 365))} Y"
        )
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
            keepUpToDate=False,
            timeout=30,
        )
        if not bars:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        frame = pd.DataFrame([
            {
                "Date": bar.date, "Open": bar.open, "High": bar.high,
                "Low": bar.low, "Close": bar.close, "Volume": bar.volume,
            }
            for bar in bars
        ])
        return frame.set_index("Date")

    def current_quote(self, symbol: str, timeout: float = 8) -> dict[str, float]:
        import math

        self._ensure_connected()
        contract = self._stock(symbol)
        # TWS can revert a newly synchronized session to live data type even
        # after the connection-level request. Reassert the configured type for
        # every quote so Paper mode reliably receives delayed data (type 3).
        self.ib.reqMarketDataType(self.market_data_type)
        self.ib.sleep(0.1)
        ticker = self.ib.reqMktData(contract, "", False, False)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                self.ib.sleep(0.25)
                values = (ticker.bid, ticker.ask, ticker.last, ticker.marketPrice())
                if any(
                    isinstance(value, (int, float)) and math.isfinite(value) and value > 0
                    for value in values
                ) and int(getattr(ticker, "marketDataType", 0) or 0) in {1, 2, 3, 4}:
                    break
            def valid(value) -> float:
                return float(value) if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else 0.0
            quote = {
                "bid": valid(ticker.bid),
                "ask": valid(ticker.ask),
                "last": valid(ticker.last),
                "market": valid(ticker.marketPrice()),
                "market_data_type": int(getattr(ticker, "marketDataType", 0) or 0),
            }
            quote_time = getattr(ticker, "time", None)
            if quote_time is not None:
                if quote_time.tzinfo is None:
                    quote_time = quote_time.replace(tzinfo=timezone.utc)
                quote["age_seconds"] = max(
                    0.0, (datetime.now(timezone.utc) - quote_time.astimezone(timezone.utc)).total_seconds()
                )
            else:
                quote["age_seconds"] = float("inf")
            if not any(quote[name] for name in ("bid", "ask", "last", "market")):
                raise RuntimeError(f"No current market quote for {symbol}")
            return quote
        finally:
            self.ib.cancelMktData(contract)

    def buy(self, symbol, qty, price, probability, stop_loss_price=None, take_profit_price=None, trailing_stop_pct=None):
        if self.native_bracket:
            if (
                stop_loss_price is None or take_profit_price is None
                or trailing_stop_pct is None or trailing_stop_pct <= 0
            ):
                raise ValueError(
                    "Native bracket BUY requires stop-loss, take-profit and trailing-stop values"
                )
            return self._place_bracket(
                symbol, qty, price, probability, stop_loss_price, take_profit_price,
                trailing_stop_pct,
            )
        return self._place(symbol, qty, "BUY", probability)

    def sell(self, symbol, qty, price, probability=0):
        return self._place(symbol, qty, "SELL", probability)

    def _place(
        self, symbol: str, qty: float, action: str, probability: float,
        _partial_attempt: int = 0,
    ):
        from ib_async import MarketOrder

        if self.read_only:
            raise RuntimeError(
                "IBKR is in read-only mode. Set IBKR_READ_ONLY=false only after paper testing."
            )
        self._ensure_connected()
        contract = self._stock(symbol)
        order = MarketOrder(
            action, qty, account=self.account, tif="DAY",
            orderRef=f"{self._order_prefix}:{symbol}:{action}",
        )
        trade = self.ib.placeOrder(contract, order)
        deadline = time.monotonic() + self.order_timeout_seconds
        while not trade.isDone() and time.monotonic() < deadline:
            self.ib.waitOnUpdate(timeout=min(0.5, max(0.01, deadline - time.monotonic())))

        timed_out = not trade.isDone()
        if timed_out:
            self.log.warning(
                "order timed out; requesting cancellation symbol=%s order_id=%s",
                symbol, order.orderId,
            )
            self.ib.cancelOrder(order)
            cancel_deadline = time.monotonic() + 5
            while not trade.isDone() and time.monotonic() < cancel_deadline:
                self.ib.waitOnUpdate(timeout=0.25)

        filled_qty = float(trade.filled())
        avg_fill_price = float(trade.orderStatus.avgFillPrice or 0)
        recorded_fills = []
        if filled_qty > 0:
            self._wait_for_commissions(trade)
            recorded_fills = self._record_trade_fills(trade, probability)

        status = str(trade.orderStatus.status)
        result = {
            "id": str(order.permId or order.orderId),
            "status": status,
            "symbol": symbol,
            "requested_qty": float(qty),
            "filled_qty": filled_qty,
            "avg_fill_price": avg_fill_price,
            "commission": sum(fill["commission"] for fill in recorded_fills),
            "realized_pnl": sum(
                fill["realized_pnl"] for fill in recorded_fills
                if fill["realized_pnl"] is not None
            ),
        }
        if filled_qty <= 0:
            if timed_out:
                raise TimeoutError(
                    f"IBKR order {order.orderId} was not filled and cancellation was requested"
                )
            raise RuntimeError(f"IBKR rejected/cancelled order {order.orderId}: {status}")
        if filled_qty + 1e-9 < float(qty):
            remaining = float(qty) - filled_qty
            if action == "BUY":
                self._place(symbol, filled_qty, "SELL", 0)
                raise RuntimeError(
                    f"Unprotected BUY partial fill {filled_qty:g}/{float(qty):g} was flattened"
                )
            if _partial_attempt >= 2:
                raise RuntimeError(
                    f"SELL remained partially filled after retries; residual={remaining:g} {symbol}"
                )
            retry = self._place(
                symbol, remaining, action, probability,
                _partial_attempt=_partial_attempt + 1,
            )
            retry["requested_qty"] = float(qty)
            retry["filled_qty"] = filled_qty + float(retry["filled_qty"])
            retry["commission"] = result["commission"] + float(retry["commission"])
            retry["realized_pnl"] = result["realized_pnl"] + float(retry["realized_pnl"])
            return retry
        return result

    def _place_bracket(
        self, symbol: str, qty: float, price: float, probability: float,
        stop_loss_price: float, take_profit_price: float,
        trailing_stop_pct: float | None,
    ):
        if self.read_only:
            raise RuntimeError("IBKR is in read-only mode")
        self._ensure_connected()
        contract = self._stock(symbol)
        entry_price = round(price * (1 + self.entry_limit_offset_pct), 2)
        stop_price = round(stop_loss_price, 2)
        take_price = round(take_profit_price, 2)
        if not 0 < stop_price < entry_price < take_price:
            raise ValueError("Invalid bracket prices: stop < entry < take-profit is required")
        bracket = self.ib.bracketOrder(
            "BUY", qty, entry_price, take_price, stop_price,
            account=self.account, tif="GTC", outsideRth=True,
        )
        if trailing_stop_pct:
            from ib_async.util import UNSET_DOUBLE
            bracket[2].orderType = "TRAIL"
            bracket[2].auxPrice = UNSET_DOUBLE
            bracket[2].trailStopPrice = stop_price
            bracket[2].trailingPercent = trailing_stop_pct * 100
            # IBKR warning 2109 is emitted when outsideRth is sent for a US
            # stock trailing order. The attribute is ignored and ib_async can
            # leave the trade in ValidationError even though TWS processes it.
            bracket[2].outsideRth = False
        bracket[0].orderRef = f"{self._order_prefix}:{symbol}:ENTRY"
        bracket[1].orderRef = f"{self._order_prefix}:{symbol}:TAKE_PROFIT"
        bracket[2].orderRef = f"{self._order_prefix}:{symbol}:STOP_LOSS"
        trades = []
        for order in bracket:
            trades.append(self.ib.placeOrder(contract, order))
            self.ib.sleep(0.05)
        parent_trade = trades[0]
        deadline = time.monotonic() + self.order_timeout_seconds
        while not parent_trade.isDone() and time.monotonic() < deadline:
            self.ib.waitOnUpdate(timeout=min(0.5, max(0.01, deadline - time.monotonic())))
        filled_qty = float(parent_trade.filled())
        incomplete = filled_qty < float(qty)
        if incomplete:
            for trade in trades:
                if not trade.isDone():
                    self.ib.cancelOrder(trade.order)
            cancel_deadline = time.monotonic() + 10
            while any(not trade.isDone() for trade in trades) and time.monotonic() < cancel_deadline:
                self.ib.waitOnUpdate(timeout=0.25)
            if any(not trade.isDone() for trade in trades):
                raise RuntimeError("Bracket cancellation was not confirmed; inspect TWS immediately")
            if filled_qty > 0:
                self._wait_for_commissions(parent_trade)
                self._record_trade_fills(parent_trade, probability)
                self._place(symbol, filled_qty, "SELL", 0)
                raise RuntimeError(
                    f"Bracket entry partially filled {filled_qty:g}/{float(qty):g}; "
                    "partial position was flattened"
                )
            raise TimeoutError("Bracket entry was not filled and all legs were cancelled")
        self._wait_for_commissions(parent_trade)
        recorded_fills = self._record_trade_fills(parent_trade, probability)
        active_children = [trade for trade in trades[1:] if not trade.isDone()]
        if len(active_children) != 2:
            self.cancel_protective_orders(symbol)
            self._place(symbol, filled_qty, "SELL", 0)
            raise RuntimeError(
                "Bracket parent filled without two active protective orders; position was flattened"
            )
        actual_fill_price = float(parent_trade.orderStatus.avgFillPrice or 0)
        stop_pct = 1 - (stop_price / entry_price)
        take_pct = (take_price / entry_price) - 1
        try:
            protection = self.ensure_protective_orders(
                symbol, filled_qty, actual_fill_price,
                stop_pct, take_pct, trailing_stop_pct or 0,
            )
        except Exception:
            self.cancel_protective_orders(symbol)
            self._place(symbol, filled_qty, "SELL", 0)
            raise RuntimeError(
                "Filled bracket could not be reconciled to actual fill price; position was flattened"
            )
        return {
            "id": str(parent_trade.order.permId or parent_trade.order.orderId),
            "status": str(parent_trade.orderStatus.status),
            "symbol": symbol,
            "requested_qty": float(qty),
            "filled_qty": filled_qty,
            "avg_fill_price": actual_fill_price,
            "commission": sum(fill["commission"] for fill in recorded_fills),
            "realized_pnl": sum(
                fill["realized_pnl"] for fill in recorded_fills
                if fill["realized_pnl"] is not None
            ),
            "take_profit_order_id": trades[1].order.orderId,
            "stop_loss_order_id": trades[2].order.orderId,
            "stop_loss_price": protection.get("stop_loss_price", stop_price),
            "take_profit_price": protection.get("take_profit_price", take_price),
            "trailing_stop_pct": trailing_stop_pct or 0,
        }

    @staticmethod
    def _commission_values(fill) -> tuple[float | None, float | None]:
        report = getattr(fill, "commissionReport", None)
        execution = getattr(fill, "execution", None)
        report_exec_id = str(getattr(report, "execId", "") or "")
        execution_id = str(getattr(execution, "execId", "") or "")
        if report is None or not execution_id or report_exec_id != execution_id:
            return None, None

        def valid(value):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) and abs(number) < 1e50 else None

        return valid(getattr(report, "commission", None)), valid(
            getattr(report, "realizedPNL", None)
        )

    def _wait_for_commissions(self, trade, timeout: float = 3) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if trade.fills and all(
                self._commission_values(fill)[0] is not None for fill in trade.fills
            ):
                return
            self.ib.waitOnUpdate(timeout=min(0.25, max(0.01, deadline - time.monotonic())))

    def _record_trade_fills(self, trade, probability: float) -> list[dict]:
        recorded = []
        side = "buy" if trade.order.action.upper() == "BUY" else "sell"
        for fill in trade.fills:
            execution = fill.execution
            exec_id = str(execution.execId)
            commission, realized_pnl = self._commission_values(fill)
            was_new = self.store.record(
                fill.contract.symbol, side, float(execution.shares), float(execution.price),
                probability, "filled", exec_id,
                commission=commission or 0, realized_pnl=realized_pnl,
                executed_at=getattr(fill, "time", None),
                purpose=getattr(self, "trade_purpose", "strategy"),
            )
            self.store.update_execution_costs(exec_id, commission, realized_pnl)
            if was_new:
                recorded.append({
                    "symbol": fill.contract.symbol,
                    "side": side,
                    "qty": float(execution.shares),
                    "price": float(execution.price),
                    "exec_id": exec_id,
                    "commission": commission or 0.0,
                    "realized_pnl": realized_pnl,
                })
        return recorded

    def protective_orders(self, symbol: str):
        self._ensure_connected()
        contract = self._stock(symbol)
        return [
            trade for trade in self.ib.openTrades()
            if trade.contract.conId == contract.conId
            and trade.order.account == self.account
            and trade.order.action.upper() == "SELL"
            and (
                int(trade.order.parentId or 0) > 0
                or str(trade.order.orderRef).startswith(
                    f"{self._order_prefix}:{symbol}:"
                )
            )
            and not trade.isDone()
        ]

    def ensure_protective_orders(
        self, symbol: str, qty: float, avg_cost: float,
        stop_loss_pct: float, take_profit_pct: float, trailing_stop_pct: float,
    ) -> dict:
        from ib_async import LimitOrder, Order

        if self.read_only:
            raise RuntimeError("Cannot repair protective orders in read-only mode")
        if qty <= 0 or avg_cost <= 0:
            raise ValueError("Protective orders require a positive long position and average cost")
        existing = self.protective_orders(symbol)
        stop_price = round(avg_cost * (1 - stop_loss_pct), 2)
        take_price = round(avg_cost * (1 + take_profit_pct), 2)
        valid = [
            trade for trade in existing
            if abs(float(trade.order.totalQuantity) - float(qty)) <= 1e-9
        ]
        take_orders = [
            trade for trade in valid if str(trade.order.orderType).upper() == "LMT"
        ]
        trail_orders = [
            trade for trade in valid if str(trade.order.orderType).upper() == "TRAIL"
        ]
        if len(take_orders) == 1 and len(trail_orders) == 1:
            take_order = take_orders[0].order
            trail_order = trail_orders[0].order
            same_parent = (
                int(take_order.parentId or 0) > 0
                and int(take_order.parentId or 0) == int(trail_order.parentId or 0)
            )
            same_oca = (
                bool(str(take_order.ocaGroup or ""))
                and str(take_order.ocaGroup) == str(trail_order.ocaGroup)
                and int(take_order.ocaType or 0) == int(trail_order.ocaType or 0) == 1
            )
            prices_valid = (
                abs(float(take_order.lmtPrice) - take_price) <= 0.02
                and float(trail_order.trailStopPrice) >= stop_price - 0.02
                and abs(float(trail_order.trailingPercent) - trailing_stop_pct * 100) <= 1e-9
            )
            policies_valid = (
                str(take_order.tif).upper() == "GTC"
                and str(trail_order.tif).upper() == "GTC"
                and bool(take_order.outsideRth)
                and not bool(trail_order.outsideRth)
            )
            if (same_parent or same_oca) and prices_valid and policies_valid:
                return {"status": "protected", "repaired": False, "orders": valid}

        for trade in existing:
            self.ib.cancelOrder(trade.order)
        deadline = time.monotonic() + 10
        while any(not trade.isDone() for trade in existing) and time.monotonic() < deadline:
            self.ib.waitOnUpdate(timeout=0.25)
        if any(not trade.isDone() for trade in existing):
            raise RuntimeError(f"Could not cancel stale protective orders for {symbol}")

        contract = self._stock(symbol)
        group = f"TradingBot-{self.account}-{symbol}-{time.time_ns()}"
        stop = Order(
            action="SELL", orderType="TRAIL", totalQuantity=qty,
            trailStopPrice=stop_price, trailingPercent=trailing_stop_pct * 100,
            account=self.account, tif="GTC", outsideRth=False, transmit=True,
            orderRef=f"{self._order_prefix}:{symbol}:STOP_LOSS",
        )
        take = LimitOrder(
            "SELL", qty, take_price, account=self.account, tif="GTC",
            outsideRth=True, transmit=True,
            orderRef=f"{self._order_prefix}:{symbol}:TAKE_PROFIT",
        )
        self.ib.oneCancelsAll([stop, take], group, 1)
        trades = [self.ib.placeOrder(contract, stop), self.ib.placeOrder(contract, take)]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(
            str(trade.orderStatus.status) not in {"PreSubmitted", "Submitted"}
            for trade in trades
        ):
            self.ib.waitOnUpdate(timeout=0.25)
        if any(
            str(trade.orderStatus.status) not in {"PreSubmitted", "Submitted"}
            for trade in trades
        ):
            for trade in trades:
                if not trade.isDone():
                    self.ib.cancelOrder(trade.order)
            raise RuntimeError(f"Protective-order repair was not confirmed for {symbol}")
        return {
            "status": "protected", "repaired": True, "orders": trades,
            "stop_loss_price": stop_price, "take_profit_price": take_price,
            "trailing_stop_pct": trailing_stop_pct,
        }

    def account_positions(self) -> list[dict]:
        self._ensure_connected()
        return [
            {
                "symbol": item.contract.symbol,
                "qty": float(item.position),
                "avg_cost": float(item.avgCost),
            }
            for item in self.ib.positions(self.account)
            if abs(float(item.position)) > 1e-9
        ]

    def account_exposure(self) -> float:
        exposure = 0.0
        for item in self.account_positions():
            quote = self.current_quote(item["symbol"])
            market_price = quote["market"] or quote["bid"] or quote["last"]
            if market_price <= 0:
                raise RuntimeError(f"No mark-to-market price for {item['symbol']}")
            if self.market_data_type == 1:
                if quote["market_data_type"] != 1:
                    raise RuntimeError(
                        f"Non-live exposure quote for {item['symbol']}"
                    )
                if quote["age_seconds"] > self.max_market_data_age_seconds:
                    raise RuntimeError(
                        f"Stale exposure quote for {item['symbol']}: "
                        f"{quote['age_seconds']:.1f}s"
                    )
            exposure += abs(item["qty"] * market_price)
        return exposure

    def account_open_orders(self) -> list[dict]:
        self._ensure_connected()
        trades = self.ib.reqAllOpenOrders()
        return [
            {
                "symbol": trade.contract.symbol,
                "action": str(trade.order.action),
                "qty": float(trade.order.totalQuantity),
                "order_ref": str(trade.order.orderRef or ""),
                "order_id": int(trade.order.orderId),
                "client_id": int(trade.order.clientId),
                "status": str(trade.orderStatus.status),
            }
            for trade in trades
            if trade.order.account == self.account and not trade.isDone()
        ]

    def available_funds(self) -> float:
        self._ensure_connected()
        values = [
            item for item in self.ib.accountSummary(self.account)
            if item.tag == "AvailableFunds" and item.currency in {self.currency, "BASE"}
        ]
        if not values:
            raise RuntimeError("IBKR did not return AvailableFunds for the trading account")
        return min(float(item.value) for item in values)

    def net_liquidation(self) -> float:
        self._ensure_connected()
        values = [
            item for item in self.ib.accountSummary(self.account)
            if item.tag == "NetLiquidation" and item.currency in {self.currency, "BASE"}
        ]
        if not values:
            raise RuntimeError("IBKR did not return NetLiquidation for the trading account")
        value = min(float(item.value) for item in values)
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError(f"Invalid IBKR NetLiquidation value: {value}")
        return value

    def account_daily_pnl(self) -> float | None:
        self._ensure_connected()
        if self._pnl_subscription is None:
            self._pnl_subscription = self.ib.reqPnL(self.account)
        pnl = self._pnl_subscription
        if not math.isfinite(float(pnl.dailyPnL)):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not math.isfinite(float(pnl.dailyPnL)):
                self.ib.waitOnUpdate(timeout=0.25)
        value = float(pnl.dailyPnL)
        if not math.isfinite(value):
            summary = {
                item.tag: float(item.value)
                for item in self.ib.accountValues(self.account)
                if item.currency in {self.currency, "BASE"}
                and item.tag in {"RealizedPnL", "UnrealizedPnL"}
            }
            realized = summary.get("RealizedPnL")
            unrealized = summary.get("UnrealizedPnL")
            if (
                realized is None or unrealized is None
                or not math.isfinite(realized) or not math.isfinite(unrealized)
            ):
                raise RuntimeError(
                    "IBKR did not return finite DailyPnL or Realized/Unrealized PnL"
                )
            value = realized + unrealized
        return value

    def market_is_open(self, symbol: str) -> bool:
        self._ensure_connected()
        contract = self._stock(symbol)
        details = self.ib.reqContractDetails(contract)
        if not details:
            raise RuntimeError(f"IBKR returned no contract schedule for {symbol}")
        detail = details[0]
        if not detail.liquidHours or not detail.timeZoneId:
            raise RuntimeError(f"IBKR returned an incomplete trading schedule for {symbol}")
        return _within_liquid_hours(
            detail.liquidHours, detail.timeZoneId, datetime.now(timezone.utc)
        )

    def cancel_protective_orders(self, symbol: str) -> list[str]:
        trades = self.protective_orders(symbol)
        for trade in trades:
            self.ib.cancelOrder(trade.order)
        deadline = time.monotonic() + 15
        while any(not trade.isDone() for trade in trades) and time.monotonic() < deadline:
            self.ib.waitOnUpdate(timeout=0.25)
        if any(not trade.isDone() for trade in trades):
            raise RuntimeError("Protective-order cancellation was not confirmed")
        return [str(trade.orderStatus.status) for trade in trades]

    def sync_executions(self) -> list[dict]:
        from ib_async import ExecutionFilter

        self._ensure_connected()
        if not self._executions_loaded:
            fills = self.ib.reqExecutions(ExecutionFilter(acctCode=self.account))
            self._executions_loaded = True
        else:
            fills = [
                fill for fill in self.ib.fills()
                if fill.execution.acctNumber == self.account
            ]
        commission_deadline = time.monotonic() + 3
        while fills and time.monotonic() < commission_deadline and any(
            self._commission_values(fill)[0] is None for fill in fills
        ):
            self.ib.waitOnUpdate(
                timeout=min(0.25, max(0.01, commission_deadline - time.monotonic()))
            )
        fills = [
            fill for fill in self.ib.fills()
            if fill.execution.acctNumber == self.account
        ]
        recorded = []
        for fill in fills:
            execution = fill.execution
            side = "buy" if str(execution.side).upper() in {"BOT", "BUY"} else "sell"
            exec_id = str(execution.execId)
            commission, realized_pnl = self._commission_values(fill)
            was_new = self.store.record(
                fill.contract.symbol, side, float(execution.shares), float(execution.price),
                0, "filled", exec_id,
                commission=commission or 0, realized_pnl=realized_pnl,
                executed_at=getattr(fill, "time", None),
                purpose=(
                    "connectivity_test"
                    if str(getattr(execution, "orderRef", "")).startswith("TradingBotTest:")
                    else getattr(self, "trade_purpose", "strategy")
                ),
            )
            self.store.update_execution_costs(exec_id, commission, realized_pnl)
            if was_new:
                recorded.append({
                    "symbol": fill.contract.symbol,
                    "side": side,
                    "qty": float(execution.shares),
                    "price": float(execution.price),
                    "exec_id": exec_id,
                    "commission": commission or 0.0,
                    "realized_pnl": realized_pnl,
                })
        return recorded

    def what_if_buy(self, symbol: str, limit_price: float, qty: float = 1):
        """Ask TWS to validate a hypothetical order without transmitting it."""
        import asyncio
        from ib_async import LimitOrder

        if self.read_only:
            raise RuntimeError("What-If validation requires IBKR_READ_ONLY=false")
        self._ensure_connected()
        contract = self._stock(symbol)
        if limit_price <= 0:
            raise ValueError("What-If limit price must be positive")
        order = LimitOrder("BUY", qty, limit_price, account=self.account, tif="DAY")
        try:
            return self.ib.run(asyncio.wait_for(
                self.ib.whatIfOrderAsync(contract, order),
                timeout=min(20, self.order_timeout_seconds),
            ))
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                "IBKR What-If order timed out; verify that TWS API Read-Only is disabled"
            ) from exc

    def close(self) -> None:
        if self.ib.isConnected():
            if self._pnl_subscription is not None:
                self.ib.cancelPnL(self.account)
                self._pnl_subscription = None
            self.ib.disconnect()

    def wait(self, seconds: float) -> None:
        self._ensure_connected()
        self.ib.sleep(seconds)
