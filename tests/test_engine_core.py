"""Tests for the engine's offline core (anamnesis/), ported from the rescue (HO-ANA-20261118-6H0J item 1).

Reviewer guide: everything here runs on the committed synthetic fixtures or on inline synthetic
conversations; nothing reads a real export. The gold-driven tests read each fixture's `checks`,
`objectives`, `anchors` and `expected_failures` and score the FTS5 index against them, so a new
fixture with those fields is picked up without changing this file.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import anamnesis
from anamnesis import (
    BranchStatus,
    HashingEmbedder,
    MessageIndex,
    bundles_from_export,
    cells_for_bundle,
    conversation_key,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = sorted(p for p in (ROOT / "fixtures").iterdir() if (p / "gold.json").exists())
TOP_K = 5


def gold(fixture: Path) -> dict:
    return json.loads((fixture / "gold.json").read_text(encoding="utf-8"))


def conv(cid="conv-A", texts=(("user", "hello quillfern"), ("assistant", "hi there"))):
    """A small synthetic linear conversation."""
    mapping, parent = {"root": {"id": "root", "message": None, "parent": None, "children": []}}, "root"
    for i, (role, text) in enumerate(texts):
        nid = f"m{i}"
        mapping[nid] = {"id": nid, "parent": parent, "children": [],
                        "message": {"id": nid, "author": {"role": role}, "create_time": 1800000000 + i,
                                    "content": {"content_type": "text", "parts": [text]}}}
        mapping[parent]["children"].append(nid)
        parent = nid
    out = {"id": cid, "title": "t", "current_node": parent, "mapping": mapping}
    if cid is None:
        del out["id"]
    return out


class ImportTests(unittest.TestCase):
    def test_engine_imports_stdlib_only(self):
        # Reconciliation §4 item 4: the offline path must import without tiktoken/openai.
        code = ("import sys, anamnesis; bad = sorted(m for m in ('tiktoken', 'openai', 'numpy', 'torch')"
                " if m in sys.modules); print(bad)")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.strip(), "[]")

    def test_engine_source_imports_only_stdlib(self):
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        for path in (ROOT / "anamnesis").glob("*.py"):
            for mod in re.findall(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", path.read_text(), re.M):
                self.assertIn(mod, allowed, f"{path.name} imports {mod}")


class UnitTests(unittest.TestCase):
    def test_gold_active_path_and_alternates(self):
        for fx in FIXTURES:
            with self.subTest(fixture=fx.name):
                g = gold(fx)
                units = {u.message_unit_id: u for b in bundles_from_export(fx / "conversation.json") for u in b.messages}
                for uid in g["active_path"]:
                    self.assertEqual(units[uid].branch_status, BranchStatus.ACTIVE, uid)
                for uid, status in g.get("alternate_units", {}).items():
                    self.assertEqual(units[uid].branch_status.value, status, uid)
                active = sorted((u for u in units.values() if u.branch_status is BranchStatus.ACTIVE),
                                key=lambda u: u.active_branch_index)
                self.assertEqual([u.message_unit_id for u in active if u.message_unit_id in set(g["active_path"])],
                                 g["active_path"])

    def test_unit_ids_use_the_normalised_key(self):
        # HO-ANA-20261116-CFNM: ids are keyed by normalize_key(id)[:48], not the raw export id.
        (b,) = [bundles_from_export_from(conv("Conv-Mixed:Case 1"))[0]]
        self.assertEqual(b.conversation_id, "conv_mixed_case_1")
        self.assertTrue(b.messages[0].message_unit_id.startswith("chatgpt-message:conv_mixed_case_1:"))
        self.assertEqual(conversation_key({"id": "x" * 60}, 0), "x" * 48)

    def test_ids_stable_across_exports_unless_id_missing(self):
        a, b = conv("conv-a"), conv("conv-b")
        first = {u.message_unit_id for x in bundles_from_export_from(a, b) for u in x.messages}
        second = {u.message_unit_id for x in bundles_from_export_from(b, a) for u in x.messages}
        self.assertEqual(first, second)
        # Without an id the key falls back to position: documented as not stable.
        self.assertEqual(conversation_key(conv(None), 3), "0003_t")

    def test_loads_zip_nested_and_sharded_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            z = tmp / "export.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("some-export/conversations.json", json.dumps([conv("z1")]))
            self.assertEqual([b.conversation_id for b in bundles_from_export(z)], ["z1"])
            d = tmp / "sharded"
            d.mkdir()
            (d / "conversations-001.json").write_text(json.dumps([conv("s2")]))
            (d / "conversations-000.json").write_text(json.dumps([conv("s1")]))
            self.assertEqual([b.conversation_id for b in bundles_from_export(d)], ["s1", "s2"])
            empty = tmp / "empty.zip"
            with zipfile.ZipFile(empty, "w") as zf:
                zf.writestr("readme.txt", "x")
            with self.assertRaises(FileNotFoundError):
                bundles_from_export(empty)


def bundles_from_export_from(*conversations):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "conversations.json"
        path.write_text(json.dumps(list(conversations)))
        return bundles_from_export(path)


class IndexGoldTests(unittest.TestCase):
    """Scores the FTS5 index against each fixture's gold objectives."""

    def setUp(self):
        self.ix = MessageIndex()

    def tearDown(self):
        self.ix.close()

    def _index(self, fx):
        bundles = bundles_from_export(fx / "conversation.json")
        self.ix.add(bundles)
        return {u.message_unit_id: u for b in bundles for u in b.messages}

    def test_objectives_find_their_anchors(self):
        for fx in FIXTURES:
            g = gold(fx)
            anchors = g.get("anchors")
            if not isinstance(anchors, dict):
                continue
            expected_miss = {e["objective"] for e in g.get("expected_failures", []) if e.get("method") == "lexical_only"}
            self.ix.close()
            self.ix = MessageIndex()
            units = self._index(fx)
            for obj in g.get("objectives", []):
                if obj["id"] not in anchors:
                    continue
                with self.subTest(fixture=fx.name, objective=obj["id"]):
                    hits = self.ix.search(obj["query"], limit=TOP_K)
                    found = {h.message_unit_id for h in hits}
                    for h in hits:  # every hit resolves to a unit and carries its true branch label
                        self.assertEqual(h.branch_status, units[h.message_unit_id].branch_status.value)
                    if obj["id"] in expected_miss:
                        self.assertFalse(set(anchors[obj["id"]]) & found,
                                         "gold expects lexical search to miss this; it found an anchor")
                    else:
                        self.assertTrue(found & set(anchors[obj["id"]]), f"no anchor in top {TOP_K}: {found}")

    def test_alternate_hits_are_never_active_and_active_only_excludes_them(self):
        fx = next(f for f in FIXTURES if f.name == "F-11")
        g = gold(fx)
        self._index(fx)
        for obj in g["objectives"]:
            hits = self.ix.search(obj["query"], limit=TOP_K)
            for uid in g["anchors"][obj["id"]]:
                hit = next(h for h in hits if h.message_unit_id == uid)
                self.assertEqual(hit.branch_status, "alternate")
            for term in g["alternate_only_terms"][obj["id"]]:
                self.assertEqual(self.ix.search(term, active_only=True), [])
                self.assertTrue(self.ix.search(term))

    def test_snippet_never_spans_two_units(self):
        fx = next(f for f in FIXTURES if f.name == "F-12")
        units = self._index(fx)
        for h in self.ix.search(gold(fx)["objectives"][0]["query"], limit=TOP_K):
            plain = h.snippet.replace("[", "").replace("]", "")
            for piece in plain.split("…"):
                self.assertIn(piece.strip(), units[h.message_unit_id].text)

    def test_query_text_cannot_inject_fts_syntax(self):
        self._index(next(f for f in FIXTURES if f.name == "F-10"))
        for q in ['quillfern" OR "x', "NEAR(quillfern archive)", "quill*", "text:quillfern", "", "((("]:
            self.ix.search(q)  # must not raise
        self.assertEqual(self.ix.search("quill*"), [])  # '*' is not a prefix operator here
        self.assertTrue(self.ix.search("quillfern*"))

    def test_readding_replaces_and_reopen_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.sqlite"
            fx = next(f for f in FIXTURES if f.name == "F-10")
            with MessageIndex(path) as ix:
                n = ix.add(bundles_from_export(fx / "conversation.json"))
                ix.add(bundles_from_export(fx / "conversation.json"))
                self.assertEqual(ix.count(), n)
            with MessageIndex(path) as ix:
                self.assertEqual(ix.count(), n)
                self.assertTrue(ix.search("quillfern"))


