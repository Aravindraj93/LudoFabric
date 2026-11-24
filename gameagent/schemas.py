"""Structured artefacts passed between agents."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["gameplay", "graphics", "vfx", "animation", "ui", "level", "audio", "tests"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data):
        """LLMs often return a list where a string is expected (or vice versa) - coerce instead of failing."""
        if isinstance(data, dict):
            data = dict(data)
            for name, field in cls.model_fields.items():
                v = data.get(name)
                if field.annotation is str and isinstance(v, list):
                    data[name] = "\n".join(str(x) for x in v)
                elif field.annotation == list[str] and isinstance(v, str):
                    data[name] = [v]
                elif field.annotation == list[str] and isinstance(v, list):
                    data[name] = [x if isinstance(x, str) else json.dumps(x) for x in v]
        return data


# ---------- Director (intent) ----------
class ArtDirection(_Base):
    style: str = Field(description="e.g. stylized low-poly, semi-realistic PBR, toon-shaded")
    palette: list[str] = Field(default_factory=list, description="hex colours")
    lighting: str = ""
    post_processing: str = ""
    camera_feel: str = ""
    references: list[str] = Field(default_factory=list)


class GameDesign(_Base):
    title: str
    genre: str
    pitch: str
    platform: str = "Windows PC"
    perspective: str = Field(description="third-person / first-person / top-down / side-scroller / ...")
    core_loop: str
    mechanics: list[str]
    controls: list[str]
    win_lose: str
    levels: list[str]
    ui_screens: list[str]
    art: ArtDirection
    vfx: list[str] = Field(description="every visual effect with its trigger and look")
    animation: list[str] = Field(description="every animated thing and how (rig/procedural/tween)")
    audio: list[str] = Field(default_factory=list)
    game_feel: list[str] = Field(default_factory=list, description="juice: hitstop, shake, squash/stretch ...")
    assets_needed: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list, description="decisions made without asking the user")


# ---------- Tech lead (plan) ----------
class SystemSpec(_Base):
    name: str
    responsibility: str
    scripts: list[str] = Field(default_factory=list, description="Assets/... .cs paths")


class AssetRequest(_Base):
    key: str = Field(description="unique snake_case id used by code, e.g. hero_character")
    kind: Literal["model", "hdri", "texture"] = "model"
    query: str = Field(description="short search query, e.g. 'wooden barrel'")
    alternatives: list[str] = Field(default_factory=list,
                                    description="1-3 simpler fallback queries, e.g. ['barrel', 'cask']")
    purpose: str = ""
    needs_rig: bool = Field(False, description="true if the model must be skinned/animated")
    preferred_source: Literal["polyhaven", "sketchfab", "any"] = "any"


class TaskSpec(_Base):
    id: str
    role: Role
    title: str
    description: str
    outputs: list[str] = Field(default_factory=list, description="files this task creates/edits")
    depends_on: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    status: Literal["pending", "done", "failed"] = "pending"


class Architecture(_Base):
    root_namespace: str
    overview: str
    systems: list[SystemSpec]
    conventions: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list, description="Assets/Scenes/*.unity in build order")


class Plan(_Base):
    architecture: Architecture
    assets: list[AssetRequest]
    tasks: list[TaskSpec]


# ---------- Specialist output ----------
class FileWrite(_Base):
    path: str = Field(description="project-relative path, must start with Assets/")
    content: str


class AgentOutput(_Base):
    files: list[FileWrite] = Field(default_factory=list)
    delete: list[str] = Field(default_factory=list)
    builder_methods: list[str] = Field(
        default_factory=list,
        description="fully-qualified static editor methods that (re)build scenes/assets, e.g. Game.Editor.LevelBuilder.Build",
    )
    notes: str = ""


class Brief(_Base):
    prompt: str = Field(description="the complete, precise engineering brief for the specialist")
    relevant_files: list[str] = Field(default_factory=list, description="existing files the specialist must read")


# ---------- Art director (review) ----------
class ReviewIssue(_Base):
    severity: Literal["critical", "major", "minor"]
    area: Role
    problem: str
    fix: str


class Review(_Base):
    score: int = Field(ge=0, le=10)
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)

