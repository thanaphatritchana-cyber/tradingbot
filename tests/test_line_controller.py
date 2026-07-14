import base64
import hashlib
import hmac

from types import SimpleNamespace

from trading_bot import line_controller
from trading_bot.line_controller import WebhookGuard, extract_commands, verify_signature


def test_signature_verification_uses_raw_body():
    body = b'{"events":[]}'
    secret = "secret"
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()

    assert verify_signature(body, signature, secret)
    assert not verify_signature(body + b" ", signature, secret)


def test_only_authorized_direct_user_can_control_bot():
    event = {
        "type": "message",
        "source": {"type": "user", "userId": "U-owner"},
        "message": {"type": "text", "text": "เริ่ม"},
    }
    unauthorized = {**event, "source": {"type": "user", "userId": "U-other"}}
    group = {**event, "source": {"type": "group", "userId": "U-owner"}}

    assert extract_commands({"events": [event]}, "U-owner") == ["start"]
    assert extract_commands({"events": [unauthorized, group]}, "U-owner") == []


def test_thai_live_confirmation_is_explicit():
    event = {
        "type": "message",
        "source": {"type": "user", "userId": "U-owner"},
        "message": {"type": "text", "text": "เริ่ม live ยืนยัน"},
    }

    assert extract_commands({"events": [event]}, "U-owner") == ["start_live"]


def test_webhook_guard_rejects_replay_and_rate_abuse():
    guard = WebhookGuard(limit=2)

    assert guard.accept("event-1", now=100)
    assert not guard.accept("event-1", now=101)
    assert guard.accept("event-2", now=102)
    assert not guard.accept("event-3", now=103)
    assert guard.accept("event-3", now=161)


def test_webhook_guard_can_persist_replay_ids_across_processes():
    claimed = set()

    def claim(event_id):
        if event_id in claimed:
            return False
        claimed.add(event_id)
        return True

    assert WebhookGuard(persistent_claim=claim).accept("persisted-1", now=1)
    assert not WebhookGuard(persistent_claim=claim).accept("persisted-1", now=2)


def test_plain_start_cannot_start_live(monkeypatch):
    monkeypatch.setattr(
        line_controller,
        "Settings",
        lambda: SimpleNamespace(broker="ibkr", ibkr_paper=False),
    )
    called = []
    monkeypatch.setattr(line_controller.control, "start", lambda: called.append(True) or 0)

    assert "ปฏิเสธ" in line_controller.execute_command("start")
    assert called == []
    assert "LIVE" in line_controller.execute_command("start_live")
    assert called == [True]
