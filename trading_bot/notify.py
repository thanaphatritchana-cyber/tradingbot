import logging
import time

import httpx


class LineNotifier:
    def __init__(self, token: str, target: str, store=None, retry_attempts: int = 3):
        self.token, self.target = token, target
        self.store = store
        self.retry_attempts = max(1, retry_attempts)
        self.log = logging.getLogger("trading-bot.line")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.target)

    def _post(self, message: str) -> None:
        response = httpx.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"to": self.target, "messages": [{"type": "text", "text": message[:5000]}]},
            timeout=15,
        )
        response.raise_for_status()

    def _post_with_retry(self, message: str) -> tuple[bool, str]:
        error = ""
        for attempt in range(self.retry_attempts):
            try:
                self._post(message)
                return True, ""
            except (httpx.HTTPError, OSError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < self.retry_attempts:
                    time.sleep(2 ** attempt)
        self.log.error("LINE notification failed after retries: %s", error)
        return False, error

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        if self.store is None:
            return self._post_with_retry(message)[0]
        notification_id = self.store.enqueue_notification(message[:5000])
        return self.flush_pending(target_id=notification_id)

    def flush_pending(self, target_id: int | None = None) -> bool:
        if not self.enabled or self.store is None:
            return False
        target_sent = target_id is None
        for notification_id, message in self.store.pending_notifications():
            notification_id = int(notification_id)
            sent, error = self._post_with_retry(str(message))
            if sent:
                self.store.mark_notification_sent(notification_id)
                if notification_id == target_id:
                    target_sent = True
                continue
            self.store.mark_notification_failed(notification_id, error)
            break
        return target_sent
