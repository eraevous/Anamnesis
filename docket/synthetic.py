"""Writes the Docket E synthetic case set (docket/cases/synthetic-v1) deterministically.

Every text here was written for this repository; nothing is drawn from a real export or record.
These are development cases written by the producer. The acceptance trial's planted discrepancy is
chosen by someone else (Kate or Research), so the producer cannot teach to it.

Run: python3 -m docket.synthetic [OUTDIR]. The test suite checks the committed copy equals this output.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "cases" / "synthetic-v1"
OBSERVED_AT = "2026-11-18T00:00:00Z"

REPLY_1 = "The fixture plan holds. Keep F-11 off the active path."
REPLY_3 = "The benchmark may need a second island before the port."
FILED_3 = "The benchmark will need a second island before the port."  # claim strengthened
REPLY_7 = "Agree with the window: two eligible wakes, 24 hours.\n"
FILED_7 = "Agree with the window: two eligible wakes, 24 hours.   \r\n\r\n"  # trailing space, CRLF
LOST_8 = "Original answer text, written once and never filed."
RECON_8 = "Answer rewritten later from scratch; not the original."


def _req(i, recipient="kate", route="api", due="2026-11-17T18:00:00Z"):
    return {"id": f"RQ-{i:02d}", "recipient": recipient, "route": route,
            "asked_at": "2026-11-17T12:00:00Z", "due_by": due,
            "case": None}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def cases():
    reqs, replies, filings, receipts, bodies = [], [], [], [], {}

    def reply(i, text=None, at="2026-11-17T13:00:00Z", rid=None, body=None):
        rec = {"request_id": f"RQ-{i:02d}", "reply_id": rid or f"RP-{i:02d}", "author": "kate:api",
               "created_at": at}
        rec.update({"text": text} if body is None else {"body": body})
        replies.append(rec)

    def filing(i, text=None, at="2026-11-17T14:00:00Z", prov="original", fid=None, body=None):
        rec = {"request_id": f"RQ-{i:02d}", "filing_id": fid or f"FL-{i:02d}", "filed_at": at,
               "provenance": prov}
        rec.update({"text": text} if body is None else {"body": body})
        filings.append(rec)

    def add(i, case, gold, **kw):
        r = _req(i, **kw)
        r["case"] = case
        reqs.append((r, gold))

    add(1, "clean control: filed unchanged", "matched")
    reply(1, REPLY_1); filing(1, REPLY_1)

    add(2, "reply came back, never filed", "missing_filing")
    reply(2, "Two wakes is enough for a first window.")

    add(3, "filing strengthens the reply's claim", "mismatch")
    reply(3, REPLY_3); filing(3, FILED_3)

    add(4, "reply replayed and filed twice", "duplicate")
    reply(4, "Ship the comparator first.", rid="RP-04")
    reply(4, "Ship the comparator first.", rid="RP-04", at="2026-11-17T13:05:00Z")
    filing(4, "Ship the comparator first.", fid="FL-04a")
    filing(4, "Ship the comparator first.", fid="FL-04b", at="2026-11-17T14:05:00Z")

    add(5, "reply arrives after due_by, filed unchanged", "late_reply")
    reply(5, "Late, but here.", at="2026-11-17T20:00:00Z")
    filing(5, "Late, but here.", at="2026-11-17T21:00:00Z")

    add(6, "reply body unreadable (digest does not match the manifest)", "unknown")
    bodies["bodies/RP-06.txt"] = ("This body was altered after pinning.", _sha("The pinned body."))
    reply(6, body={"path": "bodies/RP-06.txt"}); filing(6, "The pinned body.")

    add(7, "clean control: CRLF and trailing spaces only (decoy, must match)", "matched")
    bodies["bodies/RP-07.txt"] = (REPLY_7, None)
    bodies["bodies/FL-07.txt"] = (FILED_7, None)
    reply(7, body={"path": "bodies/RP-07.txt", "sha256": _sha(REPLY_7)})
    filing(7, body={"path": "bodies/FL-07.txt", "sha256": _sha(FILED_7)})

    add(8, "filing lost; a declared reconstruction filed later (Cockpit R2 shape)", "missing_original")
    reply(8, LOST_8)
    receipts.append({"request_id": "RQ-08", "kind": "filing_failed", "at": "2026-11-17T14:00:00Z",
                     "original_sha256": _sha(LOST_8), "note": "push lost a race twice"})
    filing(8, RECON_8, at="2026-11-17T16:00:00Z", prov="reconstruction")

    add(9, "filing lost; later filing claims 'original' but is not the lost bytes", "missing_original")
    reply(9, LOST_8)
    receipts.append({"request_id": "RQ-09", "kind": "filing_failed", "at": "2026-11-17T14:00:00Z",
                     "original_sha256": _sha(LOST_8)})
    filing(9, RECON_8, at="2026-11-17T16:00:00Z")

    add(10, "route with no unattended consumer", "unsupported", recipient="abigail", route="none")

    add(11, "nothing came back, past due", "missing_reply")

    add(12, "nothing came back, not yet due (censored)", "pending", due="2026-11-19T00:00:00Z")
    return reqs, replies, filings, receipts, bodies


def write(out: Path = OUT) -> None:
    reqs, replies, filings, receipts, bodies = cases()
    out.mkdir(parents=True, exist_ok=True)
    (out / "bodies").mkdir(exist_ok=True)

    def jsonl(rows):
        return "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)

    files = {
        "requests.jsonl": ("requests", jsonl([r for r, _ in reqs]), None),
        "replies.jsonl": ("replies", jsonl(replies), None),
        "filings.jsonl": ("filings", jsonl(filings), None),
        "receipts.jsonl": ("receipts", jsonl(receipts), None),
    }
    for path, (text, pinned) in bodies.items():
        files[path] = ("body", text, pinned)
    sources = []
    for path, (role, text, pinned) in sorted(files.items()):
        (out / path).write_bytes(text.encode("utf-8"))
        src = {"role": role, "path": path, "sha256": pinned or _sha(text)}
        if pinned:
            src["note"] = "deliberately pinned to other bytes: the unreadable-source case"
        sources.append(src)
    manifest = {
        "schema": "anamnesis.docket-e.manifest/1",
        "normalization": "docket-norm/1",
        "observed_at": OBSERVED_AT,
        "provenance": "synthetic; written for this repository by claude:anamnesis (producer)",
        "window": {"eligible_consumer_wakes": 2, "wall_cap_hours": 24,
                   "fixed_at": "2026-11-17T22:00:00Z",
                   "note": "fixed before any observation (HO-ANA-20261117-2VK1)"},
        "sources": sources,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    gold = {r["id"]: g for r, g in reqs}
    (out / "gold.json").write_text(json.dumps(gold, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
