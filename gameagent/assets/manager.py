"""Resolves the TechLead's AssetRequests into downloaded, licensed files inside the Unity project."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
from pydantic import BaseModel

from ..config import Config
from ..schemas import AssetRequest
from .polyhaven import PolyHaven
from .sketchfab import Sketchfab

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
        self.registry_path.write_text(
            json.dumps({k: v.model_dump() for k, v in self.records.items()}, indent=2), encoding="utf-8"
        )
        self._write_credits()

    def _write_credits(self) -> None:
        lines = ["# Third-party asset credits", "", "Generated automatically by LudoFabric.", ""]
        for r in self.records.values():
            if r.status != "ok":
                continue
            authors = ", ".join(a for a in r.authors if a) or "unknown"
            lines.append(f"- **{r.name}** ({r.source}) by {authors} - {r.license} - {r.url}")
        out = self.project / "Assets" / "ThirdParty" / "CREDITS.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    def fetch_all(self, requests: list[AssetRequest]) -> dict[str, AssetRecord]:
        for req in requests:
            existing = self.records.get(req.key)
            if existing and existing.status == "ok" and (self.project / existing.path).exists():
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
            rec = self._try_polyhaven(req) if src == "polyhaven" else self._try_sketchfab(req)
            if rec:
                return rec
        return None

    def _try_polyhaven(self, req: AssetRequest) -> AssetRecord | None:
        hits = self.polyhaven.search(req.kind, req.query)
        for asset_id, info, _ in hits:
            dest = self.project / "Assets" / "ThirdParty" / "PolyHaven" / req.kind.capitalize() / req.key
            try:
                main, extra = self.polyhaven.download(req.kind, asset_id, dest, self.cfg.texture_resolution)
            except Exception as exc:
                console.print(f"[yellow]    polyhaven {asset_id}: {exc}[/]")
                shutil.rmtree(dest, ignore_errors=True)
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
        if not self.sketchfab.enabled:
            return None
        for m in self.sketchfab.search(req.query, animated=req.needs_rig):
            uid = m["uid"]
            dest = self.project / "Assets" / "ThirdParty" / "Sketchfab" / req.key
            try:
                main, extra = self.sketchfab.download(uid, dest)
            except Exception as exc:
                console.print(f"[yellow]    sketchfab {uid}: {exc}[/]")
                shutil.rmtree(dest, ignore_errors=True)
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
                line += " (imported as Cubemap; use SceneKit.SetupHdriSky(path))"
            lines.append(line + f" - {r.purpose}")
        return "\n".join(lines) or "(no assets)"

