"""Deterministic failure classification, error fingerprinting and retry policy.

No LLM calls: these decide *whether* another (paid) attempt is justified.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

FailureClass = Literal["budget", "usage_limit", "cancelled", "configuration", "license", "protected_code",
                       "package", "compile", "builder", "test", "build", "runtime", "visual", "llm_output",
                       "transient", "concurrent_edit", "dependency", "unknown"]

# Ordered: first match wins. Patterns are matched against "<ExceptionType>: <message>".
_RULES: list[tuple[FailureClass, str]] = [
    ("budget", r"BudgetReached|budget reached"),
    ("usage_limit", r"UsageLimitReached|usage limit"),
    ("cancelled", r"JobCancelled|cancel(l)?ed by user"),
    ("license", r"no valid Unity (Editor )?license|no valid Editor license"),
    ("configuration", r"Configuration error|not logged in|not installed|UNITY_EDITOR|rejected the selected model|model_not_found"),
    ("protected_code", r"Protected LudoFabric integration code"),
    ("package", r"package compatibility"),
    ("concurrent_edit", r"changed while the specialist was working|Project files changed during generation|open in the Editor"),
    ("dependency", r"dependencies cannot be satisfied|dependency cycle|Unknown dependency"),
    ("compile", r"Compilation still fails|same compiler errors|clean compile|error CS\d{4}"),
    ("builder", r"[Ss]cene builders? failed"),
    ("test", r"(EditMode|PlayMode) tests still fail"),
    ("visual", r"Screenshot capture failed|visual quality gate"),
    ("build", r"Player build|smoke test failed"),
    ("llm_output", r"failed to return valid|No JSON object"),
    ("transient", r"timed out|timeout|Connection|connection|temporar|503|502|504|transport closed|reset by peer"),
    ("runtime", r"Exception|runtime errors"),
]

# Failures where repeating the same step without new input cannot help.
NON_RETRYABLE = {"budget", "usage_limit", "cancelled", "configuration", "license", "protected_code", "package"}


def classify(exc: BaseException | str) -> FailureClass:
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    for kind, pattern in _RULES:
        if re.search(pattern, text, re.I):
            return kind
    return "unknown"


_VOLATILE = [
    (re.compile(r"\(\d+,\d+\)"), "(L,C)"),                       # line/column positions
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "0x?"),                  # addresses
    (re.compile(r"\d{2}:\d{2}:\d{2}"), "T"),                     # times
    (re.compile(r"[A-Za-z]:\\[^\s'\"]+|/tmp/[^\s'\"]+"), "PATH"),  # absolute temp paths
    (re.compile(r"\b\d{5,}\b"), "N"),                            # ids, ns timestamps
    (re.compile(r"\s+"), " "),
]


def fingerprint(text: str) -> str:
    """Stable identity for 'the same failure', ignoring positions, timestamps and absolute paths."""
    norm = text or ""
    for pattern, repl in _VOLATILE:
        norm = pattern.sub(repl, norm)
    return hashlib.sha256(norm.strip()[:4000].encode()).hexdigest()[:16]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3          # per phase
    base_delay: float = 2.0        # seconds, transient failures only
    max_delay: float = 120.0

    def backoff(self, attempt: int) -> float:
        return min(self.max_delay, self.base_delay * 2 ** max(0, attempt - 1))


@dataclass(frozen=True)
class Decision:
    action: Literal["retry", "blocked", "paused"]
    reason: str
    delay: float = 0.0


def decide(kind: FailureClass, fp: str, attempts: int, seen_fingerprints: list[str], policy: RetryPolicy) -> Decision:
    """Bounded, loop-free recovery decision.

    - budget/limit/cancel => paused (resumable, state preserved)
    - configuration/licence/protected/package => blocked (needs a human or tool fix)
    - same fingerprint seen before => blocked (no repeating an approach that already failed)
    - otherwise retry until max_attempts, with backoff for transient failures.
    """
    if kind in ("budget", "usage_limit", "cancelled"):
        return Decision("paused", f"{kind}: progress saved; resume later")
    if kind in NON_RETRYABLE:
        return Decision("blocked", f"{kind} failure is not fixable by retrying generated code")
    if fp in seen_fingerprints:
        return Decision("blocked", "identical failure repeated after a retry; stopping this approach")
    if attempts >= policy.max_attempts:
        return Decision("blocked", f"retry limit {policy.max_attempts} reached")
    return Decision("retry", f"{kind} failure; attempt {attempts + 1}/{policy.max_attempts}",
                    policy.backoff(attempts + 1) if kind == "transient" else 0.0)
