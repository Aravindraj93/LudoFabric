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

    def _apply(self, out: AgentOutput) -> list[str]:
        written = []
        for f in out.files:
            try:
                self.project.write_file(f.path, f.content)
                written.append(f.path)
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
        for attempt in range(1, self.cfg.max_fix_attempts + 1):
            res = self.project.compile()
            if res.ok:
                return True
            errors = res.error_text()
            self._note(f"[yellow]  compile failed (attempt {attempt}/{self.cfg.max_fix_attempts}); fixing...[/]")
            self._apply(self.studio.fix(errors, hint_files))
        ok = self.project.compile().ok
        if not ok:
            self._note("[red]  could not reach a clean compile; continuing (later fixes may resolve it)[/]")
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
        for attempt in range(1, self.cfg.max_fix_attempts + 1):
            if not self._compile_fix_loop():
                return False
            res = self.project.run_builders(self.state.builder_methods)
            if res.ok:
                for line in (res.bridge or {}).get("info", []):
                    console.print(f"[dim]    {line}[/]")
                return True
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
        if journal.exists():
            out = AgentOutput.model_validate(json.loads(journal.read_text(encoding="utf-8")))
            written = self._apply(out)
            task.outputs = list(dict.fromkeys(task.outputs + written))
            task.status = "done" if written or out.builder_methods else "failed"
            self._save()
            return
        done = [t for t in self.state.plan.tasks if t.status == "done"]
        brief = self.studio.brief(task, self.state.design, self.state.plan, self.assets.describe_for_agents(), done)
        (self.state_dir / "prompts").mkdir(exist_ok=True)
        (self.state_dir / "prompts" / f"{task_key}.md").write_text(brief.prompt, encoding="utf-8")
        out = self.studio.execute(task.role, brief, task)
        atomic_json(journal, out.model_dump(mode="json"))
        written = self._apply(out)
        task.outputs = list(dict.fromkeys(task.outputs + written))
        task.status = "done" if written or out.builder_methods else "failed"
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
            self._compile_fix_loop(touched)
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

