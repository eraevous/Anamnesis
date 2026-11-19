"""Tests for the index storage backends (anamnesis/storage.py; HO-ANA-20261118-6H0J item 3).

Reviewer guide: synthetic inline conversations only. Each test checks one promise of the module
docstring: the digest key, rebuild-on-change, atomic build, and both shipped backends.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import shutil

from anamnesis.storage import IndexRaceError, recorded_digest
from anamnesis import CacheStore, IndexStore, InRepoStore, bundles_from_export, open_index, sources_digest
from tests.test_engine_core import conv


def write_export(root: Path, name: str, word: str) -> Path:
    path = root / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([conv(f"c-{word}", (("user", f"remember {word}"), ("assistant", "ok")))]))
    return path


def builder(paths):
    return lambda idx: idx.add(b for p in paths for b in bundles_from_export(p))


class DigestTests(unittest.TestCase):
    def test_digest_tracks_content_paths_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a, b = write_export(root, "a.json", "quillfern"), write_export(root, "b.json", "marrowlace")
            first = sources_digest(root, [a, b])
            self.assertEqual(first, sources_digest(root, [a, b]))
            b.write_text(b.read_text().replace("marrowlace", "marrowlacf"))
            self.assertNotEqual(first, sources_digest(root, [a, b]))
            before_rename = sources_digest(root, [a, b])
            renamed = root / "sources" / "c.json"
            b.rename(renamed)
            self.assertNotEqual(before_rename, sources_digest(root, [a, renamed]))

    def test_order_changes_the_key_when_it_changes_the_index(self):
        # Copilot review of b7d35f1: two exports holding different versions of one conversation
        # index different text for [a, b] and [b, a]; they must not share a cache entry.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a, b = write_export(root, "a.json", "quillfern"), write_export(root, "b.json", "marrowlace")
            b.write_text(b.read_text().replace("c-marrowlace", "c-quillfern"))  # same conversation id
            store = CacheStore(root / "cache")
            for order, word in (([a, b], "marrowlace"), ([b, a], "quillfern")):
                idx, _ = open_index(store, sources_digest(root, order), builder(order))
                with idx:
                    self.assertEqual(len(idx.search(word)), 1, order)

    def test_large_source_is_streamed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            big = root / "big.json"
            big.write_bytes(b"x" * (3 * (1 << 20) + 5))
            reads = []
            real_open = open

            def spy_open(path, mode="r", *args, **kw):
                fh = real_open(path, mode, *args, **kw)
                real_read = fh.read

                class Wrapped:
                    def read(self, n=-1):
                        reads.append(n)
                        return real_read(n)

                    def __enter__(self):
                        return self

                    def __exit__(self, *exc):
                        fh.close()

                return Wrapped()

            import anamnesis.storage as st
            st.open = spy_open
            try:
                digest = sources_digest(root, [big])
            finally:
                del st.open
            self.assertTrue(reads and all(0 < n <= 1 << 20 for n in reads))
            self.assertEqual(digest, sources_digest(root, [big]))

    def test_path_outside_root_is_refused(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            outside = write_export(Path(other), "x.json", "quillfern")
            with self.assertRaises(ValueError):
                sources_digest(d, [outside])


class BackendTests(unittest.TestCase):
    def check_backend(self, make_store):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = write_export(root, "a.json", "quillfern")
            store = make_store(root)
            self.assertIsInstance(store, IndexStore)
            digest = sources_digest(root, [src])
            idx, built = open_index(store, digest, builder([src]))
            with idx:
                self.assertTrue(built)
                self.assertEqual(idx.search("quillfern")[0].conversation_id, "c_quillfern")
            idx, built = open_index(store, digest, lambda i: self.fail("rebuilt a current index"))
            with idx:
                self.assertFalse(built)
            src.write_text(src.read_text().replace("quillfern", "marrowlace"))
            digest2 = sources_digest(root, [src])
            idx, built = open_index(store, digest2, builder([src]))
            with idx:
                self.assertTrue(built)
                self.assertEqual(idx.search("quillfern"), [])
                self.assertEqual(len(idx.search("marrowlace")), 1)
            leftovers = [p for p in root.rglob(".*") if p.name.endswith((".building", ".tmp"))]
            self.assertEqual(leftovers, [])

    def test_cache_store(self):
        self.check_backend(lambda root: CacheStore(root / ".anamnesis" / "cache"))

    def test_in_repo_store(self):
        self.check_backend(lambda root: InRepoStore(root / "index" / "messages.sqlite"))

    def test_cache_prune_keeps_current(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = write_export(root, "a.json", "quillfern")
            store = CacheStore(root / "cache")
            old = sources_digest(root, [src])
            open_index(store, old, builder([src]))[0].close()
            src.write_text(src.read_text().replace("quillfern", "marrowlace"))
            new = sources_digest(root, [src])
            open_index(store, new, builder([src]))[0].close()
            (root / "cache" / "notes.sqlite").write_text("not an index")
            self.assertEqual(store.prune(keep=new), 1)
            self.assertIsNotNone(store.fetch(new))
            self.assertIsNone(store.fetch(old))
            self.assertTrue((root / "cache" / "notes.sqlite").exists())

    def test_failed_build_leaves_old_index_and_no_temp(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = write_export(root, "a.json", "quillfern")
            store = InRepoStore(root / "index" / "messages.sqlite")
            open_index(store, sources_digest(root, [src]), builder([src]))[0].close()
            before = store.path.read_bytes()

            def boom(idx):
                idx.add(b for b in bundles_from_export(src))
                raise RuntimeError("interrupted")

            with self.assertRaises(RuntimeError):
                open_index(store, "0" * 64, boom)
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual([p.name for p in store.path.parent.iterdir()], ["messages.sqlite"])

    def test_in_repo_digest_lives_in_the_index_file(self):
        # Codex review of 59de6f7: a sidecar could name the old sources while the file held the new.
        # The digest is now inside the file, so swapping the file swaps the digest with it.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = write_export(root, "a.json", "quillfern")
            store = InRepoStore(root / "index" / "messages.sqlite")
            old = sources_digest(root, [src])
            open_index(store, old, builder([src]))[0].close()
            other = InRepoStore(root / "elsewhere" / "messages.sqlite")
            src.write_text(src.read_text().replace("quillfern", "marrowlace"))
            new = sources_digest(root, [src])
            open_index(other, new, builder([src]))[0].close()
            other.path.replace(store.path)  # a newer generation lands, by any route
            self.assertIsNone(store.fetch(old))
            self.assertEqual(store.fetch(new), store.path)
            self.assertEqual(recorded_digest(store.path), new)

    def test_unreadable_or_foreign_file_is_not_fetched(self):
        with tempfile.TemporaryDirectory() as d:
            store = InRepoStore(Path(d) / "messages.sqlite")
            store.path.write_bytes(b"not a database")
            self.assertIsNone(store.fetch("a" * 64))
            self.assertEqual(store.path.read_bytes(), b"not a database")

    def test_swap_after_fetch_is_caught_on_the_returned_connection(self):
        # Codex review of d2f2011: a file replaced between fetch's check and the open was served.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = write_export(root, "a.json", "quillfern")
            store = InRepoStore(root / "index" / "messages.sqlite")
            old = sources_digest(root, [src])
            open_index(store, old, builder([src]))[0].close()
            other_src = write_export(root, "b.json", "marrowlace")
            other = InRepoStore(root / "other" / "messages.sqlite")
            open_index(other, sources_digest(root, [other_src]), builder([other_src]))[0].close()
            real_fetch = store.fetch
            swapped = []

            def racing_fetch(digest):
                found = real_fetch(digest)
                if found is not None and not swapped:
                    shutil.copyfile(other.path, store.path)  # another writer lands after the check
                    swapped.append(True)
                return found

            store.fetch = racing_fetch
            idx, built = open_index(store, old, builder([src]))
            with idx:
                self.assertTrue(built)  # rebuilt instead of serving the replacement
                self.assertEqual(len(idx.search("quillfern")), 1)
                self.assertEqual(idx.search("marrowlace"), [])

    def test_gives_up_when_every_generation_is_replaced(self):
        import anamnesis.storage as st
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = write_export(root, "a.json", "quillfern")
            store = InRepoStore(root / "messages.sqlite")
            orig = st._open_verified
            st._open_verified = lambda path, digest: None  # every open sees a foreign generation
            try:
                with self.assertRaises(IndexRaceError):
                    open_index(store, sources_digest(root, [src]), builder([src]), attempts=2)
            finally:
                st._open_verified = orig


class BenchSmokeTest(unittest.TestCase):
    def test_bench_runs_on_tiny_synthetic_data(self):
        from benchmark.storage_bench import run
        out = run(3, 4)
        self.assertEqual(out["units"], 12)
        self.assertGreater(out["measured"]["index_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
