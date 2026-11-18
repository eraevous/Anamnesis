"""Docket E comparator: request / reply / filing receipts, compared deterministically.

Reviewer guide. This is the first Exec acceptance trial's tool (DEC-251, item E of the Convention
synthesis). It answers one question per request: did the reply that came back get filed where the
requester said it would be, unchanged? It reads only the files a pinned manifest names, checks
their sha256 digests, and writes a report whose bytes depend only on (code, normalization version,
manifest). It never writes to the records it reads and never infers authorship by similarity.

Statuses (one per request; secondary facts go in `flags`):
  matched            one reply, one original filing, equal under the pinned normalization
  missing_filing     a reply exists (successful read) but nothing was filed
  mismatch           the filing differs from the reply under the pinned normalization
  duplicate          more than one reply or filing for the request (replay)
  late_reply         the reply's event time is after the request's due_by (filing compared, see flags)
  missing_original   a filing failure is on record, or the filing says it is a reconstruction:
                     a reconstruction is never classified as the lost original
  missing_reply      nothing came back and the request was due before the snapshot
  pending            nothing came back and the request was not yet due (censored, not a failure)
  unsupported        the route has no unattended consumer; kept in its own denominator
  unknown            a source could not be read, or the read was ambiguous; never "missing"
  unpaired           a reply or filing names a request the manifest does not contain

Times are kept apart: `event_times` come from the records (asked, due, replied, filed);
`observed_at` is the manifest's snapshot time, when the inputs were pinned. The run's own wall
clock is never written into the report, so two runs produce identical bytes.

Stop conditions (the report's `stop` list; the CLI exits 2): an ungranted input (a path the
manifest does not list, one outside its folder, or a record source cited as a body), an ambiguous
normalization (texts equal only under a looser rule than the pinned one), untraceable evidence (a
filing timed before every reply, or a compared event time that is not an RFC 3339 instant with an
offset; times are compared as UTC instants, never as strings), an unreadable pinned source, a
normalization version the code does not implement, a manifest that lists a path twice or lacks one
of the four record roles, and, with --gold, any wrong gold classification (exit 3). The CLI also
refuses (exit 2) an --out path that is the manifest, a granted source or the --gold file,
by path or link. Every stop
check runs for every request before its status is chosen, so no status can hide a stop or a
secondary fact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

REPORT_SCHEMA = "anamnesis.docket-e.report/1"
MANIFEST_SCHEMA = "anamnesis.docket-e.manifest/1"
NORMALIZATION = "docket-norm/1"
ROLES = ("requests", "replies", "filings", "receipts", "body")
RECORD_ROLES = ("requests", "replies", "filings", "receipts")
STATUSES = (
    "matched", "missing_filing", "mismatch", "duplicate", "late_reply", "missing_original",
    "missing_reply", "pending", "unsupported", "unknown", "unpaired",
)
UNSUPPORTED_ROUTES = {"none", "unattended-none"}


class Stop(Exception):
    """A condition that halts the trial (re-pin before running again)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize(text: str) -> str:
    """docket-norm/1: NFC; CRLF/CR to LF; trailing spaces per line removed; outer blank lines removed.

    Deliberately narrow. Case, inner whitespace, quoting and punctuation all count as differences.
    """
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


# RFC 3339 section 5.6 date-time: full date, "T", full time with optional fraction, and an offset.
_RFC3339 = re.compile(r"(?P<sec>\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))?"
                      r"(?P<off>[Zz]|[+-]\d{2}:\d{2})", re.ASCII)


