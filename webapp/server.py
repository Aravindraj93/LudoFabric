"""LudoFabric Studio - local web dashboard.

Runs ONLY on this machine (127.0.0.1). Uses the AI CLIs you are logged into locally (Codex, Claude Code,
Gemini CLI, Qwen Code) with your own subscription - credentials never leave the CLI.
"""
from __future__ import annotations

import glob
import json
import os
import re
import threading
import time
import traceback
from pathlib import Path

from dotenv import set_key
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gameagent.config import Config
from gameagent.providers import PROVIDERS, npm_install
from gameagent.ui import bus, console
from gameagent.state import atomic_json

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
STATIC = Path(__file__).resolve().parent / "static"
STUDIO_DIR = ROOT / ".studio"


def registered_projects() -> dict:
    path = STUDIO_DIR / "projects.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def project_path(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise HTTPException(400, "bad project name")
    return Path(registered_projects().get(name) or projects_root() / name)


def state_path(path: Path) -> Path:
    return path.parent / f"{path.name}.agent" / "state.json"

app = FastAPI(title="LudoFabric Studio")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ----------------------------------------------------------------------------- settings
def projects_root() -> Path:
    p = Path(os.getenv("PROJECTS_ROOT") or r"C:\UnityProjects")
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_env(key: str, value: str) -> None:
    if not ENV_FILE.exists():
        ENV_FILE.write_text("", encoding="utf-8")
    set_key(str(ENV_FILE), key, value, quote_mode="never")
    os.environ[key] = value


class Settings(BaseModel):
    llm_backend: str | None = None
    unity_editor: str | None = None
    projects_root: str | None = None
    sketchfab_token: str | None = None
    codex_reasoning: str | None = None
    polish_rounds: int | None = None
    model_orchestrator: str | None = None
    model_coder: str | None = None
    model_vision: str | None = None


MODEL_FIELDS = ("model_orchestrator", "model_coder", "model_vision")


def provider_models(pid: str) -> dict:
    path = STUDIO_DIR / "models.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if pid in saved:
        return saved[pid]
    cfg = Config()
    return {field: getattr(cfg, field) if pid == cfg.llm_backend else "" for field in MODEL_FIELDS}


def apply_models(cfg: Config, pid: str, overrides: dict | None = None, model: str | None = None) -> None:
    values = provider_models(pid)
    for field in MODEL_FIELDS:
        value = (overrides or {}).get(field)
        setattr(cfg, field, model.strip() if model is not None else
                (value.strip() if value is not None else values.get(field, "")))


@app.get("/api/settings")
def get_settings():
    cfg = Config()
    return {
        "llm_backend": cfg.llm_backend, "unity_editor": cfg.unity_editor,
        "unity_ok": bool(cfg.unity_editor) and Path(cfg.unity_editor).exists(),
        "projects_root": str(projects_root()), "sketchfab_token_set": bool(cfg.sketchfab_token),
        "codex_reasoning": cfg.codex_reasoning, "polish_rounds": cfg.polish_rounds,
        "models": provider_models(cfg.llm_backend),
        "unity_candidates": sorted(glob.glob(r"C:\Program Files\Unity\Hub\Editor\*\Editor\Unity.exe")
                                   + glob.glob(r"C:\Program Files\Unity*\Editor\Unity.exe"), reverse=True),
    }


@app.post("/api/settings")
def post_settings(s: Settings):
    pid = s.llm_backend or Config().llm_backend
    selected = provider_models(pid)
    for field in MODEL_FIELDS:
        if getattr(s, field) is not None:
            selected[field] = getattr(s, field).strip()
    models_path = STUDIO_DIR / "models.json"
    saved = json.loads(models_path.read_text(encoding="utf-8")) if models_path.exists() else {}
    saved[pid] = selected
    atomic_json(models_path, saved)
    mapping = {"llm_backend": "LLM_BACKEND", "unity_editor": "UNITY_EDITOR", "projects_root": "PROJECTS_ROOT",
               "sketchfab_token": "SKETCHFAB_API_TOKEN", "codex_reasoning": "CODEX_REASONING_EFFORT",
               "polish_rounds": "POLISH_ROUNDS"}
    for field, key in mapping.items():
        v = getattr(s, field)
        if v is not None:
            save_env(key, str(v).strip())
    return get_settings()


