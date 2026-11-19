"""Tests for ingest and ask on an instance (anamnesis/workflow.py, `anamnesis ingest|ask`). Synthetic only."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from anamnesis import InstanceError, init_instance, load_manifest, validate_manifest
from anamnesis.cli import main
from anamnesis.workflow import ask_instance, ingest_into_instance

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
F11 = FIXTURES / "F-11" / "conversation.json"
F00 = FIXTURES / "F-00" / "conversation.json"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.inst = self.tmp / "memory"
        init_instance(self.inst)

    def copy_as(self, src: Path, folder: str) -> Path:
        dest = self.tmp / folder / "conversations.json"  # every real export uses this name
        dest.parent.mkdir()
        shutil.copy(src, dest)
        return dest

    def test_same_named_exports_both_import_and_reingest_adds_nothing(self):
        a, b = self.copy_as(F11, "a"), self.copy_as(F00, "b")
        first = ingest_into_instance(self.inst, [a, b])
        self.assertEqual(len(first.imported), 2)
        self.assertTrue(first.built)
        again = ingest_into_instance(self.inst, [a, a])
        self.assertEqual(again.imported, [])
        self.assertFalse(again.built)
        self.assertEqual(len(load_manifest(self.inst)["sources"]), 2)
        self.assertEqual(len(list((self.inst / "runs").glob("*-ingest-*.json"))), 2)

    def test_bad_export_writes_nothing(self):
        bad = self.tmp / "bad.json"
        bad.write_text('{"not": "a list"}', encoding="utf-8")
        before = (self.inst / "anamnesis.instance.json").read_bytes()
        with self.assertRaises(InstanceError):
            ingest_into_instance(self.inst, [F11, bad])
        self.assertEqual((self.inst / "anamnesis.instance.json").read_bytes(), before)
        self.assertFalse((self.inst / "sources" / "imported").exists())

    def test_changed_source_is_rebuilt_before_answering(self):
        ingest_into_instance(self.inst, [F11])
        answer, notes = ask_instance(self.inst, "zephyrine backup")
        self.assertTrue(answer.found)
        self.assertEqual(notes, [])
        src = self.inst / load_manifest(self.inst)["sources"][0]["path"]
        conv = json.loads(src.read_text(encoding="utf-8"))
        for node in conv[0]["mapping"].values():
            msg = node.get("message")
            if msg and msg.get("content", {}).get("parts"):
                msg["content"]["parts"] = ["quasar lantern"]
        src.write_text(json.dumps(conv), encoding="utf-8")
        answer, notes = ask_instance(self.inst, "quasar lantern")
        self.assertTrue(answer.found)
        self.assertIn("index rebuilt from the instance's sources", notes)
        self.assertFalse(ask_instance(self.inst, "zephyrine backup")[0].found)

    def test_in_repo_backend_writes_its_tracked_file(self):
        mpath = self.inst / "anamnesis.instance.json"
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["index"] = {"backend": "in-repo"}
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        ingest_into_instance(self.inst, [F11])
        self.assertTrue((self.inst / "index" / "messages.sqlite").is_file())
        self.assertFalse(any((self.inst / ".anamnesis").glob("index/*.sqlite")))

    def test_index_config_is_validated(self):
        manifest = load_manifest(self.inst)
        for bad in ({"backend": "s3"}, {"backend": "in-repo", "path": "../x.sqlite"}, "cache"):
            manifest["index"] = bad
            self.assertTrue(any("index." in p for p in validate_manifest(manifest)), bad)

    def test_symlinked_import_folder_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.inst / "sources" / "imported").symlink_to(outside)
        with self.assertRaises(InstanceError):
            ingest_into_instance(self.inst, [F11])
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_cache_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        shutil.rmtree(self.inst / ".anamnesis")
        (self.inst / ".anamnesis").symlink_to(outside)
        with self.assertRaises(InstanceError):
            ingest_into_instance(self.inst, [F11])
        self.assertEqual(list(outside.iterdir()), [])

    def test_ask_without_sources_and_missing_source(self):
        with self.assertRaisesRegex(InstanceError, "no sources"):
            ask_instance(self.inst, "anything")
        ingest_into_instance(self.inst, [F11])
        (self.inst / load_manifest(self.inst)["sources"][0]["path"]).unlink()
        with self.assertRaisesRegex(InstanceError, "missing"):
            ask_instance(self.inst, "anything")

    def test_unenforced_privacy_rules_are_reported(self):
        mpath = self.inst / "anamnesis.instance.json"
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["privacy"]["opaque"] = ["sources/x"]
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        res = ingest_into_instance(self.inst, [F11])
        self.assertIn("privacy.opaque lists 1 item(s); recorded, not enforced yet", res.notes)

    def test_cli_exit_codes(self):
        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(list(argv))
            return code, out.getvalue(), err.getvalue()
        self.assertEqual(run("ingest", str(self.inst), str(F11))[0], 0)
        code, out, _ = run("ask", str(self.inst), "zephyrine", "backup")
        self.assertEqual(code, 0)
        self.assertIn("source chatgpt-message:", out)
        self.assertEqual(run("ask", str(self.inst), "regatta")[0], 3)
        code, _, err = run("ingest", str(self.inst), str(self.tmp))  # a folder, not an export file
        self.assertEqual(code, 1)
        self.assertIn("export file", err)


if __name__ == "__main__":
    unittest.main()
