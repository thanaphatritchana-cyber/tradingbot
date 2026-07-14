"""Secure LINE webhook controller for starting and stopping TradingBot."""

import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import signal
import threading
import time
from collections import deque

from . import control
from .config import Settings
from .notify import LineNotifier
from .security import SingleInstance, ensure_allowed_user
from .storage import Store


ROOT = Path(__file__).resolve().parent.parent
PID_PATH = ROOT / ".line_controller.pid"
STOP_PATH = ROOT / ".line_controller.stop"
MAX_BODY_BYTES = 1_000_000
MAX_COMMANDS_PER_MINUTE = 6


class WebhookGuard:
    def __init__(
        self, max_seen: int = 2048, limit: int = MAX_COMMANDS_PER_MINUTE,
        persistent_claim=None,
    ):
        self.max_seen = max_seen
        self.limit = limit
        self.seen_order = deque()
        self.seen = set()
        self.recent = deque()
        self.lock = threading.Lock()
        self.persistent_claim = persistent_claim

    def accept(self, event_id: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            if event_id and event_id in self.seen:
                return False
            if event_id and self.persistent_claim and not self.persistent_claim(event_id):
                return False
            while self.recent and now - self.recent[0] >= 60:
                self.recent.popleft()
            if len(self.recent) >= self.limit:
                return False
            self.recent.append(now)
            if event_id:
                self.seen.add(event_id)
                self.seen_order.append(event_id)
                while len(self.seen_order) > self.max_seen:
                    self.seen.discard(self.seen_order.popleft())
            return True


def verify_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    if not signature or not channel_secret:
        return False
    expected = base64.b64encode(
        hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(signature, expected)


def extract_commands(payload: dict, allowed_user_id: str) -> list[str]:
    commands = []
    for event in payload.get("events", []):
        source = event.get("source", {})
        message = event.get("message", {})
        if (
            event.get("type") != "message"
            or message.get("type") != "text"
            or source.get("type") != "user"
            or source.get("userId") != allowed_user_id
        ):
            continue
        text = " ".join(str(message.get("text", "")).strip().lower().split())
        aliases = {
            "เริ่ม": "start", "เริ่มบอท": "start",
            "หยุด": "stop", "หยุดบอท": "stop",
            "สถานะ": "status", "ดูสถานะ": "status",
            "เริ่ม live ยืนยัน": "start_live", "start live confirm": "start_live",
            "เริ่ม": "start", "เริ่มบอท": "start", "start": "start",
            "หยุด": "stop", "หยุดบอท": "stop", "stop": "stop",
            "สถานะ": "status", "ดูสถานะ": "status", "status": "status",
        }
        commands.append(aliases.get(text, "help"))
    return commands


def execute_command(command: str) -> str:
    cfg = Settings()
    live_mode = (
        cfg.broker.strip().lower() in {"ibkr", "interactivebrokers", "interactive_brokers"}
        and not cfg.ibkr_paper
    )
    if command == "start":
        if live_mode:
            return "ปฏิเสธการเริ่ม Live: กรุณาพิมพ์ 'เริ่ม live ยืนยัน'"
        result = control.start()
        return "✅ เริ่ม TradingBot แล้ว" if result == 0 else "❌ เริ่ม TradingBot ไม่สำเร็จ กรุณาตรวจ log"
    if command == "start_live":
        if not live_mode:
            return "บัญชีปัจจุบันไม่ใช่ Live; ใช้คำสั่ง 'เริ่ม'"
        result = control.start()
        return "เริ่ม TradingBot LIVE แล้ว" if result == 0 else "เริ่ม TradingBot LIVE ไม่สำเร็จ กรุณาตรวจ log"
    if command == "stop":
        result = control.stop()
        if result == 0:
            return (
                "หยุดโปรแกรม TradingBot แล้ว\n"
                "Stop Loss/Take Profit ที่ส่งไป IBKR แล้วยังคงทำงานเพื่อป้องกัน Position"
            )
        return "⏹️ หยุด TradingBot แล้ว\nหมายเหตุ: Position ที่เปิดอยู่จะไม่ถูกขายอัตโนมัติ" if result == 0 else "❌ หยุด TradingBot ไม่สำเร็จ กรุณาตรวจ log"
    if command == "status":
        pid = control._pid()
        return f"🟢 TradingBot กำลังทำงาน (PID {pid})" if control._is_running(pid) else "⚫ TradingBot หยุดอยู่"
    return "คำสั่งที่รองรับ: เริ่ม / หยุด / สถานะ"


def make_handler(cfg: Settings, notifier: LineNotifier, event_store: Store | None = None):
    log = logging.getLogger("trading-bot.line-controller")
    guard = WebhookGuard(
        persistent_claim=event_store.claim_webhook_event if event_store else None
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):
            if self.path != "/webhook":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            signature = self.headers.get("x-line-signature", "")
            if not verify_signature(body, signature, cfg.line_channel_secret):
                log.warning("rejected webhook with invalid signature")
                self.send_error(401)
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400)
                return
            accepted_events = []
            for event in payload.get("events", []):
                event_id = str(event.get("webhookEventId", "") or "")
                try:
                    accepted = guard.accept(event_id)
                except Exception:
                    log.exception("failed to persist LINE webhook event")
                    self.send_error(503)
                    return
                if accepted:
                    accepted_events.append(event)
                else:
                    log.warning("ignored duplicate or rate-limited LINE event id=%s", event_id)
            payload = {**payload, "events": accepted_events}
            commands = extract_commands(payload, cfg.line_control_user_id)
            self.send_response(200)
            self.end_headers()
            for command in commands:
                threading.Thread(
                    target=lambda cmd=command: notifier.send(execute_command(cmd)),
                    name="line-command",
                    daemon=True,
                ).start()

        def log_message(self, format, *args):
            log.info("webhook %s", format % args)

    return Handler


def run() -> None:
    cfg = Settings()
    ensure_allowed_user(cfg.allowed_os_user)
    if cfg.line_controller_host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("LINE_CONTROLLER_HOST must be loopback only")
    if not cfg.line_channel_secret:
        raise ValueError("LINE_CHANNEL_SECRET is required")
    if not cfg.line_control_user_id.startswith("U"):
        raise ValueError("LINE_CONTROL_USER_ID must be one direct-user ID beginning with U")
    if not cfg.line_channel_access_token:
        raise ValueError("LINE_CHANNEL_ACCESS_TOKEN is required")

    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(message)s")
    notifier = LineNotifier(cfg.line_channel_access_token, cfg.line_control_user_id)
    event_store = Store(cfg.database_url)
    server = ThreadingHTTPServer(
        (cfg.line_controller_host, cfg.line_controller_port),
        make_handler(cfg, notifier, event_store),
    )
    server.timeout = 1
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with SingleInstance(ROOT / ".line_controller.lock"):
        STOP_PATH.unlink(missing_ok=True)
        PID_PATH.write_text(str(__import__("os").getpid()), encoding="ascii")
        try:
            logging.info("LINE controller listening on %s:%s", cfg.line_controller_host, cfg.line_controller_port)
            while running and not STOP_PATH.exists():
                server.handle_request()
        finally:
            server.server_close()
            PID_PATH.unlink(missing_ok=True)
            STOP_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    run()
