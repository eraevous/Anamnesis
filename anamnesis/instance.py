"""Memory instances: the private side of the engine/instance split (HO-ANA-20261118-6H0J item 2).

The engine (this package) is public and depends on no instance. An *instance* is a folder that one
person owns: their sources, what the engine derived from them, and the rules for both. Zach's
Cognitive-Scaffolding repository is meant to become one; `anamnesis init` creates a new one, or adds
the missing parts to an existing folder without touching what is already there.

Reviewer guide:
- The layout is `INSTANCE_LAYOUT`. Directories are created if missing; nothing that exists is ever
  overwritten, moved or deleted, so a folder that already has `library/` (CogScaff does) adopts the
  layout in place.
- The manifest is `anamnesis.instance.json` (JSON, so the engine stays standard-library only).
  `validate_manifest` returns a list of problems rather than raising, so `init` and later commands
  can report all of them at once.
- `privacy` holds the proposed FHC features (sealing, opacity, profile-only answers) as data with
  safe defaults. They are proposals, not decided (see the handoff); nothing enforces them yet.
- `sources/` and `.anamnesis/` (local caches such as the FTS5 index) are git-ignored by default:
  where indexes and vectors live is item 3's open question, and raw exports should not be
  committed by accident.
- The engine never imports anything from an instance and never reads outside the path it is given.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

INSTANCE_SCHEMA_VERSION = "anamnesis.instance.v1"
MANIFEST_NAME = "anamnesis.instance.json"

# Relative directory -> what it holds. Order is the order `init` reports them in.
INSTANCE_LAYOUT: Dict[str, str] = {
    "sources": "raw inputs (exports); git-ignored by default, never committed by the engine",
    "claims": "the claim ledger, claims/ledger.jsonl, append-only",
    "rendered/frames": "rendered Frames",
    "rendered/films": "rendered Films",
    "rendered/saga": "the rendered saga",
    "library": "reusable, standalone artifacts the owner curates (kept as-is if present)",
    "runs": "one JSON record per engine run: what ran, on which sources, with what result",
    ".anamnesis": "local caches (FTS5 index, vectors); git-ignored until item 3 decides where they live",
}

GITIGNORE_LINES = ["# anamnesis: raw sources and local caches stay out of git by default", "sources/", ".anamnesis/"]

SOURCE_KINDS = {"chatgpt-export"}
VISIBILITIES = {"private", "shared", "public"}


def default_manifest(name: str) -> Dict[str, Any]:
    return {
        "schema": INSTANCE_SCHEMA_VERSION,
        "name": name,
        "sources": [],
        "embedder": {"kind": "none"},
        "shelves": [{"id": "default", "description": "everything not on another shelf"}],
        "privacy": {
            "visibility": "private",
            "sealed": [],
            "opaque": [],
            "profile_only": False,
            "agent_access": "propose-only",
            "_status": "proposed FHC features (HO-ANA-20261118-6H0J); recorded, not enforced",
        },
    }


@dataclass
class InitReport:
    path: Path
    created: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)
    demo_sources: List[str] = field(default_factory=list)


class InstanceError(ValueError):
    pass


def validate_manifest(manifest: Any) -> List[str]:
    """Every problem with a manifest, as readable strings; empty when it is valid."""
    if not isinstance(manifest, dict):
        return ["manifest is not a JSON object"]
    problems: List[str] = []
    if manifest.get("schema") != INSTANCE_SCHEMA_VERSION:
        problems.append(f"schema must be {INSTANCE_SCHEMA_VERSION!r}")
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        problems.append("name must be a non-empty string")
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        problems.append("sources must be a list")
        sources = []
    seen = set()
    for i, src in enumerate(sources):
        if not isinstance(src, dict):
            problems.append(f"sources[{i}] is not an object")
            continue
        if not isinstance(src.get("kind"), str) or src["kind"] not in SOURCE_KINDS:
            problems.append(f"sources[{i}].kind must be one of {sorted(SOURCE_KINDS)}")
        p = src.get("path")
        if not isinstance(p, str) or not p:
            problems.append(f"sources[{i}].path must be a non-empty string")
        elif Path(p).is_absolute() or ".." in Path(p).parts:
            problems.append(f"sources[{i}].path must be relative and stay inside the instance")
        elif p in seen:
            problems.append(f"sources[{i}].path {p!r} is listed twice")
        else:
            seen.add(p)
    emb = manifest.get("embedder")
    if not isinstance(emb, dict) or not isinstance(emb.get("kind"), str):
        problems.append("embedder must be an object with a string kind")
    shelves = manifest.get("shelves")
    if not isinstance(shelves, list) or not shelves:
        problems.append("shelves must be a non-empty list")
    else:
        ids = [s.get("id") if isinstance(s, dict) else None for s in shelves]
        if any(not isinstance(x, str) or not x for x in ids):
            problems.append("every shelf needs a non-empty string id")
        elif len(set(ids)) != len(ids):
            problems.append("shelf ids must be unique")
    privacy = manifest.get("privacy")
    if not isinstance(privacy, dict):
        problems.append("privacy must be an object")
    else:
        if not isinstance(privacy.get("visibility"), str) or privacy["visibility"] not in VISIBILITIES:
            problems.append(f"privacy.visibility must be one of {sorted(VISIBILITIES)}")
        for key in ("sealed", "opaque"):
            if not isinstance(privacy.get(key), list):
                problems.append(f"privacy.{key} must be a list")
        if not isinstance(privacy.get("profile_only"), bool):
            problems.append("privacy.profile_only must be true or false")
    return problems


def load_manifest(path: Path) -> Dict[str, Any]:
    """Read and validate an instance's manifest; raises InstanceError listing every problem."""
    mpath = Path(path).expanduser() / MANIFEST_NAME
    if not mpath.is_file():
        raise InstanceError(f"{path} is not an anamnesis instance (no {MANIFEST_NAME})")
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstanceError(f"{mpath}: unreadable manifest: {exc}") from exc
    problems = validate_manifest(manifest)
    if problems:
        raise InstanceError(f"{mpath}: " + "; ".join(problems))
    return manifest


