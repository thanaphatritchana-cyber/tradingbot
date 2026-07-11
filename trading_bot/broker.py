from abc import ABC, abstractmethod
from .storage import Store


class Broker(ABC):
    @abstractmethod
    def buy(self, symbol: str, qty: float, price: float, probability: float): ...
    @abstractmethod
    def sell(self, symbol: str, qty: float, price: float, probability: float = 0): ...


class LocalPaperBroker(Broker):
    def __init__(self, store: Store): self.store = store
    def buy(self, symbol, qty, price, probability):
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

    def buy(self, symbol, qty, price, probability):
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
