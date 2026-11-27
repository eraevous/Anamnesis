"""Ingest and ask on an instance: the engine's answer path wired to a manifest (HO-ANA-20261118-6H0J item 4).

Reviewer guide:
- `ingest_into_instance(path, exports)` copies each export into `sources/imported/`, lists it in the
  manifest (file name prefixed with the first 16 hex digits of its SHA-256), then (re)builds the instance's index from every source the manifest lists, in manifest
  order. Every export is parsed before anything is written, so a bad export leaves the instance as it
  was. An export already imported with the same bytes is not copied or listed twice; nothing is ever
  overwritten.
- `ask_instance(path, question)` opens the index for the manifest's current sources, rebuilding it
  first if any source changed (the key is `storage.sources_digest`), and answers with `answer.ask`:
  extractive and cited, no provider, no network.
- The backend comes from the manifest's optional `index` (`{"backend": "cache"}` when absent:
  `.anamnesis/index/<digest>.sqlite`, git-ignored; or `{"backend": "in-repo", "path": ...}`). Item 3's
  choice of default is Zach's; "cache" is the default only because it commits nothing.
- Writes stay inside the instance: every write path is checked for symlinks first (`_refuse_redirects`),
  the manifest is replaced atomically, copies use exclusive create. Sources are read, never written.
- `privacy.sealed` and `privacy.opaque` are recorded but not enforced (a proposal awaiting Zach); when
  either is non-empty the report says so instead of pretending they were applied.
- Each ingest writes one run record to `runs/`.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .answer import Answer, ask, ingest
from .index import MessageIndex
from .instance import (
    DEFAULT_IN_REPO_INDEX, MANIFEST_NAME, InstanceError, _refuse_redirects, load_manifest, validate_manifest,
)
from .storage import CacheStore, IndexStore, InRepoStore, file_sha256, open_index, read_meta, sources_digest
from .units import bundles_from_export

IMPORT_DIR = "sources/imported"
CACHE_DIR = ".anamnesis/index"
RUN_SCHEMA_VERSION = "anamnesis.run.v1"
# What a malformed export raises while it is parsed; each is reported as "not a readable ChatGPT export".
# RecursionError: JSON nested deeper than the parser's stack. zlib.error and EOFError: a damaged or cut-off
# compressed member. NotImplementedError: a ZIP member compressed with a method zipfile can't read.
EXPORT_ERRORS = (ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile,
                 RecursionError, zlib.error, EOFError, NotImplementedError)


@dataclass
class InstanceIngest:
    imported: List[str] = field(default_factory=list)   # newly copied and listed
    already: List[str] = field(default_factory=list)    # same bytes already imported
    sources: int = 0
    conversations: int = 0
    units_indexed: int = 0  # units with text in the index
    digest: str = ""
    built: bool = False
    notes: List[str] = field(default_factory=list)


def _root(path: str | Path) -> Path:
    root = Path(path).expanduser()
    if root.is_symlink():
        raise InstanceError(f"{root} is a symlink; pass the real folder instead")
    return root


def index_store(root: Path, manifest: dict) -> IndexStore:
    cfg = manifest.get("index", {"backend": "cache"})
    if cfg["backend"] == "in-repo":
        rel = cfg.get("path", DEFAULT_IN_REPO_INDEX)
        _refuse_redirects(root, [rel], dirs=[str(Path(rel).parent)] if Path(rel).parent != Path(".") else [])
        if (root / rel).exists() and not _is_anamnesis_index(root / rel):
            # a build replaces this file: never clobber a database (or anything else) that isn't ours
            raise InstanceError(f"{rel} exists and is not an anamnesis index; refusing to replace it")
        return InRepoStore(root / rel)
    _refuse_redirects(root, [".anamnesis", CACHE_DIR], dirs=[CACHE_DIR])
    return CacheStore(root / CACHE_DIR)


def _is_anamnesis_index(path: Path) -> bool:
    """True only for an SQLite file whose meta table names an anamnesis index schema (read-only check)."""
    schema = read_meta(path, "schema_version")
    return isinstance(schema, str) and schema.startswith("anamnesis.")


def _source_files(root: Path, manifest: dict) -> List[Path]:
    files = []
    for src in manifest["sources"]:
        f = root / src["path"]
        if not f.exists():
            raise InstanceError(f"source {src['path']} is listed in the manifest but missing")
        try:
            f.resolve().relative_to(root.resolve())
        except ValueError:
            raise InstanceError(f"source {src['path']} resolves outside the instance") from None
        files.append(f)
    return files


def _privacy_notes(manifest: dict) -> List[str]:
    privacy = manifest.get("privacy", {})
    return [f"privacy.{k} lists {len(privacy[k])} item(s); recorded, not enforced yet"
            for k in ("sealed", "opaque") if privacy.get(k)]


def _build_from(ix: MessageIndex, root: Path, files: List[Path]) -> None:
    for f in files:  # one at a time, so a parse failure names its source
        try:
            ingest(ix, [f])
        except EXPORT_ERRORS as exc:
            rel = f.relative_to(root).as_posix()
            raise InstanceError(f"source {rel} is not a readable ChatGPT export: {exc}") from exc


def _open(root: Path, manifest: dict) -> Tuple[MessageIndex, bool, str, IndexStore]:
    """Open (building if needed) the index for the manifest's sources: (index, built, digest, store)."""
    files = _source_files(root, manifest)
    if not files:
        raise InstanceError(f"{root} has no sources; add one with `anamnesis ingest {root} <export>`")
    digest = sources_digest(root, files)
    store = index_store(root, manifest)
    if isinstance(store, CacheStore):  # the cache file itself, not only its folders: a planted link
        _refuse_redirects(root, [f"{CACHE_DIR}/{digest}.sqlite"])  # would be opened (and written) outside
    idx, built = open_index(store, digest, lambda ix: _build_from(ix, root, files))
    return idx, built, digest, store