def instant(value) -> datetime | None:
    """An RFC 3339 timestamp as a UTC instant, or None if it is missing, malformed or has no offset.

    Event times are compared as instants, never as strings: 01:00-14:00 is after 13:00Z.
    The syntax is checked against RFC 3339 first (Codex review of e9358b4): `fromisoformat()` alone
    accepts basic-format times such as 20261117T130000Z and folds an impossible offset like +00:60
    into +01:00. Component ranges are then checked by the datetime constructor.
    A fraction finer than a microsecond cannot be held by datetime, which would truncate it and let
    distinct instants compare equal (Codex review of 27497a6). Digits past the sixth must all be
    zero; otherwise the time is not faithfully representable and is treated as unreadable.
    """
    if not isinstance(value, str):
        return None
    m = _RFC3339.fullmatch(value)
    if m is None:
        return None
    off = m.group("off")
    if off == "-00:00":
        # RFC 3339 section 4.3: "-00:00" means the local offset is unknown, so it is not a traceable
        # instant (Codex security review of d33b5b3).
        return None
    if off not in ("Z", "z") and (int(off[1:3]) > 23 or int(off[4:6]) > 59):
        return None
    frac = m.group("frac") or ""
    if frac[6:].strip("0"):
        return None
    frac = f".{frac[:6]}" if frac else ""
    try:
        dt = datetime.fromisoformat(m.group("sec") + frac + ("+00:00" if off in ("Z", "z") else off))
        # Conversion can leave datetime's range (0001-01-01T00:00:00+01:00 is year 0 in UTC;
        # Copilot review of d47e1b5): an unrepresentable instant is unreadable, not a crash.
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def loose(text: str) -> str:
    """A looser rule used only to detect ambiguity: equal here but not under normalize() stops the run."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and v != ""


def record_shape_problem(role: str, rec: dict) -> str | None:
    """Why a record's fields don't have the shapes the comparator relies on, or None.

    Checked when a record is read (Copilot review of ab49108): a digest-valid record such as
    {"text": 42} or {"body": "x"} must be a malformed record of its source, not a wrong status or a
    crash. Times are not checked here; they are checked, and stop the run, where they are compared.
    """
    if role == "requests":
        if not _nonempty_str(rec.get("id")):
            return "request id must be a non-empty string"
        for key in ("route", "recipient"):
            if key in rec and rec[key] is not None and not isinstance(rec[key], str):
                return f"{key} must be a string"
        return None
    if not _nonempty_str(rec.get("request_id")):
        return "request_id must be a non-empty string"
    if role == "receipts":
        if not isinstance(rec.get("kind"), str):
            return "receipt kind must be a string"
        if "original_sha256" in rec and rec["original_sha256"] is not None \
                and not isinstance(rec["original_sha256"], str):
            return "original_sha256 must be a string"
        return None
    if "text" in rec and not isinstance(rec["text"], str):
        return "text must be a string"
    if "body" in rec:
        body = rec["body"]
        if not isinstance(body, dict) or not _nonempty_str(body.get("path")):
            return "body must be an object with a non-empty string path"
        if "sha256" in body and body["sha256"] is not None and not isinstance(body["sha256"], str):
            return "body sha256 must be a string"
    if role == "filings" and "provenance" in rec and rec["provenance"] is not None \
            and not isinstance(rec["provenance"], str):
        return "provenance must be a string"
    return None


class Inputs:
    """The manifest's granted files, read once and digest-checked."""

    def __init__(self, manifest_path: Path):
        self.root = manifest_path.parent.resolve()
        raw = manifest_path.read_bytes()
        self.manifest_sha256 = sha256_bytes(raw)
        self.manifest = json.loads(raw)
        if self.manifest.get("schema") != MANIFEST_SCHEMA:
            raise Stop(f"manifest schema is {self.manifest.get('schema')!r}, expected {MANIFEST_SCHEMA}")
        if self.manifest.get("normalization") != NORMALIZATION:
            raise Stop(f"manifest pins normalization {self.manifest.get('normalization')!r}; "
                       f"this code implements {NORMALIZATION}")
        self.granted: dict[str, dict] = {}
        for src in self.manifest["sources"]:
            if src["role"] not in ROLES:
                raise Stop(f"unknown source role {src['role']!r}")
            self._check_path(src["path"])
            key = self.key(src["path"])
            if key in self.granted:
                # A second grant for one file would silently replace the first (and its role), or
                # read the same lines twice and fabricate a replay. Spellings are compared after
                # normalization, so "replies.jsonl" and "./replies.jsonl" are one file (Copilot
                # review of d47e1b5).
                raise Stop(f"manifest lists {src['path']!r} more than once")
            self.granted[key] = src
        for role in RECORD_ROLES:
            if not any(s["role"] == role for s in self.granted.values()):
                raise Stop(f"manifest grants no {role} source")
        self.stops: list[str] = []

    @staticmethod
    def key(rel: str) -> str:
        """One spelling per granted file: "./a//b.jsonl" and "a/b.jsonl" name the same file."""
        return posixpath.normpath(rel) if isinstance(rel, str) and rel else ""

    def _check_path(self, rel: str) -> None:
        p = PurePosixPath(rel)
        if p.is_absolute() or ".." in p.parts:
            raise Stop(f"ungranted input: {rel!r} is outside the manifest's folder")

    def read(self, rel: str) -> tuple[bytes | None, str | None]:
        """Return (bytes, None) or (None, reason). Ungranted paths are a stop, not an unknown."""
        if self.key(rel) not in self.granted or ".." in PurePosixPath(rel).parts \
                or PurePosixPath(rel).is_absolute():
            self.stops.append(f"ungranted input: {rel!r} is not listed in the manifest")
            return None, "ungranted_input"
        rel = self.key(rel)
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            # A listed name that resolves elsewhere (a symlink) is still outside the grant.
            self.stops.append(f"ungranted input: {rel!r} resolves outside the manifest's folder")
            return None, "ungranted_input"
        try:
            data = path.read_bytes()
        except OSError:
            return None, "unreadable_source"
        if sha256_bytes(data) != self.granted[rel]["sha256"]:
            return None, "digest_mismatch"
        return data, None

    def records(self, role: str) -> tuple[list[dict], list[str]]:
        """All JSONL records of a role, each tagged with its locator; plus per-source read problems."""
        out, problems = [], []
        for rel, src in sorted(self.granted.items()):
            if src["role"] != role:
                continue
            data, why = self.read(rel)
            if data is None:
                problems.append(f"{rel}: {why}")
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"{rel}: undecodable_source")
                continue
            # JSONL records end at "\n" only; str.splitlines() would also split inside a JSON
            # string at U+2028 and other Unicode line boundaries, which are legal unescaped.
            parsed = []
            for n, line in enumerate(text.split("\n"), 1):
                line = line.removesuffix("\r")
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    problems.append(f"{rel}:{n}: malformed_record")
                    parsed = None
                    break
                if not isinstance(rec, dict):
                    problems.append(f"{rel}:{n}: malformed_record (not an object)")
                    parsed = None
                    break
                shape = record_shape_problem(role, rec)
                if shape:
                    problems.append(f"{rel}:{n}: malformed_record ({shape})")
                    parsed = None
                    break
                rec["_locator"] = f"{rel}:{n}"
                rec["_line_sha256"] = sha256_bytes(line.encode("utf-8"))
                parsed.append(rec)
            if parsed is not None:
                out += parsed
        return out, problems

    def body_grant_problem(self, rec: dict) -> str | None:
        """Check a record's body reference against the grant, recording any stop; None if fine.

        Runs for every record that cites a body, whatever else is wrong with it and whether or not
        it names a known request (Copilot review of d47e1b5), so no status can hide the stop.
        """
        if "body" not in rec:
            return None
        rel = (rec.get("body") or {}).get("path", "")
        key = self.key(rel)
        if key not in self.granted or ".." in PurePosixPath(rel).parts or PurePosixPath(rel).is_absolute():
            self.stops.append(f"ungranted input: {rel!r} is not listed in the manifest")
            return "ungranted_input"
        if self.granted[key]["role"] != "body":
            # A record source cited as a body would promote unrelated bytes to filed evidence.
            self.stops.append(f"ungranted input: {rel!r} is not granted as a body "
                              f"(role {self.granted[key]['role']!r})")
            return "ungranted_input"
        return None

    def text_of(self, rec: dict) -> tuple[str | None, str | None]:
        grant = self.body_grant_problem(rec)
        if "text" in rec and "body" in rec:
            return None, "ambiguous_record (both inline text and a body)"
        if "text" in rec:
            return rec["text"], None
        if "body" not in rec:
            return None, "no_text (neither inline text nor a body)"
        if grant:
            return None, grant
        body = rec.get("body") or {}
        rel = body.get("path", "")
        data, why = self.read(rel)
        if data is None:
            return None, why
        if body.get("sha256") and sha256_bytes(data) != body["sha256"]:
            return None, "digest_mismatch"
        try:
            return data.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, "undecodable_source"


