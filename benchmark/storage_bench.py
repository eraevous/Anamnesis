"""Measure index storage options on synthetic data (HO-ANA-20261118-6H0J item 3).

    python3 -m benchmark.storage_bench [--conversations 500 5000] [--messages 20] [--out FILE]

Reviewer guide:
- Data is generated here from a fixed seed: invented words, no real text. Nothing is read from disk
  but the code itself.
- What is measured on this machine: parse time, FTS5 build time, index size and its zlib size (a
  stand-in for what git stores per index version), cold query (a new process opens the file and
  runs one query; the OS page cache is not dropped, so "cold" means a cold process, not a cold
  disk), and warm query (median over repeated queries on an open index).
- What is modelled, not measured: hosted download time (size / bandwidth + round trips) and vector
  sizes (units x dim x 4 bytes). The output labels each figure `measured` or `model`.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anamnesis import CacheStore, bundles_from_export, open_index, sources_digest  # noqa: E402

SYLLABLES = "ka lo mi ren tho vas quil fern mar row lace dun pel shi zor ab ec ix om ut".split()


def synthetic_export(n_convs: int, n_msgs: int, seed: int = 7) -> list:
    rng = random.Random(seed)
    vocab = sorted({"".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 4))) for _ in range(6000)})
    convs = []
    for c in range(n_convs):
        mapping = {"root": {"id": "root", "message": None, "parent": None, "children": []}}
        parent = "root"
        for m in range(n_msgs):
            nid = f"m{m}"
            text = " ".join(rng.choice(vocab) for _ in range(rng.randint(20, 120)))
            mapping[nid] = {"id": nid, "parent": parent, "children": [], "message": {
                "id": nid, "author": {"role": "user" if m % 2 == 0 else "assistant"},
                "create_time": 1800000000 + c * 1000 + m,
                "content": {"content_type": "text", "parts": [text]}}}
            mapping[parent]["children"].append(nid)
            parent = nid
        convs.append({"id": f"syn-{c}", "title": f"synthetic {c}", "current_node": parent, "mapping": mapping})
    return convs, vocab


COLD = """
import sys, time
sys.path.insert(0, sys.argv[1])
t = time.perf_counter()
from anamnesis import MessageIndex
with MessageIndex(sys.argv[2]) as idx:
    idx.search(sys.argv[3], limit=10)
print(time.perf_counter() - t)
"""


def run(n_convs: int, n_msgs: int) -> dict:
    convs, vocab = synthetic_export(n_convs, n_msgs)
    rng = random.Random(11)
    queries = [" ".join(rng.sample(vocab, 2)) for _ in range(50)]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        src = root / "sources" / "export.json"
        src.parent.mkdir()
        src.write_text(json.dumps(convs))
        t0 = time.perf_counter()
        bundles = bundles_from_export(src)
        parse_s = time.perf_counter() - t0
        digest = sources_digest(root, [src])
        store = CacheStore(root / "cache")
        t0 = time.perf_counter()
        idx, built = open_index(store, digest, lambda i: i.add(bundles))
        build_s = time.perf_counter() - t0
        assert built
        units = idx.count()
        warm = []
        for q in queries:
            t = time.perf_counter()
            idx.search(q, limit=10)
            warm.append(time.perf_counter() - t)
        idx.close()
        path = store.fetch(digest)
        raw = path.read_bytes()
        cold = [float(subprocess.run([sys.executable, "-c", COLD, str(ROOT), str(path), q],
                                     capture_output=True, text=True, check=True).stdout)
                for q in queries[:7]]
        return {
            "conversations": n_convs, "messages_per_conversation": n_msgs, "units": units,
            "source_bytes": src.stat().st_size,
            "measured": {
                "parse_s": round(parse_s, 3), "fts_build_s": round(build_s, 3),
                "rebuild_from_sources_s": round(parse_s + build_s, 3),
                "index_bytes": len(raw), "index_zlib_bytes": len(zlib.compress(raw, 6)),
                "cold_query_ms_median": round(statistics.median(cold) * 1000, 2),
                "warm_query_ms_median": round(statistics.median(warm) * 1000, 3),
            },
            "model": {
                "vectors_bytes_dim_384": units * 384 * 4,
                "vectors_bytes_dim_1536": units * 1536 * 4,
                "hosted_fetch_s_50mbit": round(0.3 + len(zlib.compress(raw, 6)) * 8 / 50e6, 2),
                "hosted_fetch_s_200mbit": round(0.3 + len(zlib.compress(raw, 6)) * 8 / 200e6, 2),
            },
        }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--conversations", type=int, nargs="+", default=[500, 5000])
    ap.add_argument("--messages", type=int, default=20)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    results = {"python": sys.version.split()[0], "sqlite": __import__("sqlite3").sqlite_version,
               "runs": [run(n, args.messages) for n in args.conversations]}
    text = json.dumps(results, indent=2)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
