from dataclasses import dataclass
import math
import numpy as np
import pandas as pd

from .costs import estimate_profitability


@dataclass(frozen=True)
class Signal:
    side: str
    probability: float
    samples: int
    price: float
    reason: str
    expected_gross_profit: float = 0.0
    expected_trading_cost: float = 0.0
    estimated_tax: float = 0.0
    expected_net_profit: float = 0.0
    required_net_profit: float = 0.0


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


def _tp_before_sl(
    future_bars: pd.DataFrame, entry: float,
    stop_loss_pct: float, take_profit_pct: float,
) -> bool:
    stop_price = entry * (1 - stop_loss_pct)
    take_price = entry * (1 + take_profit_pct)
    for _, bar in future_bars.iterrows():
        if float(bar["low"]) <= stop_price:
            return False
        if float(bar["high"]) >= take_price:
            return True
    return False


def _latest_atr_pct(frame: pd.DataFrame, period: int = 14) -> float:
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    close = frame["Close"].astype(float)
    previous = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous).abs(), (low - previous).abs()], axis=1,
    ).max(axis=1)
    atr = true_range.rolling(period, min_periods=period).mean().iloc[-1]
    latest = close.iloc[-1]
    if not np.isfinite(atr) or not np.isfinite(latest) or latest <= 0:
        return float("nan")
    return float(atr / latest)


def _estimated_net_expectancy(
    probability: float, notional: float, stop_loss_pct: float,
    take_profit_pct: float, round_trip_fee: float,
) -> float:
    return estimate_profitability(
        probability, notional, stop_loss_pct, take_profit_pct, round_trip_fee
    ).net_profit


def analyze(
    frame: pd.DataFrame,
    min_samples: int,
    horizon: int = 20,
    stop_loss_pct: float = 0.03,
    take_profit_pct: float = 0.08,
    portfolio_value: float = 300.0,
    max_order_notional: float = 30.0,
    estimated_round_trip_commission: float = 2.01,
    estimated_exchange_fee_rate: float = 0.0,
    estimated_fx_cost_rate: float = 0.0,
    tax_rate: float = 0.0,
    min_net_profit_cost_multiple: float = 3.0,
    min_volume_ratio: float = 1.0,
    min_atr_pct: float = 0.005,
    max_atr_pct: float = 0.04,
    enforce_profitability_gate: bool = True,
) -> Signal:
    required = {"High", "Low", "Close", "Volume"}
    if frame.empty or not required.issubset(frame.columns):
        return Signal("hold", 0, 0, 0, "no market data")
    close = frame["Close"].astype(float).dropna()
    if close.empty:
        return Signal("hold", 0, 0, 0, "no market data")
    if len(close) < 100:
        return Signal("hold", 0, 0, float(close.iloc[-1]), "insufficient history")
    volume = frame["Volume"].astype(float).reindex(close.index)
    average_volume = volume.rolling(20, min_periods=20).mean().iloc[-1]
    latest_volume = volume.iloc[-1]
    if (
        not np.isfinite(average_volume) or average_volume <= 0
        or not np.isfinite(latest_volume)
        or latest_volume < average_volume * min_volume_ratio
    ):
        return Signal(
            "hold", 0, 0, float(close.iloc[-1]),
            f"volume filter not met ({latest_volume:.0f}/{average_volume:.0f})",
        )
    atr_pct = _latest_atr_pct(frame.reindex(close.index))
    if not np.isfinite(atr_pct) or not min_atr_pct <= atr_pct <= max_atr_pct:
        return Signal(
            "hold", 0, 0, float(close.iloc[-1]),
            f"volatility filter not met (ATR={atr_pct:.2%})",
        )
    df = pd.DataFrame(index=close.index)
    df["close"] = close
    df["high"] = frame["High"].astype(float).reindex(close.index)
    df["low"] = frame["Low"].astype(float).reindex(close.index)
    df["fast"] = close.ewm(span=20, adjust=False).mean()
    df["slow"] = close.ewm(span=50, adjust=False).mean()
    df["rsi"] = _rsi(close)
    df["trend"] = (df.fast > df.slow).astype(int)
    df["bucket"] = pd.cut(df.rsi, [0, 35, 50, 65, 100], labels=False, include_lowest=True)
    latest = df.iloc[-1]
    candidates = df[(df.trend == latest.trend) & (df.bucket == latest.bucket)].iloc[:-horizon].dropna()
    side = "buy" if latest.trend == 1 and 45 <= latest.rsi <= 65 else "hold"
    if side == "hold":
        return Signal(side, 0, len(candidates), float(latest.close), "trend/RSI filter not met")
    wins = 0
    for index in candidates.index:
        location = df.index.get_loc(index)
        entry = float(df.iloc[location].close)
        future_bars = df.iloc[location + 1:location + horizon + 1]
        wins += int(_tp_before_sl(
            future_bars, entry, stop_loss_pct, take_profit_pct,
        ))
    probability = _wilson_lower(wins, len(candidates))
    if probability < 0.70:
        confidence_fraction = 0.0
    elif probability < 0.80:
        confidence_fraction = 0.05
    elif probability < 0.90:
        confidence_fraction = 0.10
    elif probability <= 0.95:
        confidence_fraction = 0.15
    else:
        confidence_fraction = 0.20
    estimated_notional = min(
        portfolio_value * confidence_fraction,
        max_order_notional,
    )
    estimate = estimate_profitability(
        probability, estimated_notional, stop_loss_pct,
        take_profit_pct, estimated_round_trip_commission,
        estimated_exchange_fee_rate, estimated_fx_cost_rate,
        tax_rate, min_net_profit_cost_multiple,
    )
    reason = (
        f"EMA20>EMA50, RSI={latest.rsi:.1f}, volume ratio={latest_volume / average_volume:.2f}, "
        f"ATR={atr_pct:.2%}, TP-before-SL wins={wins}/{len(candidates)} "
        f"(90% Wilson lower bound), expected gross={estimate.gross_profit:.2f}, "
        f"cost={estimate.trading_cost:.2f}, tax={estimate.estimated_tax:.2f}, "
        f"expected net={estimate.net_profit:.2f}, required>{estimate.required_net_profit:.2f}"
    )
    if len(candidates) < min_samples:
        return Signal("hold", probability, len(candidates), float(latest.close), "insufficient comparable samples")
    metrics = (
        estimate.gross_profit, estimate.trading_cost, estimate.estimated_tax,
        estimate.net_profit, estimate.required_net_profit,
    )
    if estimated_notional <= 0 or (enforce_profitability_gate and not estimate.should_trade):
        return Signal("hold", probability, len(candidates), float(latest.close), reason, *metrics)
    return Signal(side, probability, len(candidates), float(latest.close), reason, *metrics)
