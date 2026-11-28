"""The studio's agents. Each is a role-specific prompt + model + structured output."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import prompts
from .config import Config
from .llm import LLM, FatalLLMError
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

    def api_index(self, rels: list[str] | None = None, budget: int = 12000) -> str:
        out, used = [], 0
        for rel in (list(dict.fromkeys(rels)) if rels is not None else self.project.code_files()):
            if not rel.endswith(".cs"):
                continue
            src = self.project.read_file(rel) or ""
            decls = [m.group(0).strip() for m in _DECL.finditer(src)]
            block = f"{rel}\n    " + "\n    ".join(decls[:40])
            if used + len(block) > budget:
                break
            out.append(block)
            used += len(block)
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


def _design_summary(design: GameDesign, role: str | None = None) -> str:
    if role is None:
        return design.model_dump_json(indent=1)
    data = design.model_dump(mode="json", include={"title", "genre", "platform", "perspective", "core_loop",
        "mechanics", "controls", "win_lose", "levels", "scope", "progression", "replay_incentives", "asset_policy"})
    data["art"] = {"style": design.art.style, "palette": design.art.palette}
    for section in {"graphics": ["art"], "vfx": ["vfx"], "animation": ["animation", "game_feel"],
                    "ui": ["ui_screens"], "audio": ["audio"]}.get(role, []):
        value = getattr(design, section)
        data[section] = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class Studio:
    def __init__(self, cfg: Config, llm: LLM, project: UnityProject | None = None):
        self.cfg = cfg
        self.llm = llm
        self.project = project
        self.ctx = ProjectContext(project, cfg.context_char_budget) if project else None
        self.revision_context = ""
        # Replaces the LudoFabric URP/SceneKit environment for projects not created by LudoFabric.
        self.env_override: str | None = None
        self.platform_note: str = ""  # target-platform constraints appended to specialist prompts
        self.uses_assets: bool = False  # True when the project has downloaded free assets to place

    def attach(self, project: UnityProject) -> None:
        self.project = project
        self.ctx = ProjectContext(project, self.cfg.context_char_budget)

    # ---------------------------------------------------------------- director
    def design(self, request: str, targets: list[str] | None = None) -> GameDesign:
        from .platforms import design_brief
        from .assets.manager import sources_note
        return self.llm.structured(
            GameDesign, model=self.cfg.model_orchestrator, instructions=prompts.DIRECTOR, name="director_design",
            prompt=f"USER REQUEST:\n{request}{design_brief(targets) if targets else ''}\n\n"
                   f"{self.revision_context}\n\n{sources_note(self.cfg)}\nRespect requests forbidding downloads. "
                   "Infer the full intent and produce the Game Design JSON.",
        )

    # ---------------------------------------------------------------- tech lead
    def plan(self, design: GameDesign) -> Plan:
        def validate_revision(plan):
            if not self.revision_context:
                return
            if not plan.tasks:
                raise ValueError("Revision plans require implementation tasks, not only a proposal.")
            for task in plan.tasks:
                if not task.outputs:
                    raise ValueError(f"Revision task {task.id} must name Assets outputs. Host inspection, backup, copy and build are already handled; do not plan them as standalone tasks.")
                for path in task.outputs:
                    self.project.safe_path(path)
        from .assets.manager import sources_note
        assets_note = sources_note(self.cfg) if design.asset_policy != "procedural_only" else ""
        plan = self.llm.structured(
            Plan, model=self.cfg.model_orchestrator, instructions=prompts.TECH_LEAD, name="techlead_plan",
            prompt=f"GAME DESIGN:\n{_design_summary(design)}{assets_note}\n\n{self.revision_context}\n\n"
                   "Produce architecture, asset requests and task graph JSON.",
            validate=validate_revision,
        )
        ids = {t.id for t in plan.tasks}
        for t in plan.tasks:  # drop dangling dependencies
            t.depends_on = [d for d in t.depends_on if d in ids and d != t.id]
            t.status = "pending"
        if design.asset_policy == "procedural_only":
            plan.assets = []
        return plan

    # ---------------------------------------------------------------- prompt engineer
    def brief(self, task: TaskSpec, design: GameDesign, plan: Plan, assets_text: str, done: list[TaskSpec]) -> Brief:
        by_id = {t.id: t for t in plan.tasks}
        dependencies = set(task.depends_on)
        pending = list(dependencies)
        while pending:
            dependency = by_id.get(pending.pop())
            if dependency:
                for key in dependency.depends_on:
                    if key not in dependencies:
                        dependencies.add(key); pending.append(key)
        needed = list(dict.fromkeys(task.outputs + [p for t in done if t.id in dependencies for p in t.outputs]))
        contracts = [c.model_dump(mode="json") for c in plan.contracts if c.path in needed or task.id in c.consumers]
        done_txt = "\n".join(f"- {t.id}: {', '.join(t.outputs)}" for t in done if t.id in dependencies) or "(none)"
        architecture = {"root_namespace": plan.architecture.root_namespace, "overview": plan.architecture.overview,
                        "conventions": plan.architecture.conventions, "scenes": plan.architecture.scenes}
        prompt = f"""GAME DESIGN:
{_design_summary(design, task.role)}

ARCHITECTURE:
{json.dumps(architecture, separators=(',', ':'))}

