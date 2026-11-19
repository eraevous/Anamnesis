"""Where an instance's index lives: a backend interface (HO-ANA-20261118-6H0J item 3).

Reviewer guide:
- The index is a cache derived from sources (index.py says so). A backend only decides where a
  built index file is kept and how it is found again; it never parses or indexes anything itself.
- Every built index is keyed by `sources_digest`: a SHA-256 over the source files' relative paths
  and bytes, in build order, plus the index schema version. Changing any source, or the schema, changes the key,
  so a stale index is never served; it is rebuilt.
- `open_index` builds into a temporary file in the destination folder and renames it into place,
  so a reader never sees a half-built index and a crash leaves the old one intact.
- Two backends ship, both local and standard library only:
  - `CacheStore`: `<cache_dir>/<digest>.sqlite`, outside git; a fresh clone rebuilds on first use.
  - `InRepoStore`: one tracked file (default `<instance>/index/messages.sqlite`); a fresh clone
    gets it with the repository (plain git or LFS).
- The digest is written into the index's own `meta` table before the rename, so a file and the
  digest that names it are published as one generation: no sidecar can disagree with it.
- Hosted storage (S3 or similar) fits the same two methods (`fetch`, `destination`) but is not built
  here: creating hosted storage is outside the handoff's grant. docs/research/index-storage.md
  measures the local options and models the hosted one; the choice is Zach's.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol, runtime_checkable

from .index import INDEX_SCHEMA_VERSION, MessageIndex


def sources_digest(root: str | Path, files: Iterable[str | Path]) -> str:
    """SHA-256 over the schema version and, in the order given, each file's relative path and bytes.

    Order is part of the key: a later source replaces an earlier one's units with the same id, so
    [a, b] and [b, a] can index different text. Build in the same order you hash. Each file is
    streamed into its own SHA-256, so the memory used doesn't grow with the size of an export.
    """
    root = Path(root)
    h = hashlib.sha256(INDEX_SCHEMA_VERSION.encode("utf-8") + b"\0")
    for f in files:
        rel = Path(f).resolve().relative_to(root.resolve()).as_posix()
        fh = hashlib.sha256()
        with open(root / rel, "rb") as src:
            for chunk in iter(lambda: src.read(1 << 20), b""):
                fh.update(chunk)
        h.update(rel.encode("utf-8") + b"\0" + fh.digest())
    return h.hexdigest()


@runtime_checkable
class IndexStore(Protocol):
    name: str

    def fetch(self, digest: str) -> Optional[Path]:
        """The path of an index built for `digest`, or None if this store has none."""

    def destination(self, digest: str) -> Path:
        """Where a freshly built index for `digest` should be renamed to."""


class CacheStore:
    """A local cache outside git, one file per digest. Old digests are left for `prune`."""

    name = "cache"

    def __init__(self, cache_dir: str | Path) -> None:
        self.dir = Path(cache_dir).expanduser()

    def destination(self, digest: str) -> Path:
        return self.dir / f"{digest}.sqlite"

    def fetch(self, digest: str) -> Optional[Path]:
        path = self.destination(digest)
        return path if path.is_file() else None

    def prune(self, keep: str) -> int:
        """Delete cached indexes for every digest but `keep`. Returns how many were removed."""
        removed = 0
        for p in self.dir.glob("*.sqlite"):
            if p.stem != keep and len(p.stem) == 64:
                p.unlink()
                removed += 1
        return removed


class InRepoStore:
    """One tracked index file; the sources digest it was built from is in its own `meta` table."""

    name = "in-repo"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def destination(self, digest: str) -> Path:
        return self.path

    def fetch(self, digest: str) -> Optional[Path]:
        return self.path if self.path.is_file() and recorded_digest(self.path) == digest else None


def recorded_digest(path: Path) -> Optional[str]:
    """The sources digest stored in an index file, or None if it has none or can't be read."""
    try:
        con = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key = 'sources_digest'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _open_verified(path: Path, digest: str) -> Optional[MessageIndex]:
    """Open `path` and check the digest on that same connection.

    A connection stays on the file it opened even if the path is replaced afterwards, so checking
    through it (not through the path, earlier) is what makes the returned index the requested one.
    """
    try:
        idx = MessageIndex(path)
    except (sqlite3.Error, ValueError):
        return None
    try:
        row = idx.con.execute("SELECT value FROM meta WHERE key = 'sources_digest'").fetchone()
    except sqlite3.Error:
        row = None
    if row and row[0] == digest:
        return idx
    idx.close()
    return None


def _build(store: IndexStore, digest: str, build: Callable[[MessageIndex], None]) -> Path:
    dest = store.destination(digest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # A private build folder beside the destination, so the final rename stays on one filesystem.
    workdir = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".building"))
    tmp = workdir / "index.sqlite"
    try:
        idx = MessageIndex(tmp)
        try:
            build(idx)
            with idx.con:  # the digest travels inside the file it names
                idx.con.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('sources_digest', ?)", (digest,)
                )
        finally:
            idx.close()
        os.replace(tmp, dest)
    finally:
        for leftover in workdir.iterdir():
            leftover.unlink()
        workdir.rmdir()
    return dest


class IndexRaceError(RuntimeError):
    """Another writer kept replacing the index with a different generation."""


def open_index(
    store: IndexStore, digest: str, build: Callable[[MessageIndex], None], *, attempts: int = 3
) -> tuple[MessageIndex, bool]:
    """Open the index for `digest` from `store`, building it with `build` if the store has none.

    Returns (index, built). `build` receives an empty index and fills it (e.g. `idx.add(bundles)`).
    The returned index's own digest is checked on the returned connection; if another process
    swapped in a different generation meanwhile, this rebuilds, and gives up after `attempts`.
    """
    built = False
    for _ in range(attempts):
        found = store.fetch(digest)
        if found is not None:
            idx = _open_verified(found, digest)
            if idx is not None:
                return idx, built
        path = _build(store, digest, build)
        built = True
        idx = _open_verified(path, digest)
        if idx is not None:
            return idx, True
    raise IndexRaceError(f"index for {digest[:12]} was replaced by another generation {attempts} times")
