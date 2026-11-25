"""Registry of AI coding CLIs that can power the studio using the user's OWN subscription login.

Every provider is an official CLI running locally on the user's machine with the user's own account
(personal use). LudoFabric never sees or stores the credentials - the CLI does the auth.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from .cli import find_cli

HOME = Path(os.path.expanduser("~"))


@dataclass
class Provider:
    id: str
    name: str
    bin: str
    npm_package: str
    login_args: list[str]
    plan: str
    cred_files: list[Path] = field(default_factory=list)
    env_keys: list[str] = field(default_factory=list)
    notes: str = ""

    def path(self) -> str:
        return find_cli(self.id)

    def installed(self) -> bool:
        return bool(self.path())

    def logged_in(self) -> bool | None:
        """True/False when we can tell, None when unknown (use the Test button)."""
        if not self.installed():
            return False
        if self.id == "codex":
            r = subprocess.run([self.path(), "login", "status"], capture_output=True, text=True)
            return r.returncode == 0 and "not logged in" not in (r.stdout + r.stderr).lower()
        if any(os.getenv(k) for k in self.env_keys):
            return True
        if any(p.exists() for p in self.cred_files):
            return True
        return None

    def open_login_window(self) -> None:
        """Opens a new terminal window running the CLI's own login flow (browser OAuth)."""
        cmd = [self.path()] + self.login_args
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/k"] + cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen(cmd)

    def can_logout(self) -> bool:
        return self.id == "codex"

    def logout(self) -> tuple[bool, str]:
        if not self.can_logout():
            return False, f"Sign out from inside {self.name} (run '{self.bin}' and use its logout command)."
        r = subprocess.run([self.path(), "logout"], capture_output=True, text=True)
        return r.returncode == 0, (r.stdout + r.stderr).strip() or "signed out"

    def info(self) -> dict:
        return {
            "id": self.id, "name": self.name, "installed": self.installed(), "logged_in": self.logged_in(),
            "plan": self.plan, "notes": self.notes, "install_cmd": f"npm install -g {self.npm_package}",
            "can_logout": self.can_logout(),
        }


PROVIDERS: dict[str, Provider] = {p.id: p for p in [
    Provider(
        id="codex", name="OpenAI Codex", bin="codex", npm_package="@openai/codex", login_args=["login"],
        plan="ChatGPT Plus / Pro / Business",
        notes="Sign in with ChatGPT. Best default for code generation.",
    ),
    Provider(
        id="claude", name="Claude Code", bin="claude", npm_package="@anthropic-ai/claude-code", login_args=[],
        plan="Claude Pro / Max (or ANTHROPIC_API_KEY)",
        cred_files=[HOME / ".claude" / ".credentials.json"], env_keys=["ANTHROPIC_API_KEY"],
        notes="A window opens: type /login if not prompted, then close it when done.",
    ),
    Provider(
        id="gemini", name="Google Gemini CLI", bin="gemini", npm_package="@google/gemini-cli", login_args=[],
        plan="Google account (free tier) / Gemini Code Assist / Google AI Pro",
        cred_files=[HOME / ".gemini" / "oauth_creds.json"], env_keys=["GEMINI_API_KEY"],
        notes="Same Google account as Antigravity (Antigravity itself has no scriptable CLI). "
              "Choose 'Login with Google' in the window.",
    ),
    Provider(
        id="qwen", name="Qwen Code", bin="qwen", npm_package="@qwen-code/qwen-code", login_args=[],
        plan="Qwen account (Qwen OAuth) or DashScope/OpenAI-compatible key",
        cred_files=[HOME / ".qwen" / "oauth_creds.json"], env_keys=["DASHSCOPE_API_KEY"],
        notes="Choose 'Qwen OAuth' in the window and sign in in the browser.",
    ),
]}


def npm_install(provider: Provider) -> subprocess.CompletedProcess:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("Node.js/npm not found - install Node.js LTS from https://nodejs.org")
    return subprocess.run([npm, "install", "-g", provider.npm_package], capture_output=True, text=True)

