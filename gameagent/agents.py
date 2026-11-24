"""The studio's agents. Each is a role-specific prompt + model + structured output."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import prompts
from .config import Config
from .llm import LLM
from .schemas import AgentOutput, Brief, GameDesign, Plan, Review, TaskSpec
from .unity.project import UnityProject

_DECL = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*(?:public|internal|protected)\s+(?!override\b)[^=;{(]*?"
    r"(?:class|struct|interface|enum|record)\s+\w+[^{]*|^\s*public\s+[^=;{]*\([^)]*\)|^\s*namespace\s+[\w.]+",
    re.M,
)


class ProjectContext:
    """Builds compact context for the models: file tree, public API index, selected full files."""

    def __init__(self, project: UnityProject, budget: int):
        self.project = project
        self.budget = budget

    def api_index(self) -> str:
        out = []
        for rel in self.project.code_files():
            if not rel.endswith(".cs"):
                continue
            src = self.project.read_file(rel) or ""
            decls = [m.group(0).strip() for m in _DECL.finditer(src)]
            out.append(f"{rel}\n    " + "\n    ".join(decls[:40]))
        return "\n".join(out) or "(no scripts yet)"

    def files(self, rels: list[str], budget: int | None = None) -> str:
        budget = budget or self.budget
        chunks, used = [], 0
        for rel in dict.fromkeys(rels):
            src = self.project.read_file(rel)
            if src is None:
                continue
            block = f"===== {rel} =====\n{src}\n"
            if used + len(block) > budget:
                chunks.append(f"===== {rel} ===== (omitted: context budget)\n")
                continue
            chunks.append(block)
            used += len(block)
        return "".join(chunks) or "(none)"


def _design_summary(design: GameDesign) -> str:
    return design.model_dump_json(indent=1)


class Studio:
    def __init__(self, cfg: Config, llm: LLM, project: UnityProject | None = None):
        self.cfg = cfg
        self.llm = llm
        self.project = project
        self.ctx = ProjectContext(project, cfg.context_char_budget) if project else None

    def attach(self, project: UnityProject) -> None:
        self.project = project
        self.ctx = ProjectContext(project, self.cfg.context_char_budget)

    # ---------------------------------------------------------------- director
    def design(self, request: str) -> GameDesign:
        return self.llm.structured(
            GameDesign, model=self.cfg.model_orchestrator, instructions=prompts.DIRECTOR, name="director_design",
            prompt=f"USER REQUEST:\n{request}\n\nInfer the full intent and produce the Game Design JSON.",
        )

    # ---------------------------------------------------------------- tech lead
    def plan(self, design: GameDesign) -> Plan:
        plan = self.llm.structured(
            Plan, model=self.cfg.model_orchestrator, instructions=prompts.TECH_LEAD, name="techlead_plan",
            prompt=f"GAME DESIGN:\n{_design_summary(design)}\n\nProduce architecture, asset requests and task graph JSON.",
        )
        ids = {t.id for t in plan.tasks}
        for t in plan.tasks:  # drop dangling dependencies
            t.depends_on = [d for d in t.depends_on if d in ids and d != t.id]
        return plan

    # ---------------------------------------------------------------- prompt engineer
    def brief(self, task: TaskSpec, design: GameDesign, plan: Plan, assets_text: str, done: list[TaskSpec]) -> Brief:
        done_txt = "\n".join(f"- [{t.role}] {t.title}: {', '.join(t.outputs)}" for t in done) or "(none)"
        prompt = f"""GAME DESIGN:
{_design_summary(design)}

ARCHITECTURE:
{plan.architecture.model_dump_json(indent=1)}

ASSET REGISTRY (downloaded, use these exact paths):
{assets_text}

COMPLETED TASKS:
{done_txt}

PROJECT PUBLIC API INDEX:
{self.ctx.api_index()}

TASK TO BRIEF:
{task.model_dump_json(indent=1)}

Write the specialist brief JSON."""
        return self.llm.structured(Brief, model=self.cfg.model_orchestrator, instructions=prompts.PROMPT_ENGINEER,
                                   prompt=prompt, name=f"brief_{task.id}")

    # ---------------------------------------------------------------- specialists
    def execute(self, role: str, brief: Brief, task: TaskSpec, extra_files: list[str] | None = None) -> AgentOutput:
        rels = list(brief.relevant_files) + list(task.outputs) + list(extra_files or [])
        prompt = f"""BRIEF:
{brief.prompt}

FILES YOU MUST CREATE/UPDATE: {json.dumps(task.outputs)}
ACCEPTANCE: {json.dumps(task.acceptance)}

PROJECT PUBLIC API INDEX:
{self.ctx.api_index()}

EXISTING FILE CONTENTS:
{self.ctx.files(rels)}

Return the JSON (files with COMPLETE contents, builder_methods, notes)."""
        system = prompts.SPECIALISTS.get(role, prompts.SPECIALISTS["gameplay"]) + prompts.UNITY_ENV
        return self.llm.structured(AgentOutput, model=self.cfg.model_coder, instructions=system, prompt=prompt,
                                   name=f"{role}_{task.id}")

    # ---------------------------------------------------------------- fixer
    def fix(self, error_text: str, hint_files: list[str] | None = None) -> AgentOutput:
        files = set(hint_files or [])
        for m in re.finditer(r"(Assets/[^\s:(]+\.(?:cs|shader|hlsl|asmdef))", error_text):
            files.add(m.group(1))
        prompt = f"""ERRORS:
{error_text}

PROJECT PUBLIC API INDEX:
{self.ctx.api_index()}

INVOLVED FILES:
{self.ctx.files(sorted(files))}

Fix all errors. Return JSON with complete corrected files."""
        return self.llm.structured(AgentOutput, model=self.cfg.model_coder, instructions=prompts.FIXER,
                                   prompt=prompt, name="fixer")

    # ---------------------------------------------------------------- art director
    def review(self, design: GameDesign, shots: list[Path]) -> Review:
        names = "\n".join(f"- image {i + 1}: {p.name}" for i, p in enumerate(shots))
        prompt = f"""GAME DESIGN:
{_design_summary(design)}

SCREENSHOTS (gameplay camera + overview per scene):
{names}

PROJECT PUBLIC API INDEX (to reference real classes in fixes):
{self.ctx.api_index()[:15000]}

Review and return JSON."""
        return self.llm.structured(Review, model=self.cfg.model_vision, instructions=prompts.ART_DIRECTOR,
                                   prompt=prompt, images=shots[:8], name="art_review")

