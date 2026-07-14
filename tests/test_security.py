import getpass

import pytest

from trading_bot import security
from trading_bot.security import SingleInstance, ensure_allowed_user


def test_allowed_user_rejects_another_user():
    with pytest.raises(PermissionError):
        ensure_allowed_user("a-user-that-does-not-exist")


def test_allowed_user_accepts_current_user():
    ensure_allowed_user(getpass.getuser())


def test_allowed_windows_domain_is_not_ignored(monkeypatch):
    monkeypatch.setattr(security, "current_os_identity", lambda: "DOMAIN-A\\owner")

    with pytest.raises(PermissionError):
        ensure_allowed_user("DOMAIN-B\\owner")
    ensure_allowed_user("DOMAIN-A\\owner")


def test_single_instance_rejects_second_lock(tmp_path):
    lock_path = tmp_path / "bot.lock"
    with SingleInstance(lock_path):
        with pytest.raises(RuntimeError, match="already running"):
            with SingleInstance(lock_path):
                pass
