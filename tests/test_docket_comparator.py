"""Tests for the Docket E comparator (docket/comparator.py) and its synthetic case set.

Reviewer guide: these prove the classifications the trial depends on, mechanically, on synthetic
inputs only. The headline properties: every gold case gets its gold status; a reconstruction is
never matched to a lost original; an unreadable source is `unknown`, never `missing`; ungranted
inputs and ambiguous normalization stop the run; and the report's bytes are a function of the
pinned inputs alone. They say nothing about consumer uptake, which is observed later, by a reader
outside the producing family.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from docket import comparator, synthetic

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "docket" / "cases" / "synthetic-v1"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_set(folder: Path, requests, replies=(), filings=(), receipts=(), extra_sources=(),
              normalization=comparator.NORMALIZATION):
    """A tiny manifest set for one-off cases; returns the manifest path."""
    sources = []
    for role, rows in (("requests", requests), ("replies", replies), ("filings", filings),
                       ("receipts", receipts)):
        text = "".join(json.dumps(r) + "\n" for r in rows)
        (folder / f"{role}.jsonl").write_text(text)
        sources.append({"role": role, "path": f"{role}.jsonl", "sha256": sha(text)})
    sources += list(extra_sources)
    m = {"schema": comparator.MANIFEST_SCHEMA, "normalization": normalization,
         "observed_at": "2026-11-18T00:00:00Z", "sources": sources}
    (folder / "manifest.json").write_text(json.dumps(m))
    return folder / "manifest.json"


REQ = {"id": "RQ-X", "route": "api", "asked_at": "2026-11-17T12:00:00Z", "due_by": "2026-11-17T18:00:00Z"}
REPLY = {"request_id": "RQ-X", "reply_id": "RP-X", "created_at": "2026-11-17T13:00:00Z"}
FILING = {"request_id": "RQ-X", "filing_id": "FL-X", "filed_at": "2026-11-17T14:00:00Z",
          "provenance": "original"}


class SyntheticSet(unittest.TestCase):
    def test_committed_fixture_equals_generator(self):
        with tempfile.TemporaryDirectory() as d:
            synthetic.write(Path(d))
            for p in sorted(Path(d).rglob("*")):
                if p.is_file():
                    rel = p.relative_to(d)
                    self.assertEqual((FIX / rel).read_bytes(), p.read_bytes(), str(rel))

    def test_every_gold_case_classified_correctly(self):
        report = comparator.run(FIX / "manifest.json")
        gold = json.loads((FIX / "gold.json").read_text())
        self.assertEqual(comparator.check_gold(report, gold), [])
        self.assertEqual(report["stop"], [])

    def test_required_case_kinds_present(self):
        gold = set(json.loads((FIX / "gold.json").read_text()).values())
        for status in ("matched", "missing_filing", "mismatch", "duplicate", "late_reply",
                       "unknown", "missing_original", "unsupported"):
            self.assertIn(status, gold)

    def test_report_is_deterministic(self):
        a = comparator.dumps(comparator.run(FIX / "manifest.json", "abc"))
        b = comparator.dumps(comparator.run(FIX / "manifest.json", "abc"))
        self.assertEqual(a, b)

    def test_rows_keep_event_and_observation_times_apart(self):
        report = comparator.run(FIX / "manifest.json")
        row = next(r for r in report["rows"] if r["request_id"] == "RQ-05")
        self.assertEqual(row["observed_at"], "2026-11-18T00:00:00Z")
        self.assertEqual(row["event_times"]["replied_at"], ["2026-11-17T20:00:00Z"])
        self.assertTrue(all(e["locator"] and e["line_sha256"] for e in row["evidence"]))

    def test_unsupported_route_has_its_own_denominator(self):
        report = comparator.run(FIX / "manifest.json")
        self.assertEqual(report["denominators"]["unsupported"], 1)
        self.assertEqual(report["denominators"]["supported"], len(report["rows"]) - 1)

    def test_wrong_gold_stops_with_exit_3(self):
        with tempfile.TemporaryDirectory() as d:
            gold = json.loads((FIX / "gold.json").read_text())
            gold["RQ-03"] = "matched"  # deny a true mismatch
            g = Path(d) / "gold.json"
            g.write_text(json.dumps(gold))
            rc = comparator.main([str(FIX / "manifest.json"), "--gold", str(g), "-o", str(Path(d) / "r")])
            self.assertEqual(rc, 3)


class Reconstruction(unittest.TestCase):
    def test_identical_text_declared_reconstruction_is_not_the_original(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="same")],
                          [dict(FILING, text="same", provenance="reconstruction")])
            row = comparator.run(m)["rows"][0]
            self.assertEqual(row["status"], "missing_original")
            self.assertIn("reconstruction_filed", row["flags"])

    def test_lost_bytes_recovered_exactly_count_as_original(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="lost text")],
                          [dict(FILING, text="lost text", filed_at="2026-11-17T16:00:00Z")],
                          [{"request_id": "RQ-X", "kind": "filing_failed", "at": "2026-11-17T14:00:00Z",
                            "original_sha256": sha("lost text")}])
            row = comparator.run(m)["rows"][0]
            self.assertEqual(row["status"], "matched")
            self.assertIn("original_recovered_by_digest", row["flags"])


class StopsAndUnknowns(unittest.TestCase):
    def test_ambiguous_normalization_stops(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="Keep  the window")],
                          [dict(FILING, text="keep the window")])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(report["stop"])

    def test_unlisted_body_is_ungranted_and_stops(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "secret.txt").write_text("not granted")
            m = write_set(Path(d), [REQ], [dict(REPLY, body={"path": "secret.txt"})], [dict(FILING, text="x")])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("ungranted" in s for s in report["stop"]))

    def test_path_outside_manifest_folder_refused(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], extra_sources=[{"role": "body", "path": "../x", "sha256": "0"}])
            with self.assertRaises(comparator.Stop):
                comparator.run(m)

    def test_unimplemented_normalization_refused(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], normalization="docket-norm/2")
            with self.assertRaises(comparator.Stop):
                comparator.run(m)

    def test_unreadable_whole_source_makes_every_row_unknown_not_missing(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "set"
            shutil.copytree(FIX, work)
            (work / "filings.jsonl").write_text("{}\n")  # bytes no longer match the pinned digest
            report = comparator.run(work / "manifest.json")
            self.assertEqual({r["status"] for r in report["rows"]}, {"unknown"})

    def test_filing_before_reply_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a")],
                          [dict(FILING, text="a", filed_at="2026-11-17T12:30:00Z")])
            self.assertEqual(comparator.run(m)["rows"][0]["status"], "unknown")

    def test_orphan_reply_is_unpaired(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, request_id="RQ-NONE", text="a")])
            statuses = sorted(r["status"] for r in comparator.run(m)["rows"])
            self.assertEqual(statuses, ["missing_reply", "unpaired"])


if __name__ == "__main__":
    unittest.main()
