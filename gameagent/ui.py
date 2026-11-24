"""Shared console + in-memory log bus so the CLI and the web dashboard see the same live output."""
from __future__ import annotations

import io
import sys
import threading
from collections import deque

from rich.console import Console


class LogBus(io.TextIOBase):
    """File-like sink: mirrors to stdout and keeps a numbered backlog for the web UI (SSE)."""

    def __init__(self, maxlen: int = 5000):
        self._lines: deque[tuple[int, str]] = deque(maxlen=maxlen)
        self._seq = 0
        self._buf = ""
        self._lock = threading.Condition()

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        try:
            sys.__stdout__.write(s)
            sys.__stdout__.flush()
        except Exception:
            pass
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._seq += 1
                self._lines.append((self._seq, line))
            self._lock.notify_all()
        return len(s)

    def flush(self) -> None:
        pass

    def since(self, seq: int, timeout: float = 15.0) -> list[tuple[int, str]]:
        with self._lock:
            if self._seq <= seq:
                self._lock.wait(timeout)
            return [(n, l) for n, l in self._lines if n > seq]

    def emit(self, line: str) -> None:
        self.write(line + "\n")


bus = LogBus()
console = Console(file=bus, width=150, force_terminal=False, color_system=None, soft_wrap=True)


# --- persistence: keep the console history across server restarts -------------------------------
from pathlib import Path as _Path  # noqa: E402

_LOG_FILE = _Path(__file__).resolve().parent.parent / ".studio.log"


def _load_history(max_lines: int = 500) -> None:
    if not _LOG_FILE.exists():
        return
    try:
        lines = _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    except OSError:
        return
    with bus._lock:
        for line in lines:
            bus._seq += 1
            bus._lines.append((bus._seq, line))


_load_history()
_orig_write = bus.write


def _write_and_persist(s: str) -> int:
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(s)
    except OSError:
        pass
    return _orig_write(s)


bus.write = _write_and_persist  # type: ignore[method-assign]


