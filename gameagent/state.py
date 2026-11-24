"""Persistent, resumable pipeline state (stored in <project>.agent/state.json)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from .schemas import GameDesign, Plan


def atomic_json(path: Path, value: dict) -> None:
    """Keep the previous checkpoint intact if a process stops during a write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


class State(BaseModel):
    request: str = ""
    design: GameDesign | None = None
    plan: Plan | None = None
    phase: str = "new"  # new -> designed -> planned -> project -> assets -> built -> polished -> shipped
    builder_methods: list[str] = Field(default_factory=list)
    polish_round: int = 0
    reviews: list[dict] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "State":
        if path.exists():
            return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
        return cls()

    def save(self, path: Path) -> None:
        atomic_json(path, self.model_dump(mode="json"))

    def add_builders(self, methods: list[str]) -> None:
        for m in methods:
            m = m.strip()
            if m and m not in self.builder_methods:
                self.builder_methods.append(m)