def _evidence(recs: list[dict]) -> list[dict]:
    return [{"locator": r["_locator"], "line_sha256": r["_line_sha256"]} for r in recs]


def record_facts(replies: list[dict], filings: list[dict], receipts: list[dict]) -> tuple[dict, list[str]]:
    """Event times and flags that the records state on their own, without a request or texts.

    Used where no request can govern (duplicate request ids, orphans) or where a source failed to
    read (Copilot review of ab49108): readable evidence is kept, but nothing is inferred from an
    absence, since a source that failed may hold the missing record.
    """
    times = {
        "replied_at": sorted((r.get("created_at") for r in replies), key=str),
        "filed_at": sorted((f.get("filed_at") for f in filings), key=str),
        "filing_failed_at": sorted((r.get("at") for r in receipts if r.get("kind") == "filing_failed"),
                                   key=str),
    }
    flags = []
    if any(f.get("provenance") == "reconstruction" for f in filings):
        flags.append("reconstruction_filed")
    if len(replies) > 1:
        flags.append("duplicate_replies")
    if len(filings) > 1:
        flags.append("duplicate_filings")
    if any(r.get("kind") == "filing_failed" for r in receipts):
        flags.append("filing_failed_recorded")
    return times, flags


def classify(req: dict, replies: list[dict], filings: list[dict], receipts: list[dict],
             inputs: Inputs, observed_at: str) -> dict:
    row = {
        "request_id": req["id"],
        "recipient": req.get("recipient"),
        "status": None,
        "reason": None,
        "flags": [],
        "event_times": {
            "asked_at": req.get("asked_at"),
            "due_by": req.get("due_by"),
            "replied_at": sorted((r.get("created_at") for r in replies), key=str),
            "filed_at": sorted((f.get("filed_at") for f in filings), key=str),
            "filing_failed_at": sorted((r.get("at") for r in receipts if r.get("kind") == "filing_failed"),
                                       key=str),
        },
        "observed_at": observed_at,
        "evidence": _evidence([req] + replies + filings + receipts),
    }

    def done(status: str, reason: str | None = None) -> dict:
        row["status"], row["reason"] = status, reason
        row["flags"].sort()
        return row

    # Every check below runs before any return, so no status can hide a stop condition or a
    # secondary fact (ANA Kate round 2; Codex review of 5121428). Order: read, facts, stops, status.
    texts: dict[str, str] = {}
    text_problem = None
    for rec in replies + filings:
        text, why = inputs.text_of(rec)
        if text is None:
            if text_problem is None:
                where = (rec.get("body") or {}).get("path")
                text_problem = f"{why}: {where or 'inline text'} (cited by {rec['_locator']})"
            continue
        texts[rec["_locator"]] = text

    # Secondary facts: each is observable from the records alone, whatever the primary status.
    # Times are parsed once, as UTC instants; a compared time that will not parse is untraceable.
    bad_time = None
    due = None
    asked = None
    # Every request needs a readable asked_at, events or not (Codex security review of 27497a6):
    # without it, neither a reply nor its absence (missing_reply, pending) can be attributed.
    asked = instant(req.get("asked_at"))
    if asked is None:
        bad_time = f"asked_at {req.get('asked_at')!r} at {req['_locator']}"
    if req.get("due_by") is not None:
        due = instant(req["due_by"])
        if due is None:
            bad_time = f"due_by {req['due_by']!r} at {req['_locator']}"
    elif req.get("route") not in UNSUPPORTED_ROUTES:
        # A supported request needs a deadline (Codex security review of d47e1b5): without one,
        # a missing reply would read as pending forever and a late reply as on time.
        bad_time = f"due_by missing at {req['_locator']}"
    reply_at = {}
    for r in replies:
        reply_at[r["_locator"]] = t = instant(r.get("created_at"))
        if t is None and bad_time is None:
            bad_time = f"created_at {r.get('created_at')!r} at {r['_locator']}"
    filed_at = {}
    for f in filings:
        filed_at[f["_locator"]] = t = instant(f.get("filed_at"))
        if t is None and bad_time is None:
            bad_time = f"filed_at {f.get('filed_at')!r} at {f['_locator']}"
    late = bool(due and any(t and t > due for t in reply_at.values()))
    failed = [r for r in receipts if r.get("kind") == "filing_failed"]
    # A filing_failed receipt decides missing_original, so its time is compared like any other
    # event (Codex security review of a700ab5): unreadable, or outside request..snapshot, is untraceable.
    failed_at = {}
    for r in failed:
        failed_at[r["_locator"]] = t = instant(r.get("at"))
        if t is None and bad_time is None:
            bad_time = f"filing_failed at {r.get('at')!r} at {r['_locator']}"
    if late:
        row["flags"].append("late_reply")
    if any(f.get("provenance") == "reconstruction" for f in filings):
        row["flags"].append("reconstruction_filed")
    if len(replies) > 1:
        row["flags"].append("duplicate_replies")
    if len(filings) > 1:
        row["flags"].append("duplicate_filings")
    if filings and not replies:
        row["flags"].append("filing_without_reply")
    if failed:
        row["flags"].append("filing_failed_recorded")
    lost = {r.get("original_sha256") for r in failed} - {None}
    recovered = None
    if failed and len(filings) == 1 and filings[0].get("provenance") == "original" \
            and filings[0]["_locator"] in texts:
        # Filed as "original" after a recorded failure: original only if its bytes are the lost ones.
        recovered = sha256_bytes(texts[filings[0]["_locator"]].encode("utf-8")) in lost
        row["flags"].append("original_recovered_by_digest" if recovered else "undeclared_reconstruction")

    # Mandatory stop checks, each evaluated independently of the others and of any status.
    untraceable = None
    if bad_time:
        untraceable = f"event time is not an RFC 3339 instant with an offset: {bad_time}"
        inputs.stops.append(f"untraceable evidence for {req['id']}: unreadable time, {bad_time}")
    else:
        # Every event must sit in request <= reply <= filing <= snapshot (Codex security review of
        # e9358b4): evidence dated before the request or after the snapshot cannot be traced to it.
        snapshot = instant(observed_at)
        events = list(reply_at.values()) + list(filed_at.values()) + list(failed_at.values())
        if asked > snapshot:
            # A request dated after the snapshot was not yet asked when it was taken.
            untraceable = "request asked_at is after the snapshot's observed_at (untraceable order)"
            inputs.stops.append(f"untraceable evidence for {req['id']}: asked_at after observed_at")
        elif any(t < asked for t in events):
            untraceable = "event time precedes the request (untraceable order)"
            inputs.stops.append(f"untraceable evidence for {req['id']}: event before asked_at")
        elif any(t > snapshot for t in events):
            untraceable = "event time is after the snapshot's observed_at (untraceable order)"
            inputs.stops.append(f"untraceable evidence for {req['id']}: event after observed_at")
        elif replies and any(t < min(reply_at.values()) for t in filed_at.values()):
            untraceable = "filing event time precedes the reply it copies (untraceable order)"
            inputs.stops.append(f"untraceable evidence for {req['id']}: filing precedes every reply")
    # Ambiguity is checked whenever each side has exactly one distinct text under the pinned rule,
    # however many identical replays there are (Copilot review of ab49108): a replay must not let
    # "Hello" vs "hello" escape the stop. With two distinct texts on a side, no pairing is invented.
    ambiguous = False
    reply_norms = {normalize(texts[r["_locator"]]) for r in replies if r["_locator"] in texts}
    filing_norms = {normalize(texts[f["_locator"]]) for f in filings if f["_locator"] in texts}
    if len(reply_norms) == 1 and len(filing_norms) == 1:
        (a,), (b,) = reply_norms, filing_norms
        if a != b and loose(a) == loose(b):
            ambiguous = True
            inputs.stops.append(f"ambiguous normalization for {req['id']}")

    # Validation failures: the evidence cannot be read as it stands, so the status is unknown.
    if text_problem:
        return done("unknown", text_problem)
    bad = [f for f in filings if f.get("provenance") not in ("original", "reconstruction")]
    if bad:
        return done("unknown", f"filing provenance must be 'original' or 'reconstruction', "
                               f"got {bad[0].get('provenance')!r} at {bad[0]['_locator']}")
    if untraceable:
        return done("unknown", untraceable)
    if ambiguous:
        return done("unknown", f"equal only under a looser rule than {NORMALIZATION}")

    if req.get("route") in UNSUPPORTED_ROUTES:
        return done("unsupported", f"route {req.get('route')!r} has no unattended consumer")
    if not replies:
        if due and due < instant(observed_at):
            return done("missing_reply", "no reply by the snapshot, after due_by")
        return done("pending", "no reply yet; not due at the snapshot (censored)")
    if len(replies) > 1 or len(filings) > 1:
        # No pairing is invented between duplicates; per-record facts stay in flags and evidence.
        return done("duplicate", f"{len(replies)} replies, {len(filings)} filings")

    reply = replies[0]
    lost_msg = "original filing lost; a reconstruction is not the original"
    if filings and filings[0].get("provenance") == "reconstruction":
        return done("missing_original", lost_msg)
    if failed and (not filings or not recovered):
        return done("missing_original", lost_msg)

    if not filings:
        return done("missing_filing", "reply read successfully; no filing found")

    filing = filings[0]
    if normalize(texts[reply["_locator"]]) != normalize(texts[filing["_locator"]]):
        return done("mismatch", f"filing differs from reply under {NORMALIZATION}")
    if late:
        return done("late_reply", "filed unchanged, but the reply came after due_by")
    return done("matched")


