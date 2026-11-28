"""Host-side inspection and resumable, isolated copies for existing Unity projects."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import tempfile
import zipfile
from pathlib import Path

from .state import atomic_json

ROOTS = ("Assets", "Packages", "ProjectSettings")
OPTIONAL_ROOTS = ("Docs", "Tests", "AGENTS.md", "README.md", "UserSettings/Search.index", "UserSettings/Search.settings")


def source_from_request(request: str) -> str | None:
    if not re.search(r"\b(?:improve|rework|revise|polish|modify|update)\s+(?:the\s+)?existing\b", request, re.I):
        return None
    match = re.search(r'(?i)\bat\s+(?:"([^"\r\n]+)"|`([^`\r\n]+)`|([A-Z]:[\\/][^\r\n]*?)(?=\.\s|[\r\n]|$))', request)
    if not match:
        raise ValueError('Specify the existing Unity project using Source project or --source; quote paths containing spaces.')
    return next(part for part in match.groups() if part is not None).rstrip(".")


def validate_source(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Revision source and destination must be separate, non-nested folders.")
    if not all((source / root).is_dir() for root in ROOTS):
        raise ValueError(f"Not a Unity project: {source}. Assets, Packages and ProjectSettings are required.")
    if (source / "Temp/UnityLockfile").exists():
        raise ValueError("Close the source project in Unity before creating the revision snapshot.")
    for root in ROOTS + OPTIONAL_ROOTS:
        if not (source / root).exists():
            continue
        ancestor = source
        for part in Path(root).parts:
            ancestor = ancestor / part
            if ancestor.is_symlink() or (hasattr(ancestor, 'is_junction') and ancestor.is_junction()):
                raise ValueError(f"Linked project paths are not supported for revision copies: {ancestor}")
        for path in [source / root, *(source / root).rglob("*")]:
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise ValueError(f"Linked project paths are not supported for revision copies: {path}")
    manifest = json.loads((source / "Packages/manifest.json").read_text(encoding="utf-8"))
    if any(str(value).startswith("file:") for value in manifest.get("dependencies", {}).values()):
        raise ValueError("Revision copies currently require registry, Git or embedded packages; local file: dependencies must be embedded first.")


def hashes(project: Path) -> dict[str, str]:
    result = {}
    for root in ROOTS + OPTIONAL_ROOTS:
        base = project / root
        for path in ([base] if base.is_file() else sorted(base.rglob("*"))):
            if path.is_file():
                with path.open('rb') as stream:
                    result[path.relative_to(project).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def prepare(source: Path, destination: Path, state_dir: Path, *, replace_failed_scaffold: bool = False) -> dict:
    source, destination = source.resolve(), destination.resolve()
    checkpoint = state_dir / "revision.json"
    if checkpoint.exists():
        record = json.loads(checkpoint.read_text(encoding="utf-8"))
        if record["source"] != str(source) or record["destination"] != str(destination):
            raise ValueError("Revision checkpoint belongs to a different source or destination.")
        if record.get("ready"):
            if not all((destination / root).is_dir() for root in ROOTS):
                raise RuntimeError("Revision copy is missing; restore it from the snapshot before resuming.")
            return record
        if destination.is_dir() and all((destination / root).is_dir() for root in ROOTS) and hashes(destination) == record.get("baseline"):
            record["ready"] = True
            atomic_json(checkpoint, record)
            return record
    validate_source(source, destination)
    state_dir.mkdir(parents=True, exist_ok=True)
    baseline = hashes(source)
    snapshot = state_dir / "source-snapshot.zip"
    staging = Path(tempfile.mkdtemp(prefix="revision-copy-", dir=state_dir))
    if destination.exists() and any(destination.iterdir()):
        if not replace_failed_scaffold:
            raise ValueError("Revision destination is not empty. Choose a new destination to preserve existing work.")
        # This migration is permitted only for a failed pre-revision job with no completed tasks.
        archive = state_dir / f"previous-attempt-{time.time_ns()}"
    else:
        archive = None
    with zipfile.ZipFile(snapshot, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for rel in baseline:
            bundle.write(source / rel, rel)
    if hashes(source) != baseline:
        raise RuntimeError("Source project changed during snapshot. Close editors and retry.")
    for root in ROOTS + OPTIONAL_ROOTS:
        if (source / root).is_dir():
            shutil.copytree(source / root, staging / root, dirs_exist_ok=True)
        elif (source / root).is_file():
            (staging / root).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / root, staging / root)
    if hashes(staging) != baseline:
        raise RuntimeError("Source project changed during copying. Retry with the source closed.")
    record = {"source": str(source), "destination": str(destination), "snapshot": str(snapshot),
              "baseline": baseline, "ready": False}
    atomic_json(checkpoint, record)
    if archive:
        destination.rename(archive)
    if destination.exists():
        destination.rmdir()  # Empty only; never delete project contents.
    staging.rename(destination)
    record["ready"] = True
    atomic_json(checkpoint, record)
    return record


def inspect(project: Path, record: dict, budget: int = 32000) -> str:
    lines = ["REVISION: the host has inspected and copied the source. Work ONLY in the destination.",
             f"Source (read-only): {record['source']}", f"Destination: {project}",
             f"Backup: {record['snapshot']}",
             "Preserve existing gameplay, stage IDs, progression, input, save keys and application identity unless feedback explicitly changes them.",
             "Do not plan inspection, copying, backup or build execution as AI tasks: the host performs these operations.",
             "Generate targeted Assets code edits and idempotent presentation builders opening EXISTING scenes; never replace scenes with empty ones.",
             "Do not rebuild the original gameplay or rerun old layout builders. Add tests for the revised behavior.",
             "Delivery is a separate Windows player, tested by the host. Android device performance remains unverified."]
    files = list(record["baseline"])
    lines += ["ACTUAL FILE INVENTORY (bounded):", "\n".join(files)[:8000]]
    for rel in ("ProjectSettings/ProjectVersion.txt", "ProjectSettings/EditorBuildSettings.asset", "Packages/manifest.json"):
        lines += [f"===== {rel} =====", (project / rel).read_text(encoding="utf-8", errors="replace")[:5000]]
    # Include authored data and a broad API index so the planner can select actual dependencies.
    for rel in files:
        if rel.endswith('.asset') and not any(x in rel for x in ('TextMesh Pro/', 'Settings/GA_')):
            path = project / rel
            if path.exists() and path.stat().st_size < 12000:
                text = path.read_text(encoding='utf-8', errors='replace')
                if text.startswith('%YAML'):
                    lines += [f"===== {rel} =====", text]
                    if sum(map(len, lines)) > budget // 3:
                        break
    used = sum(map(len, lines))
    scripts = [p for p in files if p.endswith((".cs", ".asmdef")) and not any(x in p for x in ("TextMesh Pro/", "GameAgentBridge/", "LudoFabricStarter/"))]
    scripts.sort(key=lambda rel: ('/Editor/' in rel, '/Tests/' in rel, rel))
    api = []
    for rel in scripts:
        path = project / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        api.append(rel + '\n' + '\n'.join(line.strip() for line in text.splitlines()
                                         if re.match(r'\s*(public |namespace )', line))[:2000])
        block = f"===== {rel} =====\n{text}"
        if used + len(block) > budget - 14000:
            continue
        lines.append(block); used += len(block)
    lines += ['ADDITIONAL PUBLIC API INDEX (bounded):', '\n'.join(api)[:14000]]
    return "\n".join(lines)[:budget]
