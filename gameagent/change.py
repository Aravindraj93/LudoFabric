"""Change requests for finished LudoFabric games and for existing Unity projects made elsewhere.

Flow (bounded, resumable, reversible):
  profile project (deterministic) -> retrieve relevant files -> change plan (1 model call)
  -> per task: deterministic brief + specialist output (journaled) -> backup -> apply
  -> verify with the tools that fit the project (compile + repair, scene builders, tests, rebuild)
  -> record the change with its verification evidence; `rollback` restores the backup.

External projects never receive the LudoFabric bridge/starter (the bridge requires URP); they are
compiled with a plain batch-mode Unity run and their Editor methods are executed directly.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from pydantic import Field, model_validator

from .config import Config
from .schemas import AgentOutput, AssetRequest, Brief, TaskSpec, _Base
from .state import atomic_json
from .ui import console

MAX_CHANGE_TASKS = 6

CHANGE_LEAD = """You are the lead engineer of a game studio receiving a change request for an EXISTING Unity game.
Plan the SMALLEST set of tasks (1-6) that fully implements the request while preserving everything else.
Rules:
- Work with the existing architecture, namespaces, folders, naming and coding style shown in the context.
- Modify existing files when the feature belongs there (list them in task.outputs); create new files only when cleaner.
- Never plan to recreate existing scenes, rename/move public APIs, or delete user content unless explicitly requested.
- Scene/prefab/material edits are done by idempotent static Editor methods that open the existing asset,
  apply the change only if missing, and save.
