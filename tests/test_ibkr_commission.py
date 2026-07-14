from types import SimpleNamespace

from trading_bot.broker import InteractiveBrokersBroker


def test_commission_ignores_ibkr_placeholder_report():
    fill = SimpleNamespace(
        execution=SimpleNamespace(execId="execution-1"),
        commissionReport=SimpleNamespace(
            execId="", commission=0.0, realizedPNL=0.0,
        ),
    )

    assert InteractiveBrokersBroker._commission_values(fill) == (None, None)


def test_commission_accepts_matching_execution_report():
    fill = SimpleNamespace(
        execution=SimpleNamespace(execId="execution-1"),
        commissionReport=SimpleNamespace(
            execId="execution-1", commission=1.006809, realizedPNL=-2.056812,
        ),
    )

    assert InteractiveBrokersBroker._commission_values(fill) == (1.006809, -2.056812)


def test_connectivity_fills_are_recorded_separately_from_strategy_track_record():
    calls = []

    class Store:
        def record(self, *args, **kwargs):
            calls.append(kwargs)
            return True

        def update_execution_costs(self, *args, **kwargs):
            pass

    broker = object.__new__(InteractiveBrokersBroker)
    broker.store = Store()
    broker.trade_purpose = "connectivity_test"
    fill = SimpleNamespace(
        contract=SimpleNamespace(symbol="T"),
        execution=SimpleNamespace(execId="test-1", shares=1, price=20),
        commissionReport=SimpleNamespace(
            execId="test-1", commission=.20, realizedPNL=0,
        ),
        time=None,
    )
    trade = SimpleNamespace(
        order=SimpleNamespace(action="BUY"), fills=[fill],
    )

    broker._record_trade_fills(trade, 1.0)

    assert calls[0]["purpose"] == "connectivity_test"
