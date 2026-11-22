"""Message-unit and bundle serialisation is pinned: key order, values and round trip.

Reviewer guide: `MessageUnit.to_dict`/`from_dict` and the bundle's pair are written from field-name
tuples in anamnesis/units.py. These tests pin what they write, byte for byte, on a fully populated
synthetic conversation and on every committed fixture, so a refactor of that code can't change a
stored unit. The expected digests were computed on main before the tuples replaced the hand-listed
fields (the tests pass on both). Synthetic data only.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from anamnesis.units import (
    ConversationMessageBundle,
    MessageUnit,
    build_conversation_message_bundle,
    bundles_from_export,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = sorted(p for p in (ROOT / "fixtures").iterdir() if (p / "conversation.json").exists())


def full_conversation() -> dict:
    """Every optional field a unit or bundle can carry is set somewhere."""
    def node(nid, parent, children, role, text, **meta):
        return {
            "id": nid, "parent": parent, "children": children,
            "message": {
                "id": f"m-{nid}", "author": {"role": role, "name": f"{role}-name"},
                "content": {"content_type": "text", "parts": [text]},
                "create_time": 1700000000.5, "update_time": 1700000001,
                "status": "finished_successfully", "end_turn": True,
                "metadata": {"model_slug": "synth-model", "default_model_slug": "synth-default",
                             "gizmo_id": "g-p-synthproject", "message_type": "next",
                             "message_source": "synth", "request_id": "req-1",
                             "turn_exchange_id": "tx-1", **meta},
            },
        }
    mapping = {
        "root": {"id": "root", "parent": None, "children": ["u1"]},
        "u1": node("u1", "root", ["a1", "a2"], "user", "quillfern question"),
        "a1": node("a1", "u1", [], "assistant", "alternate reply"),
        "a2": node("a2", "u1", [], "assistant", "chosen reply"),
    }
    return {
        "id": "conv-Full-1", "title": "Full synthetic", "current_node": "a2",
        "create_time": 1700000000, "update_time": 1700000002,
        "default_model_slug": "synth-conv-default", "project_name": "Synth project",
        "memory_scope": "project_enabled", "conversation_template_id": "g-p-synthproject",
        "mapping": mapping,
    }


UNIT_KEYS = [
    "schema_version", "message_unit_id", "source_type", "conversation_id", "message_id", "node_id",
    "author_role", "unit_type", "branch_status", "source_index", "child_node_ids",
    "external_conversation_id", "parent_node_id", "active_branch_index", "transcript_index",
    "transcript_start", "transcript_end", "author_name", "text", "content_hash", "content_type",
    "create_time", "update_time", "model_slug", "model_slug_source", "default_model_slug",
    "default_model_slug_source", "project_id", "project_id_source", "project_name", "message_type",
    "message_source", "request_id", "turn_exchange_id", "status", "end_turn",
]
BUNDLE_KEYS = [
    "schema_version", "conversation_id", "title", "messages", "external_conversation_id",
    "current_node_id", "create_time", "update_time", "default_model_slug", "project_id",
    "project_id_source", "project_name", "memory_scope", "conversation_template_id",
    "skipped_node_parents",
]


def digest(payload) -> str:
    # No sort_keys: key order is part of what is pinned.
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


class SerialisationPinned(unittest.TestCase):
    def setUp(self):
        self.bundle = build_conversation_message_bundle(full_conversation(), "conv_full_1")

    def test_full_unit_key_order(self):
        active = next(u for u in self.bundle.messages if u.node_id == "a2")
        self.assertEqual(list(active.to_dict()), UNIT_KEYS)

    def test_full_bundle_key_order(self):
        self.assertEqual(list(self.bundle.to_dict()), BUNDLE_KEYS)

    def test_full_bundle_bytes(self):
        self.assertEqual(digest(self.bundle.to_dict()), FULL_DIGEST)

    def test_full_round_trip(self):
        again = ConversationMessageBundle.from_dict(self.bundle.to_dict())
        self.assertEqual(again, self.bundle)
        for unit in self.bundle.messages:
            self.assertEqual(MessageUnit.from_dict(unit.to_dict()), unit)

    def test_tuples_cover_every_field(self):
        # A field added to a dataclass but not to its tuple would silently drop out of to_dict.
        from dataclasses import fields
        from anamnesis import units
        unit_required = {"message_unit_id", "source_type", "conversation_id", "message_id", "node_id",
                         "author_role", "unit_type", "branch_status", "source_index", "child_node_ids"}
        self.assertEqual({f.name for f in fields(MessageUnit)} - unit_required, set(units._UNIT_OPTIONAL))
        bundle_required = {"conversation_id", "title", "messages", "skipped_node_parents"}
        self.assertEqual({f.name for f in fields(ConversationMessageBundle)} - bundle_required,
                         set(units._BUNDLE_OPTIONAL))

    def test_from_dict_coerces_int_fields(self):
        payload = self.bundle.messages[0].to_dict()
        payload["transcript_start"] = "7"
        self.assertEqual(MessageUnit.from_dict(payload).transcript_start, 7)

    def test_fixtures_round_trip_and_bytes(self):
        self.assertTrue(FIXTURES)
        for fixture in FIXTURES:
            with self.subTest(fixture=fixture.name):
                bundles = bundles_from_export(fixture / "conversation.json")
                for b in bundles:
                    self.assertEqual(ConversationMessageBundle.from_dict(b.to_dict()), b)
                self.assertEqual(digest([b.to_dict() for b in bundles]), FIXTURE_DIGESTS[fixture.name])


FULL_DIGEST = "a74916af87e10c48ccd61bcd211305f23fb04f3a5f6262f7159e2efcf48f4264"
FIXTURE_DIGESTS = {
    "F-00": "3177dd7e1a4b52682e3bfec9b917e4e986dbd5e15bcf458e91bb1e9088c5cc13",
    "F-00b": "53b0836bf0198a0e1fe9c95a75e1a950b1cef28acebaf1c9e701f162c1834910",
    "F-10": "f345893cdb41f8cd3862cf67781a25a8dd7fa73de3362f13868a2534a2540f94",
    "F-11": "35f210b3d7783d9085e585070d442f04154180fe06572a544c1ced250fe2c16c",
    "F-12": "59ad326f8caf0a19347ed766994c7d7c53327335127d77e74b6b259e8f630b67",
}

if __name__ == "__main__":
    unittest.main()
