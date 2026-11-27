"""Ingest an export into an index, and answer a question with cited sources (offline, stdlib only).

HO-ANA-20261118-6H0J item 4 (the cold test: install, init, ingest a synthetic ChatGPT export, ask a
question and get an answer that cites a source). This module is steps 3 and 4 at the engine level;
it takes a `MessageIndex` and knows nothing about instances, so it does not need `anamnesis init`
(Anamnesis#15) or the storage backends (Anamnesis#16). Wiring `ingest` and `ask` into the CLI and an
instance's manifest is a follow-up once those merge.

Reviewer guide:
- The answer is extractive. Every line of it is a quote from one message unit, and each quote names
  its `message_unit_id`, conversation title, speaker and branch. Nothing is generated, paraphrased or
  combined, so the engine can answer with no provider and no network, and an answer can never state
  something that no source says.
- No hit means no answer: `Answer.found` is False, there are no citations, and `render()` says
  plainly that nothing in this memory matched. The engine never falls back to a guess.
- Branches: the active path is searched first. Only when it has no hit are alternate (regenerated-away)
  units searched, and each such citation is labelled as not the chosen reply.
- Quotes are the unit's own text, unhighlighted (`MessageIndex.search(highlight=False)`), so a literal
  `[` or `]` in a source survives; "…" marks where a long unit was cut.
- `render()` is for a terminal, and an export is untrusted input: control characters (C0 apart from
  newline and tab, DEL, C1) and bidi overrides in a quote, title, role or id are shown as visible
  escapes such as `\x1b`, so an export cannot send terminal escape sequences (clipboard writes, cleared
  lines, hidden text). `to_dict()` keeps the text exactly as the source has it.
- Branch labels: `alternate` is a regenerated-away reply ("not the chosen reply"); `unknown` means the
  export doesn't record which reply was chosen, and is labelled as undetermined, never as discarded.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

from .index import Hit, MessageIndex
from .units import BranchStatus, bundles_from_export

ANSWER_SCHEMA_VERSION = "anamnesis.answer.v1"


@dataclass(frozen=True)
class IngestReport:
    exports: int
    conversations: int
    units: int
    indexed: int  # units with text written to the index


@dataclass(frozen=True)
class Citation:
    message_unit_id: str
    conversation_id: str
    title: str
    author_role: str
    branch_status: str
    quote: str  # the unit's text, cut with "…" around the match when long; no highlight marks

    @property
    def is_active(self) -> bool:
        return self.branch_status == BranchStatus.ACTIVE.value

    def to_dict(self) -> dict:
        return {
            "message_unit_id": self.message_unit_id,
            "conversation_id": self.conversation_id,
            "title": self.title,
            "author_role": self.author_role,
            "branch_status": self.branch_status,
            "quote": self.quote,
        }


@dataclass(frozen=True)
class Answer:
    question: str
    citations: Tuple[Citation, ...]

    @property
    def found(self) -> bool:
        return bool(self.citations)

    def to_dict(self) -> dict:
        return {
            "schema": ANSWER_SCHEMA_VERSION,
            "question": self.question,
            "found": self.found,
            "citations": [c.to_dict() for c in self.citations],
        }

    def render(self) -> str:
        if not self.found:
            return "Nothing in this memory matches that question, so there is no answer to cite."
        lines = [f"From {len(self.citations)} source message(s):"]
        for n, c in enumerate(self.citations, 1):
            branch = _BRANCH_NOTE.get(c.branch_status, f", {printable(c.branch_status)} branch")
            lines.append(f'[{n}] "{printable(c.quote)}"')
            lines.append(f"    {printable(c.author_role)} in \"{printable(c.title)}\"{branch};"
                         f" source {printable(c.message_unit_id)}")
        return "\n".join(lines)


# Bidi embeddings, overrides and isolates: they can make a quote display in an order it doesn't have.
_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def printable(text: str) -> str:
    """`text` safe to print to a terminal: control characters other than newline and tab, and bidi
    controls, become visible escapes (`\\x1b`, `\\u202e`); everything else is unchanged."""
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
        elif unicodedata.category(ch) == "Cc" or ch in _BIDI_CONTROLS:
            out.append(f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


_BRANCH_NOTE = {
    BranchStatus.ACTIVE.value: "",
    BranchStatus.ALTERNATE.value: ", alternate branch (not the chosen reply)",
    BranchStatus.UNKNOWN.value: ", branch undetermined (the export doesn't record which reply was chosen)",
}


def ingest(index: MessageIndex, exports: Iterable[str | Path]) -> IngestReport:
    """Parse each export (ZIP, folder or conversations.json) and add every unit to `index`."""
    n_exports = n_conversations = n_units = n_indexed = 0
    for export in exports:
        bundles = bundles_from_export(Path(export))
        n_exports += 1
        n_conversations += len(bundles)
        n_units += sum(len(b.messages) for b in bundles)
        n_indexed += index.add(bundles)
    return IngestReport(n_exports, n_conversations, n_units, n_indexed)


def _citation(hit: Hit) -> Citation:
    return Citation(hit.message_unit_id, hit.conversation_id, hit.title, hit.author_role,
                    hit.branch_status, hit.snippet)


def ask(index: MessageIndex, question: str, *, limit: int = 3) -> Answer:
    """Answer `question` with up to `limit` quoted, cited units; active path first."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    hits: List[Hit] = index.search(question, limit=limit, active_only=True, highlight=False)
    if not hits:
        hits = index.search(question, limit=limit, highlight=False)
    return Answer(question, tuple(_citation(h) for h in hits))
