"""Start, stop and inspect the local TradingBot process."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
import json
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import Settings
from .security import ensure_allowed_user


ROOT = Path(__file__).resolve().parent.parent
PID_PATH = ROOT / ".trading_bot.pid"
STOP_PATH = ROOT / ".trading_bot.stop"
LOG_PATH = ROOT / "trading-bot.log"
CONTROLLER_PID_PATH = ROOT / ".line_controller.pid"
CONTROLLER_STOP_PATH = ROOT / ".line_controller.stop"
CONTROLLER_LOG_PATH = ROOT / "line-controller.log"
TUNNEL_PID_PATH = ROOT / ".ngrok_tunnel.pid"
TUNNEL_STOP_PATH = ROOT / ".ngrok_tunnel.stop"
TUNNEL_LOG_PATH = ROOT / "ngrok-tunnel.log"


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid() -> int | None:
    return _read_pid(PID_PATH)


def _is_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        ctypes.windll.kernel32.CloseHandle(process)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status() -> bool:
    pid = _pid()
    running = _is_running(pid)
    print(f"TradingBot is {'RUNNING' if running else 'STOPPED'}" + (f" (PID {pid})" if running else ""))
    print(f"Log: {LOG_PATH}")
    return running


def _start_service(module: str, pid_path: Path, stop_path: Path, log_path: Path, label: str) -> int:
    pid = _read_pid(pid_path)
    if _is_running(pid):
        print(f"{label} is already running (PID {pid})")
        return 0
    pid_path.unlink(missing_ok=True)
    stop_path.unlink(missing_ok=True)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", module],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=creationflags,
            close_fds=True,
        )
    for _ in range(30):
        if process.poll() is not None:
            print(f"{label} failed to start (exit {process.returncode}); check {log_path}")
            return 1
        service_pid = _read_pid(pid_path)
        if _is_running(service_pid):
            print(f"{label} started (PID {service_pid})")
            print(f"Log: {log_path}")
            return 0
        time.sleep(0.1)
    print(f"{label} did not become ready; check {log_path}")
    return 1


def start() -> int:
    return _start_service("trading_bot.main", PID_PATH, STOP_PATH, LOG_PATH, "TradingBot")


def _stop_service(pid_path: Path, stop_path: Path, log_path: Path, label: str, timeout: int = 45) -> int:
    pid = _read_pid(pid_path)
    if not _is_running(pid):
        pid_path.unlink(missing_ok=True)
        stop_path.unlink(missing_ok=True)
        print(f"{label} is already stopped")
        return 0
    stop_path.write_text("stop", encoding="ascii")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_running(pid):
            print(f"{label} stopped gracefully")
            return 0
        time.sleep(0.25)
    print(f"{label} did not stop within {timeout}s (PID {pid}); check {log_path}")
    return 1


def stop(timeout: int = 45) -> int:
    return _stop_service(PID_PATH, STOP_PATH, LOG_PATH, "TradingBot", timeout)


def controller_start() -> int:
    return _start_service(
        "trading_bot.line_controller",
        CONTROLLER_PID_PATH, CONTROLLER_STOP_PATH, CONTROLLER_LOG_PATH,
        "LINE controller",
    )


def controller_stop() -> int:
    return _stop_service(
        CONTROLLER_PID_PATH, CONTROLLER_STOP_PATH, CONTROLLER_LOG_PATH,
        "LINE controller", 15,
    )


def controller_status() -> bool:
    pid = _read_pid(CONTROLLER_PID_PATH)
    running = _is_running(pid)
    print(f"LINE controller is {'RUNNING' if running else 'STOPPED'}" + (f" (PID {pid})" if running else ""))
    print(f"Log: {CONTROLLER_LOG_PATH}")
    return running


def tunnel_start() -> int:
    if _ngrok_api_tunnels():
        print("ngrok tunnel is already active")
        return 0
    return _start_service(
        "trading_bot.ngrok_tunnel",
        TUNNEL_PID_PATH, TUNNEL_STOP_PATH, TUNNEL_LOG_PATH,
        "ngrok tunnel",
    )


def tunnel_stop() -> int:
    result = _stop_service(
        TUNNEL_PID_PATH, TUNNEL_STOP_PATH, TUNNEL_LOG_PATH,
        "ngrok tunnel", 15,
    )
    for api_base, name, _ in _ngrok_api_tunnels():
        try:
            request = Request(
                f"{api_base}/api/tunnels/{quote(name, safe='')}",
                method="DELETE",
            )
            with urlopen(request, timeout=3):
                pass
        except (OSError, URLError):
            result = 1
    if _ngrok_api_tunnels():
        print("ngrok endpoint is still active")
        return 1
    return result


def tunnel_status() -> bool:
    pid = _read_pid(TUNNEL_PID_PATH)
    managed = _is_running(pid)
    tunnels = _ngrok_api_tunnels()
    running = managed or bool(tunnels)
    detail = f" (PID {pid})" if managed else (" (ngrok API)" if tunnels else "")
    print(f"ngrok tunnel is {'RUNNING' if running else 'STOPPED'}" + detail)
    print(f"Log: {TUNNEL_LOG_PATH}")
    return running


def _ngrok_api_tunnels() -> list[tuple[str, str, str]]:
    for port in (4040, 4041, 4042):
        api_base = f"http://127.0.0.1:{port}"
        try:
            with urlopen(f"{api_base}/api/tunnels", timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError):
            continue
        return [
            (
                api_base,
                str(item.get("name", "")),
                str(item.get("public_url", "")),
            )
            for item in payload.get("tunnels", [])
            if item.get("name")
        ]
    return []


def remote_start() -> int:
    controller_result = controller_start()
    if controller_result != 0:
        return controller_result
    return tunnel_start()


def remote_stop() -> int:
    tunnel_result = tunnel_stop()
    controller_result = controller_stop()
    return tunnel_result or controller_result


def remote_status() -> bool:
    controller_running = controller_status()
    tunnel_running = tunnel_status()
    return controller_running and tunnel_running


def run() -> int:
    parser = argparse.ArgumentParser(description="Control TradingBot")
    parser.add_argument(
        "command",
        choices=(
            "start", "stop", "status",
            "controller-start", "controller-stop", "controller-status",
            "tunnel-start", "tunnel-stop", "tunnel-status",
            "remote-start", "remote-stop", "remote-status",
        ),
    )
    args = parser.parse_args()
    ensure_allowed_user(Settings().allowed_os_user)
    if args.command == "start":
        return start()
    if args.command == "stop":
        return stop()
    if args.command == "controller-start":
        return controller_start()
    if args.command == "controller-stop":
        return controller_stop()
    if args.command == "controller-status":
        controller_status()
        return 0
    if args.command == "tunnel-start":
        return tunnel_start()
    if args.command == "tunnel-stop":
        return tunnel_stop()
    if args.command == "tunnel-status":
        tunnel_status()
        return 0
    if args.command == "remote-start":
        return remote_start()
    if args.command == "remote-stop":
        return remote_stop()
    if args.command == "remote-status":
        remote_status()
        return 0
    status()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
