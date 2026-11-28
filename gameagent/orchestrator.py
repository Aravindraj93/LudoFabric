"""The orchestrator: intent -> design -> plan -> project -> assets -> build tasks -> self-heal -> visual polish -> ship."""
from __future__ import annotations

import re
import hashlib
import json
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from .agents import Studio
from .assets import AssetManager
from .config import Config
from .llm import LLM
from .schemas import AgentOutput, Brief, TaskSpec
from .state import State, atomic_json
from .ui import console
from .unity import UnityProject

PHASES = ["new", "designed", "planned", "project", "assets", "built", "polished", "shipped"]


class Orchestrator:
    def __init__(self, cfg: Config, project_path: Path):
        self.cfg = cfg
        self.project_path = project_path.resolve()
        self.state_dir = self.project_path.parent / f"{self.project_path.name}.agent"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "state.json"
        self.state = State.load(self.state_file)
        self.llm = LLM(cfg)
        self.llm.set_log_dir(self.state_dir / "llm_logs")
        self.project = UnityProject(cfg, self.project_path, self.state_dir)
        self.studio = Studio(cfg, self.llm, self.project)
        self.set_targets(None)
        self.assets: AssetManager | None = None

    # ------------------------------------------------------------------ helpers
    def set_targets(self, targets) -> None:
        """Apply user-chosen platforms. Adding a platform to a shipped game re-runs only the build stage."""
        from .platforms import design_brief, normalize
        if targets is not None:
            new = normalize(targets)
            if new != self.state.targets:
                added = [t for t in new if t not in self.state.targets]
                self.state.targets = new
                if added and self.state.phase == "shipped":
                    self.state.phase = "polished"  # deterministic rebuild; no design/code regeneration
                self._save()
        self.studio.platform_note = design_brief(self.state.targets) if self.state.targets != ["windows"] else ""

    def _save(self) -> None:
        self.state.save(self.state_file)

    def _phase_at_least(self, phase: str) -> bool:
        return PHASES.index(self.state.phase) >= PHASES.index(phase)

    def _set_phase(self, phase: str) -> None:
        if PHASES.index(phase) > PHASES.index(self.state.phase):
            self.state.phase = phase
        self._save()

    def _note(self, msg: str) -> None:
        console.print(msg)
        self.state.log.append(re.sub(r"\[/?[a-z ]+\]", "", msg))

    def _prepared_files(self, out: AgentOutput) -> dict[str, str]:
        prepared = {f.path: f.content for f in out.files}
        for patch in out.patches:
            self.project.safe_path(patch.path)
            source = prepared.get(patch.path)
            if source is None: source = self.project.read_file(patch.path)
            if source is None or not patch.old:
                raise ValueError(f"Patch requires an existing file and non-empty context: {patch.path}")
            if patch.new and patch.old in patch.new and source.count(patch.new) == 1:
                prepared[patch.path] = source  # Append-style patch already applied before an interruption.
                continue
            if source.count(patch.old) != 1:
                if patch.new and source.count(patch.new) == 1:
                    prepared[patch.path] = source  # Already applied before interruption.
                    continue
                raise ValueError(f"Patch context is stale or ambiguous: {patch.path}. Regenerate against current contents.")
            prepared[patch.path] = source.replace(patch.old, patch.new, 1)
        for rel in prepared:
            self.project.safe_path(rel)
        if set(out.delete) & set(prepared):
            raise ValueError("An agent cannot write and delete the same file in one output.")
        for path in out.delete:
            self.project.safe_path(path)
        from .safety import check_files
        for warning in check_files(prepared):  # also guards replayed journals, which bypass model validation
            self._event("safety_warning", detail=warning)
        return prepared

    def _apply(self, out: AgentOutput) -> list[str]:
        written = []
        prepared = self._prepared_files(out)
        for path, content in prepared.items():
            try:
                self.project.write_file(path, content)
                written.append(path)
            except ValueError as exc:
                console.print(f"[yellow]  skipped: {exc}[/]")
        for d in out.delete:
            try:
                self.project.delete_file(d)
            except ValueError as exc:
                console.print(f"[yellow]  skipped delete: {exc}[/]")
        self.state.add_builders(out.builder_methods)
        if written:
            console.print(f"[dim]  wrote {len(written)} file(s): {', '.join(written)}[/]")
        if out.notes:
            console.print(f"[dim]  notes: {out.notes[:300]}[/]")
        self._save()
        return written

    def _compile_fix_loop(self, hint_files: list[str] | None = None) -> bool:
        seen = set()
        package_repair_attempted = False
        last_fix: tuple[str, str, list[str]] | None = None  # (fingerprint, errors, files changed by the fix)
        for attempt in range(1, self.cfg.max_fix_attempts + 1):
            res = self.project.compile()
            if res.log_truncated:
                raise RuntimeError(res.error_text())
            if res.ok:
                self.state.evidence["compiled"] = True
                if last_fix:
                    self._remember_fix(*last_fix)
                return True
            errors = res.error_text()
            if re.search(r"No valid Unity Editor license|No valid Unity license", res.log, re.I):
                raise RuntimeError("Unity has no valid Editor license. Activate it in Unity Hub; no AI code repair was attempted.")
            if any(e["file"].startswith(("Assets/GameAgentBridge/", "Assets/LudoFabricStarter/")) for e in res.compile_errors):
                raise RuntimeError("Protected LudoFabric integration code failed to compile. Update/fix the tool; "
                                   "specialists must not overwrite its bridge or foundation.\n" + errors)
            if any(e["file"].startswith(("Library/PackageCache/", "Packages/")) for e in res.compile_errors):
                if not package_repair_attempted:
                    package_repair_attempted = True
                    if self.project.repair_package_compatibility():
                        continue
                raise RuntimeError("Unity package compatibility failed. These files are not agent-owned; "
                                   "stopped instead of spending AI calls rewriting package code.\n" + errors)
            signature = tuple(sorted((e["file"], e["code"], e["msg"]) for e in res.compile_errors)) if res.compile_errors else errors
            if signature in seen:
                raise RuntimeError("The same compiler errors returned after repair. Stopped to avoid spending more AI calls without progress.\n" + errors)
            seen.add(signature)
            self._note(f"[yellow]  compile failed (attempt {attempt}/{self.cfg.max_fix_attempts}); fixing...[/]")
            from .recovery import fingerprint
            fp = fingerprint(errors)
            self._event("debug_task", agent="fixer", fingerprint=fp, attempt=attempt,
                        files=sorted({e["file"] for e in res.compile_errors}))
            hints = list(dict.fromkeys((hint_files or []) + self._retrieve_fix_context(errors)))
            written = self._apply(self.studio.fix(errors, hints))
            last_fix = (fp, errors, written)
        ok = self.project.compile().ok
        self.state.evidence["compiled"] = ok
        if ok and last_fix:
            self._remember_fix(*last_fix)
        if not ok:
            self._note("[red]  could not reach a clean compile; progress saved[/]")
        return ok

    # ------------------------------------------------------------------ knowledge & events
    def _event(self, kind: str, **values) -> None:
        from .metrics import record
        record(self.state_dir / "events.jsonl", kind=kind, phase=self.state.phase, **values)

    @property
    def knowledge(self):
        if getattr(self, "_knowledge", None) is None:
            from .retrieval import ProjectKnowledge
            self._knowledge = ProjectKnowledge(self.state_dir / "knowledge.sqlite")
        return self._knowledge

    def _retrieve_fix_context(self, errors: str, limit: int = 4) -> list[str]:
        """Targeted files for a repair: past fixes of similar errors + keyword matches. Never the whole project."""
        try:
            self.knowledge.index(self.project_path, [p for p in self.project.code_files() if p.startswith("Assets/")])
            files = [f for fix in self.knowledge.similar_fixes(errors) for f in fix["files"]]
            idents = " ".join(re.findall(r"'([A-Za-z_]\w+)'", errors))  # CS0103/CS0246 quote the missing name
            files += [hit["path"] for hit in self.knowledge.search(idents or errors, k=limit)]
        except Exception as exc:  # Retrieval is an optimisation; never block a repair on it.
            self._event("retrieval_error", error=str(exc)[:300])
            return []
        files = [f for f in dict.fromkeys(files) if self.project.read_file(f) is not None]
        return files[:limit]

    def _remember_fix(self, fp: str, errors: str, files: list[str]) -> None:
        try:
            self.knowledge.remember_fix(fp, errors, files)
            self._event("fix_verified", fingerprint=fp, files=files)
        except Exception as exc:
            self._event("retrieval_error", error=str(exc)[:300])

    def _builder_files(self) -> list[str]:
        files = []
        for m in self.state.builder_methods:
            cls = m.rsplit(".", 2)[-2] if m.count(".") >= 1 else m
            for rel in self.project.code_files():
                if rel.endswith(".cs") and re.search(rf"\bclass\s+{re.escape(cls)}\b", self.project.read_file(rel) or ""):
                    files.append(rel)
        return files

    def _run_builders_with_fixes(self) -> bool:
        if not self.state.builder_methods:
            self._note("[yellow]  no builder methods registered[/]")
            return False
        # Bootstrap prerequisites must run before dependent scene/UI builders.
        for attempt in range(1, self.cfg.max_fix_attempts + 2):
            self.state.builder_methods.sort(key=lambda m: 0 if "TextMeshProBootstrap" in m else 1)
            self._save()
            if not self._compile_fix_loop():
                return False
            res = self.project.run_builders(self.state.builder_methods)
            self.state.evidence["builders_ok"] = res.ok
            if res.ok:
                for line in (res.bridge or {}).get("info", []):
                    console.print(f"[dim]    {line}[/]")
                return True
            if attempt > self.cfg.max_fix_attempts:
                return False
            self._note(f"[yellow]  scene builders failed (attempt {attempt}); fixing...[/]")
            self._apply(self.studio.fix(
                "Scene builder execution failed in batch mode (Editor, not play mode). Make builders robust "
                "and idempotent.\n" + res.error_text(), self._builder_files()))
        return False

    # ------------------------------------------------------------------ phases
    def run(self, request: str | None, stop_after: str | None = None, source: str | None = None) -> None:
        self._begin(request, source)
        if not self._phase_at_least("designed"):
            self._design()
        if not self._phase_at_least("planned"):
            self._plan()
        if stop_after == "planned":
            self._save()
            console.print(Panel("Design and plan ready. Review them, then start the Unity build.",
                                title="Design phase complete", border_style="green"))
            return
        self._stage_project()
        self._stage_assets()
        if not self._phase_at_least("built"):
            self._build_tasks()
        if not self._phase_at_least("polished"):
            self._polish()
        if not self._phase_at_least("shipped"):
            self._ship()
        self._report()

    # Stage helpers shared by the sequential engine and the LangGraph engine (gameagent/graph.py).
    def _begin(self, request: str | None, source: str | None = None) -> None:
        effective_request = request or self.state.request or ""
        from .revision import source_from_request, prepare, inspect
        revision_source = source or self.state.revision_source or source_from_request(effective_request)
        if revision_source:
            checkpoint = self.state_dir / "revision.json"
            migrate = (not checkpoint.exists() and self.state.plan is not None
                       and all(t.status != "done" for t in self.state.plan.tasks))
            record = prepare(Path(revision_source), self.project_path, self.state_dir,
                             replace_failed_scaffold=migrate)
            if not self.state.revision_source:
                # Archive the unsupported attempt's plan and logs; never replay its blocked outputs.
                if self.state_file.exists():
                    import shutil
                    import time
                    shutil.copy2(self.state_file, self.state_dir / f"before-revision-{time.time_ns()}.json")
                self.state = State(request=effective_request, revision_source=str(Path(revision_source).resolve()),
                                   targets=list(self.state.targets))
                self._save()
            self.studio.revision_context = inspect(self.project_path, record, self.cfg.context_char_budget)
            (self.state_dir / "REVISION_CONTEXT.md").write_text(self.studio.revision_context, encoding="utf-8")
            if not self._phase_at_least("project"):
                # Keep the copied project's packages, input settings, scenes and rendering configuration.
                self.project.install_bridge(preserve_starter=True)
        if request and not self.state.request:
            self.state.request = request
        if not self.state.request:
            raise SystemExit("No request given and no saved state to resume.")
        self._save()  # Even the very first model call may fail or hit a usage limit.
        self._reset_failed_tasks()
        console.print(Panel(self.state.request, title="Request", border_style="cyan"))

    def _reset_failed_tasks(self) -> None:
        if self.state.plan and not self._phase_at_least("built"):
            for task in self.state.plan.tasks:
                if task.status == "failed":
                    task.status = "pending"
            self._save()

    def _stage_project(self) -> None:
        if not self._phase_at_least("project"):
            console.rule("Unity project")
            if not self.state.revision_source:
                self.project.ensure_created()
            self._set_phase("project")
        else:
            self.project.install_bridge(preserve_starter=bool(self.state.revision_source))

    def _stage_assets(self) -> None:
        if self.assets is None:
            self.assets = AssetManager(self.cfg, self.project_path, self.state_dir)
        procedural = bool(self.state.design and self.state.design.asset_policy == "procedural_only")
        if procedural and self.state.plan:
            self.state.plan.assets = []
        if not self._phase_at_least("assets"):
            console.rule("Asset acquisition")
            self.assets.fetch_all(self.state.plan.assets)
            self._set_phase("assets")
        self.studio.uses_assets = not procedural and bool(self.assets.records)


    def _design(self) -> None:
        console.rule("Director: understanding intent")
        d = self.studio.design(self.state.request, self.state.targets if self.state.targets != ["windows"] else None)
        self.state.design = d
        (self.state_dir / "GDD.json").write_text(d.model_dump_json(indent=2), encoding="utf-8")
        console.print(Panel(f"[bold]{d.title}[/] - {d.genre}\n{d.pitch}\n\n[bold]Art:[/] {d.art.style}\n"
                            f"[bold]Assumptions:[/]\n- " + "\n- ".join(d.assumptions), title="Game Design"))
        self._set_phase("designed")

    def _plan(self) -> None:
        console.rule("Tech lead: architecture & task graph")
        plan = self.studio.plan(self.state.design)
        self.state.plan = plan
        (self.state_dir / "PLAN.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        t = Table("id", "role", "title", "deps")
        for task in plan.tasks:
            t.add_row(task.id, task.role, task.title, ", ".join(task.depends_on))
        console.print(t)
        self._set_phase("planned")

    def _ready_tasks(self) -> list[TaskSpec]:
        tasks = self.state.plan.tasks
        finished = {t.id for t in tasks if t.status == "done"}
        ready = [t for t in tasks if t.status == "pending" and all(d in finished for d in t.depends_on)]
        if not ready and any(t.status == "pending" for t in tasks):
            failed = [t.id for t in tasks if t.status == "failed"]
            if failed:
                raise RuntimeError(f"Tasks blocked by failed prerequisites: {', '.join(failed)}. "
                                   "Inspect the failed task's output before resuming.")
            raise RuntimeError("Task dependencies cannot be satisfied; saved progress can be resumed after fixing the plan.")
        return ready

    def _do_task(self, task: TaskSpec) -> None:
        console.print(f"[bold cyan]> [{task.role}] {task.id}: {task.title}[/]")
        # Journal complete model output before changing Unity files. Interrupted writes can be replayed.
        task_key = hashlib.sha256(task.id.encode()).hexdigest()
        journal = self.state_dir / "task_outputs" / f"{task_key}.json"
        signature = hashlib.sha256(json.dumps({
            "task": task.model_dump(mode="json", exclude={"status"}),
            "design": self.state.design.model_dump(mode="json") if self.state.design else None,
            "contracts": [c.model_dump(mode="json") for c in self.state.plan.contracts] if self.state.plan else [],
        }, sort_keys=True).encode()).hexdigest()
        def file_hash(path):
            source = self.project.read_file(path)
            return hashlib.sha256(source.replace("\r\n", "\n").encode()).hexdigest() if source is not None else None
        if journal.exists():
            saved = json.loads(journal.read_text(encoding="utf-8"))
            valid = "version" not in saved or (saved["signature"] == signature and all(
                file_hash(path) in (before, saved["produced"].get(path, before))
                for path, before in saved["inputs"].items()))
            if valid:
                out = AgentOutput.model_validate(saved.get("output", saved))
                self._require_task_output(task, out)
                written = self._apply(out)
                task.outputs = list(dict.fromkeys(task.outputs + written))
                task.status = "done" if written or out.builder_methods or out.delete else "failed"
                self._save()
                return
            console.print("[dim]  Task inputs changed; regenerating its stale checkpoint.[/]")
        done = [t for t in self.state.plan.tasks if t.status == "done"]
        brief = self.studio.brief(task, self.state.design, self.state.plan, self.assets.describe_for_agents(), done)
        (self.state_dir / "prompts").mkdir(exist_ok=True)
        (self.state_dir / "prompts" / f"{task_key}.md").write_text(brief.prompt, encoding="utf-8")
        before_generation = {path: file_hash(path) for path in self.project.code_files()}
        before_generation.update({path: file_hash(path) for path in task.outputs + brief.relevant_files})
        out = self.studio.execute(task.role, brief, task)
        self._require_task_output(task, out)
        prepared = self._prepared_files(out)
        dependencies = set(task.depends_on)
        if self.state.plan:
            previous = set()
            while previous != dependencies:
                previous = set(dependencies)
                dependencies.update(d for t in self.state.plan.tasks if t.id in previous for d in t.depends_on)
        paths = set(brief.relevant_files) | set(prepared) | set(out.delete)
        if self.state.plan:
            paths.update(p for t in self.state.plan.tasks if t.id in dependencies for p in t.outputs)
        for path in paths:
            if file_hash(path) != before_generation.get(path):
                raise RuntimeError(f"{path} changed while the specialist was working. No generated files were applied; "
                                   "resume to regenerate against the current project.")
        produced = {path: hashlib.sha256(content.replace("\r\n", "\n").encode()).hexdigest() for path, content in prepared.items()}
        produced.update({path: None for path in out.delete})
        atomic_json(journal, {"version": 2, "signature": signature, "output": out.model_dump(mode="json"),
                              "inputs": {path: file_hash(path) for path in paths}, "produced": produced})
        written = self._apply(out)
        task.outputs = list(dict.fromkeys(task.outputs + written))
        task.status = "done" if written or out.builder_methods or out.delete else "failed"
        self._save()

    @staticmethod
    def _require_task_output(task: TaskSpec, out: AgentOutput) -> None:
        if not (out.files or out.patches or out.delete or out.builder_methods):
            raise RuntimeError(f"Task {task.id} produced no implementation output. "
                               f"Specialist reported: {out.notes or 'No reason supplied.'}")

    def _build_tasks(self) -> None:
        console.rule("Production: specialists at work")
        wave = 0
        while True:
            ready = self._ready_tasks()
            if not ready:
                break
            wave += 1
            console.print(f"[magenta]Wave {wave}: {', '.join(t.id for t in ready)}[/]")
            touched: list[str] = []
            for task in ready:
                try:
                    self._do_task(task)
                except Exception as exc:
                    task.status = "failed"
                    self._note(f"[red]  task {task.id} failed: {exc}[/]")
                    self._save()
                    raise
                touched += task.outputs
                self._save()
            if not self._compile_fix_loop(touched):
                raise RuntimeError("Compilation still fails. Progress saved; inspect errors before resuming.")
        console.rule("Building scenes")
        if not all(t.status == "done" for t in self.state.plan.tasks):
            raise RuntimeError("Production tasks remain incomplete. Resume to retry them.")
        if not self._run_builders_with_fixes():
            raise RuntimeError("Scene builders failed. Progress saved; resume to retry.")
        self._set_phase("built")

    def _polish(self) -> None:
        while self.state.polish_round < self.cfg.polish_rounds:
            r = self.state.polish_round + 1
            console.rule(f"Art direction review - round {r}")
            res, shots = self.project.screenshots(self.state_dir / f"screens_round{r}")
            if not shots:
                self._note(f"[yellow]  no screenshots captured: {res.error_text()[:500]}[/]")
                raise RuntimeError("Screenshot capture failed. Resume to retry the unfinished review round.")
            from .quality import deterministic_review
            stats, gate = deterministic_review(shots)
            self.state.evidence["visual_gate_ok"] = gate is None
            self.state.evidence["screenshot_stats"] = stats
            self._event("visual_gate", ok=gate is None, round=r, issues=len(gate.issues) if gate else 0)
            # Blocking defects need no vision model: skip that call and route straight to correction.
            review = gate or self.studio.review(self.state.design, shots)
            if gate is None:
                self.state.evidence["review_score"] = review.score
            self.state.reviews.append(review.model_dump())
            self._save()
            console.print(Panel(f"Score: [bold]{review.score}/10[/]\n{review.summary}\n\n" + "\n".join(
                f"[{i.severity}] ({i.area}) {i.problem} -> {i.fix}" for i in review.issues), title="Art Director"))
            if review.score >= 9 or not review.issues:
                self.state.polish_round = r
                self._save()
                break
            by_area: dict[str, list] = {}
            for issue in review.issues:
                if issue.severity != "minor" or len(review.issues) <= 4:
                    by_area.setdefault(issue.area, []).append(issue)
            for area, issues in by_area.items():
                task = TaskSpec(
                    id=f"polish{r}_{area}", role=area, title=f"Polish pass ({area})",
                    description="Address the Art Director review:\n" + "\n".join(
                        f"- [{i.severity}] {i.problem}\n  FIX: {i.fix}" for i in issues),
                    acceptance=[i.fix for i in issues],
                )
                console.print(f"[bold cyan]> polish [{area}] ({len(issues)} issues)[/]")
                try:
                    done = [t for t in self.state.plan.tasks if t.status == "done"]
                    brief = self.studio.brief(task, self.state.design, self.state.plan,
                                              self.assets.describe_for_agents(), done)
                    brief = Brief(prompt=brief.prompt,
                                  relevant_files=list(dict.fromkeys(brief.relevant_files + self._builder_files())))
                    self._apply(self.studio.execute(area, brief, task))
                except Exception as exc:
                    self._note(f"[red]  polish {area} failed: {exc}[/]")
                    self._save()
                    raise
            if not self._run_builders_with_fixes():
                raise RuntimeError("Polish scene builders failed. Resume to retry this round.")
            self.state.polish_round = r
            self._save()
        self._set_phase("polished")

    def _ship(self) -> None:
        console.rule("QA & build")
        for platform in ("EditMode", "PlayMode"):
            ok, details = self.project.run_tests(platform)
            if not ok:
                self._note(f"[yellow]  {platform} tests failed; fixing[/]")
                self._apply(self.studio.fix(f"Unity {platform} test failures:\n{details}"))
                self._compile_fix_loop()
                ok, details = self.project.run_tests(platform)
                if not ok:
                    self.state.evidence[f"tests_{platform}"] = False
                    raise RuntimeError(f"Unity {platform} tests still fail after repair:\n{details}")
            self.state.evidence[f"tests_{platform}"] = True
            self._event("tests", platform=platform, ok=True, details=details[:300])
        from .platforms import PLATFORMS, app_id, module_installed
        builds = self.state.evidence.setdefault("builds", {})
        failed = []
        for target in self.state.targets:
            spec = PLATFORMS[target]
            done = builds.get(target, {})
            if done.get("ok") and Path(done.get("artifact", "")).exists():
                continue  # built before an interruption; changes clear this record to force a rebuild
            if module_installed(self.cfg.unity_editor, target) is False:
                builds[target] = {"ok": False, "status": "module_missing",
                                  "note": f"Install '{spec.label} Build Support' for this Editor in Unity Hub, then resume."}
                self._note(f"[yellow]  {spec.label}: build support module not installed; skipped[/]")
                failed.append(target)
                continue
            console.rule(f"Build: {spec.label}")
            if target == "windows":
                ok = self._ship_windows(spec.artifact(self.project_path, self.state.design.title))
            else:
                bundle_id = None if self.state.revision_source else app_id(self.state.design.title)
                ok = self._ship_other(target, spec, bundle_id)
            if not ok:
                failed.append(target)
        self.state.evidence["smoke_applicable"] = "windows" in self.state.targets
        built = [t for t in self.state.targets if builds.get(t, {}).get("ok")]
        self.state.evidence["build_ok"] = bool(built) and not failed
        self._save()
        if failed:
            raise RuntimeError("Builds incomplete for: " + ", ".join(
                f"{t} ({builds.get(t, {}).get('status', 'failed')})" for t in failed)
                + ". Successful builds are kept; resume to retry. Project was not marked shipped.")
        self._set_phase("shipped")

    def _ship_other(self, target: str, spec, bundle_id: str) -> bool:
        out = spec.artifact(self.project_path, self.state.design.title)
        builds = self.state.evidence["builds"]
        for attempt in range(1, 3):
            self._compile_fix_loop()
            res = self.project.build(out, target, bundle_id)
            if res.ok and out.exists():
                builds[target] = {"ok": True, "status": "built", "artifact": str(out), "app_id": bundle_id,
                                  "note": spec.note, "verified": "build only (cannot be run on this host)"}
                self._note(f"[green]  {spec.label} build OK: {out}[/]")
                return True
            errors = res.error_text()
            if re.search(r"build support .* not installed|SDK|NDK|JDK", errors, re.I):
                builds[target] = {"ok": False, "status": "toolchain_missing", "note": errors[:600]}
                return False  # environment problem: AI code repair cannot fix it
            self._note(f"[yellow]  {spec.label} build failed (attempt {attempt}); fixing[/]")
            self._apply(self.studio.fix(f"{spec.label} player build failed:\n" + errors))
        builds[target] = {"ok": False, "status": "failed", "note": "build failed after repair"}
        return False

    def _ship_windows(self, exe: Path) -> bool:
        builds = self.state.evidence["builds"]
        for attempt in range(1, 3):
            self._compile_fix_loop()
            res = self.project.build(exe)
            if not res.ok:
                self._note(f"[yellow]  build failed (attempt {attempt}); fixing[/]")
                self._apply(self.studio.fix("Player build failed:\n" + res.error_text()))
                continue
            ok, issues = self.project.smoke_run(exe)
            self.state.evidence["smoke_ok"] = ok
            if ok:
                self.state.evidence["artifact"] = str(exe)
                builds["windows"] = {"ok": True, "status": "built", "artifact": str(exe), "verified": "smoke run clean"}
                self._note(f"[green]  build OK and smoke run clean: {exe}[/]")
                return True
            self._note(f"[yellow]  runtime errors in smoke run (attempt {attempt}); fixing[/]")
            self._apply(self.studio.fix("Runtime errors from the built player log:\n" + issues))
            self._run_builders_with_fixes()
        builds["windows"] = {"ok": False, "status": "failed", "note": "build or smoke run failed after repair"}
        return False

    def _report(self) -> None:
        from .quality import assess_maturity
        from .metrics import summarize
        d, p = self.state.design, self.state.plan
        self.state.evidence["stages_designed"] = len(d.progression)
        self.state.maturity = assess_maturity(self.state.phase, self.state.evidence)
        self._save()
        usage = summarize(self.state_dir / "metrics.jsonl")
        lines = [f"# {d.title}", "", d.pitch, "",
                 f"**Maturity:** {self.state.maturity['level']} (next level needs: "
                 f"{', '.join(self.state.maturity['next_level_missing']) or 'n/a'})", "",
                 f"**Model requests:** {usage['model_requests']} (cache hits {usage['cache_hits']}); "
                 f"reported tokens in/out {usage['tokens']['input']}/{usage['tokens']['output']}", "",
                 "## Tasks", ""]
        if self.state.revision_source:
            from .revision import hashes
            record = json.loads((self.state_dir / "revision.json").read_text(encoding="utf-8"))
            current = hashes(self.project_path)
            changes = {"added": sorted(set(current) - set(record['baseline'])),
                       "deleted": sorted(set(record['baseline']) - set(current)),
                       "modified": sorted(p for p in current.keys() & record['baseline'].keys()
                                          if current[p] != record['baseline'][p])}
            atomic_json(self.state_dir / "revision_changes.json", changes)
            lines += [f"Revision source: {self.state.revision_source}", f"Source snapshot: {record['snapshot']}",
                      "File changes: revision_changes.json. Visual quality and Android device performance require human review.", ""]
        lines += [f"- [{'x' if t.status == 'done' else ' '}] ({t.role}) {t.title}" for t in p.tasks]
        lines += ["", "## Reviews", ""]
        lines += [f"- round {i + 1}: {r['score']}/10 - {r['summary']}" for i, r in enumerate(self.state.reviews)]
        lines += ["", "## Assets", "", self.assets.describe_for_agents(), "", "## Log", ""] + [f"- {l}" for l in self.state.log]
        (self.state_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
        console.print(Panel(f"Project: {self.project_path}\nReport: {self.state_dir / 'REPORT.md'}",
                            title="Done", border_style="green"))

