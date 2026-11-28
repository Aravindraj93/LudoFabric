"""Poly Haven (https://polyhaven.com) - CC0 models, HDRIs and PBR textures. Public API, no key needed."""
from __future__ import annotations

import math
import re
from pathlib import Path

import httpx
from .safety import Budget, contained, download, url_name

API = "https://api.polyhaven.com"
HEADERS = {"User-Agent": "LudoFabric-UnityOrchestrator/0.1 (autonomous game dev tool)"}


STOP = {"the", "a", "an", "of", "with", "and", "for", "old", "small", "large", "big", "low", "poly", "lowpoly",
        "stylized", "simple", "lightly", "light"}


def _stem(t: str) -> str:
    for suf in ("ies", "es", "s"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t


def _tokens(text: str) -> set[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return {_stem(t) for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1 and not t.isdigit()}


def _query_tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1 and t not in STOP]


class PolyHaven:
    def __init__(self, client: httpx.Client):
        self.client = client
        self._index: dict[str, dict] = {}

    def _assets(self, kind: str) -> dict:
        api_type = {"model": "models", "hdri": "hdris", "texture": "textures"}[kind]
        if api_type not in self._index:
            r = self.client.get(f"{API}/assets", params={"type": api_type}, headers=HEADERS)
            r.raise_for_status()
            self._index[api_type] = r.json()
        return self._index[api_type]

    def search(self, kind: str, query: str, limit: int = 5) -> list[tuple[str, dict, float]]:
        qt = _query_tokens(query)
        if not qt:
            return []
        q, noun = set(qt), qt[-1]  # English head noun is usually last: "wooden ROWBOAT"
        scored = []
        for asset_id, info in self._assets(kind).items():
            words = _tokens(info.get("name", "")) | _tokens(asset_id)
            tags = set().union(*(_tokens(t) for t in info.get("tags", []) + info.get("categories", []))) \
                if (info.get("tags") or info.get("categories")) else set()
            vocab = words | tags
            # the head noun must match (name or tag) - avoids "wooden rowboat" -> "wooden table"
            if noun not in vocab:
                continue
            matched = q & vocab
            if len(matched) / len(q) < 0.5:
                continue
            if noun not in words and matched != q:  # noun only in tags -> require a full match
                continue
            overlap = len(q & words) * 3 + len(q & tags) * 2 + (2 if noun in words else 0)
            score = overlap + math.log10(1 + info.get("download_count", 0)) * 0.3
            scored.append((asset_id, info, score))
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:limit]

    def _download(self, url: str, dest: Path) -> None:
        download(self.client, url, dest, self._budget, HEADERS)

    @staticmethod
    def _pick_res(options: dict, wanted: str) -> str:
        order = [wanted, "1k", "2k", "4k"]
        for res in order:
            if res in options:
                return res
        return next(iter(options))

    def download(self, kind: str, asset_id: str, dest_dir: Path, resolution: str) -> tuple[Path, dict]:
        self._budget = Budget()
        r = self.client.get(f"{API}/files/{asset_id}", headers=HEADERS)
        r.raise_for_status()
        files = r.json()
        info = self._assets(kind).get(asset_id, {})
        extra: dict = {"authors": list(info.get("authors", {}).keys())}

        if kind == "model":
            gl = files["gltf"]
            entry = gl[self._pick_res(gl, resolution)]["gltf"]
            main = contained(dest_dir, url_name(entry["url"]))
            includes = [(contained(dest_dir, rel), inc) for rel, inc in entry.get("include", {}).items()]
            self._download(entry["url"], main)
            for target, inc in includes:
                self._download(inc["url"], target)
            return main, extra

        if kind == "hdri":
            h = files["hdri"]
            entry = h[self._pick_res(h, resolution)]["hdr"]
            main = contained(dest_dir, url_name(entry["url"]))
            self._download(entry["url"], main)
            return main, extra

        # texture set: download the maps Unity's Lit shader needs
        maps = {}
        for map_key, label in (("Diffuse", "albedo"), ("nor_gl", "normal"), ("Rough", "roughness"), ("AO", "ao")):
            if map_key not in files:
                continue
            res = self._pick_res(files[map_key], resolution)
            fmt = files[map_key][res].get("jpg") or files[map_key][res].get("png")
            if not fmt:
                continue
            p = contained(dest_dir, url_name(fmt["url"]))
            self._download(fmt["url"], p)
            maps[label] = p
        if "albedo" not in maps:
            raise RuntimeError(f"texture {asset_id} has no diffuse map")
        extra["maps"] = {k: str(v) for k, v in maps.items()}
        return maps["albedo"], extra