def run(manifest_path: Path, code_version: str = "unpinned") -> dict:
    inputs = Inputs(manifest_path)
    observed_at = inputs.manifest["observed_at"]
    if instant(observed_at) is None:
        raise Stop(f"manifest observed_at {observed_at!r} is not an RFC 3339 instant with an offset")
    data = {}
    source_problems = []
    for role in ("requests", "replies", "filings", "receipts"):
        data[role], probs = inputs.records(role)
        source_problems += probs

    by_req: dict = {}
    for r in data["requests"]:
        by_req.setdefault(r["id"], []).append(r)
    rows = []
    for rid in sorted(by_req):
        pick = lambda role: [x for x in data[role] if x.get("request_id") == rid]  # noqa: E731
        if len(by_req[rid]) > 1:
            # Two request records with one id: which one governs is unknowable, so nothing is classified
            # and no request's times are used; the replies', filings' and receipts' own facts are kept.
            inputs.stops.append(f"duplicate request id {rid!r} ({len(by_req[rid])} records)")
            times, flags = record_facts(pick("replies"), pick("filings"), pick("receipts"))
            rows.append({
                "request_id": rid, "recipient": None, "status": "unknown",
                "reason": f"{len(by_req[rid])} request records share this id", "flags": sorted(flags),
                "event_times": {"asked_at": None, "due_by": None, **times}, "observed_at": observed_at,
                "evidence": _evidence(by_req[rid] + pick("replies") + pick("filings") + pick("receipts")),
            })
            continue
        by_req[rid] = by_req[rid][0]
        if source_problems:
            # A whole source failed to read: nothing about any request can be said to be missing, but
            # what was read is kept (locators, event times, flags the records state themselves).
            row = classify(by_req[rid], [], [], [], inputs, observed_at)
            times, flags = record_facts(pick("replies"), pick("filings"), pick("receipts"))
            row["event_times"].update(times)
            row.update(status="unknown", reason="source unreadable: " + "; ".join(source_problems),
                       flags=sorted(set(row["flags"]) | set(flags)),
                       evidence=_evidence([by_req[rid]] + pick("replies") + pick("filings")
                                          + pick("receipts")))
            rows.append(row)
            continue
        rows.append(classify(by_req[rid], pick("replies"), pick("filings"), pick("receipts"),
                             inputs, observed_at))
    if source_problems:
        # A pinned source failed: the inputs no longer match the pin, so the run cannot stand,
        # and request membership is unknown, so nothing is called unpaired.
        inputs.stops.append("pinned source unreadable: " + "; ".join(source_problems))
    for role in () if source_problems else ("replies", "filings", "receipts"):
        for x in data[role]:
            if x.get("request_id") not in by_req:
                inputs.body_grant_problem(x)  # an orphan's body must be granted too
                # The record's own event time is kept: a missing request doesn't make it unavailable.
                times, _ = record_facts(*([x] if role == r else [] for r in ("replies", "filings",
                                                                             "receipts")))
                rows.append({"request_id": x.get("request_id"), "status": "unpaired",
                             "reason": f"{role[:-1]} names no request in the manifest", "flags": [],
                             "event_times": {k: v for k, v in times.items() if v},
                             "observed_at": observed_at, "evidence": _evidence([x])})

    counts = {s: 0 for s in STATUSES}
    for r in rows:
        counts[r["status"]] += 1
    return {
        "schema": REPORT_SCHEMA,
        "code_version": code_version,
        "normalization": NORMALIZATION,
        "manifest_sha256": inputs.manifest_sha256,
        "observed_at": observed_at,
        "window": inputs.manifest.get("window"),
        "denominators": {
            "supported": sum(v for k, v in counts.items() if k not in ("unsupported", "unpaired")),
            "unsupported": counts["unsupported"],
        },
        "counts": counts,
        "stop": sorted(set(inputs.stops)),
        "rows": rows,
    }


