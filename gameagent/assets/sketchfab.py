"""Sketchfab Data API v3 - downloadable CC0 / CC-BY models (characters, creatures, vehicles...).
Requires a free API token: https://sketchfab.com/settings/password
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

import httpx
from .safety import Budget, contained, download, extract

API = "https://api.sketchfab.com/v3"
# ShareAlike requires additional obligations, not just attribution.
ALLOWED_LICENSES = {"cc0", "by"}


def allowed_licenses() -> set[str]:
    return ALLOWED_LICENSES | ({"by-sa"} if os.getenv("ALLOW_SHAREALIKE_ASSETS", "0") == "1" else set())


def _license_slug(lic: dict | None) -> str:
    if not lic:
        return ""
    slug = (lic.get("slug") or "").lower()
    if slug:
        return slug
    label = (lic.get("label") or "").lower()
    if "cc0" in label or "public domain" in label:
        return "cc0"
    if "noncommercial" in label or "non-commercial" in label or "noderiv" in label:
        return "restricted"
    if "sharealike" in label:
        return "by-sa"
    if "attribution" in label:
        return "by"
    return ""


class Sketchfab:
    def __init__(self, client: httpx.Client, token: str, max_faces: int):
        self.client = client
        self.token = token
        self.max_faces = max_faces

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}"}

    def search(self, query: str, animated: bool = False, limit: int = 5) -> list[dict]:
        params = {
            "type": "models",
            "q": query,
            "downloadable": "true",
            "count": 24,
            "max_face_count": self.max_faces,
            "sort_by": "-likeCount",
        }
        if animated:
            params["animated"] = "true"
        r = self.client.get(f"{API}/search", params=params, headers=self._headers())
        r.raise_for_status()
        results = []
        for m in r.json().get("results", []):
            slug = _license_slug(m.get("license"))
            if slug not in allowed_licenses():
                continue
            results.append(m)
            if len(results) >= limit:
                break
        return results

    def model_info(self, uid: str) -> dict:
        r = self.client.get(f"{API}/models/{uid}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    def download(self, uid: str, dest_dir: Path) -> tuple[Path, dict]:
        self._budget = Budget()
        info = self.model_info(uid)
        slug = _license_slug(info.get("license"))
        if slug not in allowed_licenses():
            raise RuntimeError(f"license '{slug}' not allowed")
        r = self.client.get(f"{API}/models/{uid}/download", headers=self._headers())
        r.raise_for_status()
        links = r.json()
        dest_dir.mkdir(parents=True, exist_ok=True)
        extra = {
            "license": (info.get("license") or {}).get("label", slug),
            "authors": [(info.get("user") or {}).get("displayName") or (info.get("user") or {}).get("username", "")],
            "url": info.get("viewerUrl", f"https://sketchfab.com/3d-models/{uid}"),
            "animations": info.get("animationCount", 0),
        }
        if "glb" in links:
            main = contained(dest_dir, "model.glb")
            self._stream(links["glb"]["url"], main)
            return main, extra
        archive = contained(dest_dir, "model.zip")
        self._stream(links["gltf"]["url"], archive)
        extract(archive, dest_dir, Budget())
        archive.unlink()
        gltfs = sorted(dest_dir.rglob("*.gltf"))
        if not gltfs:
            raise RuntimeError("archive contained no .gltf")
        return gltfs[0], extra

    def _stream(self, url: str, dest: Path) -> None:
        download(self.client, url, dest, self._budget)

