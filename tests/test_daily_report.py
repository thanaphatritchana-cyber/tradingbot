from datetime import date, datetime, timezone

from trading_bot.main import _latest_reportable_date
from trading_bot.storage import summarize_trades


def test_summary_has_daily_and_cumulative_realized_profit():
    rows = [
        (datetime(2026, 7, 11, 2, tzinfo=timezone.utc), "AAPL", "buy", 2, 100),
        (datetime(2026, 7, 12, 3, tzinfo=timezone.utc), "AAPL", "sell", 1, 110),
        (datetime(2026, 7, 13, 3, tzinfo=timezone.utc), "AAPL", "sell", 1, 95),
    ]

    result = summarize_trades(rows, date(2026, 7, 13), "Asia/Bangkok")

    assert result.daily_profit == -5
    assert result.daily_trades == 1
    assert result.daily_losses == 1
    assert result.total_profit == 5
    assert result.total_trades == 2
    assert result.total_wins == 1


def test_summary_keeps_cost_basis_separate_by_symbol():
    rows = [
        (datetime(2026, 7, 13, tzinfo=timezone.utc), "AAPL", "buy", 1, 100),
        (datetime(2026, 7, 13, tzinfo=timezone.utc), "MSFT", "buy", 1, 200),
        (datetime(2026, 7, 13, 1, tzinfo=timezone.utc), "AAPL", "sell", 1, 110),
        (datetime(2026, 7, 13, 1, tzinfo=timezone.utc), "MSFT", "sell", 1, 190),
    ]

    result = summarize_trades(rows, date(2026, 7, 13), "Asia/Bangkok")

    assert result.daily_profit == 0
    assert result.daily_wins == 1
    assert result.daily_losses == 1


def test_cumulative_summary_stops_at_report_date():
    rows = [
        (datetime(2026, 7, 12, tzinfo=timezone.utc), "AAPL", "buy", 1, 100),
        (datetime(2026, 7, 12, 1, tzinfo=timezone.utc), "AAPL", "sell", 1, 110),
        (datetime(2026, 7, 13, tzinfo=timezone.utc), "MSFT", "buy", 1, 100),
        (datetime(2026, 7, 13, 1, tzinfo=timezone.utc), "MSFT", "sell", 1, 150),
    ]

    result = summarize_trades(rows, date(2026, 7, 12), "Asia/Bangkok")

    assert result.total_profit == 10
    assert result.total_trades == 1


def test_latest_reportable_date_uses_previous_day_before_schedule():
    before_schedule = datetime(2026, 7, 13, 16, 58, tzinfo=timezone.utc)  # 23:58 Bangkok
    at_schedule = datetime(2026, 7, 13, 16, 59, tzinfo=timezone.utc)  # 23:59 Bangkok

    assert _latest_reportable_date(before_schedule, "23:59", "Asia/Bangkok") == date(2026, 7, 12)
    assert _latest_reportable_date(at_schedule, "23:59", "Asia/Bangkok") == date(2026, 7, 13)


def test_summary_deducts_buy_and_sell_commissions():
    rows = [
        (datetime(2026, 7, 13, tzinfo=timezone.utc), "AAPL", "buy", 1, 316.86, 1.000003),
        (datetime(2026, 7, 13, 1, tzinfo=timezone.utc), "AAPL", "sell", 1, 316.80, 1.006724),
    ]

    result = summarize_trades(rows, date(2026, 7, 13), "Asia/Bangkok")

    assert round(result.daily_profit, 6) == -2.066727
    assert round(result.total_profit, 6) == -2.066727
    assert round(result.daily_fees, 6) == 2.006727
    assert result.daily_losses == 1


def test_default_minimum_probability_is_ninety_percent():
    from trading_bot.config import Settings

    assert Settings(_env_file=None).min_win_probability == 0.90
