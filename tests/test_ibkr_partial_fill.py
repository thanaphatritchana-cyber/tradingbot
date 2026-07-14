from types import SimpleNamespace

from trading_bot.broker import InteractiveBrokersBroker


class FakeStore:
    def record(self, *args, **kwargs):
        return True

    def update_execution_costs(self, *args, **kwargs):
        return None


class FakeTrade:
    def __init__(self, order, contract, qty, exec_id):
        order.orderId = int(exec_id.split("-")[-1])
        self.order = order
        self.orderStatus = SimpleNamespace(status="Filled", avgFillPrice=100)
        execution = SimpleNamespace(execId=exec_id, shares=qty, price=100)
        report = SimpleNamespace(execId=exec_id, commission=1, realizedPNL=1)
        self.fills = [
            SimpleNamespace(
                execution=execution,
                commissionReport=report,
                contract=contract,
            )
        ]
        self._qty = qty

    def isDone(self):
        return True

    def filled(self):
        return self._qty


class FakePartialIB:
    def __init__(self):
        self.calls = 0

    def placeOrder(self, contract, order):
        self.calls += 1
        return FakeTrade(order, contract, 0.5, f"exec-{self.calls}")


def test_partial_sell_is_retried_until_requested_quantity_is_filled():
    broker = object.__new__(InteractiveBrokersBroker)
    broker.read_only = False
    broker.account = "DU123"
    broker.order_timeout_seconds = 1
    broker.ib = FakePartialIB()
    broker.store = FakeStore()
    broker._ensure_connected = lambda: None
    broker._stock = lambda symbol: SimpleNamespace(symbol=symbol)

    result = broker._place("AAPL", 1, "SELL", 0)

    assert result["filled_qty"] == 1
    assert result["commission"] == 2
    assert broker.ib.calls == 2
