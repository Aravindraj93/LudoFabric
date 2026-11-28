"""Contained, bounded asset IO. Static path checks are not a filesystem sandbox."""
from contextlib import closing
from pathlib import Path
import os
import re
import stat
import tempfile
import time
from urllib.parse import unquote, urlsplit
import zipfile

RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 2000


def validate_key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) or RESERVED.fullmatch(value):
        raise ValueError("Asset key must be a safe lowercase snake_case identifier, not a device name")
    return value


def contained(root: Path, rel: str) -> Path:
    parts = rel.replace("\\", "/").split("/")
    if not parts or any(not p or p in (".", "..") or p.endswith((".", " ")) or
                        re.search(r'[<>:"|?*\x00-\x1f]', p) or RESERVED.fullmatch(p) for p in parts):
        raise ValueError(f"Unsafe asset path: {rel}")
    target = root.joinpath(*parts)
    # Reject existing junctions/symlinks, including ancestors of the download root.
    for p in (target, *target.parents):
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            raise ValueError(f"Linked asset path rejected: {p}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Asset path escapes destination: {rel}")
    return target


def url_name(url: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if "/" in name or "\\" in name:
        raise ValueError("Unsafe URL filename")
    return name


class Budget:
    def __init__(self, max_file=MAX_FILE, max_total=MAX_TOTAL, max_files=MAX_FILES, seconds=300):
        self.max_file, self.max_total, self.max_files = max_file, max_total, max_files
        self.used = self.files = 0
        self.deadline = time.monotonic() + seconds

    def consume(self, size: int, file_size: int):
        self.used += size
        if file_size > self.max_file or self.used > self.max_total or time.monotonic() > self.deadline:
            raise ValueError("Asset size/time budget exceeded")

    def begin(self):
        self.files += 1
        if self.files > self.max_files:
            raise ValueError("Asset file-count budget exceeded")


def download(client, url: str, dest: Path, budget: Budget, headers=None):
    contained(dest.parent, dest.name)
    budget.begin()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=dest.parent, prefix=".download-")
    try:
        with os.fdopen(fd, "wb") as f, client.stream("GET", url, headers=headers, follow_redirects=True) as response:
            response.raise_for_status()
            length = response.headers.get("Content-Length")
            if length and (int(length) > budget.max_file or int(length) + budget.used > budget.max_total):
                raise ValueError("Asset declared size exceeds budget")
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                budget.consume(len(chunk), size)
                f.write(chunk)
        contained(dest.parent, dest.name)
        os.replace(temp, dest)
    finally:
        Path(temp).unlink(missing_ok=True)


def extract(archive: Path, root: Path, budget: Budget):
    with zipfile.ZipFile(archive) as z:
        entries, seen = [], set()
        for info in z.infolist():
            rel = info.filename.rstrip("/")
            target = contained(root, rel)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                raise ValueError("Archive links/special/encrypted files are forbidden")
            if rel.casefold() in seen:
                raise ValueError("Archive contains duplicate/case-colliding paths")
            seen.add(rel.casefold())
            if info.file_size > budget.max_file or info.file_size > max(1, info.compress_size) * 100:
                raise ValueError("Archive size/compression ratio exceeds budget")
            entries.append((info, target))
        if len(entries) > budget.max_files or sum(i.file_size for i, _ in entries) > budget.max_total:
            raise ValueError("Archive exceeds extraction budget")
        for info, target in entries:
            budget.begin()
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("xb") as dst:
                size = 0
                while chunk := src.read(65536):
                    size += len(chunk)
                    budget.consume(len(chunk), size)
                    dst.write(chunk)
