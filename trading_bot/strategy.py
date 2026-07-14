from dataclasses import dataclass
import math
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Signal:
    side: str
    probability: float
    samples: int
    price: float
    reason: str


def profit_target_pct(
    frame: pd.DataFrame,
    minimum: float = 0.03,
    maximum: float = 0.10,
    atr_multiplier: float = 3.0,
    period: int = 14,
) -> float:
    """Choose a volatility-based profit target, clamped to configured bounds."""
    if not 0 < minimum <= maximum:
        raise ValueError("profit target requires 0 < minimum <= maximum")
    required = {"High", "Low", "Close"}
    if frame.empty or not required.issubset(frame.columns):
        return minimum
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    close = frame["Close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(period, min_periods=period).mean().iloc[-1]
    latest = close.iloc[-1]
    if not np.isfinite(atr) or not np.isfinite(latest) or latest <= 0:
        return minimum
    return float(np.clip((atr / latest) * atr_multiplier, minimum, maximum))


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, np.nan)))


def _wilson_lower(wins: int, total: int, z: float = 1.645) -> float:
    if total == 0:
        return 0.0
    p = wins / total
    den = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / den)


def analyze(frame: pd.DataFrame, min_samples: int, horizon: int = 5) -> Signal:
    if frame.empty or "Close" not in frame:
        return Signal("hold", 0, 0, 0, "no market data")
    close = frame["Close"].astype(float).dropna()
    if close.empty:
        return Signal("hold", 0, 0, 0, "no market data")
    if len(close) < 100:
        return Signal("hold", 0, 0, float(close.iloc[-1]), "insufficient history")
    df = pd.DataFrame(index=close.index)
    df["close"] = close
    df["fast"] = close.ewm(span=20, adjust=False).mean()
    df["slow"] = close.ewm(span=50, adjust=False).mean()
    df["rsi"] = _rsi(close)
    df["trend"] = (df.fast > df.slow).astype(int)
    df["bucket"] = pd.cut(df.rsi, [0, 35, 50, 65, 100], labels=False, include_lowest=True)
    df["future"] = close.shift(-horizon) / close - 1
    latest = df.iloc[-1]
    candidates = df[(df.trend == latest.trend) & (df.bucket == latest.bucket)].iloc[:-horizon].dropna()
    side = "buy" if latest.trend == 1 and 45 <= latest.rsi <= 65 else "hold"
    if side == "hold":
        return Signal(side, 0, len(candidates), float(latest.close), "trend/RSI filter not met")
    wins = int((candidates.future > 0).sum())
    probability = _wilson_lower(wins, len(candidates))
    reason = f"EMA20>EMA50, RSI={latest.rsi:.1f}, historical wins={wins}/{len(candidates)} (90% Wilson lower bound)"
    if len(candidates) < min_samples:
        return Signal("hold", probability, len(candidates), float(latest.close), "insufficient comparable samples")
    return Signal(side, probability, len(candidates), float(latest.close), reason)
