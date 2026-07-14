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
        trade = SimpleNamespace(
            contract=contract,
            order=order,
            orderStatus=SimpleNamespace(status="Submitted"),
            isDone=lambda: False,
        )
        self.placed.append(trade)
        return trade

    def waitOnUpdate(self, timeout):
        return True


def test_missing_protection_is_repaired_with_matching_oca_orders():
    broker = object.__new__(InteractiveBrokersBroker)
    broker.read_only = False
    broker.account = "DU123"
    broker.ib = FakeIB()
    broker.protective_orders = lambda symbol: []
    broker._stock = lambda symbol: SimpleNamespace(symbol=symbol, conId=1)

    result = broker.ensure_protective_orders("AAPL", 2, 100, 0.02, 0.05)

    assert result["repaired"] is True
    assert result["stop_loss_price"] == 98
    assert result["take_profit_price"] == 105
    assert len(broker.ib.placed) == 2
    stop, take = [trade.order for trade in broker.ib.placed]
    assert stop.totalQuantity == take.totalQuantity == 2
    assert stop.ocaGroup == take.ocaGroup
    assert stop.ocaType == take.ocaType == 1
    assert stop.orderRef == "TradingBot:AAPL:STOP_LOSS"
    assert take.orderRef == "TradingBot:AAPL:TAKE_PROFIT"


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
