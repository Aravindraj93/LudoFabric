"""Local project knowledge: incremental keyword index + resolved-error memory (SQLite, no services).

- Source files are re-indexed only when their content hash changes.
- Search returns *paths and snippets*, so callers add only relevant files to a prompt.
- Resolved compile errors are remembered by fingerprint and message so later repairs start with the
  files that fixed a similar error before.

Connections are short-lived so Windows never keeps the database locked between operations.
A semantic retriever (for example the adaptive-rag library) can implement the same `search`
signature; this module stays dependency-free.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_SCHEMA = """
    CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, sha TEXT, indexed REAL);
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(path UNINDEXED, body);
    CREATE TABLE IF NOT EXISTS resolved(fp TEXT, message TEXT, files TEXT, created REAL);
    CREATE VIRTUAL TABLE IF NOT EXISTS resolved_fts USING fts5(fp UNINDEXED, message);
"""


def _fts_query(text: str, limit: int = 12) -> str:
    words = list(dict.fromkeys(w.lower() for w in _TOKEN.findall(text or "")))[:limit]
    return " OR ".join(f'"{w}"' for w in words)


class ProjectKnowledge:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(str(self.db_path), timeout=10)) as db:
            with db:  # commit on success, roll back on error
                yield db

    # ---------------------------------------------------------------- incremental indexing
    def index(self, root: Path, rels: list[str], chunk_lines: int = 60) -> dict:
        changed = removed = 0
        with self._db() as db:
            known = dict(db.execute("SELECT path, sha FROM files"))
            for rel in rels:
                try:
                    text = (root / rel).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                sha = hashlib.sha256(text.encode()).hexdigest()
                if known.pop(rel, None) == sha:
                    continue
                changed += 1
                db.execute("DELETE FROM chunks WHERE path=?", (rel,))
                lines = text.splitlines()
                for start in range(0, max(1, len(lines)), chunk_lines):
                    db.execute("INSERT INTO chunks(path, body) VALUES(?, ?)",
                               (rel, f"{rel}:{start + 1}\n" + "\n".join(lines[start:start + chunk_lines])))
                db.execute("INSERT OR REPLACE INTO files VALUES(?, ?, ?)", (rel, sha, time.time()))
            for rel in known:  # deleted since the previous index
                removed += 1
                db.execute("DELETE FROM chunks WHERE path=?", (rel,))
                db.execute("DELETE FROM files WHERE path=?", (rel,))
        return {"changed": changed, "removed": removed}

    def search(self, query: str, k: int = 5) -> list[dict]:
        q = _fts_query(query)
        if not q:
            return []
        with self._db() as db:
            rows = db.execute("SELECT path, snippet(chunks, 1, '', '', ' ... ', 24), bm25(chunks) AS s "
                              "FROM chunks WHERE chunks MATCH ? ORDER BY s LIMIT ?", (q, k * 3)).fetchall()
        out, seen = [], set()
        for path, snip, score in rows:
            if path not in seen:
                seen.add(path)
                out.append({"path": path, "snippet": snip, "score": round(-score, 3)})
            if len(out) == k:
                break
        return out

    # ---------------------------------------------------------------- resolved error memory
    def remember_fix(self, fp: str, message: str, files: list[str]) -> None:
        if not files:
            return
        with self._db() as db:
            db.execute("INSERT INTO resolved VALUES(?, ?, ?, ?)", (fp, message[:4000], "\n".join(files), time.time()))
            db.execute("INSERT INTO resolved_fts VALUES(?, ?)", (fp, message[:4000]))

    def similar_fixes(self, message: str, k: int = 3) -> list[dict]:
        q = _fts_query(message)
        if not q:
            return []
        out = []
        with self._db() as db:
            fps = [r[0] for r in db.execute(
                "SELECT fp FROM resolved_fts WHERE resolved_fts MATCH ? ORDER BY bm25(resolved_fts) LIMIT ?", (q, k))]
            for fp in dict.fromkeys(fps):
                row = db.execute("SELECT message, files FROM resolved WHERE fp=? ORDER BY created DESC", (fp,)).fetchone()
                if row:
                    out.append({"fingerprint": fp, "message": row[0][:300], "files": row[1].split("\n")})
        return out
