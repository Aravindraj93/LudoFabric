"""Turns a project's saved state into human- and AI-friendly documents (Markdown / JSON)."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path


def _ul(items) -> str:
    items = items or []
    if isinstance(items, str):
        items = [items]
    return "\n".join(f"- {str(i).strip()}" for i in items) or "- (none)"


def _sec(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}\n"


def design_md(d: dict) -> str:
    art = d.get("art") or {}
    palette = " ".join(f"`{c}`" for c in art.get("palette", [])) or "(none)"
    parts = [
        f"# {d.get('title', 'Untitled')}: Game Design Document\n",
        f"**Genre:** {d.get('genre', '')}  \n**Perspective:** {d.get('perspective', '')}  \n**Platform:** {d.get('platform', '')}\n",
        f"> {d.get('pitch', '')}\n",
        _sec("Core loop", d.get("core_loop", "")),
        _sec("Mechanics", _ul(d.get("mechanics"))),
        _sec("Controls", _ul(d.get("controls"))),
        _sec("Win / lose", d.get("win_lose", "") if isinstance(d.get("win_lose"), str) else _ul(d.get("win_lose"))),
        _sec("Levels", _ul(d.get("levels"))),
        _sec("Scope and asset policy", f"Scope: {d.get('scope', 'vertical_slice')}\n\nAsset policy: {d.get('asset_policy', 'free_assets')}"),
        _sec("Progression", "\n\n".join(
            f"### {stage.get('title', '')} ({stage.get('id', '')})\n\n"
            f"**Objective:** {stage.get('objective', '')}\n\n**New challenge:** {stage.get('new_challenge', '')}\n\n"
            f"**Difficulty:** {stage.get('difficulty', '')}\n\n**Unlock:** {stage.get('unlock_rule', '')}"
            for stage in d.get("progression", [])) or "No campaign progression requested."),
        _sec("Replay incentives", _ul(d.get("replay_incentives"))),
        _sec("UI screens", _ul(d.get("ui_screens"))),
        _sec("Art direction", "\n".join([
            f"**Style:** {art.get('style', '')}", "", f"**Palette:** {palette}", "",
            f"**Lighting:** {art.get('lighting', '')}", "", f"**Post-processing:** {art.get('post_processing', '')}", "",
            f"**Camera feel:** {art.get('camera_feel', '')}", "", "**References:**", _ul(art.get("references")),
        ])),
        _sec("Visual effects (VFX)", _ul(d.get("vfx"))),
        _sec("Animation", _ul(d.get("animation"))),
        _sec("Game feel / juice", _ul(d.get("game_feel"))),
        _sec("Audio", _ul(d.get("audio"))),
        _sec("Assets needed", _ul(d.get("assets_needed"))),
        _sec("Decisions made by the AI director", _ul(d.get("assumptions"))),
    ]
    return "\n".join(parts)


def plan_md(p: dict) -> str:
    a = p.get("architecture") or {}
    lines = [f"# Production plan\n", _sec("Architecture", f"**Root namespace:** `{a.get('root_namespace', '')}`\n\n{a.get('overview', '')}")]
    sys_rows = ["| System | Responsibility | Scripts |", "|---|---|---|"]
    for s in a.get("systems", []):
        scripts = "<br>".join(f"`{x}`" for x in s.get("scripts", []))
        sys_rows.append(f"| {s.get('name', '')} | {s.get('responsibility', '').replace('|', '/')} | {scripts} |")
    lines.append(_sec("Systems", "\n".join(sys_rows)))
    lines.append(_sec("Scenes (build order)", _ul([f"`{x}`" for x in a.get("scenes", [])])))
    lines.append(_sec("Conventions", _ul(a.get("conventions"))))
    if p.get("contracts"):
        lines.append(_sec("Shared code contracts", "\n\n".join(
            f"### {contract.get('path', '')}\n\nOwner: {contract.get('owner', '')}\n\n" +
            _ul(contract.get("public_api")) for contract in p["contracts"])))
    asset_rows = ["| Key | Kind | Search | Purpose |", "|---|---|---|---|"]
    for x in p.get("assets", []):
        asset_rows.append(f"| `{x.get('key', '')}` | {x.get('kind', '')} | {x.get('query', '')} | {x.get('purpose', '').replace('|', '/')} |")
    lines.append(_sec("Assets", "\n".join(asset_rows)))
    task_md = []
    for i, t in enumerate(p.get("tasks", []), 1):
        task_md.append(
            f"### {i}. [{t.get('role', '')}] {t.get('title', '')}  _(id: {t.get('id', '')}, status: {t.get('status', 'pending')})_\n\n"
            f"{t.get('description', '')}\n\n"
            f"**Outputs:**\n{_ul([f'`{o}`' for o in t.get('outputs', [])])}\n\n"
            f"**Depends on:** {', '.join(t.get('depends_on', [])) or 'nothing'}\n\n"
            f"**Acceptance:**\n{_ul(t.get('acceptance'))}\n")
    lines.append(_sec("Tasks", "\n".join(task_md)))
    return "\n".join(lines)


AI_PROMPT_HEADER = """# AI build prompt: {title}

