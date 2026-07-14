from trading_bot.config import Settings
from trading_bot.paper_roundtrip import validate_paper_safety


def settings(**changes):
    values = dict(
        ibkr_paper=True,
        ibkr_account="DU123",
        ibkr_port=7497,
        ibkr_read_only=False,
        kill_switch=True,
        max_order_notional=1000,
    )
    values.update(changes)
    return Settings(_env_file=None, **values)


def test_paper_roundtrip_requires_all_safety_guards():
    assert validate_paper_safety(settings()) == []
    assert validate_paper_safety(settings(ibkr_paper=False))
    assert validate_paper_safety(settings(ibkr_account="U123"))
    assert validate_paper_safety(settings(ibkr_port=7496))
    assert validate_paper_safety(settings(kill_switch=False))
