"""Structured artefacts passed between agents."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

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


class StageDesign(_Base):
    id: str
    title: str
    objective: str
    new_challenge: str
    difficulty: str
    unlock_rule: str = "Complete the previous stage"


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
    scope: Literal["prototype", "vertical_slice", "campaign"] = "vertical_slice"
    progression: list[StageDesign] = Field(default_factory=list, description="Distinct stages and their difficulty progression")
    replay_incentives: list[str] = Field(default_factory=list)
    asset_policy: Literal["procedural_only", "free_assets"] = "free_assets"

    @model_validator(mode="after")
    def _campaign_has_progression(self):
        if self.scope == "campaign":
            if len(self.progression) < 2 or len({s.id for s in self.progression}) != len(self.progression):
                raise ValueError("Campaign scope requires at least two distinct stages with unique IDs.")
        return self


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


class CodeContract(_Base):
    path: str
    owner: Role
    public_api: list[str]
    consumers: list[str] = Field(default_factory=list)


class Plan(_Base):
    architecture: Architecture
    assets: list[AssetRequest]
    tasks: list[TaskSpec]
    contracts: list[CodeContract] = Field(default_factory=list, description="Shared signatures agreed before specialist implementation")

    @model_validator(mode="after")
    def _validate_graph(self):
        by_id = {t.id: t for t in self.tasks}
        if len(by_id) != len(self.tasks): raise ValueError("Task IDs must be unique.")
        visiting, visited = set(), set()
        def visit(key):
            if key in visiting: raise ValueError(f"Task dependency cycle at {key}")
            if key in visited: return
            if key not in by_id: raise ValueError(f"Unknown dependency {key}")
            visiting.add(key)
            for dep in by_id[key].depends_on: visit(dep)
            visiting.remove(key); visited.add(key)
        for key in by_id: visit(key)
        paths = [c.path for c in self.contracts]
        if len(paths) != len(set(paths)): raise ValueError("Each shared contract needs one owning file/role.")
        return self


# ---------- Specialist output ----------
def _safe_output_path(value):
    if not isinstance(value, str): raise ValueError("Asset paths must be strings.")
    value = value.replace("\\", "/")
    if (not value.startswith("Assets/") or ".." in value.split("/") or ":" in value or "\0" in value or
        value.startswith(("Assets/GameAgentBridge", "Assets/LudoFabricStarter"))):
        raise ValueError("Write only to agent-owned Assets/ paths, not the protected bridge/foundation.")
    return value


class FileWrite(_Base):
    path: str = Field(pattern=r"^Assets/", description="Agent-owned project-relative path under Assets/")
    content: str
    _validate_path = field_validator("path", mode="before")(_safe_output_path)


class FilePatch(_Base):
    path: str = Field(pattern=r"^Assets/")
    old: str = Field(min_length=1, description="Exact non-empty text occurring once in the current file")
    new: str
    _validate_path = field_validator("path", mode="before")(_safe_output_path)


class AgentOutput(_Base):
    files: list[FileWrite] = Field(default_factory=list)
    patches: list[FilePatch] = Field(default_factory=list, description="Small exact-match edits to existing files")
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

