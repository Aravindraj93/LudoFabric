"""Local measurements, never guessed subscription-token balances."""
import json
from pathlib import Path
import time


def record(path: Path | None, **values) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"timestamp": time.time(), **values}, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass  # Optional telemetry must never fail a game checkpoint.


def summarize(path: Path) -> dict:
    events = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                if isinstance(event, dict): events.append(event)
            except ValueError: continue
    llm = [e for e in events if e.get("kind") == "llm"]
    unity = [e for e in events if e.get("kind") == "unity"]
    usages = [e["usage"] for e in llm if e.get("status") == "ok" and isinstance(e.get("usage"), dict)]
    def count(value):
        return max(0, value) if isinstance(value, int) else 0
    return {"model_requests": sum(e.get("status") != "cache_hit" for e in llm),
            "cache_hits": sum(e.get("status") == "cache_hit" for e in llm),
            "failed_model_requests": sum(e.get("status") == "error" for e in llm),
            "model_seconds": round(sum(e.get("seconds", 0) for e in llm), 2),
            "unity_runs": len(unity), "unity_seconds": round(sum(e.get("seconds", 0) for e in unity), 2),
            "input_characters": sum(e.get("input_characters", 0) for e in llm if e.get("status") != "cache_hit"),
            "reported_input_tokens": sum(count(u.get("input_tokens", u.get("prompt_tokens", 0))) for u in usages),
            "reported_output_tokens": sum(count(u.get("output_tokens", u.get("completion_tokens", 0))) for u in usages),
            "requests_with_token_usage": len(usages),
            "token_usage": "API token counts are included when reported. CLI token usage is unavailable; character counts are not tokens."}
