"""LLM access with pluggable backends.

- codex : OpenAI Codex CLI (`codex exec`) signed in with a ChatGPT Plus/Pro account - no API key needed.
- openai: OpenAI Responses API with an API key.
- chat  : any OpenAI-compatible Chat Completions endpoint (Gemini free tier, Ollama, LM Studio, OpenRouter, ...).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Type, TypeVar, Callable

from pydantic import BaseModel, ValidationError

from .config import Config
from .ui import console
from .state import atomic_json
from .metrics import record

T = TypeVar("T", bound=BaseModel)

LIMIT_PATTERNS = re.compile(r"usage limit|rate limit|too many requests|429|quota", re.I)


class FatalLLMError(SystemExit):
    pass


class BudgetReached(FatalLLMError):
    pass


class UsageLimitReached(FatalLLMError):
    """A resumable pause, rather than a failed production task."""
    def __init__(self, message: str, retry_at: float):
        super().__init__(message)
        self.retry_at = retry_at


def _cli_error_summary(output: str) -> str:
    """Extract the provider error without echoing the CLI's copy of the entire game prompt."""
    for line in reversed(output.splitlines()):
        if not line.startswith("ERROR:"):
            continue
        try:
            payload = json.loads(line.partition(":")[2].strip())
            error = payload.get("error", payload)
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])[:1500]
        except (ValueError, TypeError, AttributeError):
            pass
    return output.strip()[-1500:]


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("No JSON object found in model output")


# --------------------------------------------------------------------------- backends
class _OpenAIResponses:
    def __init__(self, cfg: Config):
        from openai import OpenAI
        self.cfg = cfg
        self.client = OpenAI(api_key=cfg.openai_api_key, base_url=cfg.openai_base_url, timeout=900, max_retries=0)

    def call(self, model: str, instructions: str, prompt: str, images: list[Path], json_mode: bool) -> str:
        from openai import AuthenticationError, BadRequestError, NotFoundError, PermissionDeniedError
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(Path(img).read_bytes()).decode()
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}"})
        kwargs: dict[str, Any] = {"model": model or "gpt-5", "instructions": instructions,
                                  "input": [{"role": "user", "content": content}]}
        if json_mode and self.cfg.json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}
        try:
            response = self.client.responses.create(**kwargs)
            self.last_usage = response.usage.model_dump() if response.usage else None
            return response.output_text or ""
        except (AuthenticationError, PermissionDeniedError, NotFoundError, BadRequestError) as exc:
            raise FatalLLMError(f"LLM request rejected ({exc.__class__.__name__}): {exc}")


class _ChatCompletions:
    def __init__(self, cfg: Config):
        from openai import OpenAI
        self.cfg = cfg
        self.client = OpenAI(api_key=cfg.openai_api_key or "local", base_url=cfg.openai_base_url,
                             timeout=900, max_retries=0)

    def call(self, model: str, instructions: str, prompt: str, images: list[Path], json_mode: bool) -> str:
        from openai import AuthenticationError, BadRequestError, NotFoundError, PermissionDeniedError
        user: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(Path(img).read_bytes()).decode()
            user.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        kwargs: dict[str, Any] = {"model": model, "messages": [
            {"role": "system", "content": instructions}, {"role": "user", "content": user}]}
        if json_mode and self.cfg.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except (AuthenticationError, PermissionDeniedError, NotFoundError, BadRequestError) as exc:
            raise FatalLLMError(f"LLM request rejected ({exc.__class__.__name__}): {exc}")
        self.last_usage = resp.usage.model_dump() if resp.usage else None
        return resp.choices[0].message.content or ""


