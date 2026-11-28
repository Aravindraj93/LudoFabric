"""LangGraph orchestration engine.

The graph manages *workflow* (routing, bounded retries, failure classification, pause/block decisions,
SQLite checkpoints). Deterministic Unity work and specialist generation stay in the existing
Orchestrator stage methods, which are idempotent and skip already-completed phases, so a resumed run
never repeats completed side effects (task outputs are additionally journaled).

Graph state is small and typed: phase, attempt counters, error fingerprints and the last failure.
Design docs, plans, sources and screenshots stay on disk in <project>.agent/ and are referenced by path.
"""
from __future__ import annotations

import time
import hashlib
import json
import uuid
from typing import Literal, TypedDict

from .recovery import RetryPolicy, classify, decide, fingerprint

STAGES = ["design", "plan", "project", "assets", "build", "polish", "ship", "report"]


class GraphState(TypedDict, total=False):
    project: str
    stop_after: str | None
    next_stage: str
    failed_stage: str | None
    attempts: dict[str, int]
    fingerprints: dict[str, list[str]]
    last_error: str | None
    failure_class: str | None
    retry_at: float | None
    outcome: Literal["running", "completed", "planned", "paused", "blocked"]
    history: list[dict]          # bounded: last 50 stage events


class WorkflowStopped(RuntimeError):
    def __init__(self, outcome: str, message: str, original: BaseException | None = None):
        super().__init__(message)
        self.outcome = outcome
        self.original = original


def _hist(state: GraphState, **event) -> list[dict]:
    return (state.get("history", []) + [{"t": round(time.time(), 1), **event}])[-50:]


