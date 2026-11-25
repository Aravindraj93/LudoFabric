"""Resolve provider CLIs without preferring a VS Code-bundled Codex alpha."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil


def find_cli(provider: str) -> str:
    override = os.getenv(f"{provider.upper()}_BIN", "").strip()
    if override:
        return override
    if provider == "codex" and os.name == "nt":
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
        roots = [Path(__file__).resolve().parent.parent / ".local-tools/codex/node_modules/@openai"]
        if os.getenv("APPDATA"):
            roots.append(Path(os.environ["APPDATA"]) / "npm/node_modules/@openai")
        for root in roots:
            candidates = sorted((root / f"codex-win32-{arch}").glob("vendor/*/bin/codex.exe"))
            candidates += sorted((root / "codex").glob("vendor/*/codex/codex.exe"))
            if candidates:
                return str(candidates[0])
    return shutil.which(provider) or ""