class _CodexCLI:
    """Runs `codex exec` non-interactively, read-only, in an empty temp dir; the final message is the answer."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.workdir = Path(tempfile.gettempdir()) / "gameagent_codex"
        self.workdir.mkdir(exist_ok=True)

    def call(self, model: str, instructions: str, prompt: str, images: list[Path], json_mode: bool) -> str:
        full = (
            "You are being used as a pure text-generation function inside an automated pipeline.\n"
            "Do NOT run shell commands, do NOT read or edit files. Reply ONLY with the final answer"
            + (" as a single JSON object (no markdown fences, no commentary)" if json_mode else "")
            + ".\n\n=== ROLE / SYSTEM INSTRUCTIONS ===\n" + instructions
            + "\n\n=== TASK ===\n" + prompt
        )
        out_file = self.workdir / f"answer_{time.time_ns()}.txt"
        cmd = [self.cfg.codex_bin, "exec", "-", "--skip-git-repo-check", "--ephemeral",
               "--ignore-user-config",
               "--sandbox", "read-only", "--color", "never", "-C", str(self.workdir), "-o", str(out_file)]
        if model:
            cmd += ["-m", model]
        if self.cfg.codex_reasoning:
            cmd += ["-c", f'model_reasoning_effort="{self.cfg.codex_reasoning}"']
        for img in images:
            cmd.append(f"--image={Path(img).resolve()}")
        try:
            proc = subprocess.run(cmd, input=full, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=self.cfg.codex_timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"codex exec timed out after {self.cfg.codex_timeout}s")
        answer = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
        out_file.unlink(missing_ok=True)
        if proc.returncode != 0 or not answer.strip():
            err = (proc.stderr or "") + "\n" + (proc.stdout or "")
            diagnostic = _cli_error_summary(err)
            if "login" in diagnostic.lower() and ("not logged" in diagnostic.lower() or "unauthorized" in diagnostic.lower()):
                raise FatalLLMError("Codex CLI is not logged in. Run: codex login")
            if LIMIT_PATTERNS.search(diagnostic):
                raise _UsageLimit(diagnostic)
            if re.search(r'model_not_found|model .{0,100}(?:not supported|not found|not available)|invalid_request_error', diagnostic, re.I):
                raise FatalLLMError(diagnostic + " Clear the job Model override and role model "
                                    "fields to use the CLI default, or test another supported model in Setup.")
            if "unexpected argument '--ignore-user-config'" in err:
                raise FatalLLMError("Update Codex CLI: this integration requires --ignore-user-config "
                                    "to isolate model and MCP settings.\n" + err[-2000:])
            raise RuntimeError(f"codex exec failed (rc={proc.returncode}): {err.strip()[-12000:]}")
        return answer


class _UsageLimit(RuntimeError):
    pass


class _AgentCLI:
    """Claude Code (`claude -p`), Gemini CLI and Qwen Code (`gemini`/`qwen` with piped stdin) in headless mode.
    Each uses the user's own login stored by that CLI. Images are copied to the work dir and referenced by path."""

    def __init__(self, cfg: Config, provider_id: str):
        from .providers import PROVIDERS
        self.cfg = cfg
        self.provider = PROVIDERS[provider_id]
        self.workdir = Path(tempfile.gettempdir()) / f"gameagent_{provider_id}"
        self.workdir.mkdir(exist_ok=True)

    def call(self, model: str, instructions: str, prompt: str, images: list[Path], json_mode: bool) -> str:
        exe = self.provider.path()
        if not exe:
            raise FatalLLMError(f"{self.provider.name} CLI not installed: npm install -g {self.provider.npm_package}")
        img_refs = []
        for i, img in enumerate(images):
            dst = self.workdir / f"shot_{i}{Path(img).suffix}"
            dst.write_bytes(Path(img).read_bytes())
            img_refs.append(dst)
        full = (
            "You are being used as a pure text-generation function inside an automated pipeline.\n"
            "Do NOT run shell commands and do NOT edit files"
            + (" (you MAY read the attached image files listed below)" if img_refs else "")
            + ". Reply ONLY with the final answer"
            + (" as a single JSON object (no markdown fences, no commentary)" if json_mode else "")
            + ".\n\n=== ROLE / SYSTEM INSTRUCTIONS ===\n" + instructions + "\n\n=== TASK ===\n" + prompt
        )
        pid = self.provider.id
        if img_refs:
            if pid == "claude":
                full += "\n\nATTACHED IMAGES (read them):\n" + "\n".join(str(p) for p in img_refs)
            else:  # gemini / qwen use @path file injection
                full += "\n\nATTACHED IMAGES: " + " ".join(f"@{p.name}" for p in img_refs)
        cmd = [exe, "-p", "--output-format", "text"] if pid == "claude" else [exe]
        if model:
            cmd += ["--model", model] if pid == "claude" else ["-m", model]
        try:
            proc = subprocess.run(cmd, input=full, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=self.cfg.codex_timeout, cwd=self.workdir)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{self.provider.name} timed out after {self.cfg.codex_timeout}s")
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "")[-1500:]
        if proc.returncode != 0 or not out:
            low = (err + out).lower()
            if re.search(r'model_not_found|unknown model|invalid model|model .{0,100}(?:not supported|not found|not available)', low):
                raise FatalLLMError(f"{self.provider.name} rejected the selected model. Choose a model available "
                                    f"to this account and test it in Setup.\n{(err or out)[-1500:]}")
            if any(s in low for s in ("login", "authenticat", "unauthorized", "oauth")) and "limit" not in low:
                raise FatalLLMError(f"{self.provider.name} is not logged in. Log in from the dashboard or run "
                                    f"'{self.provider.bin}' once in a terminal.\n{err[-400:]}")
            if LIMIT_PATTERNS.search(err + out):
                raise _UsageLimit(err or out)
            raise RuntimeError(f"{self.provider.name} failed (rc={proc.returncode}): {(err or out)[-800:]}")
        return out


