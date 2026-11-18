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

    def test_unreadable_requests_source_stops_and_claims_no_unpaired(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a")])
            (Path(d) / "requests.jsonl").unlink()
            report = comparator.run(m)
            self.assertEqual(report["rows"], [])
            self.assertTrue(any("pinned source unreadable" in s for s in report["stop"]))
            self.assertEqual(comparator.main([str(m), "-o", str(Path(d) / "r")]), 2)

    def test_record_with_both_text_and_body_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a", body={"path": "gone.txt"})],
                          extra_sources=[{"role": "body", "path": "gone.txt", "sha256": "0"}])
            row = comparator.run(m)["rows"][0]
            self.assertEqual(row["status"], "unknown")
            self.assertIn("ambiguous_record", row["reason"])

    def test_symlink_out_of_the_manifest_folder_stops(self):
        with tempfile.TemporaryDirectory() as d:
            outside = Path(d) / "outside.txt"
            outside.write_text("a")
            case = Path(d) / "case"
            case.mkdir()
            (case / "body.txt").symlink_to(outside)
            m = write_set(case, [REQ], [dict(REPLY, body={"path": "body.txt"})], [dict(FILING, text="a")],
                          extra_sources=[{"role": "body", "path": "body.txt", "sha256": sha("a")}])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("resolves outside" in s for s in report["stop"]))

    def test_duplicate_request_id_stops_and_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ, dict(REQ, route="none")], [dict(REPLY, text="a")],
                          [dict(FILING, text="a")])
            report = comparator.run(m)
            self.assertEqual([r["status"] for r in report["rows"]], ["unknown"])
            self.assertEqual(report["denominators"]["unsupported"], 0)
            self.assertTrue(any("duplicate request id" in s for s in report["stop"]))

    def test_filing_without_explicit_provenance_is_unknown(self):
        for prov in (None, "Original", "copy"):
            with tempfile.TemporaryDirectory() as d:
                filing = dict(FILING, text="a")
                if prov is None:
                    del filing["provenance"]
                else:
                    filing["provenance"] = prov
                m = write_set(Path(d), [REQ], [dict(REPLY, text="a")], [filing])
                self.assertEqual(comparator.run(m)["rows"][0]["status"], "unknown", prov)

    def test_unicode_line_separator_inside_a_record_is_not_a_record_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a\u2028b")], [dict(FILING, text="a\u2028b")])
            # json.dumps escapes U+2028 by default; write the raw character to exercise the reader.
            for role in ("replies", "filings"):
                p = Path(d) / f"{role}.jsonl"
                p.write_text(p.read_text().replace("\\u2028", "\u2028"), encoding="utf-8")
            mf = json.loads(m.read_text())
            for src in mf["sources"]:
                src["sha256"] = hashlib.sha256((Path(d) / src["path"]).read_bytes()).hexdigest()
            m.write_text(json.dumps(mf))
            self.assertIn("\u2028", (Path(d) / "replies.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(comparator.run(m)["rows"][0]["status"], "matched")

    def test_malformed_record_is_a_source_problem_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a")])
            p = Path(d) / "replies.jsonl"
            p.write_text(p.read_text() + "{not json\n")
            mf = json.loads(m.read_text())
            for src in mf["sources"]:
                src["sha256"] = hashlib.sha256((Path(d) / src["path"]).read_bytes()).hexdigest()
            m.write_text(json.dumps(mf))
            report = comparator.run(m)
            self.assertEqual({r["status"] for r in report["rows"]}, {"unknown"})
            self.assertTrue(any("malformed_record" in s for s in report["stop"]))

    def test_filing_before_reply_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a")],
                          [dict(FILING, text="a", filed_at="2026-11-17T12:30:00Z")])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("untraceable evidence" in s for s in report["stop"]))

    # Overlapping cases (ANA Kate, round 2): the primary status may not hide a secondary fact
    # or a stop condition.

    def test_duplicate_with_reconstruction_keeps_reconstruction_flag(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a")],
                          [dict(FILING, text="a"),
                           dict(FILING, filing_id="FL-Y", text="a", provenance="reconstruction")])
            row = comparator.run(m)["rows"][0]
            self.assertEqual(row["status"], "duplicate")
            self.assertIn("reconstruction_filed", row["flags"])
            self.assertIn("duplicate_filings", row["flags"])

    def test_late_and_duplicate_keeps_late_flag(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a", created_at="2026-11-17T19:00:00Z")],
                          [dict(FILING, text="a", filed_at="2026-11-17T20:00:00Z"),
                           dict(FILING, filing_id="FL-Y", text="a", filed_at="2026-11-17T21:00:00Z")])
            row = comparator.run(m)["rows"][0]
            self.assertEqual(row["status"], "duplicate")
            self.assertIn("late_reply", row["flags"])

    def test_no_reply_with_duplicate_filings_keeps_filing_facts(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [],
                          [dict(FILING, text="a"), dict(FILING, filing_id="FL-Y", text="b")])
            row = comparator.run(m)["rows"][0]
            self.assertEqual(row["status"], "missing_reply")
            self.assertIn("duplicate_filings", row["flags"])
            self.assertIn("filing_without_reply", row["flags"])

    def test_reconstruction_with_untraceable_order_stops(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a")],
                          [dict(FILING, text="a", provenance="reconstruction",
                                filed_at="2026-11-17T12:30:00Z")])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("untraceable evidence" in s for s in report["stop"]))

    def test_late_and_altered_is_mismatch_with_late_flag(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="may", created_at="2026-11-17T19:00:00Z")],
                          [dict(FILING, text="will", filed_at="2026-11-17T20:00:00Z")])
            row = comparator.run(m)["rows"][0]
            self.assertEqual(row["status"], "mismatch")
            self.assertIn("late_reply", row["flags"])

    # Codex review of 5121428: no early return may skip a stop check or drop a secondary fact.

    def test_ambiguous_normalization_stops_even_when_route_unsupported(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [dict(REQ, route="none")], [dict(REPLY, text="Hello")],
                          [dict(FILING, text="hello")])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("ambiguous normalization" in s for s in report["stop"]))

    def test_ambiguous_normalization_stops_even_for_a_reconstruction(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="Hello")],
                          [dict(FILING, text="hello", provenance="reconstruction")])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertIn("reconstruction_filed", report["rows"][0]["flags"])
            self.assertTrue(any("ambiguous normalization" in s for s in report["stop"]))

    def test_untraceable_order_keeps_secondary_facts(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a", created_at="2026-11-17T19:00:00Z")],
                          [dict(FILING, text="a", provenance="reconstruction",
                                filed_at="2026-11-17T12:30:00Z")],
                          [{"request_id": "RQ-X", "kind": "filing_failed", "at": "2026-11-17T12:00:00Z"}])
            row = comparator.run(m)["rows"][0]
            self.assertEqual(row["status"], "unknown")
            for flag in ("reconstruction_filed", "late_reply", "filing_failed_recorded"):
                self.assertIn(flag, row["flags"])

    def test_invalid_provenance_does_not_hide_untraceable_order(self):
        with tempfile.TemporaryDirectory() as d:
            f = dict(FILING, text="a", filed_at="2026-11-17T12:30:00Z")
            del f["provenance"]
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a")], [f])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("untraceable evidence" in s for s in report["stop"]))
            self.assertEqual(comparator.main([str(m), "-o", str(Path(d) / "r.json")]), 2)

    def test_unreadable_body_does_not_hide_untraceable_order_or_flags(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "b.txt").write_text("a")
            body = {"role": "body", "path": "b.txt", "sha256": sha("a")}
            m = write_set(Path(d), [REQ],
                          [dict(REPLY, body={"path": "b.txt", "sha256": "0" * 64},
                                created_at="2026-11-17T19:00:00Z"),
                           dict(REPLY, reply_id="RP-Y", text="a", created_at="2026-11-17T19:30:00Z")],
                          [dict(FILING, text="a", filed_at="2026-11-17T12:30:00Z")],
                          extra_sources=[body])
            report = comparator.run(m)
            row = report["rows"][0]
            self.assertEqual(row["status"], "unknown")
            self.assertIn("digest_mismatch", row["reason"])
            self.assertIn("late_reply", row["flags"])
            self.assertIn("duplicate_replies", row["flags"])
            self.assertTrue(any("untraceable evidence" in s for s in report["stop"]))

    # Codex security review of d95b0c6: role grants, instants, duplicate grants.

    def test_body_must_be_granted_as_a_body(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ])
            req_src = next(s for s in json.loads(m.read_text())["sources"] if s["role"] == "requests")
            cite = {"path": "requests.jsonl", "sha256": req_src["sha256"]}
            m = write_set(Path(d), [REQ], [dict(REPLY, body=cite)], [dict(FILING, body=cite)])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("not granted as a body" in s for s in report["stop"]))
            self.assertEqual(comparator.main([str(m), "-o", str(Path(d) / "r.json")]), 2)

    def test_order_is_compared_as_instants_not_strings(self):
        with tempfile.TemporaryDirectory() as d:
            # 01:00-14:00 is 15:00Z; the filing at 13:00Z precedes it.
            m = write_set(Path(d), [dict(REQ, due_by="2026-11-18T18:00:00Z")],
                          [dict(REPLY, text="a", created_at="2026-11-18T01:00:00-14:00")],
                          [dict(FILING, text="a", filed_at="2026-11-18T13:00:00Z")])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("untraceable evidence" in s for s in report["stop"]))

    def test_lateness_is_compared_as_instants(self):
        with tempfile.TemporaryDirectory() as d:
            # 12:00-08:00 is 20:00Z, after due_by 18:00Z, though the string sorts earlier.
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a", created_at="2026-11-17T12:00:00-08:00")],
                          [dict(FILING, text="a", filed_at="2026-11-17T21:00:00Z")])
            row = comparator.run(m)["rows"][0]
            self.assertEqual(row["status"], "late_reply")

    def test_unparseable_or_offsetless_time_is_untraceable(self):
        for bad in ("yesterday", "2026-11-17T13:00:00"):
            with tempfile.TemporaryDirectory() as d:
                m = write_set(Path(d), [REQ], [dict(REPLY, text="a", created_at=bad)],
                              [dict(FILING, text="a")])
                report = comparator.run(m)
                self.assertEqual(report["rows"][0]["status"], "unknown", bad)
                self.assertTrue(any("untraceable evidence" in s for s in report["stop"]), bad)

    # Codex review of e9358b4: strict RFC 3339 syntax, and request <= reply <= filing <= snapshot.
    def test_non_rfc3339_time_is_untraceable_not_normalized(self):
        for bad in ("2026-11-17T13:00:00+00:60", "20261117T130000Z", "2026-11-17T13:00:00+0000",
                    "2026-11-17 13:00:00Z", "2026-11-17T13:00Z"):
            with tempfile.TemporaryDirectory() as d:
                m = write_set(Path(d), [REQ], [dict(REPLY, text="a", created_at=bad)],
                              [dict(FILING, text="a")])
                report = comparator.run(m)
                self.assertEqual(report["rows"][0]["status"], "unknown", bad)
                self.assertTrue(any("untraceable evidence" in s for s in report["stop"]), bad)

    def test_valid_rfc3339_forms_still_parse(self):
        for good in ("2026-11-17T13:00:00Z", "2026-11-17t13:00:00z", "2026-11-17T13:00:00.25+00:00",
                     "2026-11-17T08:00:00-05:00"):
            self.assertIsNotNone(comparator.instant(good), good)

    def test_event_before_the_request_is_untraceable(self):
        for reply_at, filed_at in (("2026-11-17T11:00:00Z", "2026-11-17T14:00:00Z"),
                                   ("2026-11-17T10:00:00Z", "2026-11-17T11:00:00Z")):
            with tempfile.TemporaryDirectory() as d:
                m = write_set(Path(d), [REQ], [dict(REPLY, text="a", created_at=reply_at)],
                              [dict(FILING, text="a", filed_at=filed_at)])
                report = comparator.run(m)
                self.assertEqual(report["rows"][0]["status"], "unknown")
                self.assertTrue(any("before asked_at" in s for s in report["stop"]))
                self.assertEqual(comparator.main([str(m)]), 2)

    def test_event_after_the_snapshot_is_untraceable(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, text="a")],
                          [dict(FILING, text="a", filed_at="2026-11-18T00:00:01Z")])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("after observed_at" in s for s in report["stop"]))

    def test_missing_asked_at_with_evidence_is_untraceable(self):
        with tempfile.TemporaryDirectory() as d:
            req = {k: v for k, v in REQ.items() if k != "asked_at"}
            m = write_set(Path(d), [req], [dict(REPLY, text="a")], [dict(FILING, text="a")])
            report = comparator.run(m)
            self.assertEqual(report["rows"][0]["status"], "unknown")
            self.assertTrue(any("untraceable evidence" in s for s in report["stop"]))

    def test_duplicate_manifest_path_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ])
            man = json.loads(m.read_text())
            req_src = next(s for s in man["sources"] if s["role"] == "requests")
            man["sources"].append(dict(req_src, role="body"))
            m.write_text(json.dumps(man))
            with self.assertRaises(comparator.Stop):
                comparator.run(m)
            self.assertEqual(comparator.main([str(m)]), 2)

    def test_manifest_missing_a_required_role_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ])
            man = json.loads(m.read_text())
            man["sources"] = [s for s in man["sources"] if s["role"] != "requests"]
            m.write_text(json.dumps(man))
            with self.assertRaises(comparator.Stop):
                comparator.run(m)

    def test_orphan_reply_is_unpaired(self):
        with tempfile.TemporaryDirectory() as d:
            m = write_set(Path(d), [REQ], [dict(REPLY, request_id="RQ-NONE", text="a")])
            statuses = sorted(r["status"] for r in comparator.run(m)["rows"])
            self.assertEqual(statuses, ["missing_reply", "unpaired"])


if __name__ == "__main__":
    unittest.main()
