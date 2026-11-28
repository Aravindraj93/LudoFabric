"""Resolves the TechLead's AssetRequests into downloaded, licensed files inside the Unity project."""
from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

import httpx
from pydantic import BaseModel

from ..config import Config
from ..schemas import AssetRequest
from .polyhaven import PolyHaven
from .sketchfab import Sketchfab
from .safety import contained, validate_key
from ..state import atomic_json

from ..ui import console


class AssetRecord(BaseModel):
    key: str
    kind: str
    query: str
    purpose: str = ""
    source: str = ""            # polyhaven | sketchfab | none
    source_id: str = ""
    name: str = ""
    path: str = ""              # Unity project-relative path of the main file (Assets/...)
    maps: dict[str, str] = {}   # texture sets: albedo/normal/roughness/ao
    license: str = ""
    authors: list[str] = []
    url: str = ""
    animated: bool = False
    status: str = "missing"     # ok | missing


ASSET_USAGE = """

USING DOWNLOADED FREE ASSETS (from the ASSET REGISTRY in your brief; never invent paths):
- Only use registry entries marked with a path. Entries marked MISSING must get a deliberate procedural fallback
  (generated mesh or primitives + palette materials matching the art style), never an empty placeholder or a pink cube.
- Models (.glb/.gltf, imported by glTFast as prefabs): in Editor builders use
  SceneKit.InstantiateModel(path, position, rotation, targetHeight: <real-world metres>, parent, addMeshColliders)
  so scale is correct regardless of source units; check the result for null and fall back procedurally.
  Use addMeshColliders only for static set dressing the player touches; for moving/gameplay objects add simple
  Box/Capsule colliders sized from renderer bounds. Mark static scenery isStatic for batching and NavMesh.
- At runtime, reference models through serialized prefab fields assigned by the builder (AssetDatabase is Editor-only).
- Variation: reuse one asset with random yaw, +/-15% scale and clustering instead of requesting near-duplicates.
- Materials: glTF materials import as URP-compatible; to unify a mixed-source or stylized look, override them with
  palette-tinted materials (SceneKit.CreateLitMaterial) rather than editing third-party files.
- HDRI: SceneKit.SetupHdriSky(path). PBR textures: build a URP Lit material from the registry maps (albedo/normal/roughness/ao)
  and set tiling to world scale for ground/walls.
- Animated models (animated=true): glTFast imports their clips; drive them via the Animator/Animation components
  created in code and verify the clip exists before playing it. Unrigged models are animated procedurally (tweens, bob, spin).
- Never modify, move or delete files under Assets/ThirdParty/; licences and Assets/ThirdParty/CREDITS.md are generated."""

ASSET_ROLES = {"level", "graphics", "animation", "gameplay", "vfx"}

EXTERNAL_ASSET_USAGE = """
FREE ASSETS IN AN EXISTING PROJECT:
- Use only real paths in the asset registry; never invent a download or claim a missing model is available.
- Instantiate imported GameObject prefabs with PrefabUtility.InstantiatePrefab in Editor methods and assign
  serialized prefab references for runtime use. Do not use GameAgent.Bridge.SceneKit or LudoFabricStarter.
- Preserve the project's render pipeline and import settings. Use its existing shaders/material conventions.
  Do not assume imported materials are compatible: inspect them and assign compatible materials to copies.
- Keep original third-party files and attribution intact; write material variants outside Assets/ThirdParty/.
- Scale from renderer bounds, configure appropriate colliders, and check imported animation clips before use.
- Missing assets are unresolved requirements: report them honestly; use a reversible temporary fallback only
  when necessary. Do not describe a primitive fallback as a finished licensed character or model.
- HDRIs/textures use the project's existing texture importer and sky/material APIs, not SceneKit helpers.
"""


