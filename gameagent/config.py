from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from .cli import find_cli

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass
class Config:
    # LLM backend: codex (ChatGPT Plus/Pro login via Codex CLI) | openai (API key, Responses API)
    #              | chat (any OpenAI-compatible Chat Completions endpoint: Gemini, Ollama, LM Studio, OpenRouter...)
    llm_backend: str = field(default_factory=lambda: _env("LLM_BACKEND", "codex").lower())
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str | None = field(default_factory=lambda: _env("OPENAI_BASE_URL") or None)
    # Empty model = backend default (for codex: the default model of your ChatGPT plan)
    model_orchestrator: str = field(default_factory=lambda: _env("MODEL_ORCHESTRATOR"))
    model_coder: str = field(default_factory=lambda: _env("MODEL_CODER"))
    model_vision: str = field(default_factory=lambda: _env("MODEL_VISION"))
    json_mode: bool = field(default_factory=lambda: _env("LLM_JSON_MODE", "1") == "1")

    # Codex CLI backend
    codex_bin: str = field(default_factory=lambda: find_cli("codex"))
    codex_reasoning: str = field(default_factory=lambda: _env("CODEX_REASONING_EFFORT"))  # low|medium|high
    codex_timeout: int = field(default_factory=lambda: _int("CODEX_TIMEOUT_SECONDS", 1500))
    codex_limit_wait_minutes: int = field(default_factory=lambda: _int("CODEX_LIMIT_WAIT_MINUTES", 300))
    codex_wait_on_limit: bool = field(default_factory=lambda: _env("CODEX_WAIT_ON_LIMIT", "0") == "1")
    codex_limit_retry_minutes: int = field(default_factory=lambda: max(1, _int("CODEX_LIMIT_RETRY_MINUTES", 300)))

    # Unity
    unity_editor: str = field(default_factory=lambda: _env("UNITY_EDITOR"))
    unity_timeout: int = field(default_factory=lambda: _int("UNITY_TIMEOUT_SECONDS", 1800))
    gltfast_version: str = field(default_factory=lambda: _env("GLTFAST_VERSION", "6.8.0"))

    # Assets
    sketchfab_token: str = field(default_factory=lambda: _env("SKETCHFAB_API_TOKEN"))
    texture_resolution: str = field(default_factory=lambda: _env("ASSET_TEXTURE_RESOLUTION", "1k"))
    max_faces: int = field(default_factory=lambda: _int("ASSET_MAX_FACES", 150000))

    # Behaviour
    max_fix_attempts: int = field(default_factory=lambda: _int("MAX_FIX_ATTEMPTS", 4))
    polish_rounds: int = field(default_factory=lambda: _int("POLISH_ROUNDS", 2))
    context_char_budget: int = field(default_factory=lambda: _int("CONTEXT_CHAR_BUDGET", 90000))

    def validate(self, need_unity: bool = True) -> None:
        errors = []
        if self.llm_backend == "codex":
            if not self.codex_bin:
                errors.append("Codex CLI not found. Install it: npm install -g @openai/codex")
            else:
                st = subprocess.run([self.codex_bin, "login", "status"], capture_output=True, text=True)
                if st.returncode != 0 or "not logged in" in (st.stdout + st.stderr).lower():
                    errors.append("Codex CLI is not logged in. Run:  codex login   (choose 'Sign in with ChatGPT')")
        elif self.llm_backend in ("claude", "gemini", "qwen"):
            from .providers import PROVIDERS
            p = PROVIDERS[self.llm_backend]
            if not p.installed():
                errors.append(f"{p.name} CLI not installed: npm install -g {p.npm_package}")
            elif p.logged_in() is False:
                errors.append(f"{p.name} is not logged in: run '{p.bin}' once in a terminal and sign in")
        elif self.llm_backend in ("openai", "chat"):
            key = self.openai_api_key
            local = bool(self.openai_base_url) and any(h in self.openai_base_url for h in ("localhost", "127.0.0.1"))
            if not local and (not key or key.startswith("sk-...") or len(key) < 20):
                errors.append("OPENAI_API_KEY is missing or still the placeholder - put your real key in .env")
            if self.llm_backend == "chat" and not self.model_orchestrator:
                errors.append("LLM_BACKEND=chat requires MODEL_ORCHESTRATOR / MODEL_CODER / MODEL_VISION in .env")
        else:
            errors.append(f"Unknown LLM_BACKEND '{self.llm_backend}' (use codex, claude, gemini, qwen, openai or chat)")
        if need_unity and (not self.unity_editor or not Path(self.unity_editor).exists()):
            errors.append(f"UNITY_EDITOR does not point to Unity.exe: '{self.unity_editor}'")
        if errors:
            raise SystemExit("Configuration error:\n  - " + "\n  - ".join(errors))