# ----------------------------------------------------------------------------- providers
@app.get("/api/providers")
def providers():
    return [p.info() for p in PROVIDERS.values()] + [api_provider_info(pid) for pid in ("openai", "chat")]


def api_provider_info(pid: str) -> dict:
    return {"id": pid, "name": "OpenAI API" if pid == "openai" else "OpenAI-compatible API",
            "installed": True, "logged_in": None, "api": True,
            "plan": "Configure API key / endpoint in .env; API billing is separate from CLI subscriptions.",
            "can_logout": False}


@app.get("/api/providers/{pid}/models")
def model_choices(pid: str):
    if pid not in PROVIDERS and pid not in ("openai", "chat"):
        raise HTTPException(400, "unknown provider")
    choices = []
    if pid == "codex":
        cache = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex") / "models_cache.json"
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            choices = [{"id": m["slug"], "label": m.get("display_name") or m["slug"]}
                       for m in data.get("models", []) if m.get("slug") and
                       m.get("visibility", "list") != "hide" and "auto-review" not in m["slug"]]
        except (OSError, ValueError, TypeError, KeyError):
            pass
    return {"models": choices, "selected": provider_models(pid),
            "note": "Suggestions may be stale or belong to another CLI surface. Test a model to check account access. "
                    "Enter any supported model ID, or leave blank for the provider default."}


class ModelTest(BaseModel):
    model: str = ""


def _provider(pid: str):
    if pid not in PROVIDERS:
        raise HTTPException(404, "unknown provider")
    return PROVIDERS[pid]


@app.post("/api/providers/{pid}/install")
def install(pid: str):
    p = _provider(pid)
    console.print(f"Installing {p.name}: npm install -g {p.npm_package} ...")
    r = npm_install(p)
    console.print((r.stdout or "")[-1500:] + (r.stderr or "")[-1500:])
    return {"ok": r.returncode == 0 and p.installed(), "provider": p.info()}


@app.post("/api/providers/{pid}/login")
def login(pid: str):
    p = _provider(pid)
    if not p.installed():
        raise HTTPException(400, f"{p.name} is not installed")
    p.open_login_window()
    return {"ok": True, "message": f"A terminal window opened for {p.name}. Finish the sign-in there. {p.notes}"}


@app.post("/api/providers/{pid}/logout")
def logout(pid: str):
    p = _provider(pid)
    ok, msg = p.logout()
    console.print(f"{p.name} sign out: {msg}")
    return {"ok": ok, "message": msg, "provider": p.info()}


@app.post("/api/providers/{pid}/test")
def test(pid: str, body: ModelTest | None = None):
    from gameagent.llm import LLM
    p = _provider(pid) if pid not in ("openai", "chat") else None
    cfg = Config()
    cfg.llm_backend = pid
    apply_models(cfg, pid)
    try:
        llm = LLM(cfg)
        t0 = time.time()
        out = llm.complete(model=body.model.strip() if body else "", instructions="You are a connectivity test.",
                           prompt="Reply with exactly: GAMEAGENT_OK", name=f"test_{pid}", retries=1)
        ok = "GAMEAGENT_OK" in out
        return {"ok": ok, "seconds": round(time.time() - t0), "output": out[:300],
                "provider": p.info() if p else api_provider_info(pid)}
    except BaseException as exc:  # FatalLLMError is a SystemExit
        return {"ok": False, "output": str(exc)[:800], "provider": p.info() if p else api_provider_info(pid)}


# ----------------------------------------------------------------------------- jobs
class JobRequest(BaseModel):
    request: str | None = None
    project: str | None = None
    provider: str = "codex"
    mode: str = "design"  # design | build
    model: str | None = None
    model_orchestrator: str | None = None
    model_coder: str | None = None
    model_vision: str | None = None