def sources_note(cfg: Config, gltf_supported: bool = True) -> str:
    """Tells planners which free sources are ACTUALLY usable in this run, so they never plan unobtainable assets."""
    sketchfab = bool(cfg.sketchfab_token)
    lines = ["\n\nFREE ASSET SOURCES AVAILABLE IN THIS RUN (plan only what these can supply):",
             "- Poly Haven (CC0, no attribution required): photoreal scanned props, nature (trees, rocks, plants, logs), "
             "furniture, tools, crates/barrels, HDRI skies, PBR ground/wall textures. NO characters, creatures, vehicles, "
             "weapons or stylized assets."]
    if sketchfab:
        from .sketchfab import allowed_licenses
        licences = "CC0 / CC-BY"
        if "by-sa" in allowed_licenses():
            licences += " / CC-BY-SA (ShareAlike obligations require review before distribution)"
        lines.append(f"- Sketchfab (ENABLED; only {licences}, downloadable, <= {cfg.max_faces} faces; credits "
                     "are generated): characters, creatures, vehicles, stylized/low-poly sets. Set needs_rig=true for "
                     "animated characters and preferred_source='sketchfab' for anything Poly Haven lacks.")
    else:
        lines.append("- Sketchfab: DISABLED (no SKETCHFAB_API_TOKEN). Do NOT request characters, creatures, vehicles or "
                     "rigged models from that source. Record availability limits and use a deliberate procedural "
                     "alternative only where it fits the requested art direction.")
    if not gltf_supported:
        lines.append("- This project has no glTF importer (com.unity.cloud.gltfast): request ONLY kind=hdri or "
                     "kind=texture; models cannot be imported.")
    lines.append("- Every request: short generic query + 1-3 simpler alternatives; a clear purpose; never a quota. "
                 "Unfound assets become MISSING and agents build a styled procedural fallback, so plan for that.")
    return "\n".join(lines)


