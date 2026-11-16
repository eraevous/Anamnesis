"""Tests for the benchmark fixture generator and the committed fixtures.

Reviewer guide: these tests prove the fixtures are well formed, deterministic and fresh, and that
the properties each fixture's README promises (F-00b shares no query terms with its target islands,
F-11's wording exists only off the active path) hold mechanically. They do not run any retrieval;
scoring retrieval against the gold waits on the offline-core port (living-memory note section 7).
"""

from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from benchmark import fixturegen, gold_schema

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

STOPWORDS = {"a", "an", "the", "in", "of", "to", "on", "and", "or", "for", "is", "it"}


def terms(text: str) -> set[str]:
    """Crude surface terms: lowercase words, trailing plural 's' stripped, stopwords dropped."""
    out = set()
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in STOPWORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


def load(fid: str):
    conv = json.loads((FIXTURES / fid / "conversation.json").read_text(encoding="utf-8"))
    gold = json.loads((FIXTURES / fid / "gold.json").read_text(encoding="utf-8"))
    return conv, gold


def unit_texts(conv: list, gold: dict) -> dict[str, str]:
    cid = conv[0]["id"]
    return {f"chatgpt-message:{cid}:{n['message']['id']}": n["message"]["content"]["parts"][0]
            for n in conv[0]["mapping"].values() if n["message"]}


