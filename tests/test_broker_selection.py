from types import SimpleNamespace

import pytest

from trading_bot import main


def test_unknown_broker_does_not_silently_use_local():
    cfg = SimpleNamespace(broker="unknown")

    with pytest.raises(ValueError, match="BROKER must be one of"):
        main.create_broker(cfg, object())


def test_local_broker_selection():
    cfg = SimpleNamespace(broker="local")
    store = object()

    result = main.create_broker(cfg, store)

    assert result.store is store


def test_ibkr_paper_mode_rejects_standard_live_port():
    cfg = SimpleNamespace(
        broker="ibkr", ibkr_host="127.0.0.1",
        ibkr_paper=True, ibkr_port=7496,
    )

    with pytest.raises(ValueError, match="IBKR_PAPER"):
        main.create_broker(cfg, object())


def test_order_value_supports_dict_and_broker_model():
    assert main._order_value({"filled_qty": 2}, "filled_qty", 1) == 2
    assert main._order_value(SimpleNamespace(filled_qty="3"), "filled_qty", 1) == "3"
    assert main._order_value(SimpleNamespace(filled_qty=None), "filled_qty", 1) == 1


def test_order_quantity_uses_confidence_tiers_and_absolute_cap():
    cfg = SimpleNamespace(
        starting_cash=300,
        max_order_notional=30,
    )

    assert main._calculate_order_qty(cfg, 10, 0.70) == 1
    assert main._calculate_order_qty(cfg, 10, 0.80) == 3
    assert main._calculate_order_qty(cfg, 10, 0.90) == 3
    assert main._calculate_order_qty(cfg, 10, 0.96) == 3
    assert main._calculate_order_qty(cfg, 100, 0.99) == 0
    assert main._calculate_order_qty(cfg, 0, 0.99) == 0
    assert main._calculate_order_qty(cfg, 30.06, 0.99) == 0


def test_growing_portfolio_never_exceeds_fifty_dollar_order_cap():
    cfg = SimpleNamespace(starting_cash=1_000, max_order_notional=50)

    assert main._confidence_order_budget(cfg, 0.96, portfolio_value=10_000) == 50
    assert main._calculate_order_qty(cfg, 10, 0.96, portfolio_value=10_000) == 5


def test_what_if_commission_uses_round_trip_and_safety_buffer():
    state = SimpleNamespace(commission=.20)

    assert main._what_if_round_trip_commission(state, .10) == pytest.approx(.44)


def test_what_if_commission_rejects_invalid_values():
    with pytest.raises(RuntimeError, match="invalid commission"):
        main._what_if_round_trip_commission(SimpleNamespace(commission=float("nan")), .10)
    sentinel = SimpleNamespace(
        commission=1.797693e308, minCommission=.20, maxCommission=.22,
    )
    assert main._what_if_round_trip_commission(sentinel, .10) == pytest.approx(.484)
    with pytest.raises(RuntimeError, match="invalid commission"):
        main._what_if_round_trip_commission(
            SimpleNamespace(
                commission=1.797693e308,
                minCommission=1.797693e308,
                maxCommission=1.797693e308,
            ),
            .10,
        )


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.6999, 0), (0.70, 0.05), (0.7999, 0.05), (0.80, 0.10),
        (0.8999, 0.10), (0.90, 0.15), (0.95, 0.15), (0.9501, 0.20),
    ],
)
def test_confidence_allocation_boundaries(probability, expected):
    assert main._confidence_allocation_pct(probability) == expected


def test_risk_limits_block_new_orders():
    cfg = SimpleNamespace(
        max_daily_loss=50,
        max_daily_loss_pct=0.02,
        starting_cash=100_000,
        max_orders_per_day=5,
        max_concurrent_positions=5,
        max_consecutive_losses=3,
        max_total_exposure=2000,
    )
    safe = SimpleNamespace(
        daily_profit=0,
        daily_buys=0,
        daily_consecutive_losses=0,
    )

    assert main._risk_block_reason(cfg, safe, 500, 1000) is None
    assert "exposure" in main._risk_block_reason(cfg, safe, 1500, 1000)
    assert "daily net loss" in main._risk_block_reason(
        cfg, SimpleNamespace(daily_profit=-50, daily_buys=0, daily_consecutive_losses=0), 0, 1
    )
    assert "daily order" in main._risk_block_reason(
        cfg, SimpleNamespace(daily_profit=0, daily_buys=5, daily_consecutive_losses=0), 0, 1
    )
    assert "consecutive" in main._risk_block_reason(
        cfg, SimpleNamespace(daily_profit=0, daily_buys=0, daily_consecutive_losses=3), 0, 1
    )
    assert "concurrent position" in main._risk_block_reason(cfg, safe, 0, 1, open_positions=5)
    assert "daily net loss" in main._risk_block_reason(
        cfg, safe, 0, 1, broker_daily_pnl=-50
    )
    assert main._daily_loss_limit(cfg, portfolio_value=1_000) == 20
