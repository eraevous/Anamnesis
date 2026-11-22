"""Tests for ingest and ask on an instance (anamnesis/workflow.py, `anamnesis ingest|ask`). Synthetic only."""
from __future__ import annotations

import io
import json
import shutil
import struct
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from anamnesis import InstanceError, init_instance, load_manifest, validate_manifest
from anamnesis.cli import main
from anamnesis.workflow import ask_instance, ingest_into_instance

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
F11 = FIXTURES / "F-11" / "conversation.json"
F00 = FIXTURES / "F-00" / "conversation.json"


class WorkflowTests(unittest.TestCase):
    """Requirements: R-ENG-6 (implementer mapping)."""

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

    def test_index_path_cannot_name_instance_data(self):
        """Codex review of d57aec7 (P1): index.path = the manifest let a build replace it with SQLite."""
        mpath = self.inst / "anamnesis.instance.json"
        ingest_into_instance(self.inst, [F11])
        source = load_manifest(self.inst)["sources"][0]["path"]
        for bad in ("anamnesis.instance.json", source, "claims/ledger.jsonl", "library/x.sqlite",
                    ".anamnesis/index/x.sqlite"):
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
            manifest["index"] = {"backend": "in-repo", "path": bad}
            self.assertTrue(validate_manifest(manifest), bad)
            before = mpath.read_bytes()
            mpath.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(InstanceError):
                ask_instance(self.inst, "zephyrine")
            self.assertEqual(json.loads(mpath.read_text(encoding="utf-8"))["index"]["path"], bad)
            mpath.write_bytes(before)
        self.assertTrue((self.inst / source).read_bytes().startswith(b"["))

    def test_in_repo_index_never_replaces_a_foreign_database(self):
        """Codex security review of 8579912 (P2): index.path naming another SQLite file clobbered it."""
        import sqlite3
        foreign = self.inst / "important.sqlite"
        con = sqlite3.connect(foreign)
        con.execute("CREATE TABLE secrets (v TEXT)")
        con.execute("INSERT INTO secrets VALUES ('keep me')")
        con.commit()
        con.close()
        before = foreign.read_bytes()
        mpath = self.inst / "anamnesis.instance.json"
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        manifest["index"] = {"backend": "in-repo", "path": "important.sqlite"}
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(InstanceError, "not an anamnesis index"):
            ingest_into_instance(self.inst, [F11])
        self.assertEqual(foreign.read_bytes(), before)
        foreign.write_bytes(b"not sqlite at all")
        with self.assertRaises(InstanceError):
            ingest_into_instance(self.inst, [F11])
        self.assertEqual(foreign.read_bytes(), b"not sqlite at all")
        foreign.unlink()  # absent: built; present and ours: rebuilt in place
        ingest_into_instance(self.inst, [F11])
        ingest_into_instance(self.inst, [F00])
        self.assertTrue(ask_instance(self.inst, "zephyrine")[0].found)

    def test_symlinked_cache_file_is_refused(self):
        """Codex review of d57aec7 (P2): a linked <digest>.sqlite was opened and written outside."""
        ingest_into_instance(self.inst, [F11])
        cached = next((self.inst / ".anamnesis" / "index").glob("*.sqlite"))
        outside = self.tmp / "outside.sqlite"
        outside.write_bytes(b"")
        cached.unlink()
        cached.symlink_to(outside)
        with self.assertRaises(InstanceError):
            ask_instance(self.inst, "zephyrine")
        self.assertEqual(outside.read_bytes(), b"")

    def test_malformed_index_and_source_entries_are_problems_not_crashes(self):
        """Copilot review of 493be7d: a non-string backend or source path raised TypeError."""
        manifest = load_manifest(self.inst)
        manifest["index"] = {"backend": ["cache"]}
        self.assertTrue(any("index.backend" in p for p in validate_manifest(manifest)))
        manifest["index"] = {"backend": "in-repo"}
        manifest["sources"] = [{"kind": "chatgpt-export", "path": []}]
        self.assertTrue(any("sources[0].path" in p for p in validate_manifest(manifest)))

    def test_corrupt_listed_source_is_an_instance_error(self):
        """Copilot review of 493be7d: a listed source broken after import escaped as a traceback."""
        ingest_into_instance(self.inst, [F11])
        src = self.inst / load_manifest(self.inst)["sources"][0]["path"]
        src.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(InstanceError, "is not a readable ChatGPT export"):
            ask_instance(self.inst, "zephyrine")

    def _zip_export(self, name: str) -> Path:
        path = self.tmp / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("conversations.json", json.dumps(json.loads(F11.read_text(encoding="utf-8")) * 200))
        return path

    def assert_refused_cleanly(self, export: Path) -> None:
        before = (self.inst / "anamnesis.instance.json").read_bytes()
        with self.assertRaisesRegex(InstanceError, "not a readable ChatGPT export"):
            ingest_into_instance(self.inst, [export])
        self.assertEqual((self.inst / "anamnesis.instance.json").read_bytes(), before)
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["ingest", str(self.inst), str(export)]), 1)
        self.assertIn("not a readable ChatGPT export", err.getvalue())

    def test_deeply_nested_export_is_refused_without_a_traceback(self):
        """Idle security cycle: JSON nested past the parser's stack raised RecursionError."""
        deep = self.tmp / "deep.json"
        deep.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
        self.assert_refused_cleanly(deep)

    def test_damaged_zip_member_is_refused_without_a_traceback(self):
        """Idle security cycle: a corrupted deflate stream raised zlib.error."""
        good = self._zip_export("good.zip")
        raw = bytearray(good.read_bytes())
        start = raw.find(b"conversations.json") + len("conversations.json")
        for k in range(start + 20, start + 60):
            raw[k] ^= 0xFF
        bad = self.tmp / "damaged.zip"
        bad.write_bytes(bytes(raw))
        self.assert_refused_cleanly(bad)

    def test_unsupported_zip_compression_is_refused_without_a_traceback(self):
        """Idle security cycle: a member compressed with an unknown method raised NotImplementedError."""
        raw = bytearray(self._zip_export("good.zip").read_bytes())
        for sig, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):  # local and central headers
            at = raw.find(sig) + offset
            raw[at:at + 2] = struct.pack("<H", 99)
        bad = self.tmp / "method.zip"
        bad.write_bytes(bytes(raw))
        self.assert_refused_cleanly(bad)

    def test_renamed_copy_of_an_imported_export_is_not_listed_again(self):
        """Copilot review of 493be7d: the same bytes under a new name were listed again, after newer ones."""
        a, b = self.copy_as(F11, "a"), self.copy_as(F00, "b")
        ingest_into_instance(self.inst, [a])
        ingest_into_instance(self.inst, [b])
        renamed = self.tmp / "old-export.json"
        shutil.copy(a, renamed)
        res = ingest_into_instance(self.inst, [renamed, renamed])
        self.assertEqual(res.imported, [])
        paths = [s["path"] for s in load_manifest(self.inst)["sources"]]
        self.assertEqual(len(paths), 2)
        self.assertEqual(res.already, [paths[0], paths[0]])

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