def init_instance(path: Path, name: str | None = None, demo: bool = False) -> InitReport:
    """Create an instance at `path`, or complete one in place. Never overwrites an existing file.

    With `demo=True`, copies the repository's synthetic fixtures into `sources/demo/` and lists them
    in a new manifest, so the engine can be tried with no real data. Demo needs a fresh manifest.
    """
    root = Path(path).expanduser()
    if root.exists() and not root.is_dir():
        raise InstanceError(f"{root} exists and is not a directory")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise InstanceError("name must be a non-empty string")  # checked before any write
    effective_name = name if name is not None else (root.resolve().name.strip() or "memory")
    report = InitReport(path=root)
    manifest_path = root / MANIFEST_NAME
    existing_manifest = manifest_path.exists()
    if existing_manifest:
        load_manifest(root)  # an invalid existing manifest is reported, never replaced
        if demo:
            raise InstanceError(f"{manifest_path} already exists; --demo only fills a new instance")

    demo_files: List[Path] = []
    if demo:
        demo_files = _demo_fixture_files()
        for f in demo_files:  # refuse before creating anything, so a refusal leaves no partial instance
            target = root / "sources" / "demo" / f"{f.parent.name}.json"
            if target.exists():
                raise InstanceError(f"{target} already exists; refusing to overwrite it")

    write_paths = [*INSTANCE_LAYOUT, "claims/ledger.jsonl", ".gitignore", MANIFEST_NAME]
    if demo:
        write_paths += ["sources/demo", *(f"sources/demo/{f.parent.name}.json" for f in demo_files)]
    _refuse_redirects(root, write_paths)

    root.mkdir(parents=True, exist_ok=True)
    for rel in INSTANCE_LAYOUT:
        d = root / rel
        if d.exists():
            report.kept.append(rel + "/")
        else:
            d.mkdir(parents=True)
            report.created.append(rel + "/")

    ledger = root / "claims" / "ledger.jsonl"
    _create_if_missing(ledger, "", report)

    gi = root / ".gitignore"
    if gi.exists():
        try:
            current = gi.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise InstanceError(f"{gi}: cannot read it as UTF-8 text: {exc}") from exc
        missing = [line for line in GITIGNORE_LINES[1:] if line not in current]
        if missing:
            with gi.open("a", encoding="utf-8") as fh:
                prefix = "" if not current or current[-1] == "" else "\n"
                fh.write(prefix + "\n".join([GITIGNORE_LINES[0], *missing]) + "\n")
            report.created.append(".gitignore (appended " + ", ".join(missing) + ")")
        else:
            report.kept.append(".gitignore")
    else:
        _create_if_missing(gi, "\n".join(GITIGNORE_LINES) + "\n", report)

    if not existing_manifest:
        manifest = default_manifest(effective_name)
        if demo:
            dest = root / "sources" / "demo"
            dest.mkdir(parents=True, exist_ok=True)
            for f in demo_files:
                target = dest / f"{f.parent.name}.json"
                with f.open("rb") as src, target.open("xb") as out:  # exclusive: never overwrites
                    shutil.copyfileobj(src, out)
                rel = target.relative_to(root).as_posix()
                manifest["sources"].append({"kind": "chatgpt-export", "path": rel, "synthetic": True})
                report.demo_sources.append(rel)
        problems = validate_manifest(manifest)
        if problems:  # unreachable while name is checked above; kept so a bad manifest is never written
            raise InstanceError("; ".join(problems))
        _create_if_missing(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", report)
    else:
        report.kept.append(MANIFEST_NAME)
    return report


def _refuse_redirects(root: Path, rels: List[str]) -> None:
    """Refuse, before writing anything, any write path that is or passes through a symlink inside
    the instance (it could send a write outside it), whose parent components exist as non-directories,
    or whose destination exists as the wrong kind: a directory destination that is not a directory,
    or a file destination that is not a regular file."""
    dir_rels = set(INSTANCE_LAYOUT) | {"sources/demo"}
    for rel in rels:
        current = root
        parts = Path(rel).parts
        for i, part in enumerate(parts):
            current = current / part
            if current.is_symlink():
                raise InstanceError(f"{current} is a symlink; init writes only inside the instance and will not follow it")
            last = i == len(parts) - 1
            if not current.exists():
                continue
            if not last or rel in dir_rels:
                if not current.is_dir():
                    raise InstanceError(f"{current} exists and is not a directory")
            elif not current.is_file():
                raise InstanceError(f"{current} exists and is not a regular file")
            elif current.stat().st_nlink > 1:  # a hard link would carry an append to another path
                raise InstanceError(f"{current} has other hard links; init will not write to it")


def _create_if_missing(path: Path, text: str, report: InitReport) -> None:
    rel = path.relative_to(report.path).as_posix()
    try:
        with path.open("x", encoding="utf-8") as fh:  # exclusive: never clobbers a racing writer
            fh.write(text)
        report.created.append(rel)
    except FileExistsError:
        report.kept.append(rel)


def _demo_fixture_files() -> List[Path]:
    """The synthetic fixture conversations shipped beside the engine (repository checkout)."""
    fixtures = Path(__file__).resolve().parent.parent / "fixtures"
    files = sorted(fixtures.glob("*/conversation.json")) if fixtures.is_dir() else []
    if not files:
        raise InstanceError(f"no synthetic fixtures found under {fixtures}; --demo needs a repository checkout")
    return files
