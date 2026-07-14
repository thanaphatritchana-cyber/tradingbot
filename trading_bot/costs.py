from dataclasses import dataclass


@dataclass(frozen=True)
class ProfitabilityEstimate:
    gross_profit: float
    commission: float
    exchange_fee: float
    fx_cost: float
    estimated_tax: float
    trading_cost: float
    net_profit: float
    required_net_profit: float

    @property
    def should_trade(self) -> bool:
        return self.net_profit > self.required_net_profit


def estimate_profitability(
    probability: float,
    notional: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    round_trip_commission: float,
    exchange_fee_rate: float = 0.0,
    fx_cost_rate: float = 0.0,
    tax_rate: float = 0.0,
    minimum_cost_multiple: float = 3.0,
) -> ProfitabilityEstimate:
    """Estimate probability-weighted, after-cost and after-tax profit.

    Exchange and FX rates are round-trip rates applied to entry notional. Tax is
    estimated only on a profitable outcome; losses do not create a tax credit.
    """
    probability = min(1.0, max(0.0, float(probability)))
    notional = max(0.0, float(notional))
    win_profit = notional * max(0.0, take_profit_pct)
    loss = notional * max(0.0, stop_loss_pct)
    commission = max(0.0, float(round_trip_commission))
    exchange_fee = notional * max(0.0, float(exchange_fee_rate))
    fx_cost = notional * max(0.0, float(fx_cost_rate))
    trading_cost = commission + exchange_fee + fx_cost
    gross_profit = probability * win_profit - (1.0 - probability) * loss
    taxable_win = max(0.0, win_profit - trading_cost)
    estimated_tax = probability * taxable_win * max(0.0, float(tax_rate))
    net_profit = gross_profit - trading_cost - estimated_tax
    return ProfitabilityEstimate(
        gross_profit=gross_profit,
        commission=commission,
        exchange_fee=exchange_fee,
        fx_cost=fx_cost,
        estimated_tax=estimated_tax,
        trading_cost=trading_cost,
        net_profit=net_profit,
        required_net_profit=max(0.0, float(minimum_cost_multiple)) * trading_cost,
    )
