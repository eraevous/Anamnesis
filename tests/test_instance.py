"""Tests for memory instances and `anamnesis init` (HO-ANA-20261118-6H0J item 2).

Reviewer guide: every test works in a fresh temporary folder; the demo instance is built from the
committed synthetic fixtures only. "Adopt in place" is tested on a folder shaped like an existing
instance (a `library/` with a file, an existing `.gitignore`), with synthetic content.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from anamnesis import (
    INSTANCE_LAYOUT,
    InstanceError,
    MessageIndex,
    bundles_from_export,
    init_instance,
    load_manifest,
    validate_manifest,
)
from anamnesis.instance import MANIFEST_NAME, default_manifest

ROOT = Path(__file__).resolve().parent.parent


def tree_digest(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir")
            for p in sorted(root.rglob("*"))}


class InitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_init_creates_layout_and_a_valid_manifest(self):
        inst = self.tmp / "memory"
        init_instance(inst)
        for rel in INSTANCE_LAYOUT:
            self.assertTrue((inst / rel).is_dir(), rel)
        self.assertEqual((inst / "claims" / "ledger.jsonl").read_text(), "")
        manifest = load_manifest(inst)
        self.assertEqual(manifest["name"], "memory")
        self.assertEqual(manifest["privacy"]["visibility"], "private")
        self.assertEqual(manifest["privacy"]["agent_access"], "propose-only")
        ignored = (inst / ".gitignore").read_text().splitlines()
        self.assertIn("sources/", ignored)
        self.assertIn(".anamnesis/", ignored)

    def test_second_init_changes_nothing(self):
        inst = self.tmp / "memory"
        init_instance(inst, demo=True)
        before = tree_digest(inst)
        report = init_instance(inst)
        self.assertEqual(report.created, [])
        self.assertEqual(tree_digest(inst), before)

    def test_adopts_an_existing_folder_in_place(self):
        inst = self.tmp / "existing"
        (inst / "library").mkdir(parents=True)
        (inst / "library" / "README.md").write_text("synthetic library note\n")
        (inst / "core_seed.yml").write_text("version: 1\n")
        (inst / ".gitignore").write_text("*.pyc\n")
        init_instance(inst)
        self.assertEqual((inst / "library" / "README.md").read_text(), "synthetic library note\n")
        self.assertEqual((inst / "core_seed.yml").read_text(), "version: 1\n")
        lines = (inst / ".gitignore").read_text().splitlines()
        self.assertEqual(lines[0], "*.pyc")
        self.assertIn("sources/", lines)
        self.assertIn(".anamnesis/", lines)
        self.assertEqual(load_manifest(inst)["name"], "existing")

    def test_invalid_existing_manifest_is_reported_not_replaced(self):
        inst = self.tmp / "memory"
        inst.mkdir()
        (inst / MANIFEST_NAME).write_text('{"schema": "something-else"}')
        with self.assertRaises(InstanceError):
            init_instance(inst)
        self.assertEqual((inst / MANIFEST_NAME).read_text(), '{"schema": "something-else"}')
        self.assertEqual(sorted(p.name for p in inst.iterdir()), [MANIFEST_NAME])

    def test_demo_refuses_an_existing_instance(self):
        inst = self.tmp / "memory"
        init_instance(inst)
        before = tree_digest(inst)
        with self.assertRaises(InstanceError):
            init_instance(inst, demo=True)
        self.assertEqual(tree_digest(inst), before)

    def test_demo_never_overwrites_a_source_and_leaves_no_partial_instance(self):
        inst = self.tmp / "memory"
        (inst / "sources" / "demo").mkdir(parents=True)
        (inst / "sources" / "demo" / "F-11.json").write_text("mine")
        before = tree_digest(inst)
        with self.assertRaises(InstanceError):
            init_instance(inst, demo=True)
        self.assertEqual(tree_digest(inst), before)

    def test_symlinks_cannot_redirect_writes_outside(self):
        """Codex review of 4c582b2: a symlinked claims/ or .gitignore sent writes to a sibling."""
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "ignore").write_text("theirs\n")
        for link, target in (("claims", outside), (".gitignore", outside / "ignore"),
                             ("rendered", outside), ("dangling", None)):
            inst = self.tmp / f"inst-{link.strip('.')}"
            inst.mkdir()
            if target is None:
                (inst / "runs").symlink_to(self.tmp / "nowhere")
            else:
                (inst / link).symlink_to(target)
            before = tree_digest(outside)
            with self.assertRaises(InstanceError):
                init_instance(inst)
            self.assertEqual(tree_digest(outside), before)
            self.assertFalse((inst / MANIFEST_NAME).exists())

    def test_layout_path_that_is_a_file_is_refused(self):
        inst = self.tmp / "memory"
        inst.mkdir()
        (inst / "claims").write_text("not a folder")
        with self.assertRaises(InstanceError):
            init_instance(inst)
        self.assertEqual(sorted(p.name for p in inst.iterdir()), ["claims"])

    def test_blank_name_is_refused_before_any_write(self):
        """Codex review of 9d623a4: a whitespace --name hit an assert after demo files were copied."""
        inst = self.tmp / "memory"
        for blank in ("   ", ""):
            with self.assertRaises(InstanceError):
                init_instance(inst, name=blank, demo=True)
            self.assertFalse(inst.exists())
        init_instance(inst, name="ok", demo=True)  # a retry with a good name works
        self.assertEqual(load_manifest(inst)["name"], "ok")

    def test_path_that_is_a_file_is_refused(self):
        f = self.tmp / "afile"
        f.write_text("x")
        with self.assertRaises(InstanceError):
            init_instance(f)

    def test_init_writes_nothing_outside_the_instance(self):
        (self.tmp / "neighbour.txt").write_text("keep")
        init_instance(self.tmp / "memory", demo=True)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["memory", "neighbour.txt"])

    def test_demo_instance_is_searchable_and_cites_its_source(self):
        inst = self.tmp / "memory"
        report = init_instance(inst, demo=True)
        manifest = load_manifest(inst)
        self.assertEqual(len(manifest["sources"]), len(list((ROOT / "fixtures").glob("*/conversation.json"))))
        self.assertTrue(all(s["synthetic"] for s in manifest["sources"]))
        self.assertEqual(report.demo_sources, [s["path"] for s in manifest["sources"]])
        with MessageIndex(inst / ".anamnesis" / "index.sqlite") as ix:
            for src in manifest["sources"]:
                ix.add(bundles_from_export(inst / src["path"]))
            hits = ix.search("zephyrine backup agreed")
        self.assertTrue(hits)
        self.assertTrue(hits[0].message_unit_id.startswith("chatgpt-message:"))


class ManifestTests(unittest.TestCase):
    def test_default_manifest_is_valid(self):
        self.assertEqual(validate_manifest(default_manifest("m")), [])

    def test_every_problem_is_listed(self):
        m = default_manifest("m")
        m["sources"] = [
            {"kind": "chatgpt-export", "path": "../outside.json"},
            {"kind": "chatgpt-export", "path": "/abs.json"},
            {"kind": "chatgpt-export", "path": "sources/a.json"},
            {"kind": "chatgpt-export", "path": "sources/a.json"},
            {"kind": "mystery", "path": "sources/b.json"},
        ]
        m["privacy"]["visibility"] = "everyone"
        m["shelves"] = [{"id": "x"}, {"id": "x"}]
        problems = validate_manifest(m)
        joined = " | ".join(problems)
        for needle in ("sources[0].path", "sources[1].path", "listed twice", "sources[4].kind",
                       "privacy.visibility", "shelf ids must be unique"):
            self.assertIn(needle, joined)

    def test_non_string_enums_are_problems_not_crashes(self):
        """Codex review of 4c582b2: a list or object kind/visibility raised TypeError."""
        m = default_manifest("m")
        m["sources"] = [{"kind": ["chatgpt-export"], "path": "sources/a.json"}, {"kind": {}, "path": "sources/b.json"}]
        m["privacy"]["visibility"] = ["private"]
        joined = " | ".join(validate_manifest(m))
        for needle in ("sources[0].kind", "sources[1].kind", "privacy.visibility"):
            self.assertIn(needle, joined)

    def test_not_an_object(self):
        self.assertEqual(validate_manifest([]), ["manifest is not a JSON object"])


class EngineIndependenceTests(unittest.TestCase):
    def test_engine_names_no_instance(self):
        """R-ENG-3: the engine depends on no instance; no module names one or a home-folder path."""
        pattern = re.compile(r"cognitive.?scaffolding|cogscaff|~/|/home/|/Users/", re.IGNORECASE)
        for path in sorted((ROOT / "anamnesis").glob("*.py")):
            code = "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))
            # docstrings may mention CogScaff as an example of an instance; strip triple-quoted text
            code = re.sub(r'"""[\s\S]*?"""', "", code)
            self.assertIsNone(pattern.search(code), f"{path.name} references an instance or a home path")


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "anamnesis", *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)

    def test_init_then_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = Path(tmp) / "memory"
            r = self.run_cli("init", str(inst), "--demo", "--name", "demo")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("(synthetic)", r.stdout)
            r = self.run_cli("check", str(inst))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("ok: demo", r.stdout)

    def test_check_on_a_plain_folder_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("check", tmp)
            self.assertEqual(r.returncode, 1)
            self.assertIn("not an anamnesis instance", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_check_reports_a_malformed_manifest_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = default_manifest("m")
            m["privacy"]["visibility"] = {"x": 1}
            (Path(tmp) / MANIFEST_NAME).write_text(json.dumps(m))
            r = self.run_cli("check", tmp)
            self.assertEqual(r.returncode, 1)
            self.assertIn("privacy.visibility", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_usage_error(self):
        self.assertEqual(self.run_cli().returncode, 2)


if __name__ == "__main__":
    unittest.main()