class ReplaceTests(unittest.TestCase):
    def test_readding_a_unit_with_no_text_removes_its_old_row(self):
        # Codex finding on 0a273ff: an emptied unit stayed searchable with its old text.
        before = conv("conv-r", (("user", "where is the answer"), ("assistant", "answer quillfern")))
        after = json.loads(json.dumps(before))
        after["mapping"]["m1"]["message"]["content"]["parts"] = [""]
        with MessageIndex() as ix:
            ix.add(bundles_from_export_from(before))
            ix.add(bundles_from_export_from(after))
            self.assertEqual(ix.search("quillfern"), [])
            self.assertEqual(ix.count(), 1)


class CellTests(unittest.TestCase):
    def test_cells_walk_through_nodes_with_no_message(self):
        # Codex finding on 0a273ff: user -> (message: null) -> assistant produced no Cell.
        c = conv("conv-n", (("user", "ask"), ("assistant", "reply")))
        c["mapping"]["gap"] = {"id": "gap", "message": None, "parent": "m0", "children": ["m1"]}
        c["mapping"]["m0"]["children"] = ["gap"]
        c["mapping"]["m1"]["parent"] = "gap"
        (b,) = bundles_from_export_from(c)
        cells = cells_for_bundle(b)
        self.assertEqual([(x.first.split(":")[-1], x.second.split(":")[-1], x.direction) for x in cells],
                         [("m0", "m1", "human->model")])
        self.assertEqual(type(b).from_dict(b.to_dict()).skipped_node_parents, b.skipped_node_parents)

    def test_rolling_cells_overlap_and_cover_alternates(self):
        fx = next(f for f in FIXTURES if f.name == "F-11")
        (bundle,) = bundles_from_export(fx / "conversation.json")
        cells = cells_for_bundle(bundle)
        units = {u.message_unit_id: u for u in bundle.messages}
        g = gold(fx)
        # sliding by one along the active path: consecutive texted turns (system skipped)
        turns = [uid for uid in g["active_path"] if units[uid].author_role not in ("system", "developer")]
        active_pairs = {(c.first, c.second) for c in cells if c.branch_status == "active"}
        self.assertEqual(active_pairs, set(zip(turns, turns[1:])))
        for uid in g["alternate_units"]:  # every alternate sits in a Cell, labelled alternate
            touching = [c for c in cells if uid in (c.first, c.second)]
            self.assertTrue(touching, uid)
            self.assertTrue(all(c.branch_status == "alternate" for c in touching))
        for c in cells:  # one branch path: second's parent is first (no message-less nodes here)
            self.assertEqual(units[c.second].parent_node_id, units[c.first].node_id)
            self.assertIn(c.direction, {"human->model", "model->human"})
            self.assertEqual(c.platform, "chatgpt")
            self.assertEqual(c.model, "synthetic-model")

    def test_direction_and_model_absent(self):
        (b,) = bundles_from_export_from(conv(texts=(("user", "a"), ("assistant", "b"), ("tool", "c"))))
        cells = cells_for_bundle(b)
        self.assertEqual([c.direction for c in cells], ["human->model", "other"])
        self.assertIsNone(cells[0].model)  # R-MEM-5: recorded only when the source has it
        self.assertNotIn("model", cells[0].to_dict())


class EmbedderTests(unittest.TestCase):
    def test_hashing_embedder_is_an_embedder_and_deterministic(self):
        e = HashingEmbedder(dim=32)
        self.assertIsInstance(e, anamnesis.Embedder)
        a, b = e.embed(["zephyrine backup", "zephyrine backup"])
        self.assertEqual(a, b)
        self.assertEqual(len(a), 32)
        self.assertIn("32", e.model_id)


if __name__ == "__main__":
    unittest.main()
