import pandas as pd
import numpy as np
from trading_bot.strategy import analyze, profit_target_pct


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
