"""Supervise the private ngrok endpoint used by the LINE webhook controller."""

import logging
import os
from pathlib import Path
import subprocess
import time

from .config import Settings
from .security import SingleInstance, ensure_allowed_user


ROOT = Path(__file__).resolve().parent.parent
PID_PATH = ROOT / ".ngrok_tunnel.pid"
STOP_PATH = ROOT / ".ngrok_tunnel.stop"
LOCK_PATH = ROOT / ".ngrok_tunnel.lock"


def run() -> int:
    cfg = Settings()
    ensure_allowed_user(cfg.allowed_os_user)
    if not cfg.ngrok_authtoken.strip():
        raise RuntimeError("NGROK_AUTHTOKEN is required")

    executable = Path(cfg.ngrok_path).expanduser()
    if not executable.is_file():
        raise FileNotFoundError(f"ngrok executable was not found: {executable}")

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("trading-bot.ngrok")
    environment = os.environ.copy()
    environment["NGROK_AUTHTOKEN"] = cfg.ngrok_authtoken.strip()

    with SingleInstance(LOCK_PATH):
        STOP_PATH.unlink(missing_ok=True)
        PID_PATH.write_text(str(os.getpid()), encoding="ascii")
        process = subprocess.Popen(
            [
                str(executable), "http", cfg.ngrok_upstream,
                "--log", "stdout", "--log-format", "json",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=subprocess.STDOUT,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        log.info("ngrok tunnel process started")
        stop_requested = False
        try:
            while process.poll() is None:
                if STOP_PATH.exists():
                    log.info("stop requested")
                    stop_requested = True
                    break
                time.sleep(0.25)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            PID_PATH.unlink(missing_ok=True)
            STOP_PATH.unlink(missing_ok=True)
        if process.returncode not in (0, None) and not stop_requested:
            log.error("ngrok exited with code %s", process.returncode)
            return process.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