class AssetManager:
    def __init__(self, cfg: Config, project: Path, state_dir: Path):
        self.cfg = cfg
        self.project = project
        self.registry_path = state_dir / "assets.json"
        self.http = httpx.Client(timeout=120)
        self.polyhaven = PolyHaven(self.http)
        self.sketchfab = Sketchfab(self.http, cfg.sketchfab_token, cfg.max_faces)
        self.records: dict[str, AssetRecord] = {}
        if self.registry_path.exists():
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            self.records = {k: AssetRecord(**v) for k, v in data.items()}

    def _rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.project.resolve()).as_posix()

    def save(self) -> None:
        atomic_json(self.registry_path, {k: v.model_dump() for k, v in self.records.items()})
        self._write_credits()

    def _write_credits(self) -> None:
        lines = ["# Third-party asset credits", "", "Generated automatically by LudoFabric.", ""]
        for r in self.records.values():
            if r.status != "ok":
                continue
            authors = ", ".join(a for a in r.authors if a) or "unknown"
            lines.append(f"- **{r.name}** ({r.source}) by {authors} - {r.license} - {r.url}")
        out = contained(self.project, "Assets/ThirdParty/CREDITS.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    def fetch_all(self, requests: list[AssetRequest]) -> dict[str, AssetRecord]:
        for req in requests:
            validate_key(req.key)  # also defend callers using model_copy/model_construct
            existing = self.records.get(req.key)
            if (existing and existing.status == "ok" and existing.kind == req.kind and existing.query == req.query
                    and contained(self.project, existing.path).exists()
                    and all(contained(self.project, p).exists() for p in existing.maps.values())):
                continue
            try:
                rec = self._fetch(req)
            except Exception as exc:
                console.print(f"[red]  asset '{req.key}' failed: {exc}[/]")
                rec = None
            if rec is None:
                rec = AssetRecord(key=req.key, kind=req.kind, query=req.query, purpose=req.purpose, status="missing")
                console.print(f"[yellow]  asset '{req.key}' not found -> agents will use procedural/primitive fallback[/]")
            else:
                console.print(f"[green]  asset '{req.key}' <- {rec.source}:{rec.name} ({rec.license})[/]")
            self.records[req.key] = rec
            self.save()
        return self.records

    def _fetch(self, req: AssetRequest) -> AssetRecord | None:
        for q in [req.query] + [a for a in req.alternatives if a and a != req.query]:
            rec = self._fetch_query(req.model_copy(update={"query": q}))
            if rec:
                rec.query = req.query
                return rec
        return None

    def _fetch_query(self, req: AssetRequest) -> AssetRecord | None:
        want_sketchfab_first = req.kind == "model" and (req.needs_rig or req.preferred_source == "sketchfab")
        sources = ["sketchfab", "polyhaven"] if want_sketchfab_first else ["polyhaven", "sketchfab"]
        if req.kind != "model":
            sources = ["polyhaven"]
        for src in sources:
            try:
                rec = self._try_polyhaven(req) if src == "polyhaven" else self._try_sketchfab(req)
            except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
                console.print(f"[yellow]    {src} search/download failed: {type(exc).__name__}; trying next source[/]")
                continue
            if rec:
                return rec
        return None

    def _acquire(self, dest: Path, fetch):
        """Publish a validated download; existing libraries are never deleted or overwritten."""
        dest = contained(self.project, dest.relative_to(self.project).as_posix())
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".asset-staging-") as tmp:
            stage = Path(tmp)
            main, extra = fetch(stage)
            def relative(p):
                rel = Path(p).relative_to(stage).as_posix()
                checked = contained(stage, rel)
                if not checked.is_file():
                    raise ValueError("Downloaded asset is missing")
                return rel
            main_rel = relative(main)
            maps = {k: relative(v) for k, v in extra.get("maps", {}).items()}
            for p in stage.rglob("*"):
                contained(stage, p.relative_to(stage).as_posix())
            published = dest if not dest.exists() else dest.with_name(dest.name + "_" + uuid.uuid4().hex[:8])
            published = contained(self.project, published.relative_to(self.project).as_posix())
            shutil.copytree(stage, published)
            extra = dict(extra, maps={k: str(published / rel) for k, rel in maps.items()})
            return published / main_rel, extra

    def _try_polyhaven(self, req: AssetRequest) -> AssetRecord | None:
        validate_key(req.key)
        hits = self.polyhaven.search(req.kind, req.query)
        for asset_id, info, _ in hits:
            dest = self.project / "Assets" / "ThirdParty" / "PolyHaven" / req.kind.capitalize() / req.key
            try:
                main, extra = self._acquire(dest, lambda stage: self.polyhaven.download(
                    req.kind, asset_id, stage, self.cfg.texture_resolution))
            except Exception as exc:
                console.print(f"[yellow]    polyhaven {asset_id}: {exc}[/]")
                continue
            return AssetRecord(
                key=req.key, kind=req.kind, query=req.query, purpose=req.purpose, source="polyhaven",
                source_id=asset_id, name=info.get("name", asset_id), path=self._rel(main),
                maps={k: self._rel(Path(v)) for k, v in extra.get("maps", {}).items()},
                license="CC0", authors=extra.get("authors", []),
                url=f"https://polyhaven.com/a/{asset_id}", status="ok",
            )
        return None

    def _try_sketchfab(self, req: AssetRequest) -> AssetRecord | None:
        validate_key(req.key)
        if not self.sketchfab.enabled:
            return None
        for m in self.sketchfab.search(req.query, animated=req.needs_rig):
            uid = m["uid"]
            dest = self.project / "Assets" / "ThirdParty" / "Sketchfab" / req.key
            try:
                main, extra = self._acquire(dest, lambda stage: self.sketchfab.download(uid, stage))
            except Exception as exc:
                console.print(f"[yellow]    sketchfab {uid}: {exc}[/]")
                continue
            return AssetRecord(
                key=req.key, kind=req.kind, query=req.query, purpose=req.purpose, source="sketchfab",
                source_id=uid, name=m.get("name", uid), path=self._rel(main), license=extra["license"],
                authors=extra["authors"], url=extra["url"], animated=bool(extra.get("animations")), status="ok",
            )
        return None

    def describe_for_agents(self) -> str:
        """Compact asset registry text injected into prompts."""
        lines = []
        for r in self.records.values():
            if r.status != "ok":
                lines.append(f"- {r.key} [{r.kind}] MISSING (use procedural/primitive fallback) - {r.purpose}")
                continue
            line = f"- {r.key} [{r.kind}] '{r.name}' path={r.path}"
            if r.maps:
                line += " maps=" + json.dumps(r.maps)
            if r.kind == "model":
                line += f" animated={r.animated} (glTF: load with AssetDatabase.LoadAssetAtPath<GameObject>(path))"
            if r.kind == "hdri":
                line += " (HDR image; configure texture/cubemap import for the project's sky)"
            lines.append(line + f" license={r.license} source={r.url} - {r.purpose}")
        return "\n".join(lines) or "(no assets)"

