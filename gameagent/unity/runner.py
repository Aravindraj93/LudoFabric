"""Runs Unity in batch mode and parses compiler errors / bridge results."""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


from ..ui import console
from ..metrics import record

CS_ERROR = re.compile(r"^(?P<file>[^\s(][^\r\n(]*\.cs)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>CS\d+): (?P<msg>.*)$", re.M)
MAX_CAPTURE_BYTES = 16 * 1024 * 1024


def read_log(path: Path) -> tuple[str, bool]:
    """Keep full logs on disk but bound memory use; truncated evidence cannot pass QA."""
    if not path.exists():
        return '', False
    with path.open('rb') as stream:
        size = path.stat().st_size
        if size <= MAX_CAPTURE_BYTES:
            return stream.read(MAX_CAPTURE_BYTES).decode('utf-8', errors='replace'), False
        head = stream.read(MAX_CAPTURE_BYTES // 4)
        stream.seek(-MAX_CAPTURE_BYTES * 3 // 4, 2)
        tail = stream.read(MAX_CAPTURE_BYTES * 3 // 4)
    return (head.decode('utf-8', errors='replace') +
            '\n[Unity log capture truncated; full log retained on disk.]\n' +
            tail.decode('utf-8', errors='replace')), True


@dataclass
class UnityResult:
    returncode: int
    log: str
    compile_errors: list[dict] = field(default_factory=list)
    bridge: dict | None = None
    log_truncated: bool = False

    @property
    def ok(self) -> bool:
        if self.returncode != 0 or self.compile_errors or self.log_truncated:
            return False
        if self.bridge is not None:
            return bool(self.bridge.get("ok"))
        return self.returncode == 0

    def error_text(self, limit: int = 12000) -> str:
        parts = []
        if self.log_truncated:
            parts.append('Unity log exceeded the capture limit. Verification is incomplete; inspect the full log on disk for repeated errors before retrying.')
        if self.compile_errors:
            parts.append("COMPILER ERRORS:\n" + "\n".join(
                f"{e['file']}({e['line']},{e['col']}): {e['code']}: {e['msg']}" for e in self.compile_errors))
        if self.bridge and self.bridge.get("errors"):
            parts.append("EDITOR/RUNTIME ERRORS:\n" + "\n---\n".join(self.bridge["errors"]))
        if not parts and not self.ok:
            tail = "\n".join(l for l in self.log.splitlines() if "error" in l.lower() or "exception" in l.lower())
            parts.append("UNITY LOG (errors):\n" + (tail or self.log[-4000:]))
        return "\n\n".join(parts)[:limit]


class UnityRunner:
    def __init__(self, editor: str, project: Path, logs_dir: Path, timeout: int):
        self.editor = editor
        self.project = project
        self.logs_dir = logs_dir
        self.timeout = timeout
        logs_dir.mkdir(parents=True, exist_ok=True)

    def _lockfile_check(self) -> None:
        lock = self.project / "Temp" / "UnityLockfile"
        if lock.exists():
            try:
                lock.unlink()  # stale lock from a crashed run; fails if the Editor really has it open
            except OSError:
                raise RuntimeError("The Unity project is open in the Editor. Close it so the agent can run Unity headless.")

    def run(self, args: list[str], name: str, nographics: bool = True, method: str | None = None,
            extra: dict[str, str] | None = None, quit: bool = True) -> UnityResult:
        if self.project.exists():
            self._lockfile_check()
        stamp = time.strftime("%H%M%S")
        log_path = self.logs_dir / f"{stamp}_{name}.log"
        result_path = self.logs_dir / f"{stamp}_{name}.result.json"
        cmd = [self.editor, "-batchmode", "-logFile", str(log_path)]
        if quit:
            cmd.append("-quit")
        if nographics:
            cmd.append("-nographics")
        cmd += args
        if method:
            cmd += ["-executeMethod", method, "-agentResult", str(result_path)]
        for k, v in (extra or {}).items():
            cmd += [k, v]

        console.print(f"[dim]  unity> {name} ...[/]")
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, timeout=self.timeout, capture_output=True, text=True)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rc = -1
        log, truncated = read_log(log_path)
        errors = self._parse_errors(log)
        bridge = None
        if result_path.exists():
            try:
                bridge = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                bridge = None
        res = UnityResult(rc, log, errors, bridge, truncated)
        record(self.logs_dir.parent / "metrics.jsonl", kind="unity", name=name, ok=res.ok,
               seconds=round(time.time() - t0, 3), compile_errors=len(errors))
        console.print(f"[dim]  unity< {name}: rc={rc} ok={res.ok} ({time.time() - t0:.0f}s, "
                      f"{len(errors)} compile errors)[/]")
        return res

    @staticmethod
    def _parse_errors(log: str) -> list[dict]:
        seen, out = set(), []
        for m in CS_ERROR.finditer(log):
            d = m.groupdict()
            d["file"] = d["file"].strip().replace("\\", "/")
            key = (d["file"], d["line"], d["code"], d["msg"])
            if key not in seen:
                seen.add(key)
                out.append(d)
        return out

