"""Tests for ingest and cited answers (anamnesis/answer.py; HO-ANA-20261118-6H0J item 4, steps 3 and 4).

Reviewer guide: synthetic fixtures only. The anchors come from each fixture's gold.json, so these
tests check that an answer cites the units the gold says hold the answer, that every quote is text
from the unit it cites, and that a question with no source gets no answer.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from anamnesis import MessageIndex, ask, bundles_from_export, ingest
from anamnesis.answer import ANSWER_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


def gold(name: str) -> dict:
    return json.loads((FIXTURES / name / "gold.json").read_text(encoding="utf-8"))


def units_of(*names: str) -> dict:
    return {u.message_unit_id: u for n in names
            for b in bundles_from_export(FIXTURES / n / "conversation.json") for u in b.messages}


class IngestTests(unittest.TestCase):
    def test_report_counts_every_export_conversation_and_unit(self):
        names = ["F-00", "F-11", "F-12"]
        with MessageIndex() as ix:
            report = ingest(ix, [FIXTURES / n / "conversation.json" for n in names])
            self.assertEqual(report.exports, 3)
            self.assertEqual(report.conversations, 3)
            units = units_of(*names)
            self.assertEqual(report.units, len(units))
            self.assertEqual(report.indexed, sum(1 for u in units.values() if u.text))
            self.assertEqual(ix.count(), report.indexed)

    def test_ingesting_the_same_export_twice_does_not_duplicate(self):
        path = FIXTURES / "F-00" / "conversation.json"
        with MessageIndex() as ix:
            first = ingest(ix, [path])
            ingest(ix, [path])
            self.assertEqual(ix.count(), first.indexed)


class AskTests(unittest.TestCase):
    def setUp(self):
        self.ix = MessageIndex()
        self.names = ["F-00", "F-11"]
        ingest(self.ix, [FIXTURES / n / "conversation.json" for n in self.names])
        self.units = units_of(*self.names)

    def tearDown(self):
        self.ix.close()

    def assert_quotes_are_from_their_units(self, answer):
        for c in answer.citations:
            unit = self.units[c.message_unit_id]
            self.assertEqual(c.branch_status, unit.branch_status.value)
            self.assertEqual(c.author_role, unit.author_role)
            for piece in c.quote.split("…"):
                self.assertIn(piece.strip(), unit.text)

    def test_answer_cites_a_gold_anchor_on_the_active_path(self):
        g = gold("F-00")
        obj = next(o for o in g["objectives"] if o["id"] == "O-1")
        answer = ask(self.ix, obj["query"])
        self.assertTrue(answer.found)
        self.assertTrue({c.message_unit_id for c in answer.citations} & set(g["anchors"]["O-1"]))
        self.assertTrue(all(c.is_active for c in answer.citations))
        self.assert_quotes_are_from_their_units(answer)
        text = answer.render()
        for c in answer.citations:
            self.assertIn(c.message_unit_id, text)
        self.assertNotIn("not the chosen reply", text)

    def test_alternate_only_text_is_cited_and_labelled_as_not_chosen(self):
        # Alternate-only within F-11 (its gold), so F-11 is indexed alone here.
        g = gold("F-11")
        self.ix.close()
        self.ix = MessageIndex()
        ingest(self.ix, [FIXTURES / "F-11" / "conversation.json"])
        for oid, terms in g["alternate_only_terms"].items():
            for term in terms:
                with self.subTest(objective=oid, term=term):
                    answer = ask(self.ix, term)
                    self.assertTrue(answer.found)
                    self.assertTrue(all(not c.is_active for c in answer.citations))
                    self.assertTrue({c.message_unit_id for c in answer.citations} & set(g["anchors"][oid]))
                    self.assertIn("not the chosen reply", answer.render())
                    self.assert_quotes_are_from_their_units(answer)

    def test_active_hits_suppress_alternates(self):
        # F-11 O-1: the best lexical match is the regenerated-away reply "the zephyrine backup is
        # agreed", which the chosen path contradicts ("Leave it unresolved"). The answer must quote
        # the chosen path only, so it never presents the discarded claim as what was said.
        g = gold("F-11")
        query = g["objectives"][0]["query"]
        alternate = g["anchors"]["O-1"][0]
        self.assertEqual(self.ix.search(query, limit=10)[0].message_unit_id, alternate)
        answer = ask(self.ix, query, limit=10)
        self.assertTrue(answer.found)
        self.assertTrue(all(c.is_active for c in answer.citations))
        self.assertNotIn(alternate, [c.message_unit_id for c in answer.citations])
        self.assertIn("unresolved", answer.render())

    def test_no_source_means_no_answer(self):
        for q in ["xyzzyplugh", "", "((( ***", "quill*"]:
            with self.subTest(q=q):
                answer = ask(self.ix, q)
                self.assertFalse(answer.found)
                self.assertEqual(answer.citations, ())
                self.assertIn("no answer to cite", answer.render())

    def test_limit_is_respected_and_must_be_positive(self):
        self.assertLessEqual(len(ask(self.ix, "routing queue task", limit=2).citations), 2)
        with self.assertRaises(ValueError):
            ask(self.ix, "routing", limit=0)

    def test_to_dict_is_versioned_json_with_plain_quotes(self):
        d = ask(self.ix, "emergent routing").to_dict()
        self.assertEqual(d["schema"], ANSWER_SCHEMA_VERSION)
        self.assertTrue(d["found"])
        json.dumps(d)
        for c in d["citations"]:
            self.assertNotIn("[", c["quote"])
            self.assertEqual(set(c), {"message_unit_id", "conversation_id", "title", "author_role",
                                      "branch_status", "quote"})


class QuoteAndBranchLabelTests(unittest.TestCase):
    """Codex review of 84dd33b: literal brackets survive; unknown branches aren't called discarded."""

    @staticmethod
    def export(tmp: str, texts, current_node=True) -> Path:
        mapping, parent = {"root": {"id": "root", "message": None, "parent": None, "children": []}}, "root"
        for i, (role, text) in enumerate(texts):
            nid = f"m{i}"
            mapping[nid] = {"id": nid, "parent": parent, "children": [],
                            "message": {"id": nid, "author": {"role": role}, "create_time": 1800000000 + i,
                                        "content": {"content_type": "text", "parts": [text]}}}
            mapping[parent]["children"].append(nid)
            parent = nid
        convo = {"id": "conv-q", "title": "q", "mapping": mapping}
        if current_node:
            convo["current_node"] = parent
        path = Path(tmp) / "conversations.json"
        path.write_text(json.dumps([convo]), encoding="utf-8")
        return path

    def test_literal_brackets_in_a_source_are_quoted_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp, MessageIndex() as ix:
            ingest(ix, [self.export(tmp, [("user", "Use values[0] for routing."), ("assistant", "ok")])])
            answer = ask(ix, "routing")
            self.assertEqual(answer.citations[0].quote, "Use values[0] for routing.")
            self.assertEqual(answer.to_dict()["citations"][0]["quote"], "Use values[0] for routing.")
            self.assertIn('"Use values[0] for routing."', answer.render())
            # the index keeps highlighting by default
            self.assertIn("[routing]", ix.search("routing")[0].snippet)

    def test_unknown_branch_is_labelled_undetermined_not_discarded(self):
        with tempfile.TemporaryDirectory() as tmp, MessageIndex() as ix:
            ingest(ix, [self.export(tmp, [("user", "the kestrel plan"), ("assistant", "noted")],
                                    current_node=False)])
            answer = ask(ix, "kestrel")
            self.assertTrue(answer.found)
            self.assertEqual({c.branch_status for c in answer.citations}, {"unknown"})
            text = answer.render()
            self.assertIn("branch undetermined", text)
            self.assertNotIn("not the chosen reply", text)


class FreshProcessTests(unittest.TestCase):
    def test_a_new_process_answers_from_the_index_file_alone(self):
        # Steps 3 and 4 of the cold test across a process boundary: ingest writes an index file,
        # a separate process with no export opens it and gets a cited answer.
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "index.sqlite"
            with MessageIndex(db) as ix:
                ingest(ix, [FIXTURES / "F-00" / "conversation.json"])
            code = ("import json, sys; from anamnesis import MessageIndex, ask\n"
                    "with MessageIndex(sys.argv[1]) as ix: print(json.dumps(ask(ix, sys.argv[2]).to_dict()))")
            out = subprocess.run([sys.executable, "-c", code, str(db), "seepage routing"], cwd=ROOT,
                                 capture_output=True, text=True, check=True)
            d = json.loads(out.stdout)
            self.assertTrue(d["found"])
            self.assertIn("chatgpt-message:conv_synth_f00:msg-f-00-u-23",
                          [c["message_unit_id"] for c in d["citations"]])


if __name__ == "__main__":
    unittest.main()
