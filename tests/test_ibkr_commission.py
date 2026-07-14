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