- Add or update an EditMode test task when the change has testable logic.
- task.role must be one of: gameplay, graphics, vfx, animation, ui, level, audio, tests.
- Use real file paths from the context; new files go under Assets/.
- Record assumptions and risks honestly. If the request is ambiguous, choose the most reasonable reversible interpretation."""


class ChangePlan(_Base):
    summary: str
    impact: list[str] = Field(default_factory=list, description="existing systems/files affected")
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list, description="how a player/tester can confirm the change")
    touches_scenes: bool = False
    assets: list[AssetRequest] = Field(default_factory=list,
        description="free models/HDRIs/textures to download for this change (only from the available sources)")
    tasks: list[TaskSpec]

    @model_validator(mode="after")
    def _check(self):
        if not 1 <= len(self.tasks) <= MAX_CHANGE_TASKS:
            raise ValueError(f"Plan 1-{MAX_CHANGE_TASKS} tasks.")
        ids = [t.id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("Task IDs must be unique.")
        for t in self.tasks:
            t.depends_on = [d for d in t.depends_on if d in ids and d != t.id]
            if not t.outputs:
                raise ValueError(f"Task {t.id} must list the Assets/ files it creates or edits.")
        order, done = [], set()
        while len(order) < len(self.tasks):
            ready = [t for t in self.tasks if t.id not in done and all(d in done for d in t.depends_on)]
            if not ready:
                raise ValueError("Task dependency cycle.")
            for t in ready:
                order.append(t); done.add(t.id)
        self.tasks = order
        return self


# --------------------------------------------------------------------------- deterministic profiling
def is_unity_project(path: Path) -> bool:
    return (path / "Assets").is_dir() and (path / "ProjectSettings").is_dir()


def profile_project(path: Path) -> dict:
    def read(rel):
        p = path / rel
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    version = re.search(r"m_EditorVersion:\s*(\S+)", read("ProjectSettings/ProjectVersion.txt"))
    try:
        deps = json.loads(read("Packages/manifest.json") or "{}").get("dependencies", {})
    except ValueError:
        deps = {}
    rp = ("hdrp" if "com.unity.render-pipelines.high-definition" in deps else
          "urp" if "com.unity.render-pipelines.universal" in deps else "builtin")
    handler = re.search(r"activeInputHandler:\s*(\d)", read("ProjectSettings/ProjectSettings.asset"))
    input_mode = {"0": "legacy", "1": "input_system", "2": "both"}.get(handler.group(1) if handler else "0", "legacy")
    if input_mode == "legacy" and "com.unity.inputsystem" in deps and not handler:
        input_mode = "input_system"
    build_scenes = re.findall(r"path:\s*(Assets/\S+\.unity)", read("ProjectSettings/EditorBuildSettings.asset"))
    skip = ("Assets/Samples/", "Assets/TextMesh Pro/", "Assets/ThirdParty/", "Assets/Plugins/")
    scenes, scripts, asmdefs, namespaces = [], 0, [], {}
    for p in (path / "Assets").rglob("*"):
        rel = p.relative_to(path).as_posix()
        if rel.startswith(skip) or not p.is_file():
            continue
        if p.suffix == ".unity" and len(scenes) < 40:
            scenes.append(rel)
        elif p.suffix == ".asmdef":
            asmdefs.append(rel)
        elif p.suffix == ".cs":
            scripts += 1
            if scripts <= 400:
                for ns in re.findall(r"^\s*namespace\s+([\w.]+)", p.read_text(encoding="utf-8", errors="replace"), re.M):
                    namespaces[ns] = namespaces.get(ns, 0) + 1
    tests = [a for a in asmdefs if "TestRunner" in read(a) or "nunit" in read(a).lower()]
    return {"unity_version": version.group(1) if version else "unknown", "render_pipeline": rp,
            "input": input_mode, "packages": sorted(k for k in deps if k.startswith("com.unity.") and
                                                      not k.startswith("com.unity.modules."))[:40],
            "build_scenes": build_scenes, "scenes": scenes, "scripts": scripts, "asmdefs": asmdefs[:30],
            "test_assemblies": tests, "namespaces": sorted(namespaces, key=namespaces.get, reverse=True)[:8],
            "ludofabric_bridge": (path / "Assets/GameAgentBridge").exists()}


def environment_for(profile: dict) -> str:
    shader = {"urp": '"Universal Render Pipeline/Lit" (_BaseColor)', "hdrp": '"HDRP/Lit" (_BaseColor)',
              "builtin": '"Standard" (_Color)'}[profile["render_pipeline"]]
    inputs = {"legacy": "the legacy UnityEngine.Input API (the Input System is not active)",
              "input_system": "UnityEngine.InputSystem (legacy Input is disabled)",
              "both": "whichever input API the existing scripts already use"}[profile["input"]]
    return f"""

TARGET ENVIRONMENT (detected from this EXISTING project; overrides every other environment assumption):
- Unity {profile['unity_version']}, render pipeline: {profile['render_pipeline'].upper()}. Default lit shader: {shader}.
- Input: use {inputs}.
- Installed Unity packages: {', '.join(profile['packages']) or '(none listed)'}. Do NOT add packages or use APIs from missing ones.
- This project was NOT generated by LudoFabric: GameAgent.Bridge.SceneKit and Assets/LudoFabricStarter do NOT exist.
- Existing namespaces: {', '.join(profile['namespaces']) or '(global namespace)'}; assembly definitions: {', '.join(profile['asmdefs']) or 'none (Assembly-CSharp)'}.
  New code must compile inside the right assembly; Editor-only code goes in a folder named Editor.
