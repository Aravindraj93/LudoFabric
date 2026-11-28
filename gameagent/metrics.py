"""Local measurements, never guessed subscription-token balances."""
import json
import os
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


def load_events(path: Path) -> list[dict]:
    events = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                if isinstance(event, dict): events.append(event)
            except ValueError: continue
    return events


def _count(value) -> int:
    return max(0, value) if isinstance(value, int) else 0


def token_counts(usage: dict | None) -> tuple[int, int, int]:
    """(input, output, cached) across OpenAI Responses and Chat Completions usage shapes."""
    if not isinstance(usage, dict):
        return 0, 0, 0
    inp = _count(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
    out = _count(usage.get("output_tokens", usage.get("completion_tokens", 0)))
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached = _count(details.get("cached_tokens", 0)) if isinstance(details, dict) else 0
    return inp, out, cached


def pricing() -> dict[str, list[float]]:
    """User-supplied USD per 1M tokens: LLM_PRICING_JSON={"model":[input,output,cached_input]}.
    No built-in prices: provider prices change and must not be invented."""
    try:
        data = json.loads(os.getenv("LLM_PRICING_JSON", "") or "{}")
        return {k: [float(x) for x in v] for k, v in data.items() if isinstance(v, list) and len(v) >= 2}
    except (ValueError, TypeError):
        return {}


def agent_of(name: str) -> str:
    """Call names are '<role>_<task>' / 'director_design' / 'fixer' ..."""
    return (name or "unknown").split("_", 1)[0]


def usage_totals(events: list[dict]) -> dict:
    prices = pricing()
    per_agent: dict[str, dict] = {}
    total = {"input": 0, "output": 0, "cached": 0, "retry_input": 0, "retry_output": 0}
    cost, priced, unpriced = 0.0, 0, 0
    for e in events:
        if e.get("kind") != "llm" or e.get("status") != "ok":
            continue
        inp, out, cached = token_counts(e.get("usage"))
        agent = per_agent.setdefault(agent_of(e.get("name", "")), {"calls": 0, "input": 0, "output": 0, "cached": 0})
        agent["calls"] += 1; agent["input"] += inp; agent["output"] += out; agent["cached"] += cached
        total["input"] += inp; total["output"] += out; total["cached"] += cached
        if e.get("retry"):
            total["retry_input"] += inp; total["retry_output"] += out
        price = prices.get(e.get("model") or "")
        if price and (inp or out):
            cached_price = price[2] if len(price) > 2 else price[0]
            cost += ((inp - cached) * price[0] + cached * cached_price + out * price[1]) / 1_000_000
            priced += 1
        elif inp or out:
            unpriced += 1
    return {"tokens": total, "per_agent": per_agent,
            "estimated_cost_usd": round(cost, 6) if priced else None,
            "priced_requests": priced, "unpriced_requests": unpriced}


def summarize(path: Path) -> dict:
    events = load_events(path)
    llm = [e for e in events if e.get("kind") == "llm"]
    unity = [e for e in events if e.get("kind") == "unity"]
    usages = [e["usage"] for e in llm if e.get("status") == "ok" and isinstance(e.get("usage"), dict)]
    count = _count
    return {**usage_totals(events),
            "retried_requests": sum(bool(e.get("retry")) for e in llm if e.get("status") == "ok"),
            "model_requests": sum(e.get("status") != "cache_hit" for e in llm),
            "cache_hits": sum(e.get("status") == "cache_hit" for e in llm),
            "failed_model_requests": sum(e.get("status") == "error" for e in llm),
            "model_seconds": round(sum(e.get("seconds", 0) for e in llm), 2),
            "unity_runs": len(unity), "unity_seconds": round(sum(e.get("seconds", 0) for e in unity), 2),
            "input_characters": sum(e.get("input_characters", 0) for e in llm if e.get("status") != "cache_hit"),
            "reported_input_tokens": sum(count(u.get("input_tokens", u.get("prompt_tokens", 0))) for u in usages),
            "reported_output_tokens": sum(count(u.get("output_tokens", u.get("completion_tokens", 0))) for u in usages),
            "requests_with_token_usage": len(usages),
            "token_usage": "API token counts are included when reported. CLI token usage is unavailable; character counts are not tokens."}
