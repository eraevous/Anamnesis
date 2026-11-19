"""A message-level, branch-aware full-text index over message units (SQLite FTS5, stdlib only).

HO-ANA-20261118-6H0J item 1; the stage-4 "index" of the pipeline reconciliation
(relay-kit-private kairos/research/anamnesis/pipeline-reconciliation.md §3), re-keyed to
`message_unit_id` as that note asks.

Reviewer guide:
- One FTS row per message unit with text, on every branch. A hit always names its
  `message_unit_id` and its `branch_status`; an alternate unit is never reported as active.
- A snippet comes from one unit's text only, so it can never span two messages or two speakers.
- Queries are free text, not FTS5 syntax: words are extracted and quoted, then OR-ed and ranked by
  bm25, so user input cannot inject FTS5 operators. A few English stopwords are
  dropped from the query unless nothing else is left. `active_only=True` filters to the active path.
- The index is a cache built from units; it can be thrown away and rebuilt. Where it lives
  (git, local cache, hosted) is item 3 of the handoff and is not decided here.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .units import BranchStatus, ConversationMessageBundle

INDEX_SCHEMA_VERSION = "anamnesis.fts-index.v1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS units (
    rowid INTEGER PRIMARY KEY,
    message_unit_id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    title TEXT NOT NULL,
    branch_status TEXT NOT NULL,
    author_role TEXT NOT NULL,
    create_time TEXT,
    model_slug TEXT,
    content_hash TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(text, tokenize = 'unicode61');
"""

def _is_word_char(ch: str) -> bool:
    # Letters, digits and "_" (as in \w), plus combining marks, so a decomposed accent
    # ("cafe" + U+0301) stays inside its word, as FTS5's unicode61 tokenizer keeps it.
    return ch.isalnum() or ch == "_" or unicodedata.category(ch).startswith("M")


def query_terms(query: str) -> List[str]:
    """Words of a free-text query, split the way unicode61 splits the indexed text."""
    terms, current = [], []
    for ch in query:
        if _is_word_char(ch):
            current.append(ch)
        elif current:
            terms.append("".join(current))
            current = []
    if current:
        terms.append("".join(current))
    return terms
# Dropped from a query unless nothing else is left, so "in" or "a" alone never makes a hit.
STOPWORDS = frozenset("a an the in of to on and or for is it".split())


@dataclass(frozen=True)
class Hit:
    message_unit_id: str
    conversation_id: str
    title: str
    branch_status: str
    author_role: str
    snippet: str
    score: float


def fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        return True
    except sqlite3.OperationalError:
        return False


class MessageIndex:
    """Open or create an index at `path` (or ":memory:")."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.con = sqlite3.connect(self.path)
        self.con.executescript(_SCHEMA)
        with self.con:  # commit now, so an idle index never holds the write lock
            self.con.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                (INDEX_SCHEMA_VERSION,),
            )
        found = self.con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        if found != INDEX_SCHEMA_VERSION:
            raise ValueError(f"index schema {found!r} is not {INDEX_SCHEMA_VERSION!r}; rebuild it")

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "MessageIndex":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def add(self, bundles: Iterable[ConversationMessageBundle]) -> int:
        """Index every unit with text. Re-adding a unit replaces it. Returns rows written."""
        written = 0
        with self.con:
            for bundle in bundles:
                for unit in bundle.messages:
                    old = self.con.execute(
                        "SELECT rowid FROM units WHERE message_unit_id = ?", (unit.message_unit_id,)
                    ).fetchone()
                    if old:  # replace, even when the new version has no text
                        self.con.execute("DELETE FROM units_fts WHERE rowid = ?", old)
                        self.con.execute("DELETE FROM units WHERE rowid = ?", old)
                    if not unit.text:
                        continue
                    cur = self.con.execute(
                        "INSERT INTO units(message_unit_id, conversation_id, title, branch_status,"
                        " author_role, create_time, model_slug, content_hash)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            unit.message_unit_id,
                            unit.conversation_id,
                            bundle.title,
                            unit.branch_status.value,
                            unit.author_role,
                            None if unit.create_time is None else str(unit.create_time),
                            unit.model_slug,
                            unit.content_hash,
                        ),
                    )
                    self.con.execute(
                        "INSERT INTO units_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, unit.text)
                    )
                    written += 1
        return written

    def count(self) -> int:
        return self.con.execute("SELECT count(*) FROM units").fetchone()[0]

    def search(self, query: str, *, limit: int = 10, active_only: bool = False,
               highlight: bool = True) -> List[Hit]:
        """Ranked hits. `highlight` marks matched words in the snippet with [ and ]; pass False
        for a snippet that is the unit's text unchanged (apart from "…" where it is cut)."""
        words = query_terms(query)
        words = [w for w in words if w.lower() not in STOPWORDS] or words
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
        sql = (
            "SELECT u.message_unit_id, u.conversation_id, u.title, u.branch_status, u.author_role,"
            " snippet(units_fts, 0, ?, ?, '…', 16), bm25(units_fts)"
            " FROM units_fts JOIN units u ON u.rowid = units_fts.rowid"
            " WHERE units_fts MATCH ?"
        )
        params: list = ["[", "]"] if highlight else ["", ""]
        params.append(match)
        if active_only:
            sql += " AND u.branch_status = ?"
            params.append(BranchStatus.ACTIVE.value)
        sql += " ORDER BY bm25(units_fts), u.message_unit_id LIMIT ?"
        params.append(int(limit))
        return [
            Hit(r[0], r[1], r[2], r[3], r[4], r[5], -float(r[6]))
            for r in self.con.execute(sql, params)
        ]
