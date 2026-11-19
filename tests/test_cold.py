"""The cold test (HO-ANA-20261118-6H0J item 4): install, `init ~/memory`, ingest a synthetic ChatGPT
export, ask a question, get an answer that cites a source.

Reviewer guide:
- "Clean environment" here means: a fresh HOME, an environment with only PATH and HOME, no access to
  this repository (the package is staged alone into an empty folder, or installed into a new venv),
  and a synthetic export written by the test itself (not the repository's fixtures).
- Default mode stages the `anamnesis` package by copying it, and runs `python -E -s -m anamnesis`
  from the staging folder, so nothing from this checkout or the user's site-packages is importable.
- `ANAMNESIS_COLD_INSTALL=1` does the real install instead: a new venv and `pip install <repo>`
  (pip may use the network to fetch setuptools; that is the "network beyond the install"), then the
  `anamnesis` console script. Kept opt-in so the default test run needs no network.
- After the install nothing uses the network: the engine is standard library only and offline.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def synthetic_export(path: Path) -> None:
    """A two-turn ChatGPT export ZIP with one regenerated (alternate) reply. Invented content."""
    def node(nid, parent, children, role, text):
        return {"id": nid, "parent": parent, "children": children, "message": None if role is None else {
            "id": nid, "author": {"role": role}, "create_time": 1.0,
            "content": {"content_type": "text", "parts": [text]}}}
    mapping = {
        "root": node("root", None, ["u1"], None, ""),
        "u1": node("u1", "root", ["a1", "a1b"], "user", "Which colour did we pick for the lighthouse mural?"),
        "a1": node("a1", "u1", [], "assistant", "We settled on cobalt teal for the lighthouse mural."),
        "a1b": node("a1b", "u1", [], "assistant", "Maybe saffron would suit the lighthouse mural."),
    }
    conv = [{"id": "cold-test-conv-1", "title": "Mural planning (synthetic)", "create_time": 1.0,
             "update_time": 2.0, "current_node": "a1", "mapping": mapping}]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("conversations.json", json.dumps(conv))


class ColdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anamnesis-cold-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home)}
        if os.environ.get("ANAMNESIS_COLD_INSTALL") == "1":
            venv = self.tmp / "venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, env=self.env)
            pip_env = dict(os.environ, HOME=str(self.home))  # proxy settings for the install only
            src = self.tmp / "src"  # install from a copy: a build must not leave files in the checkout
            shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", "build", "*.egg-info", "__pycache__"))
            subprocess.run([str(venv / "bin" / "pip"), "install", "-q", str(src)], check=True, env=pip_env)
            self.cmd, self.cwd = [str(venv / "bin" / "anamnesis")], self.home
        else:
            stage = self.tmp / "stage"
            shutil.copytree(ROOT / "anamnesis", stage / "anamnesis",
                            ignore=shutil.ignore_patterns("__pycache__"))
            self.cmd, self.cwd = [sys.executable, "-E", "-s", "-m", "anamnesis"], stage

    def run_cli(self, *args):
        return subprocess.run([*self.cmd, *args], cwd=self.cwd, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def test_install_init_ingest_ask_cites_a_source(self):
        export = self.tmp / "chatgpt-export.zip"
        synthetic_export(export)

        r = self.run_cli("init", "~/memory")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.home / "memory" / "anamnesis.instance.json").is_file())

        r = self.run_cli("ingest", "~/memory", str(export))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("index built", r.stdout)

        r = self.run_cli("ask", "~/memory", "what colour for the lighthouse mural?", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        answer = json.loads(r.stdout)
        self.assertTrue(answer["found"])
        cited = {c["message_unit_id"]: c for c in answer["citations"]}
        reply = cited.get("chatgpt-message:cold_test_conv_1:a1")
        self.assertIsNotNone(reply, cited)  # the chosen reply is cited by its unit id
        self.assertIn("cobalt teal", reply["quote"])
        self.assertTrue(all(c["branch_status"] == "active" for c in answer["citations"]))
        self.assertNotIn("saffron", " ".join(c["quote"] for c in answer["citations"]))  # discarded reply

        r = self.run_cli("ask", "~/memory", "who won the regatta?")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)  # nothing to cite: no answer
        self.assertIn("no answer to cite", r.stdout)


if __name__ == "__main__":
    unittest.main()
