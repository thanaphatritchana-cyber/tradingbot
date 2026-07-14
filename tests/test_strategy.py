import pandas as pd
import numpy as np
import pytest
from trading_bot.strategy import (
    _estimated_net_expectancy,
    _tp_before_sl,
    analyze,
    profit_target_pct,
)
from trading_bot.costs import estimate_profitability


def test_short_history_holds():
    frame = pd.DataFrame({"Close": np.arange(20) + 100.0})
    assert analyze(frame, 10).side == "hold"


def test_probability_is_bounded():
    rng = np.random.default_rng(7)
    close = 100 * np.cumprod(1 + rng.normal(.001, .005, 400))
    result = analyze(pd.DataFrame({"Close": close}), 10)
    assert 0 <= result.probability <= 1


def test_profit_target_is_clamped_to_three_and_ten_percent():
    low_volatility = pd.DataFrame({
        "High": np.full(20, 100.1),
        "Low": np.full(20, 99.9),
        "Close": np.full(20, 100.0),
    })
    high_volatility = pd.DataFrame({
        "High": np.full(20, 120.0),
        "Low": np.full(20, 80.0),
        "Close": np.full(20, 100.0),
    })

    assert profit_target_pct(low_volatility) == 0.03
    assert profit_target_pct(high_volatility) == 0.10


def test_profit_target_falls_back_to_minimum_without_ohlc():
    assert profit_target_pct(pd.DataFrame({"Close": [100.0]})) == 0.03


def test_tp_before_sl_uses_intrabar_stop_first_conservatively():
    both = pd.DataFrame({"high": [109.0], "low": [96.0]})
    take_then = pd.DataFrame({"high": [108.1], "low": [99.0]})

    assert not _tp_before_sl(both, 100, 0.03, 0.08)
    assert _tp_before_sl(take_then, 100, 0.03, 0.08)


def test_estimated_expectancy_includes_round_trip_fee():
    without_fee = _estimated_net_expectancy(0.70, 30, 0.03, 0.08, 0)
    with_fee = _estimated_net_expectancy(0.70, 30, 0.03, 0.08, 1)

    assert with_fee == pytest.approx(without_fee - 1)


def test_profitability_gate_requires_net_strictly_above_three_times_cost():
    estimate = estimate_profitability(
        probability=1, notional=100, stop_loss_pct=.03, take_profit_pct=.07,
        round_trip_commission=.50, exchange_fee_rate=.002,
        minimum_cost_multiple=3,
    )

    assert estimate.trading_cost == pytest.approx(.70)
    assert estimate.net_profit == pytest.approx(6.30)
    assert estimate.required_net_profit == pytest.approx(2.10)
    assert estimate.should_trade


def test_profitability_estimates_tax_only_on_positive_win():
    estimate = estimate_profitability(
        probability=1, notional=100, stop_loss_pct=.03, take_profit_pct=.05,
        round_trip_commission=.50, tax_rate=.20, minimum_cost_multiple=0,
    )

    assert estimate.estimated_tax == pytest.approx(.90)
    assert estimate.net_profit == pytest.approx(3.60)


def test_entry_requires_supporting_volume():
    frame = pd.DataFrame({
        "High": np.full(120, 101.0),
        "Low": np.full(120, 99.0),
        "Close": np.full(120, 100.0),
        "Volume": np.r_[np.full(119, 1_000.0), 1.0],
    })

    result = analyze(frame, 30)

    assert result.side == "hold"
    assert "volume filter" in result.reason


def test_entry_rejects_volatility_below_configured_range():
    frame = pd.DataFrame({
        "High": np.full(120, 100.01),
        "Low": np.full(120, 99.99),
        "Close": np.full(120, 100.0),
        "Volume": np.full(120, 1_000.0),
    })

    result = analyze(frame, 30, min_atr_pct=.005, max_atr_pct=.04)

    assert result.side == "hold"
    assert "volatility filter" in result.reason


def test_current_profile_uses_ten_percent_take_profit():
    from trading_bot.config import Settings

    assert Settings(_env_file=None).take_profit_pct == .10
