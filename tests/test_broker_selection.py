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
    cfg = SimpleNamespace(broker="ibkr", ibkr_paper=True, ibkr_port=7496)

    with pytest.raises(ValueError, match="IBKR_PAPER"):
        main.create_broker(cfg, object())


def test_order_value_supports_dict_and_broker_model():
    assert main._order_value({"filled_qty": 2}, "filled_qty", 1) == 2
    assert main._order_value(SimpleNamespace(filled_qty="3"), "filled_qty", 1) == "3"
    assert main._order_value(SimpleNamespace(filled_qty=None), "filled_qty", 1) == 1


def test_order_quantity_respects_absolute_notional_cap():
    cfg = SimpleNamespace(
        starting_cash=100_000,
        risk_per_trade=0.005,
        max_position_pct=0.10,
        max_order_notional=1_000,
        stop_loss_pct=0.02,
    )

    assert main._calculate_order_qty(cfg, 100) == 10
    assert main._calculate_order_qty(cfg, 0) == 0


def test_risk_limits_block_new_orders():
    cfg = SimpleNamespace(
        max_daily_loss=50,
        max_orders_per_day=5,
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
