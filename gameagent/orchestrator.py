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
        self.assets: AssetManager | None = None

    # ------------------------------------------------------------------ helpers
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
        for attempt in range(1, self.cfg.max_fix_attempts + 1):
            res = self.project.compile()
            if res.ok:
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
            self._apply(self.studio.fix(errors, hint_files))
        ok = self.project.compile().ok
        if not ok:
            self._note("[red]  could not reach a clean compile; progress saved[/]")
        return ok

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
    def run(self, request: str | None, stop_after: str | None = None) -> None:
        if request and not self.state.request:
            self.state.request = request
        if not self.state.request:
            raise SystemExit("No request given and no saved state to resume.")
        self._save()  # Even the very first model call may fail or hit a usage limit.
        if self.state.plan and not self._phase_at_least("built"):
            for task in self.state.plan.tasks:
                if task.status == "failed":
                    task.status = "pending"
            self._save()
        console.print(Panel(self.state.request, title="Request", border_style="cyan"))

        if not self._phase_at_least("designed"):
            self._design()
        if not self._phase_at_least("planned"):
            self._plan()
        if stop_after == "planned":
            self._save()
            console.print(Panel("Design and plan ready. Review them, then start the Unity build.",
                                title="Design phase complete", border_style="green"))
            return
        if not self._phase_at_least("project"):
            console.rule("Unity project")
            self.project.ensure_created()
            self._set_phase("project")
        else:
            self.project.install_bridge()
        self.assets = AssetManager(self.cfg, self.project_path, self.state_dir)
        if not self._phase_at_least("assets"):
            console.rule("Asset acquisition")
            self.assets.fetch_all(self.state.plan.assets)
            self._set_phase("assets")
        if not self._phase_at_least("built"):
            self._build_tasks()
        if not self._phase_at_least("polished"):
            self._polish()
        if not self._phase_at_least("shipped"):
            self._ship()
        self._report()

    def _design(self) -> None:
        console.rule("Director: understanding intent")
        d = self.studio.design(self.state.request)
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
            review = self.studio.review(self.state.design, shots)
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
                    raise RuntimeError(f"Unity {platform} tests still fail after repair:\n{details}")
        exe = self.project_path / "Build" / f"{re.sub(r'[^A-Za-z0-9]+', '', self.state.design.title) or 'Game'}.exe"
        for attempt in range(1, 3):
            self._compile_fix_loop()
            res = self.project.build(exe)
            if not res.ok:
                self._note(f"[yellow]  build failed (attempt {attempt}); fixing[/]")
                self._apply(self.studio.fix("Player build failed:\n" + res.error_text()))
                continue
            ok, issues = self.project.smoke_run(exe)
            if ok:
                self._note(f"[green]  build OK and smoke run clean: {exe}[/]")
                break
            self._note(f"[yellow]  runtime errors in smoke run (attempt {attempt}); fixing[/]")
            self._apply(self.studio.fix("Runtime errors from the built player log:\n" + issues))
            self._run_builders_with_fixes()
        else:
            raise RuntimeError("Player build or smoke test failed. Project was not marked shipped; resume to retry.")
        self._set_phase("shipped")

    def _report(self) -> None:
        d, p = self.state.design, self.state.plan
        lines = [f"# {d.title}", "", d.pitch, "", "## Tasks", ""]
        lines += [f"- [{'x' if t.status == 'done' else ' '}] ({t.role}) {t.title}" for t in p.tasks]
        lines += ["", "## Reviews", ""]
        lines += [f"- round {i + 1}: {r['score']}/10 - {r['summary']}" for i, r in enumerate(self.state.reviews)]
        lines += ["", "## Assets", "", self.assets.describe_for_agents(), "", "## Log", ""] + [f"- {l}" for l in self.state.log]
        (self.state_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
        console.print(Panel(f"Project: {self.project_path}\nReport: {self.state_dir / 'REPORT.md'}",
                            title="Done", border_style="green"))

