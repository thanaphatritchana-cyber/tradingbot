import httpx

from trading_bot.notify import LineNotifier


class FakeStore:
    def __init__(self):
        self.rows = []
        self.sent = []
        self.failed = []

    def enqueue_notification(self, message):
        row = (len(self.rows) + 1, message)
        self.rows.append(row)
        return row[0]

    def pending_notifications(self):
        return [row for row in self.rows if row[0] not in self.sent]

    def mark_notification_sent(self, notification_id):
        self.sent.append(notification_id)

    def mark_notification_failed(self, notification_id, error):
        self.failed.append((notification_id, error))


class OkResponse:
    def raise_for_status(self):
        return None


def test_durable_notification_is_queued_then_marked_sent(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: OkResponse())
    notifier = LineNotifier("token", "target", store=store, retry_attempts=1)

    assert notifier.send("trade filled") is True
    assert store.rows == [(1, "trade filled")]
    assert store.sent == [1]


def test_failed_notification_remains_pending(monkeypatch):
    store = FakeStore()

    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", fail)
    notifier = LineNotifier("token", "target", store=store, retry_attempts=1)

    assert notifier.send("trade filled") is False
    assert store.sent == []
    assert store.failed and store.failed[0][0] == 1