def check_gold(report: dict, gold: dict) -> list[str]:
    # Unpaired rows are checked too (Copilot review of ab49108): an expected orphan must be found,
    # and an unexpected one is a wrong classification.
    got = {r["request_id"]: r["status"] for r in report["rows"]}
    wrong = [f"{rid}: gold {want}, got {got.get(rid)}" for rid, want in sorted(gold.items())
             if got.get(rid) != want]
    wrong += [f"{rid}: classified {st} but absent from gold" for rid, st in sorted(got.items())
              if rid not in gold]
    return wrong


def dumps(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def output_aliases_input(out: Path, manifest_path: Path, extra: tuple = ()) -> str | None:
    """The input that `out` would overwrite (by path, symlink or hard link), or None.

    The comparator never writes to what it reads (Copilot review of ab49108), the --gold file
    included (Codex review of 2541776).
    """
    try:
        man = json.loads(manifest_path.read_bytes())
        rels = [s["path"] for s in man.get("sources", [])]
    except (OSError, ValueError, TypeError, KeyError):
        rels = []
    inputs = [manifest_path] + [manifest_path.parent / r for r in rels] + [Path(p) for p in extra if p]
    target = out.resolve()
    for p in inputs:
        if p.resolve() == target:
            return str(p)
        try:
            if out.exists() and p.exists() and out.samefile(p):
                return str(p)
        except OSError:
            continue
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("manifest", type=Path)
    ap.add_argument("-o", "--out", type=Path, help="write the report here (default stdout)")
    ap.add_argument("--code-version", default="unpinned", help="the comparator's commit SHA")
    ap.add_argument("--gold", type=Path, help="gold statuses {request_id: status}; any miss stops")
    a = ap.parse_args(argv)
    try:
        report = run(a.manifest, a.code_version)
    except Stop as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 2
    rc = 2 if report["stop"] else 0
    if a.gold:
        wrong = check_gold(report, json.loads(a.gold.read_text()))
        if wrong:
            report["stop"] = sorted(set(report["stop"]) | {"wrong gold classification: " + w for w in wrong})
            rc = 3
    text = dumps(report)
    if a.out:
        clash = output_aliases_input(a.out, a.manifest, (a.gold,))
        if clash:
            print(f"STOP: --out would overwrite an input: {clash}", file=sys.stderr)
            return 2
        a.out.write_text(text)
    else:
        sys.stdout.write(text)
    for s in report["stop"]:
        print(f"STOP: {s}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
