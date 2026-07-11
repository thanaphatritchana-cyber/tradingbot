import pandas as pd
import numpy as np
from trading_bot.strategy import analyze


def test_short_history_holds():
    frame = pd.DataFrame({"Close": np.arange(20) + 100.0})
    assert analyze(frame, 10).side == "hold"


def test_probability_is_bounded():
    rng = np.random.default_rng(7)
    close = 100 * np.cumprod(1 + rng.normal(.001, .005, 400))
    result = analyze(pd.DataFrame({"Close": close}), 10)
    assert 0 <= result.probability <= 1