def ingest_into_instance(path: str | Path, exports: Iterable[str | Path]) -> InstanceIngest:
    root = _root(path)
    manifest = load_manifest(root)
    out = InstanceIngest(notes=_privacy_notes(manifest))
    listed = {s["path"] for s in manifest["sources"]}
    by_content = {}  # full SHA-256 -> path, for every listed source that exists as a file
    for s in manifest["sources"]:
        f = root / s["path"]
        if f.is_file() and not f.is_symlink():
            by_content.setdefault(file_sha256(f), s["path"])

    plan: List[Tuple[Optional[Path], str]] = []
    for export in exports:
        src = Path(export).expanduser()
        if not src.is_file():
            raise InstanceError(f"{src}: pass an export file (conversations.json or the export ZIP)")
        try:  # parse first: a bad export is refused before anything is written
            bundles_from_export(src)
        except EXPORT_ERRORS as exc:
            raise InstanceError(f"{src}: not a readable ChatGPT export: {exc}") from exc
        # Named by content, so every export's usual name (conversations.json) never collides.
        digest = file_sha256(src)
        if digest in by_content:  # same bytes already listed or planned, under any name: keep that entry,
            out.already.append(by_content[digest])  # so an older copy never moves after a newer export
            continue
        rel = f"{IMPORT_DIR}/{digest[:16]}-{src.name}"
        by_content[digest] = rel
        target = root / rel
        if target.exists():
            if target.is_file() and file_sha256(target) == digest:
                out.already.append(rel)
                if rel not in listed:
                    plan.append((None, rel))  # present but unlisted: list it
                continue
            raise InstanceError(f"{target} already exists with different content; refusing to overwrite it")
        plan.append((src, rel))

    if plan:
        _refuse_redirects(root, ["sources", IMPORT_DIR, MANIFEST_NAME, *(r for _, r in plan)],
                          dirs=[IMPORT_DIR])
        (root / IMPORT_DIR).mkdir(parents=True, exist_ok=True)
        for src, rel in plan:
            if src is not None:
                with src.open("rb") as fh, (root / rel).open("xb") as dst:  # exclusive: never overwrites
                    shutil.copyfileobj(fh, dst)
                out.imported.append(rel)
            if rel not in listed:
                manifest["sources"].append({"kind": "chatgpt-export", "path": rel})
                listed.add(rel)
        _write_manifest(root, manifest)

    idx, out.built, out.digest, store = _open(root, manifest)
    try:
        out.sources = len(manifest["sources"])
        out.conversations = idx.conversation_count()
        out.units_indexed = idx.count()
    finally:
        idx.close()
    if isinstance(store, CacheStore):  # re-check just before deleting: the folder may have changed since
        _refuse_redirects(root, [".anamnesis", CACHE_DIR], dirs=[CACHE_DIR])
        store.prune(keep=out.digest)
    _write_run(root, {"command": "ingest", "imported": out.imported, "already": out.already,
                      "sources": [s["path"] for s in manifest["sources"]], "sources_digest": out.digest,
                      "index_backend": store.name, "index_built": out.built,
                      "conversations": out.conversations, "units_indexed": out.units_indexed})
    return out


def ask_instance(path: str | Path, question: str, *, limit: int = 3) -> Tuple[Answer, List[str]]:
    """Answer from the instance's sources. Returns the answer and notes (rebuild, privacy)."""
    root = _root(path)
    manifest = load_manifest(root)
    notes = _privacy_notes(manifest)
    idx, built, _, _ = _open(root, manifest)
    try:
        if built:
            notes.insert(0, "index rebuilt from the instance's sources")
        return ask(idx, question, limit=limit), notes
    finally:
        idx.close()


def _write_manifest(root: Path, manifest: dict) -> None:
    problems = validate_manifest(manifest)
    if problems:
        raise InstanceError("; ".join(problems))
    fd, tmp = tempfile.mkstemp(dir=root, prefix=f".{MANIFEST_NAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, root / MANIFEST_NAME)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _write_run(root: Path, record: dict) -> None:
    _refuse_redirects(root, ["runs"])
    now = datetime.now(timezone.utc)
    record = {"schema": RUN_SCHEMA_VERSION, "at": now.isoformat(timespec="seconds"), **record}
    name = f"runs/{now.strftime('%Y%m%dT%H%M%SZ')}-{record['command']}-{uuid.uuid4().hex[:8]}.json"
    (root / "runs").mkdir(exist_ok=True)
    with (root / name).open("x", encoding="utf-8") as fh:
        fh.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