- Preserve existing behaviour, public APIs, assets and scenes. Prefer small exact-match patches to existing files.
- Scene/prefab edits: idempotent `public static void` Editor methods (Assets/Editor/...) that open the existing scene with
  EditorSceneManager.OpenScene, find objects by name/component, apply the change only if missing, mark dirty and save.
  List them in builder_methods. Never rebuild an existing scene from scratch."""


# --------------------------------------------------------------------------- runner
class ChangeRunner:
    def __init__(self, orch):
        self.orch = orch
        self.cfg: Config = orch.cfg
        self.unity_ok = bool(self.cfg.unity_editor) and Path(self.cfg.unity_editor).exists()
        self.project_notes = self._project_notes()
        st = orch.state
        if st.origin == "external" or (not st.design and not (orch.project_path / "Assets/GameAgentBridge").exists()):
            st.origin = "external"
        if st.origin == "external":
            st.profile = profile_project(orch.project_path)
            orch.studio.env_override = environment_for(st.profile)
            orch.project.compile = self._plain_compile  # the bridge is not installed in external projects
            # Never "repair" an external project's packages with LudoFabric's URP package set.
            orch.project.repair_package_compatibility = lambda: False

    @property
    def external(self) -> bool:
        return self.orch.state.origin == "external"

    def _plain_compile(self):
        return self.orch.project.runner.run(["-projectPath", str(self.orch.project_path)], "compile_plain")

    # ------------------------------------------------------------ context (bounded; never the whole project)
    def _project_notes(self) -> str:
        chunks = []
        for rel, limit, tail in (("AGENTS.md", 10000, False), ("README.md", 5000, False),
                                 ("Docs/PROJECT_MEMORY.md", 10000, True), ("Docs/ROADMAP.md", 5000, True)):
            path = (self.orch.project_path / rel).resolve()
            if path.is_relative_to(self.orch.project_path) and path.is_file():
                text = path.read_text(encoding='utf-8', errors='replace')
                chunks.append(f'===== {rel} ({"latest entries" if tail else "instructions"}) =====\n' +
                              (text[-limit:] if tail else text[:limit]))
        return '\n\n'.join(chunks)

    def _context(self, request: str) -> str:
        orch, st = self.orch, self.orch.state
        orch.knowledge.index(orch.project_path, orch.project.code_files())
        hits = orch.knowledge.search(request, k=8)
        paths = [h["path"] for h in hits]
        parts = ['PROJECT INSTRUCTIONS AND RECENT HISTORY:\n' + self.project_notes] if self.project_notes else []
        if st.design:
            from .agents import _design_summary
            parts.append("ORIGINAL GAME DESIGN:\n" + _design_summary(st.design, "gameplay"))
        if st.plan:
            parts.append("ARCHITECTURE:\n" + json.dumps({"root_namespace": st.plan.architecture.root_namespace,
                         "overview": st.plan.architecture.overview, "conventions": st.plan.architecture.conventions,
                         "scenes": st.plan.architecture.scenes}, separators=(",", ":")))
        profile = st.profile or profile_project(orch.project_path)
        parts.append("PROJECT PROFILE:\n" + json.dumps(profile, separators=(",", ":")))
        tree = orch.project.code_files()
        parts.append(f"CODE FILES ({len(tree)}; first 200):\n" + "\n".join(tree[:200]))
        parts.append("MOST RELEVANT CODE (keyword retrieval):\n" + "\n".join(
            f"- {h['path']}: {h['snippet'][:240]}" for h in hits) or "(none)")
        parts.append("PUBLIC API OF RELEVANT FILES:\n" + orch.studio.ctx.api_index(paths, budget=10000))
        if st.changes:
            parts.append("PREVIOUS CHANGES:\n" + "\n".join(f"- {c['id']}: {c['request'][:160]} ({c['status']})"
                                                           for c in st.changes[-5:]))
        return "\n\n".join(parts)

    # ------------------------------------------------------------ backups
    def _backup(self, cdir: Path, rec: dict, paths: list[str]) -> None:
        for rel in paths:
            if rel in rec["backed_up"] or rel in rec["created"]:
                continue
            src = self.orch.project_path / rel
            if src.exists():
                dst = cdir / "backup" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                rec["backed_up"].append(rel)
            else:
                rec["created"].append(rel)

    # ------------------------------------------------------------ free assets for a change
    def _gltf_supported(self) -> bool:
        if not self.external:
            return True
        manifest = self.orch.project_path / "Packages/manifest.json"
        if not manifest.exists():
            return False
        dependencies = json.loads(manifest.read_text(encoding="utf-8")).get("dependencies", {})
        return "com.unity.cloud.gltfast" in dependencies or "com.atteneder.gltfast" in dependencies

    def _sources(self) -> str:
        from .assets.manager import sources_note
        if self.orch.state.design and self.orch.state.design.asset_policy == "procedural_only":
            return "\n\nASSETS: this game is procedural_only; request no downloads."
        return sources_note(self.cfg, self._gltf_supported())

    def _fetch_assets(self, plan: ChangePlan) -> None:
        orch = self.orch
        if orch.state.design and orch.state.design.asset_policy == "procedural_only":
            plan.assets = []
            orch.studio.uses_assets = False
            return
        unsupported = [a.key for a in plan.assets if a.kind == "model" and not self._gltf_supported()]
        if unsupported:
            orch._event("asset_import_blocked", keys=unsupported, reason="No supported glTF importer installed")
            console.print(f"[yellow]Models unavailable: {', '.join(unsupported)} (glTF importer missing).[/]")
        requests = [a for a in plan.assets if a.kind != "model" or self._gltf_supported()]
        if orch.assets is None and (requests or (orch.state_dir / "assets.json").exists()):
            from .assets import AssetManager
            orch.assets = AssetManager(self.cfg, orch.project_path, orch.state_dir)
        if requests:
            console.rule("Asset acquisition (change)")
            orch.assets.fetch_all(requests)  # registry + CREDITS.md updated; already-downloaded keys are skipped
        orch.studio.uses_assets = bool(orch.assets and orch.assets.records)

    def _asset_text(self) -> str:
        if self.orch.state.design and self.orch.state.design.asset_policy == "procedural_only":
            return "(procedural_only: do not download or use third-party assets for this change)"
        return self.orch.assets.describe_for_agents() if self.orch.assets else "(no assets)"

    # ------------------------------------------------------------ main entry
    def run(self, request: str) -> dict:
        orch, st = self.orch, self.orch.state
        if not is_unity_project(orch.project_path):
            raise SystemExit(f"Not a Unity project (Assets/ and ProjectSettings/ required): {orch.project_path}")
        if st.origin != "external" and st.phase not in ("built", "polished", "shipped"):
            raise SystemExit("This LudoFabric project is not finished yet. Resume it first, or change the plan before building.")
        rec = next((c for c in st.changes if c["request"] == request and c["status"] in ("in_progress", "failed")), None)
        if rec is None:
            rec = {"id": f"c{len(st.changes) + 1:03d}", "request": request, "status": "in_progress",
                   "created_at": time.time(), "tasks": [], "backed_up": [], "created": [], "verification": {}}
            st.changes.append(rec)
        rec["status"] = "in_progress"
        if not st.request:
            st.request = f"Existing Unity project: {orch.project_path.name}"
        orch._save()
        cdir = orch.state_dir / "changes" / rec["id"]
        cdir.mkdir(parents=True, exist_ok=True)
        orch._event("change_start", change=rec["id"], external=self.external)
        console.rule(f"Change {rec['id']}: {request[:80]}")
        try:
            plan_file = cdir / "plan.json"
            if plan_file.exists():
                plan = ChangePlan.model_validate_json(plan_file.read_text(encoding="utf-8"))
            else:
                plan = orch.llm.structured(ChangePlan, model=self.cfg.model_orchestrator, instructions=CHANGE_LEAD,
                                           name="changelead_plan",
                                           prompt=f"CHANGE REQUEST:\n{request}\n\n{self._context(request)}"
                                                  f"{self._sources()}\n\nReturn the change plan JSON.")
                atomic_json(plan_file, plan.model_dump(mode="json"))
            self._fetch_assets(plan)
            rec["summary"], rec["acceptance"] = plan.summary, plan.acceptance
            previous = {t["id"]: t for t in rec.get("tasks", [])}
            rec["tasks"] = [previous.get(t.id) or {"id": t.id, "role": t.role, "title": t.title, "status": "pending",
                                                   "files": []} for t in plan.tasks]
            orch._save()
            builders: list[str] = []
            for task, trec in zip(plan.tasks, rec["tasks"]):
                if trec["status"] == "done":  # applied before an interruption: never apply twice
                    journal = cdir / f"task_{re.sub(r'[^A-Za-z0-9_-]', '_', task.id)}.json"
                    out = AgentOutput.model_validate_json(journal.read_text(encoding="utf-8"))
                else:
                    out = self._task(cdir, rec, plan, task, request)
                builders += [m for m in out.builder_methods if m not in builders]
                trec.update(status="done", files=[f.path for f in out.files] + [p.path for p in out.patches])
                orch._save()
            rec["builders"] = builders
            rec["status"] = "applied"
            orch._save()
            self._verify(rec, plan, builders)
        except BaseException as exc:
            rec["status"] = "failed"
            rec["error"] = str(exc)[:2000]
            orch._event("change_failed", change=rec["id"], error=str(exc)[:500])
            orch._save()
            raise
        orch._event("change_done", change=rec["id"], status=rec["status"])
        orch._save()
        atomic_json(cdir / "change.json", rec)
        console.print(f"[green]Change {rec['id']} {rec['status']}. Roll back with: python main.py rollback "
                      f"--project \"{orch.project_path}\" --change {rec['id']}[/]")
        return rec

    def _task(self, cdir: Path, rec: dict, plan: ChangePlan, task: TaskSpec, request: str) -> AgentOutput:
        orch = self.orch
        journal = cdir / f"task_{re.sub(r'[^A-Za-z0-9_-]', '_', task.id)}.json"
        console.print(f"[bold cyan]> [{task.role}] {task.id}: {task.title}[/]")
        if journal.exists():  # resumed: reuse the generated output, never pay for it twice
            out = AgentOutput.model_validate_json(journal.read_text(encoding="utf-8"))
        else:
            hits = [h["path"] for h in orch.knowledge.search(f"{task.title} {task.description}", k=4)]
            relevant = [p for p in dict.fromkeys(task.outputs + hits) if orch.project.read_file(p) is not None]
            prompt = (f"CHANGE REQUEST (from the user):\n{request}\n\nCHANGE PLAN SUMMARY:\n{plan.summary}\n"
                      f"ASSUMPTIONS: {json.dumps(plan.assumptions)}\n\nASSET REGISTRY (downloaded, use these exact paths):\n"
                      f"{self._asset_text()}\n\nYOUR TASK:\n{task.model_dump_json(indent=1)}\n\n"
                      "Implement only this task. Keep all existing behaviour that the request does not change. "
                      "Use exact-match patches for edits to existing files; return complete content only for new files.")
            if orch.state.design:
                from .agents import _design_summary
                prompt = "GAME DESIGN:\n" + _design_summary(orch.state.design, task.role) + "\n\n" + prompt
            if self.project_notes:
                prompt += '\n\nPROJECT INSTRUCTIONS AND RECENT HISTORY:\n' + self.project_notes
            out = orch.studio.execute(task.role, Brief(prompt=prompt, relevant_files=relevant), task)
            orch._require_task_output(task, out)
            atomic_json(journal, out.model_dump(mode="json"))
        orch._require_task_output(task, out)
        prepared = orch._prepared_files(out)
        self._backup(cdir, rec, list(prepared) + list(out.delete))
        orch._save()
        orch._apply(out)
        return out

    def _verify(self, rec: dict, plan: ChangePlan, builders: list[str]) -> None:
        orch, v = self.orch, rec["verification"]
        if not self.unity_ok:
            v["note"] = "Unity Editor not configured: files changed but NOT compiled or tested."
            rec["status"] = "applied_unverified"
            return
        touched = [f for t in rec["tasks"] for f in t["files"]]
        v["compiled"] = orch._compile_fix_loop(touched)
        if not v["compiled"]:
            raise RuntimeError("Change does not compile after bounded repair. Roll back or retry the change.")
        if builders or (plan.touches_scenes and not self.external):
            if self.external:
                v["builders"] = self._run_external_builders(builders)
            else:
                v["builders"] = orch._run_builders_with_fixes()
            if not v["builders"]:
                raise RuntimeError("Scene/asset update methods failed. Roll back or retry the change.")
        has_tests = (orch.state.profile.get("test_assemblies") if self.external else True)
        if has_tests:
            ok, details = orch.project.run_tests("EditMode")
            if not ok:
                orch._apply(orch.studio.fix(f"Unity EditMode test failures after change '{rec['request']}':\n{details}", touched))
                orch._compile_fix_loop(touched)
                ok, details = orch.project.run_tests("EditMode")
            v["tests_EditMode"], v["tests_detail"] = ok, details[:500]
            if not ok:
                raise RuntimeError("EditMode tests fail after the change and one targeted repair:\n" + details[:1500])
        if not self.external and orch.state.phase == "shipped":
            orch.state.evidence["builds"] = {}  # the code changed: every target must be rebuilt
            orch._ship()  # rebuild all chosen platforms + tests + Windows smoke run
            v["rebuilt"] = {t: b.get("artifact") for t, b in orch.state.evidence["builds"].items() if b.get("ok")}
            v["smoke_ok"] = orch.state.evidence.get("smoke_ok")
        rec["status"] = "verified"

    def _run_external_builders(self, methods: list[str]) -> bool:
        orch = self.orch
        for method in methods:
            for attempt in range(2):
                res = orch.project.runner.run(["-projectPath", str(orch.project_path)], "change_builder",
                                              nographics=False, method=method)
                if res.returncode == 0 and not res.compile_errors:
                    break
                if attempt:
                    return False
                orch._apply(orch.studio.fix(f"Editor method {method} failed in batch mode:\n{res.error_text()}"))
                if not orch._compile_fix_loop():
                    return False
        return True


def rollback(orch, change_id: str) -> dict:
    from .locking import project_lock
    with project_lock(orch.project_path):
        # Reload after acquiring the lock; another process may have completed a change.
        from .state import State
        orch.state = State.load(orch.state_file)
        return _rollback(orch, change_id)


def _rollback(orch, change_id: str) -> dict:
    rec = next((c for c in orch.state.changes if c["id"] == change_id), None)
    if rec is None:
        raise SystemExit(f"Unknown change {change_id}")
    if rec["status"] == "rolled_back":
        return rec
    later = [c["id"] for c in orch.state.changes[orch.state.changes.index(rec) + 1:] if c["status"] != "rolled_back"]
    if later:
        raise SystemExit(f"Roll back later changes first: {', '.join(reversed(later))}")
    cdir = orch.state_dir / "changes" / change_id
    for rel in rec["backed_up"]:
        target = orch.project.safe_path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cdir / "backup" / rel, target)
    for rel in rec["created"]:
        orch.project.delete_file(rel)
    orch.state.builder_methods = [m for m in orch.state.builder_methods if m not in rec.get("builders", [])]
    rec["status"] = "rolled_back"
    orch._event("change_rolled_back", change=change_id)
    orch._save()
    return rec


def run_change(cfg: Config, project_path: Path, request: str, targets=None) -> dict:
    from .locking import project_lock
    with project_lock(project_path):
        return _run_change(cfg, project_path, request, targets)


def _run_change(cfg: Config, project_path: Path, request: str, targets=None) -> dict:
    from .orchestrator import Orchestrator
    orch = Orchestrator(cfg, project_path)
    if targets is not None:
        from .platforms import normalize
        orch.state.targets = normalize(targets)  # a change does not by itself trigger a full re-ship
        orch.set_targets(None)
    (orch.state_dir / "CANCEL").unlink(missing_ok=True)
    return ChangeRunner(orch).run(request)