SHARED CODE CONTRACTS (preserve signatures):
{json.dumps(contracts, separators=(',', ':'))}

ASSET REGISTRY (downloaded, use these exact paths):
{assets_text}

COMPLETED TASKS:
{done_txt}

PROJECT PUBLIC API INDEX:
{self.ctx.api_index(needed)}

TASK TO BRIEF:
{task.model_dump_json(indent=1)}

Write the specialist brief JSON."""
        if self.revision_context:
            prompt += "\nHOST INSPECTION AND REVISION CONSTRAINTS:\n" + self.revision_context
        if self.cfg.brief_mode == "template" or (self.cfg.brief_mode == "auto" and contracts and len(task.outputs) <= 4):
            return Brief(prompt=prompt + "\nImplement the task directly; follow every contract and acceptance criterion.",
                         relevant_files=[p for p in needed if self.project.read_file(p) is not None])
        # Briefing is a summarisation task: route to the optional light model when configured.
        result = self.llm.structured(Brief, model=self.cfg.model_light or self.cfg.model_orchestrator,
                                     instructions=prompts.PROMPT_ENGINEER,
                                     prompt=prompt, name=f"brief_{task.id}")
        result.relevant_files = list(dict.fromkeys(result.relevant_files +
            [p for p in needed if self.project.read_file(p) is not None]))
        return result

    # ---------------------------------------------------------------- specialists
    def execute(self, role: str, brief: Brief, task: TaskSpec, extra_files: list[str] | None = None) -> AgentOutput:
        rels = list(brief.relevant_files) + list(task.outputs) + list(extra_files or [])
        before = {rel: self.project.read_file(rel) for rel in rels}
        prompt = f"""BRIEF:
{brief.prompt}

FILES YOU MUST CREATE/UPDATE: {json.dumps(task.outputs)}
ACCEPTANCE: {json.dumps(task.acceptance)}

PROJECT PUBLIC API INDEX:
{self.ctx.api_index(rels)}

EXISTING FILE CONTENTS:
{self.ctx.files(rels)}

Return JSON: complete files for new code, or small exact-match patches to existing code, plus builder_methods and notes."""
        if self.revision_context:
            prompt += "\nHOST INSPECTION AND REVISION CONSTRAINTS:\n" + self.revision_context
        if self.env_override:
            system = (f"You are the Unity {role} specialist modifying an existing project. Return AgentOutput JSON "
                      "with files, exact-match patches, delete, builder_methods and notes. Preserve existing scenes "
                      "and public APIs; do not recreate the game or invent missing packages.\n" + self.env_override)
        else:
            system = prompts.SPECIALISTS.get(role, prompts.SPECIALISTS["gameplay"]) + prompts.UNITY_ENV
        system += self.platform_note
        from .assets.manager import ASSET_ROLES, ASSET_USAGE, EXTERNAL_ASSET_USAGE
        if self.uses_assets and role in ASSET_ROLES:
            system += EXTERNAL_ASSET_USAGE if self.env_override else ASSET_USAGE
        return self.llm.structured(AgentOutput, model=self.cfg.model_coder, instructions=system, prompt=prompt,
                                   name=f"{role}_{task.id}", validate=lambda out: self._validate_output(out, before))

    def _validate_output(self, out: AgentOutput, before: dict) -> None:
        if any(self.project.read_file(path) != text for path, text in before.items()):
            raise FatalLLMError("Project files changed during generation. No output was applied; resume against current files.")
        prepared = {f.path: f.content for f in out.files}
        for patch in out.patches:
            source = prepared.get(patch.path)
            if source is None: source = self.project.read_file(patch.path)
            if source is None or source.count(patch.old) != 1:
                if source is not None and patch.new and source.count(patch.new) == 1: continue
                raise ValueError(f"Patch context does not match exactly once in {patch.path}; return a corrected exact-match patch.")
            prepared[patch.path] = source.replace(patch.old, patch.new, 1)
        for path in set(prepared) | set(out.delete): self.project.safe_path(path)
        if set(prepared) & set(out.delete): raise ValueError("Do not both write and delete the same file.")
        from .safety import check_files
        check_files(prepared)  # strict mode: the violation text is sent back to the model for regeneration

    # ---------------------------------------------------------------- fixer
    def fix(self, error_text: str, hint_files: list[str] | None = None) -> AgentOutput:
        files = set(hint_files or [])
        for m in re.finditer(r"(Assets/[^\s:(]+\.(?:cs|shader|hlsl|asmdef))", error_text):
            files.add(m.group(1))
        before = {rel: self.project.read_file(rel) for rel in files}
        prompt = f"""ERRORS:
{error_text}

PROJECT PUBLIC API INDEX:
{self.ctx.api_index(sorted(files))}

INVOLVED FILES:
{self.ctx.files(sorted(files))}

Fix the diagnosed errors with the smallest supported change. Prefer exact-match patches over rewriting large existing files."""
        return self.llm.structured(AgentOutput, model=self.cfg.model_coder,
                                   instructions=("Repair only the diagnosed Unity errors. Return AgentOutput JSON with "
                                                 "small exact-match patches; preserve unrelated code.\n" + self.env_override
                                                 if self.env_override else prompts.FIXER),
                                   prompt=prompt, name="fixer", validate=lambda out: self._validate_output(out, before))

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

