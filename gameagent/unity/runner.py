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


@dataclass
class UnityResult:
    returncode: int
    log: str
    compile_errors: list[dict] = field(default_factory=list)
    bridge: dict | None = None

    @property
    def ok(self) -> bool:
        if self.returncode != 0 or self.compile_errors:
            return False
        if self.bridge is not None:
            return bool(self.bridge.get("ok"))
        return self.returncode == 0

    def error_text(self, limit: int = 12000) -> str:
        parts = []
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
        log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        errors = self._parse_errors(log)
        bridge = None
        if result_path.exists():
            try:
                bridge = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                bridge = None
        res = UnityResult(rc, log, errors, bridge)
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