class GraphEngine:
    def __init__(self, orch, policy: RetryPolicy | None = None, sleep=time.sleep):
        self.orch = orch
        self.policy = policy or RetryPolicy(max_attempts=orch.cfg.max_phase_retries)
        self.sleep = sleep
        self._errors: dict[str, BaseException] = {}
        self.stage_fns = {
            "design": lambda: orch._phase_at_least("designed") or orch._design(),
            "plan": lambda: orch._phase_at_least("planned") or orch._plan(),
            "project": orch._stage_project,
            "assets": orch._stage_assets,
            "build": lambda: orch._phase_at_least("built") or orch._build_tasks(),
            "polish": lambda: orch._phase_at_least("polished") or orch._polish(),
            "ship": lambda: orch._phase_at_least("shipped") or orch._ship(),
            "report": orch._report,
        }

    # ---------------------------------------------------------------- nodes
    def _stage_node(self, name: str):
        def node(state: GraphState) -> GraphState:
            self.orch._event("stage_start", stage=name)
            try:
                if name in ("build", "polish", "ship", "report") and self.orch.assets is None:
                    self.orch._stage_assets()  # reattach asset registry after a restart
                self.stage_fns[name]()
            except BaseException as exc:  # SystemExit subclasses (budget, limit, fatal) included
                if isinstance(exc, KeyboardInterrupt):
                    raise
                self._errors[name] = exc
                self.orch._event("stage_failed", stage=name, error=str(exc)[:500])
                return {"failed_stage": name, "last_error": f"{type(exc).__name__}: {exc}"[:4000],
                        "retry_at": getattr(exc, 'retry_at', None),
                        "history": _hist(state, stage=name, ok=False)}
            self.orch._event("stage_done", stage=name)
            nxt = STAGES[STAGES.index(name) + 1] if name != "report" else "END"
            if name == "plan" and state.get("stop_after") == "planned":
                nxt = "END"
                return {"failed_stage": None, "next_stage": nxt, "outcome": "planned",
                        "history": _hist(state, stage=name, ok=True)}
            return {"failed_stage": None, "next_stage": nxt,
                    "outcome": "completed" if nxt == "END" else "running",
                    "history": _hist(state, stage=name, ok=True)}
        return node

    def _recover(self, state: GraphState) -> GraphState:
        stage = state["failed_stage"]
        error = state.get("last_error") or ""
        kind = classify(error)
        fp = fingerprint(error)
        attempts = dict(state.get("attempts", {}))
        fps = {k: list(v) for k, v in state.get("fingerprints", {}).items()}
        d = decide(kind, fp, attempts.get(stage, 0), fps.get(stage, []), self.policy)
        attempts[stage] = attempts.get(stage, 0) + 1
        fps.setdefault(stage, []).append(fp)
        self.orch._event("recovery", stage=stage, failure_class=kind, fingerprint=fp, action=d.action, reason=d.reason)
        self.orch._note(f"[yellow]  recovery: {stage} -> {d.action} ({d.reason})[/]")
        if d.action == "retry":
            if d.delay:
                self.sleep(d.delay)
            self.orch._reset_failed_tasks()  # failed tasks become pending again; done tasks are kept
        return {"attempts": attempts, "fingerprints": fps, "failure_class": kind,
                "next_stage": stage if d.action == "retry" else "END",
                "outcome": "running" if d.action == "retry" else d.action,
                "history": _hist(state, stage=stage, recovery=d.action, failure_class=kind)}

    # ---------------------------------------------------------------- routing
    @staticmethod
    def _after_stage(state: GraphState) -> str:
        if state.get("failed_stage"):
            return "recover"
        return state["next_stage"]

    @staticmethod
    def _after_recover(state: GraphState) -> str:
        return state["next_stage"]

    def build(self, checkpointer=None):
        from langgraph.graph import END, START, StateGraph
        g = StateGraph(GraphState)
        for name in STAGES:
            g.add_node(name, self._stage_node(name))
        g.add_node("recover", self._recover)
        g.add_edge(START, "design")
        targets = {s: s for s in STAGES} | {"recover": "recover", "END": END}
        for name in STAGES:
            g.add_conditional_edges(name, self._after_stage, targets)
        g.add_conditional_edges("recover", self._after_recover, {s: s for s in STAGES} | {"END": END})
        return g.compile(checkpointer=checkpointer)

    # ---------------------------------------------------------------- entry
    def run(self, request: str | None, stop_after: str | None = None, source: str | None = None) -> GraphState:
        from langgraph.checkpoint.sqlite import SqliteSaver
        self.orch._begin(request, source)
        db = str(self.orch.state_dir / "graph_checkpoints.sqlite")
        identity = hashlib.sha256(json.dumps([self.orch.state.request, self.orch.state.targets,
                                             self.orch.state.revision_source]).encode()).hexdigest()
        workflow = self.orch.state.workflow
        if workflow.get('identity') != identity:
            workflow = {'identity': identity, 'thread_id': uuid.uuid4().hex}
            self.orch.state.workflow = workflow
            self.orch._save()
        thread = workflow['thread_id']
        with SqliteSaver.from_conn_string(db) as saver:
            graph = self.build(saver)
            limit = (len(STAGES) + 1) * (self.policy.max_attempts + 2) + 5  # hard loop ceiling
            config = {"configurable": {"thread_id": thread}, "recursion_limit": limit}
            saved = graph.get_state(config)
            if saved.next:
                # Continue the unfinished super-step on the SAME thread; do not restart the graph.
                if saved.values.get('stop_after') != stop_after:
                    graph.update_state(config, {'stop_after': stop_after})
                final = graph.invoke(None, config=config, durability='sync')
            else:
                # A planned/paused/blocked invocation ended at END. A user Resume starts another
                # pass on the same thread while retaining the previous recovery history.
                old = saved.values or {}
                final = graph.invoke({"project": self.orch.project_path.name, "stop_after": stop_after,
                                      "attempts": old.get('attempts', {}), "fingerprints": old.get('fingerprints', {}),
                                      "history": old.get('history', []), "failed_stage": None,
                                      "last_error": None, "outcome": "running"}, config=config, durability='sync')
        self.orch.state.log.append(f"graph run {thread}: {final.get('outcome')}")
        self.orch._save()
        if final.get("outcome") in ("paused", "blocked"):
            stage = final.get("failed_stage")
            original = self._errors.get(stage)
            if original is not None and final["outcome"] == "paused":
                raise original  # keep the existing UsageLimitReached / BudgetReached / JobCancelled contract
            if final['outcome'] == 'paused':
                from .llm import BudgetReached, UsageLimitReached, JobCancelled
                message = final.get('last_error') or 'Workflow paused; progress saved.'
                if final.get('failure_class') == 'budget': raise BudgetReached(message)
                if final.get('failure_class') == 'cancelled': raise JobCancelled(message)
                if final.get('failure_class') == 'usage_limit':
                    raise UsageLimitReached(message, final.get('retry_at') or time.time())
            raise WorkflowStopped(final["outcome"], f"Stage '{stage}' {final['outcome']} "
                                  f"({final.get('failure_class')}): {final.get('last_error', '')[:1500]}", original)
        return final


def use_graph(cfg) -> bool:
    if cfg.engine == "legacy":
        return False
    try:
        import langgraph.checkpoint.sqlite  # noqa: F401
        return True
    except ImportError:
        if cfg.engine == "graph":
            raise SystemExit("ORCHESTRATOR_ENGINE=graph needs: pip install langgraph langgraph-checkpoint-sqlite")
        return False


def run_project(cfg, project_path, request, stop_after=None, targets=None, source=None):
    """Single entry point used by the CLI and dashboard."""
    from .locking import project_lock
    with project_lock(project_path):
        return _run_project(cfg, project_path, request, stop_after, targets, source)


def _run_project(cfg, project_path, request, stop_after=None, targets=None, source=None):
    from .orchestrator import Orchestrator
    orch = Orchestrator(cfg, project_path)
    orch.set_targets(targets)
    (orch.state_dir / "CANCEL").unlink(missing_ok=True)
    if use_graph(cfg):
        return GraphEngine(orch).run(request, stop_after, source=source)
    return orch.run(request, stop_after, source=source)
