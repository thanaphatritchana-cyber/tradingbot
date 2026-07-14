import getpass
import os
from pathlib import Path


def current_os_user() -> str:
    """Return the user attached to the process token, not an environment variable."""
    if os.name == "nt":
        import ctypes

        size = ctypes.c_ulong(256)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
            raise OSError("Windows could not identify the current process user")
        return buffer.value
    return getpass.getuser()


def current_os_identity() -> str:
    """Return DOMAIN\\user on Windows so an allowed domain cannot be ignored."""
    if os.name != "nt":
        return current_os_user()
    import ctypes

    size = ctypes.c_ulong(0)
    ctypes.windll.secur32.GetUserNameExW(2, None, ctypes.byref(size))
    if size.value:
        buffer = ctypes.create_unicode_buffer(size.value)
        if ctypes.windll.secur32.GetUserNameExW(2, buffer, ctypes.byref(size)):
            return buffer.value
    return current_os_user()


def ensure_allowed_user(allowed_user: str) -> None:
    allowed_user = allowed_user.strip()
    if not allowed_user:
        return
    actual_identity = current_os_identity()
    actual_name = actual_identity.rsplit("\\", 1)[-1]
    expected = allowed_user if "\\" in allowed_user else allowed_user.rsplit("\\", 1)[-1]
    actual = actual_identity if "\\" in allowed_user else actual_name
    if actual.casefold() != expected.casefold():
        raise PermissionError(
            f"TradingBot is restricted to OS user {allowed_user!r}; "
            f"current identity is {actual_identity!r}"
        )


class SingleInstance:
    """Hold an OS-level file lock so only one bot process can run."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("Another TradingBot instance is already running") from exc
        return self

    def __exit__(self, *_):
        if not self.handle:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None