class JobRunner:
    def __init__(self):
        self.thread: threading.Thread | None = None
        self.status = {"running": False, "project": None, "mode": None, "error": None, "started": None}
        saved = STUDIO_DIR / "job.json"
        if saved.exists():
            self.status.update(json.loads(saved.read_text(encoding="utf-8")))
            if self.status.get("running"):
                self.status.update(running=False, state="interrupted",
                                   error="Tool closed during the job. Select the project and Resume.")

    def persist(self) -> None:
        atomic_json(STUDIO_DIR / "job.json", self.status)
        if self.status.get("project_path"):
            path = Path(self.status["project_path"])
            atomic_json(state_path(path).parent / "job.json", self.status)

    def busy(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, job: JobRequest, project_path: Path) -> None:
        cfg = Config()
        apply_models(cfg, job.provider, job.model_dump(), job.model)
        # Resolve configuration now so settings changed during execution cannot alter this job.
        for field in MODEL_FIELDS:
            setattr(job, field, getattr(cfg, field))
        job.model = None
        self.status = {"running": True, "project": project_path.name, "mode": job.mode, "error": None,
                       "started": time.time(), "provider": job.provider, "project_path": str(project_path),
                       "state": "running"}
        self.status["models"] = {field: getattr(job, field) for field in MODEL_FIELDS}
        self.persist()
        self.thread = threading.Thread(target=self._run, args=(job, project_path), daemon=True)
        self.thread.start()

    def _run(self, job: JobRequest, project_path: Path) -> None:
        from gameagent.orchestrator import Orchestrator
        try:
            cfg = Config()
            cfg.llm_backend = job.provider
            apply_models(cfg, job.provider, job.model_dump(), job.model)
            cfg.validate(need_unity=(job.mode == "build"))
            orch = Orchestrator(cfg, project_path)
            orch.run(job.request, stop_after="planned" if job.mode == "design" else None)
            self.status["state"] = "completed"
        except BaseException as exc:  # includes SystemExit from validation / fatal LLM errors
            from gameagent.llm import UsageLimitReached
            msg = str(exc) or exc.__class__.__name__
            if not isinstance(exc, SystemExit):
                msg += "\n" + traceback.format_exc()[-2000:]
            self.status["error"] = msg
            self.status["state"] = "waiting_for_limit" if isinstance(exc, UsageLimitReached) else "failed"
            if isinstance(exc, UsageLimitReached):
                self.status["retry_at"] = exc.retry_at
            console.print(f"JOB FAILED: {msg}")
        finally:
            self.status["running"] = False
            self.persist()
            console.print("=== job finished ===")


runner = JobRunner()


def slugify(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)[:4]
    return "".join(w.capitalize() for w in words) or f"Game{int(time.time())}"


@app.post("/api/jobs")
def start_job(job: JobRequest):
    if runner.busy():
        raise HTTPException(409, "A job is already running (Unity allows one build at a time).")
    if job.provider not in PROVIDERS and job.provider not in ("openai", "chat"):
        raise HTTPException(400, "unknown provider")
    name = re.sub(r"[^A-Za-z0-9_-]", "", job.project or "") or slugify(job.request or "")
    path = project_path(name)
    state = state_path(path)
    if not job.request and not state.exists():
        raise HTTPException(400, "Describe your game first.")
    if job.request and state.exists():
        saved = json.loads(state.read_text(encoding="utf-8"))
        if saved.get("request") and saved["request"] != job.request:
            raise HTTPException(409, "This project has a different saved request. Use Resume or choose a new project name.")
    runner.start(job, path)
    return {"ok": True, "project": name}


class ImportProject(BaseModel):
    path: str


