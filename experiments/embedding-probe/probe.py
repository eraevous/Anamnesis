"""Local embedding probe for Anamnesis D3 (handoff HO-KAI-20261115-936P).

Synthetic data only (fixture.json). Builds rolling Cells (D2, reading R1), indexes them
four ways, and compares lexical (SQLite FTS5 BM25), dense (a local embedding model) and
hybrid (reciprocal-rank fusion) retrieval at conversation level.

This is an experiment, not project code: the embedding dependencies are not added to the
repository. Run it in a throwaway virtualenv:

    python3 -m venv /tmp/emb && /tmp/emb/bin/pip install wordllama numpy
    /tmp/emb/bin/python -I experiments/embedding-probe/probe.py

The OpenAI embedder is defined behind the same interface but is never called here.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- units (D2, D5)
@dataclass(frozen=True)
class Message:
    conversation: str
    index: int
    speaker: str  # "human" | "model"
    text: str
    platform: str
    model: str


@dataclass(frozen=True)
class Cell:
    """Rolling Cell: consecutive pair (m[i], m[i+1]), sliding by one (reading R1)."""

    conversation: str
    call: Message
    response: Message

    @property
    def id(self) -> str:
        return f"cell:{self.conversation}:{self.call.index}->{self.response.index}"

    @property
    def direction(self) -> str:
        return f"{self.call.speaker}->{self.response.speaker}"

    @property
    def platform(self) -> str:  # D5: platform and model recorded on every Cell
        return self.response.platform

    @property
    def model(self) -> str:
        return self.response.model


def rolling_cells(messages: Sequence[Message]) -> list[Cell]:
    return [Cell(m.conversation, m, n) for m, n in zip(messages, messages[1:])]


# ---------------------------------------------------------------- embedder interface (D3)
class Embedder(Protocol):
    model_id: str  # stored on every vector row, so a re-embed is a new index
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class LocalWordLlama:
    """Static token-embedding model whose weights ship inside the pip wheel (no download)."""

    def __init__(self) -> None:
        import wordllama  # type: ignore

        pkg = Path(wordllama.__file__).parent
        # cache_dir=package dir makes the loader find the bundled tokenizer and weights.
        self._wl = wordllama.WordLlama.load(cache_dir=pkg, disable_download=True)
        self.model_id = f"wordllama-l2_supercat-256@{getattr(wordllama, '__version__', '?')}"
        self.dim = 256

    def embed(self, texts):
        return self._wl.embed(list(texts), norm=True).tolist()


class OpenAIEmbedder:
    """Same interface for the provider path. Not called by this probe (no key, no spend)."""

    def __init__(self, model: str = "text-embedding-3-small", dim: int = 1536) -> None:
        self.model_id, self.dim = f"openai:{model}", dim
        self._model = model

    def embed(self, texts):  # pragma: no cover - intentionally unused here
        import urllib.request

        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=json.dumps({"model": self._model, "input": list(texts)}).encode(),
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req) as r:
            return [d["embedding"] for d in json.load(r)["data"]]


# ---------------------------------------------------------------- retrieval
def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1.0
    )


def rank_conversations(scored_units: list[tuple[str, float]]) -> list[str]:
    best: dict[str, float] = {}
    for conv, s in scored_units:
        best[conv] = max(best.get(conv, -1e9), s)
    return [c for c, _ in sorted(best.items(), key=lambda kv: -kv[1])]


def fts_rank(units: list[tuple[str, str]], query: str) -> list[str]:
    db = sqlite3.connect(":memory:")
    db.execute("create virtual table u using fts5(conv unindexed, body)")
    db.executemany("insert into u values (?, ?)", units)
    terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]
    if not terms:
        return []
    rows = db.execute(
        "select conv, bm25(u) from u where u match ? order by bm25(u)",
        (" OR ".join(terms),),
    ).fetchall()
    return rank_conversations([(c, -s) for c, s in rows])


def rrf(*rankings: list[str], k: int = 60) -> list[str]:
    score: dict[str, float] = {}
    for r in rankings:
        for i, c in enumerate(r):
            score[c] = score.get(c, 0.0) + 1.0 / (k + i + 1)
    return [c for c, _ in sorted(score.items(), key=lambda kv: -kv[1])]


def metrics(ranked: list[list[str]], gold: list[str]) -> dict[str, float]:
    r1 = sum(1 for r, g in zip(ranked, gold) if r[:1] == [g]) / len(gold)
    r3 = sum(1 for r, g in zip(ranked, gold) if g in r[:3]) / len(gold)
    mrr = sum((1 / (r.index(g) + 1)) if g in r else 0 for r, g in zip(ranked, gold)) / len(gold)
    return {"recall@1": round(r1, 3), "recall@3": round(r3, 3), "mrr": round(mrr, 3)}


def main() -> int:
    fx = json.loads((HERE / "fixture.json").read_text())
    messages: list[Message] = []
    cells: list[Cell] = []
    for c in fx["conversations"]:
        ms = [Message(c["id"], i, s, t, c["platform"], c["model"]) for i, (s, t) in enumerate(c["messages"])]
        messages += ms
        cells += rolling_cells(ms)
    qs, gold = [q["q"] for q in fx["queries"]], [q["gold"] for q in fx["queries"]]

    # Structural checks on rolling Cells (D2, R1)
    n_msgs, n_cells = len(messages), len(cells)
    dirs = sorted({c.direction for c in cells})
    assert n_cells == n_msgs - len(fx["conversations"])
    assert all(c.platform and c.model for c in cells)

    emb: Embedder = LocalWordLlama()
    t0 = time.perf_counter()
    texts = [m.text for m in messages] + [f"{c.call.text}\n{c.response.text}" for c in cells]
    vecs = emb.embed(texts)
    t_embed = time.perf_counter() - t0
    mvec, cvec = vecs[: len(messages)], vecs[len(messages):]
    qvec = emb.embed(qs)

    def dense(units_vecs, units_conv, qv):
        return rank_conversations([(c, cos(qv, v)) for c, v in zip(units_conv, units_vecs)])

    idx = {
        "user_only": [(m, v) for m, v in zip(messages, mvec) if m.speaker == "human"],
        "reply_only": [(m, v) for m, v in zip(messages, mvec) if m.speaker == "model"],
        "message": list(zip(messages, mvec)),
    }
    results: dict[str, dict] = {}
    cell_conv = [c.conversation for c in cells]
    cell_text = [(c.conversation, f"{c.call.text}\n{c.response.text}") for c in cells]

    lex = [fts_rank(cell_text, q) for q in qs]
    results["lexical FTS5 (Cell)"] = metrics(lex, gold)
    den_cell = [dense(cvec, cell_conv, qv) for qv in qvec]
    results["dense (Cell)"] = metrics(den_cell, gold)
    for name, rows in idx.items():
        results[f"dense ({name})"] = metrics(
            [dense([v for _, v in rows], [m.conversation for m, _ in rows], qv) for qv in qvec], gold
        )
    results["hybrid RRF (FTS5 + dense Cell)"] = metrics([rrf(a, b) for a, b in zip(lex, den_cell)], gold)

    per_query = [
        {"q": q, "gold": g, "lexical_top": (l[:1] or ["-"])[0], "dense_top": d[0]}
        for q, g, l, d in zip(qs, gold, lex, den_cell)
    ]
    out = {
        "embedder": emb.model_id,
        "dim": emb.dim,
        "messages": n_msgs,
        "rolling_cells": n_cells,
        "cell_directions": dirs,
        "embed_seconds_for_units": round(t_embed, 4),
        "units_embedded": len(texts),
        "results": results,
        "per_query": per_query,
    }
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
