from types import SimpleNamespace
from datetime import datetime, timezone

from trading_bot.broker import InteractiveBrokersBroker, _within_liquid_hours


class FakeIB:
    def __init__(self):
        self.placed = []

    @staticmethod
    def oneCancelsAll(orders, group, oca_type):
        for order in orders:
            order.ocaGroup = group
            order.ocaType = oca_type
        return orders

    def placeOrder(self, contract, order):
        state = {"done": False}
        trade = SimpleNamespace(
            contract=contract,
            order=order,
            orderStatus=SimpleNamespace(status="Submitted"),
            isDone=lambda: state["done"],
            _state=state,
        )
        self.placed.append(trade)
        return trade

    def cancelOrder(self, order):
        for trade in self.placed:
            if trade.order is order:
                trade._state["done"] = True
                trade.orderStatus.status = "Cancelled"

    def waitOnUpdate(self, timeout):
        return True


def test_missing_protection_is_repaired_with_matching_oca_orders():
    broker = object.__new__(InteractiveBrokersBroker)
    broker.read_only = False
    broker.account = "DU123"
    broker.ib = FakeIB()
    broker.protective_orders = lambda symbol: []
    broker._stock = lambda symbol: SimpleNamespace(symbol=symbol, conId=1)

    result = broker.ensure_protective_orders("AAPL", 2, 100, 0.03, 0.08, 0.02)

    assert result["repaired"] is True
    assert result["stop_loss_price"] == 97
    assert result["take_profit_price"] == 108
    assert len(broker.ib.placed) == 2
    stop, take = [trade.order for trade in broker.ib.placed]
    assert stop.totalQuantity == take.totalQuantity == 2
    assert stop.ocaGroup == take.ocaGroup
    assert stop.ocaType == take.ocaType == 1
    assert stop.orderRef == "TradingBot:AAPL:STOP_LOSS"
    assert take.orderRef == "TradingBot:AAPL:TAKE_PROFIT"
    assert stop.orderType == "TRAIL"
    assert stop.trailStopPrice == 97
    assert stop.trailingPercent == 2
    assert stop.transmit is True
    assert take.transmit is True
    assert stop.outsideRth is False
    assert take.outsideRth is True


def test_ibkr_liquid_hours_include_holidays_and_regular_session():
    hours = "20260703:CLOSED;20260706:0930-20260706:1600"

    assert not _within_liquid_hours(
        hours, "US/Eastern", datetime(2026, 7, 3, 15, tzinfo=timezone.utc)
    )
    assert _within_liquid_hours(
        hours, "US/Eastern", datetime(2026, 7, 6, 15, tzinfo=timezone.utc)
    )
    assert not _within_liquid_hours(
        hours, "US/Eastern", datetime(2026, 7, 6, 22, tzinfo=timezone.utc)
    )


def test_existing_protection_requires_prices_trailing_policy_and_oca():
    from ib_async import LimitOrder, Order

    broker = object.__new__(InteractiveBrokersBroker)
    broker.read_only = False
    broker.account = "DU123"
    broker.ib = FakeIB()
    broker._stock = lambda symbol: SimpleNamespace(symbol=symbol, conId=1)
    stop = Order(
        action="SELL", orderType="TRAIL", totalQuantity=2,
        trailStopPrice=97, trailingPercent=2, tif="GTC", outsideRth=False,
        ocaGroup="safe", ocaType=1,
    )
    take = LimitOrder(
        "SELL", 2, 108, tif="GTC", outsideRth=True,
        ocaGroup="safe", ocaType=1,
    )
    existing = [broker.ib.placeOrder(None, stop), broker.ib.placeOrder(None, take)]
    broker.protective_orders = lambda symbol: existing

    result = broker.ensure_protective_orders("AAPL", 2, 100, 0.03, 0.08, 0.02)

    assert result["repaired"] is False
    assert len(broker.ib.placed) == 2
