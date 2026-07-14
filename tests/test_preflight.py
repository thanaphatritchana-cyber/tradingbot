from trading_bot.config import Settings
from types import SimpleNamespace

from trading_bot.preflight import (
    LIVE_CONFIRMATION, LIVE_COST_CONFIRMATION, LIVE_TAX_CONFIRMATION,
    validate_paper_track_record, validate_settings,
)
from trading_bot.main import live_config_fingerprint


def base_settings(**changes):
    values = dict(
        broker="ibkr",
        allowed_os_user="owner",
        line_channel_access_token="token",
        line_target_id="U-owner",
        ibkr_account="DU123",
        max_order_notional=1000,
        max_total_exposure=2000,
        stop_loss_pct=0.02,
        take_profit_pct=0.04,
        estimated_round_trip_commission=0.10,
        ibkr_paper=True,
        ibkr_read_only=True,
        kill_switch=True,
        ibkr_port=7497,
        live_trading_confirm="",
        ibkr_native_bracket=True,
    )
    values.update(changes)
    return Settings(_env_file=None, **values)


def test_current_safe_settings_pass_paper_validation():
    assert validate_settings(base_settings(), "paper") == []


def test_live_validation_requires_all_explicit_safety_changes():
    errors = validate_settings(base_settings(), "live")
    assert any("IBKR_PAPER" in error for error in errors)
    assert any("IBKR_READ_ONLY" in error for error in errors)
    assert any("KILL_SWITCH" in error for error in errors)
    assert any("DU paper account" in error for error in errors)
    assert any("LIVE_TRADING_CONFIRM" in error for error in errors)

    ready = base_settings(
        ibkr_paper=False,
        ibkr_read_only=False,
        kill_switch=False,
        ibkr_port=7496,
        ibkr_account="U123",
        ibkr_market_data_type=1,
        live_trading_confirm=LIVE_CONFIRMATION,
        live_tax_confirm=LIVE_TAX_CONFIRMATION,
        live_cost_model_confirm=LIVE_COST_CONFIRMATION,
    )
    assert validate_settings(ready, "live") == []


def test_live_fingerprint_changes_when_risk_changes():
    first = base_settings(max_order_notional=1000)
    second = base_settings(max_order_notional=2000)

    assert live_config_fingerprint(first) != live_config_fingerprint(second)


def test_live_fingerprint_changes_when_strategy_changes():
    first = base_settings(min_win_probability=0.70, signal_horizon_bars=20)
    second = base_settings(min_win_probability=0.80, signal_horizon_bars=40)

    assert live_config_fingerprint(first) != live_config_fingerprint(second)


def test_take_profit_range_is_limited_to_three_to_ten_percent():
    assert validate_settings(base_settings(take_profit_pct=0.03, take_profit_max_pct=0.10), "paper") == []
    assert any(
        "3% and 10%" in error
        for error in validate_settings(
            base_settings(take_profit_pct=0.08, take_profit_max_pct=0.05), "paper"
        )
    )


def test_live_requires_a_positive_paper_track_record():
    cfg = base_settings(
        min_paper_closed_trades_for_live=30,
        min_paper_win_rate_for_live=0.50,
    )
    weak = SimpleNamespace(
        total_trades=2, total_profit=-4.12, total_wins=0,
        total_gross_profit=1, total_gross_loss=5.12,
        active_trading_days=2, max_drawdown=5.12,
    )
    strong = SimpleNamespace(
        total_trades=30, total_profit=100, total_wins=18,
        total_gross_profit=180, total_gross_loss=80,
        active_trading_days=20, max_drawdown=20,
    )

    errors = validate_paper_track_record(cfg, weak)
    assert any("30 closed trades" in error for error in errors)
    assert any("net profit" in error for error in errors)
    assert any("win rate" in error for error in errors)
    assert validate_paper_track_record(cfg, strong) == []
