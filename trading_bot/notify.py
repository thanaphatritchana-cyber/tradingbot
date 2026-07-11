import httpx


class LineNotifier:
    def __init__(self, token: str, target: str):
        self.token, self.target = token, target

    def send(self, message: str) -> None:
        if not self.token or not self.target:
            return
        response = httpx.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"to": self.target, "messages": [{"type": "text", "text": message[:5000]}]},
            timeout=15,
        )
        response.raise_for_status()