# --------------------------------------------------------------------------- facade
class LLM:
    def __init__(self, cfg: Config, log_dir: Path | None = None):
        self.cfg = cfg
        if cfg.llm_backend in ("claude", "gemini", "qwen"):
            self.backend = _AgentCLI(cfg, cfg.llm_backend)
        else:
            self.backend = {"codex": _CodexCLI, "openai": _OpenAIResponses, "chat": _ChatCompletions}[cfg.llm_backend](cfg)
        self.log_dir = log_dir
        self._calls = 0
        self._requests = 0
        self._started = time.monotonic()

    def set_log_dir(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)

    def _log(self, name: str, instructions: str, prompt: str, output: str) -> None:
        if not self.log_dir:
            return
        self._calls += 1
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)[:60]
        (self.log_dir / f"{time.time_ns()}_{self._calls:04d}_{safe}.md").write_text(
            f"# {name}\n\n## System\n\n{instructions}\n\n## Prompt\n\n{prompt}\n\n## Output\n\n{output}\n",
            encoding="utf-8")

    def complete(self, *, model: str, instructions: str, prompt: str, images: list[Path] | None = None,
                 json_mode: bool = False, name: str = "call", retries: int = 4) -> str:
        # Cache exact requests so a restart after receiving an answer need not spend tokens again.
        image_paths = [Path(p).resolve() for p in (images or [])]
        request = dict(backend=self.cfg.llm_backend, model=model, instructions=instructions,
                       prompt=prompt, images=[str(p) for p in image_paths], json_mode=json_mode,
                       endpoint=self.cfg.openai_base_url, reasoning=self.cfg.codex_reasoning,
                       image_hashes=[hashlib.sha256(p.read_bytes()).hexdigest() for p in image_paths])
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        cache = self.log_dir / "responses" / f"{key}.json" if self.log_dir else None
        metrics = self.log_dir.parent / "metrics.jsonl" if self.log_dir else None
        pending = self.log_dir.parent / "pending_call.json" if self.log_dir else None
        if cache and cache.exists():
            record(metrics, kind="llm", name=name, status="cache_hit", seconds=0, model=model)
            return json.loads(cache.read_text(encoding="utf-8"))["output"]
        if pending:
            atomic_json(pending, dict(name=name, key=key, status="running", request=request))
        delay, waited_for_limit, attempt = 10.0, 0, 0
        label = model or f"{self.cfg.llm_backend} default model"
        while True:
            if ((self.cfg.max_llm_calls > 0 and self._requests >= self.cfg.max_llm_calls) or
                (self.cfg.max_job_seconds > 0 and time.monotonic() - self._started >= self.cfg.max_job_seconds)):
                if pending:
                    atomic_json(pending, dict(name=name, key=key, status="budget_paused", request=request))
                raise BudgetReached("Configured AI request/time budget reached. Progress saved; resume to start a new budget.")
            attempt += 1
            self._requests += 1
            started = time.monotonic()
            try:
                t0 = time.time()
                console.print(f"  ... {name} -> {label} (thinking; can take 1-5 min)")
                out = self.backend.call(model, instructions, prompt, list(images or []), json_mode)
                console.print(f"[dim]  {name}: answered in {time.time() - t0:.0f}s ({len(out)} chars)[/]")
                self._log(name, instructions, prompt, out)
                record(metrics, kind="llm", name=name, status="ok", model=model,
                       seconds=round(time.monotonic() - started, 3), input_characters=len(instructions) + len(prompt),
                       output_characters=len(out), backend=self.cfg.llm_backend,
                       usage=getattr(self.backend, "last_usage", None) if isinstance(getattr(self.backend, "last_usage", None), dict) else None)
                if cache:
                    atomic_json(cache, {"output": out})
                if pending:
                    pending.unlink(missing_ok=True)
                return out
            except FatalLLMError as exc:
                record(metrics, kind="llm", name=name, status="error", seconds=round(time.monotonic() - started, 3))
                self._log(name + "_error", instructions, prompt, str(exc))
                raise
            except _UsageLimit as exc:
                record(metrics, kind="llm", name=name, status="limit", seconds=round(time.monotonic() - started, 3))
                retry_at = time.time() + self.cfg.codex_limit_retry_minutes * 60
                if pending:
                    atomic_json(pending, dict(name=name, key=key, status="waiting_for_limit",
                                             retry_at=retry_at, request=request, error=str(exc)[-12000:]))
                if not self.cfg.codex_wait_on_limit:
                    raise UsageLimitReached(
                        "AI usage limit reached. Progress is saved. Resume this project after your allowance resets.",
                        retry_at) from exc
                # ChatGPT plan limits reset every few hours: wait instead of failing the whole run
                if waited_for_limit >= self.cfg.codex_limit_wait_minutes:
                    raise UsageLimitReached(f"Usage limit not reset after {waited_for_limit} min. "
                                            "Resume this project later.", retry_at) from exc
                console.print(f"[yellow]Plan usage limit reached; waiting 15 min "
                              f"({waited_for_limit}/{self.cfg.codex_limit_wait_minutes} min waited). "
                              f"You can also stop now (Ctrl+C) and 'resume' later.[/]")
                time.sleep(15 * 60)
                waited_for_limit += 15
            except Exception as exc:
                record(metrics, kind="llm", name=name, status="error", seconds=round(time.monotonic() - started, 3))
                self._log(name + "_error", instructions, prompt, str(exc))
                if attempt >= retries:
                    raise
                msg = str(exc)
                wait = 60.0 if LIMIT_PATTERNS.search(msg) else delay
                console.print(f"[yellow]LLM error ({exc.__class__.__name__}: {msg[:300]}); "
                              f"retry {attempt}/{retries} in {wait:.0f}s[/]")
                time.sleep(wait)
                delay *= 2

    def structured(self, schema: Type[T], *, model: str, instructions: str, prompt: str,
                   images: list[Path] | None = None, name: str = "structured", retries: int = 3,
                   validate: Callable[[T], None] | None = None) -> T:
        """Ask for JSON matching a pydantic schema; re-prompts with the validation error on failure."""
        schema_hint = json.dumps(schema.model_json_schema(), indent=1)
        full_prompt = f"{prompt}\n\nRespond ONLY with a single JSON object that matches this JSON schema:\n{schema_hint}"
        last_err = ""
        for attempt in range(1, retries + 1):
            p = full_prompt if not last_err else (
                f"{full_prompt}\n\nYour previous answer was invalid: {last_err}\nReturn corrected JSON only.")
            raw = self.complete(model=model, instructions=instructions, prompt=p, images=images,
                                json_mode=True, name=name)
            try:
                result = schema.model_validate(_extract_json(raw))
                if validate: validate(result)
                return result
            except (ValueError, ValidationError) as exc:
                last_err = str(exc)[:2000]
                console.print(f"[yellow]Invalid model output for {name} (attempt {attempt}): {last_err[:200]}[/]")
        raise RuntimeError(f"Model failed to return valid {schema.__name__}: {last_err}")