> Paste this whole document into any capable coding AI (Codex, Claude Code, Gemini, Cursor, Copilot...).
> It contains the complete design, the architecture and an ordered task list.

You are a senior Unity team in one person: gameplay programmer, technical artist, VFX artist, animator,
UI engineer and level designer. Build the game at its stated scope; preserve requested progression and level count.

## Hard requirements
- Unity 6 (6000.x), Universal Render Pipeline, C#, new Input System (UnityEngine.InputSystem), Cinemachine 3, TextMeshPro.
- Clean modular architecture: one class per file, ScriptableObject configs for tunables, events between systems.
- Everything must compile on the first try: no pseudo-code, no TODO stubs, complete files only.
- Follow the art direction and performance constraints. Do not add expensive post-processing, downloaded
  assets or scope that the design forbids. Preserve the shared API contracts and executable acceptance criteria.
- Use free CC0 / CC-BY 3D assets (Poly Haven, Sketchfab) where listed, and build the rest procedurally.
- Work through the tasks IN ORDER. After each task, list the files you created and how to test them in the Editor.
"""


def ai_prompt_md(d: dict, p: dict | None) -> str:
    out = [AI_PROMPT_HEADER.format(title=d.get("title", "the game")), "---\n", design_md(d)]
    if p:
        out += ["---\n", plan_md(p)]
    else:
        out += ["---\n", "## Next step\n\nNo production plan yet. First propose the architecture and an ordered task list, then implement it.\n"]
    return "\n".join(out)


def list_docs(agent_dir: Path, state: dict) -> list[dict]:
    """Returns doc descriptors + content generators for a project."""
    d, p = state.get("design"), state.get("plan")
    docs: list[dict] = []

    def add(doc_id, title, desc, icon, filename, content, kind="md"):
        docs.append({"id": doc_id, "title": title, "desc": desc, "icon": icon, "filename": filename,
                     "kind": kind, "content": content, "size": len(content.encode("utf-8"))})

    if d:
        add("ai-prompt", "AI build prompt", "One file to paste into any AI model to build this game", "🤖",
            "AI_BUILD_PROMPT.md", ai_prompt_md(d, p))
        add("gdd", "Game design document", "Mechanics, controls, art direction, VFX, animation, audio", "🎬",
            "GAME_DESIGN.md", design_md(d))
    if p:
        add("plan", "Production plan", "Architecture, systems, scenes, assets and the ordered task list", "🧠",
            "PRODUCTION_PLAN.md", plan_md(p))
    report = agent_dir / "REPORT.md"
    if report.exists():
        add("report", "Build report", "What was built, reviews and logs", "📋", "REPORT.md",
            report.read_text(encoding="utf-8", errors="replace"))
    prompts_dir = agent_dir / "prompts"
    if prompts_dir.exists():
        titles = {t.get("id"): t.get("title") for t in (p or {}).get("tasks", [])}
        titles.update({hashlib.sha256(str(t.get("id", "")).encode()).hexdigest(): t.get("title")
                       for t in (p or {}).get("tasks", [])})
        for f in sorted(prompts_dir.glob("*.md")):
            label = titles.get(f.stem) or f.stem[:12]
            add(f"task-{f.stem}", f"Task brief: {label}", "The exact prompt sent to the specialist agent",
                "🛠️", f"task_{f.name}", f.read_text(encoding="utf-8", errors="replace"))
    if d:
        add("gdd-json", "Design (JSON)", "Machine-readable design for tools and other agents", "🧩", "GDD.json",
            json.dumps(d, indent=2, ensure_ascii=False), kind="json")
    if p:
        add("plan-json", "Plan (JSON)", "Machine-readable plan", "🧩", "PLAN.json",
            json.dumps(p, indent=2, ensure_ascii=False), kind="json")
    return docs