class GeneratorTests(unittest.TestCase):
    def test_committed_fixtures_are_fresh(self):
        self.assertEqual(fixturegen.main(["--check"]), 0, "run: python3 -m benchmark.fixturegen")

    def test_every_outline_has_a_fixture_and_back(self):
        self.assertEqual(fixturegen.outline_ids(), sorted(p.name for p in FIXTURES.iterdir() if p.is_dir()))

    def test_deterministic(self):
        for fid in fixturegen.outline_ids():
            self.assertEqual(fixturegen.render(fid), fixturegen.render(fid), fid)

    def test_message_unit_id_format(self):
        # Must match the rescued core's anamnesis.message-unit.v1 id: chatgpt-message:<conv>:<msg>.
        self.assertEqual(fixturegen.unit_id("conv-x", "msg-1"), "chatgpt-message:conv-x:msg-1")
        self.assertEqual(fixturegen.message_id("F-00", "A-20'"), "msg-f-00-a-20p")

    def test_conversation_id_must_survive_core_normalisation(self):
        # The rescued core keys units by normalize_filename(id)[:48]; "conv-x" would become "conv_x"
        # there and every gold unit id would miss.
        outline = fixturegen.load_outline("F-10")
        for bad in ("conv-synth-rt", "Conv_X", "c" * 49, "a__b"):
            outline["conversation_id"] = bad
            with self.assertRaises(ValueError, msg=bad):
                fixturegen.build(outline)

    def test_extends_rejects_unknown_turn(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            base = json.loads((fixturegen.OUTLINES / "F-10.json").read_text(encoding="utf-8"))
            (d / "B.json").write_text(json.dumps(base), encoding="utf-8")
            (d / "X.json").write_text(json.dumps({"extends": "B", "id": "X", "replace_turns": {"U-99": "x"}}),
                                      encoding="utf-8")
            with self.assertRaises(ValueError):
                fixturegen.load_outline("X", d)

    def test_alternate_must_match_sibling_role(self):
        outline = fixturegen.load_outline("F-10")
        outline["alternates"] = [{"label": "U-4'", "sibling_of": "A-4", "text": "x"}]
        with self.assertRaises(ValueError):
            fixturegen.build(outline)

    def test_unknown_gold_label_rejected(self):
        outline = fixturegen.load_outline("F-10")
        outline["gold"]["anchors"]["O-1"] = ["U-77"]
        with self.assertRaises(ValueError):
            fixturegen.build(outline)


class ExportShapeTests(unittest.TestCase):
    """conversation.json must look like a ChatGPT export so the rescued core ingests it unchanged."""

    def test_tree(self):
        for fid in fixturegen.outline_ids():
            conv, gold = load(fid)
            self.assertEqual(len(conv), 1, fid)
            c = conv[0]
            m = c["mapping"]
            roots = [k for k, n in m.items() if n["parent"] is None]
            self.assertEqual(len(roots), 1, fid)
            self.assertIsNone(m[roots[0]]["message"], fid)
            for k, n in m.items():
                self.assertEqual(n["id"], k, fid)
                for ch in n["children"]:
                    self.assertEqual(m[ch]["parent"], k, fid)
                if n["parent"] is not None:
                    self.assertIn(k, m[n["parent"]]["children"], fid)
            self.assertEqual(m[c["current_node"]]["children"], [], f"{fid}: current_node must be a leaf")

    def test_active_path_is_current_node_ancestry(self):
        for fid in fixturegen.outline_ids():
            conv, gold = load(fid)
            c = conv[0]
            path, node = [], c["current_node"]
            while c["mapping"][node]["message"] is not None:
                path.append(f"chatgpt-message:{c['id']}:{node}")
                node = c["mapping"][node]["parent"]
            self.assertEqual(list(reversed(path)), gold["active_path"], fid)

    def test_timestamps_increase_along_active_path(self):
        for fid in fixturegen.outline_ids():
            conv, gold = load(fid)
            m = conv[0]["mapping"]
            ts = [m[u.rsplit(":", 1)[1]]["message"]["create_time"] for u in gold["active_path"]]
            self.assertEqual(ts, sorted(ts), fid)
            self.assertEqual(len(ts), len(set(ts)), fid)

    def test_assistant_messages_record_model(self):
        # R-MEM-5: every Cell records platform and model when the source has them.
        for fid in fixturegen.outline_ids():
            conv, _ = load(fid)
            for n in conv[0]["mapping"].values():
                if n["message"] and n["message"]["author"]["role"] == "assistant":
                    self.assertTrue(n["message"]["metadata"].get("model_slug"), fid)


class GoldTests(unittest.TestCase):
    def test_committed_gold_validates(self):
        for fid in fixturegen.outline_ids():
            conv, gold = load(fid)
            roles = {f"chatgpt-message:{conv[0]['id']}:{n['message']['id']}": n["message"]["author"]["role"]
                     for n in conv[0]["mapping"].values() if n["message"]}
            gold_schema.validate(gold, texts=unit_texts(conv, gold), roles=roles)

    def _gold(self):
        conv, gold = load("F-00")
        return copy.deepcopy(gold), unit_texts(conv, gold)

    def test_overlapping_islands_rejected(self):
        gold, texts = self._gold()
        gold["islands"][1]["first"] = gold["islands"][0]["last"]
        with self.assertRaises(gold_schema.GoldError):
            gold_schema.validate(gold, texts=texts)

    def test_non_verbatim_quote_rejected(self):
        gold, texts = self._gold()
        gold["evidence"][0]["quote"] += " (paraphrased)"
        with self.assertRaises(gold_schema.GoldError):
            gold_schema.validate(gold, texts=texts)

    def test_unaccepted_assistant_text_cannot_be_eligible(self):
        gold, texts = self._gold()
        rejected = next(e for e in gold["evidence"] if e.get("acceptance_status") == "rejected")
        rejected["canonical_eligibility"] = "eligible"
        with self.assertRaises(gold_schema.GoldError):
            gold_schema.validate(gold, texts=texts)

    def test_core_claim_needs_every_support_eligible(self):
        # Copilot review on #10: one eligible span must not carry an ineligible one.
        gold, texts = self._gold()
        rejected = next(e["id"] for e in gold["evidence"] if e.get("acceptance_status") == "rejected")
        core = next(c for c in gold["claims"] if c["placement"] == "core")
        core["support"].append(rejected)
        with self.assertRaises(gold_schema.GoldError):
            gold_schema.validate(gold, texts=texts)

    def test_claim_support_must_name_evidence_not_units(self):
        gold, texts = self._gold()
        gold["claims"][0]["support"] = [gold["evidence"][0]["unit"]]
        with self.assertRaises(gold_schema.GoldError):
            gold_schema.validate(gold, texts=texts)

    def test_acceptance_unit_must_be_a_user_unit(self):
        # Copilot review on #10: an assistant can't accept its own proposal.
        conv, gold = load("F-00")
        gold = copy.deepcopy(gold)
        roles = {f"chatgpt-message:{conv[0]['id']}:{n['message']['id']}": n["message"]["author"]["role"]
                 for n in conv[0]["mapping"].values() if n["message"]}
        accepted = next(e for e in gold["evidence"] if e.get("acceptance_status") == "accepted")
        accepted["acceptance_unit"] = accepted["unit"]
        with self.assertRaises(gold_schema.GoldError):
            gold_schema.validate(gold, texts=unit_texts(conv, gold), roles=roles)

    def test_never_eligible_classes_rejected_even_when_accepted(self):
        # Copilot review on #10: design section 4 rules 3, 4 and 6.
        for cls in sorted(gold_schema.NEVER_ELIGIBLE):
            gold, texts = self._gold()
            accepted = next(e for e in gold["evidence"] if e.get("acceptance_status") == "accepted")
            accepted["artifact_class"] = cls
            with self.assertRaises(gold_schema.GoldError, msg=cls):
                gold_schema.validate(gold, texts=texts)

    def test_alternate_on_active_path_rejected(self):
        gold, texts = self._gold()
        gold["alternate_units"][gold["active_path"][3]] = "alternate"
        with self.assertRaises(gold_schema.GoldError):
            gold_schema.validate(gold, texts=texts)


class FixturePropertyTests(unittest.TestCase):
    """The promises each README makes, checked mechanically."""

    def _island_units(self, gold, iid):
        isl = next(i for i in gold["islands"] if i["id"] == iid)
        a, b = gold["active_path"].index(isl["first"]), gold["active_path"].index(isl["last"])
        return gold["active_path"][a:b + 1]

    def test_f00_target_islands_share_query_terms(self):
        conv, gold = load("F-00")
        texts = unit_texts(conv, gold)
        q = terms(next(o["query"] for o in gold["objectives"] if o["id"] == "O-1"))
        for iid in ("I-1", "I-2"):
            self.assertTrue(any(q & terms(texts[u]) for u in self._island_units(gold, iid)), iid)

    def test_f00b_target_islands_share_no_query_terms(self):
        conv, gold = load("F-00b")
        texts = unit_texts(conv, gold)
        nst = gold["no_shared_terms"]
        q = terms(next(o["query"] for o in gold["objectives"] if o["id"] == nst["objective"]))
        for iid in nst["islands"]:
            for u in self._island_units(gold, iid):
                self.assertEqual(q & terms(texts[u]), set(), f"{iid} {u}")

    def test_f00b_only_distractors_match_the_query_lexically(self):
        conv, gold = load("F-00b")
        texts = unit_texts(conv, gold)
        q = terms(next(o["query"] for o in gold["objectives"] if o["id"] == "O-1"))
        hits = {u for u in gold["active_path"] if q & terms(texts[u])}
        distractors = {u for iid in ("I-D1", "I-D2") for u in self._island_units(gold, iid)}
        self.assertEqual(hits, distractors)

    def test_f00_and_f00b_share_the_same_shape(self):
        _, g0 = load("F-00")
        _, g1 = load("F-00b")
        self.assertEqual(list(g0["labels"]), list(g1["labels"]))
        self.assertEqual([(i["id"], i["placement"]) for i in g0["islands"]],
                         [(i["id"], i["placement"]) for i in g1["islands"]])

    def test_f11_alternate_only_terms(self):
        conv, gold = load("F-11")
        texts = unit_texts(conv, gold)
        for oid, words in gold["alternate_only_terms"].items():
            for w in words:
                on_active = [u for u in gold["active_path"] if w in terms(texts[u])]
                self.assertEqual(on_active, [], f"{oid}: '{w}' occurs on the active path")
                self.assertTrue(any(w in terms(texts[u]) for u in gold["anchors"][oid]), oid)

    def test_f12_query_spans_two_speakers(self):
        conv, gold = load("F-12")
        texts = unit_texts(conv, gold)
        roles = {f"chatgpt-message:{conv[0]['id']}:{n['message']['id']}": n["message"]["author"]["role"]
                 for n in conv[0]["mapping"].values() if n["message"]}
        anchors = gold["anchors"]["O-1"]
        self.assertEqual({roles[u] for u in anchors}, {"user", "assistant"})
        for u in anchors:
            self.assertIn("zephyrine backup", texts[u])

    def test_synthetic_marker(self):
        for fid in fixturegen.outline_ids():
            readme = (FIXTURES / fid / "README.md").read_text(encoding="utf-8")
            self.assertIn("Synthetic text only", readme, fid)


if __name__ == "__main__":
    unittest.main()