@app.post("/api/projects/import")
def import_project(body: ImportProject):
    if runner.busy():
        raise HTTPException(409, "Wait for the current job before opening another project.")
    path = Path(body.path).expanduser()
    if not path.is_absolute():
        raise HTTPException(400, "Enter the full Unity project folder path.")
    path = path.resolve()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", path.name):
        raise HTTPException(400, "Project folder names currently support letters, numbers, hyphens and underscores.")
    if not state_path(path).exists():
        raise HTTPException(400, "No LudoFabric checkpoint found. Keep the adjacent <Project>.agent folder with the project. "
                            "An arbitrary Unity project cannot be resumed without its saved plan.")
    from gameagent.state import State
    try:
        State.load(state_path(path))
    except (ValueError, OSError) as exc:
        raise HTTPException(400, f"Checkpoint cannot be read: {exc}")
    known = registered_projects()
    existing = Path(known.get(path.name) or projects_root() / path.name).resolve()
    if existing != path and (state_path(existing).exists() or path.name in known):
        raise HTTPException(409, "Another project with this name is already registered.")
    known[path.name] = str(path)
    atomic_json(STUDIO_DIR / "projects.json", known)
    return {"ok": True, "project": path.name, "path": str(path)}


@app.get("/api/jobs/current")
def current_job():
    return {**runner.status, "running": runner.busy()}


@app.get("/api/events")
def events(since: int = 0):
    def gen():
        last = since
        while True:
            lines = bus.since(last, timeout=15)
            if not lines:
                yield ": keep-alive\n\n"
                continue
            for n, line in lines:
                last = n
                yield f"id: {n}\ndata: {json.dumps(line)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


# ----------------------------------------------------------------------------- projects
def _agent_dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise HTTPException(400, "bad project name")
    return state_path(project_path(name)).parent


@app.get("/api/projects")
def list_projects():
    out = []
    dirs = set(projects_root().glob("*.agent"))
    dirs.update(state_path(Path(p)).parent for p in registered_projects().values())
    for d in sorted((p for p in dirs if p.exists()), key=lambda p: p.stat().st_mtime, reverse=True):
        st = d / "state.json"
        if not st.exists():
            continue
        try:
            s = json.loads(st.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"name": d.name[:-6], "phase": s.get("phase"), "request": s.get("request", ""),
                    "title": (s.get("design") or {}).get("title"), "modified": d.stat().st_mtime})
    return out


@app.get("/api/projects/{name}")
def project(name: str):
    d = _agent_dir(name)
    st = d / "state.json"
    if not st.exists():
        raise HTTPException(404, "not found")
    s = json.loads(st.read_text(encoding="utf-8"))
    shots = sorted(str(p.relative_to(d)).replace("\\", "/") for p in d.glob("screens_round*/*.png"))
    report = (d / "REPORT.md").read_text(encoding="utf-8") if (d / "REPORT.md").exists() else ""
    return {"name": name, "state": s, "screenshots": shots, "report": report,
            "unity_project": str(project_path(name))}


@app.get("/api/projects/{name}/file/{rel:path}")
def project_file(name: str, rel: str):
    d = _agent_dir(name).resolve()
    p = (d / rel).resolve()
    if not p.is_relative_to(d) or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(p)


def _project_docs(name: str) -> list[dict]:
    from gameagent.docs import list_docs
    d = _agent_dir(name)
    st = d / "state.json"
    if not st.exists():
        raise HTTPException(404, "not found")
    return list_docs(d, json.loads(st.read_text(encoding="utf-8")))


@app.get("/api/projects/{name}/docs")
def project_docs(name: str):
    return [{k: v for k, v in doc.items() if k != "content"} for doc in _project_docs(name)]


@app.get("/api/projects/{name}/docs.zip")
def project_docs_zip(name: str):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for doc in _project_docs(name):
            z.writestr(doc["filename"], doc["content"])
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}_docs.zip"'})


@app.get("/api/projects/{name}/docs/{doc_id}")
def project_doc(name: str, doc_id: str, download: bool = False):
    doc = next((x for x in _project_docs(name) if x["id"] == doc_id), None)
    if not doc:
        raise HTTPException(404, "document not found")
    media = "application/json" if doc["kind"] == "json" else "text/markdown"
    headers = {"Content-Disposition": f'attachment; filename="{doc["filename"]}"'} if download else {}
    return Response(doc["content"], media_type=f"{media}; charset=utf-8", headers=headers)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "studio.html").read_text(encoding="utf-8")

