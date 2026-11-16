"""Generate benchmark fixtures from readable outlines (fixture plan, "Shared format").

Reviewer guide
--------------
- A reviewer edits ``benchmark/outlines/<id>.json``, never ``fixtures/<id>/*``. Those files are
  generated, and ``--check`` (run by the tests) fails when they drift from their outline.
- ``conversation.json`` is shaped like a ChatGPT export's ``conversations.json`` (a list holding one
  conversation: ``mapping`` of nodes with ``parent``/``children``, ``current_node``), so the rescued
  offline core can ingest it unchanged. This module writes that shape; it does not parse exports.
  The rescued core stays the single parser (pipeline reconciliation, HO-MEM-20261107-3TNX).
- ``gold.json`` names units by ``message_unit_id`` in the rescued core's format,
  ``chatgpt-message:<conversation key>:<message_id>`` (schema ``anamnesis.message-unit.v1``), and
  keeps a ``labels`` table back to the outline's readable labels (``U-19``, ``A-20'``).
- Output is deterministic: fixed timestamps, sorted keys, no randomness. Same outline, same bytes.

Outline format
--------------
``turns`` is the active path in order, as ``[label, text]``. The label's prefix gives the role:
``S-`` system, ``U-`` user, ``A-`` assistant. ``alternates`` are leaf siblings that were edited or
regenerated away (``sibling_of`` names the active turn they branch beside). An outline may
``extends`` another, replacing ``replace_turns`` texts and any top-level key it sets (``gold``,
``alternates``, ``readme`` ...). Gold fields use labels; ``gold_schema`` validates them.

Usage: ``python3 -m benchmark.fixturegen`` (write all) or ``python3 -m benchmark.fixturegen --check``.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from benchmark import gold_schema

ROOT = Path(__file__).resolve().parent.parent
OUTLINES = ROOT / "benchmark" / "outlines"
FIXTURES = ROOT / "fixtures"

ROLES = {"S": "system", "U": "user", "A": "assistant"}
TURN_GAP = 10  # seconds between active turns; alternates sit 5s before their sibling

# Conversation ids must be fixed points of the rescued core's conversation key
# (core/workflows/chatgpt_corpus.py _conversation_key: normalize_filename(id)[:48]), because the core
# builds message_unit_id from that key, not from the raw export id. Choosing ids that normalisation
# leaves unchanged keeps gold unit ids equal to the core's without copying its parsing rules here.
CONVERSATION_ID_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
CONVERSATION_ID_MAX = 48

# Gold keys whose values are labels (or lists/maps of labels) and get translated to unit ids.
_LABEL_RE = re.compile(r"^[SUA]-\d+'*$")


def role_of(label: str) -> str:
    try:
        return ROLES[label.split("-", 1)[0]]
    except KeyError:
        raise ValueError(f"label {label!r} must start with S-, U- or A-") from None


def message_id(fixture_id: str, label: str) -> str:
    # "A-20'" -> "msg-f-00-a-20p": stable, readable, and safe in a unit id.
    return "msg-" + (fixture_id + "-" + label).lower().replace("'", "p")


def unit_id(conversation_id: str, msg_id: str) -> str:
    return f"chatgpt-message:{conversation_id}:{msg_id}"


def load_outline(fixture_id: str, outlines: Path = OUTLINES) -> dict:
    """Load an outline, resolving ``extends`` (one level or more)."""
    raw = json.loads((outlines / f"{fixture_id}.json").read_text(encoding="utf-8"))
    base_id = raw.pop("extends", None)
    if base_id is None:
        return raw
    base = copy.deepcopy(load_outline(base_id, outlines))
    replace = raw.pop("replace_turns", {})
    known = {label for label, _ in base["turns"]}
    unknown = set(replace) - known
    if unknown:
        raise ValueError(f"{fixture_id}: replace_turns names unknown turns {sorted(unknown)}")
    base["turns"] = [[label, replace.get(label, text)] for label, text in base["turns"]]
    base.update(raw)
    return base


def build(outline: dict) -> tuple[list, dict, str]:
    """Return (conversation export list, gold dict, README text) for one outline."""
    fid = outline["id"]
    cid = outline["conversation_id"]
    if not CONVERSATION_ID_RE.match(cid) or len(cid) > CONVERSATION_ID_MAX:
        raise ValueError(f"{fid}: conversation_id {cid!r} must match {CONVERSATION_ID_RE.pattern}, "
                         f"at most {CONVERSATION_ID_MAX} chars (see CONVERSATION_ID_RE)")
    t0 = int(outline["start_time"])
    model = outline.get("model", "synthetic-model")

    labels: dict[str, str] = {}  # label -> message id
    order: list[str] = []
    mapping: dict[str, dict] = {}

    def node(label: str, text: str, parent: str, t: int) -> str:
        if label in labels:
            raise ValueError(f"{fid}: duplicate label {label}")
        mid = message_id(fid, label)
        role = role_of(label)
        labels[label] = mid
        order.append(label)
        mapping[mid] = {
            "id": mid,
            "parent": parent,
            "children": [],
            "message": {
                "id": mid,
                "author": {"role": role},
                "content": {"content_type": "text", "parts": [text]},
                "create_time": t,
                "metadata": {"model_slug": model} if role == "assistant" else {},
            },
        }
        mapping[parent]["children"].append(mid)
        return mid

    # Real exports start at a root node with no message.
    root = f"root-{fid.lower()}"
    mapping[root] = {"id": root, "parent": None, "children": [], "message": None}

    parent = root
    times: dict[str, int] = {}
    for i, (label, text) in enumerate(outline["turns"], start=1):
        times[label] = t0 + TURN_GAP * i
        parent = node(label, text, parent, times[label])
    current = parent

    for alt in outline.get("alternates", []):
        sib = alt["sibling_of"]
        if sib not in labels:
            raise ValueError(f"{fid}: alternate {alt['label']} names unknown sibling {sib}")
        sib_parent = mapping[labels[sib]]["parent"]
        if role_of(alt["label"]) != role_of(sib):
            raise ValueError(f"{fid}: alternate {alt['label']} has a different role from {sib}")
        node(alt["label"], alt["text"], sib_parent, times[sib] - TURN_GAP // 2)

    last_t = max(m["message"]["create_time"] for m in mapping.values() if m["message"])
    conversation = [{
        "id": cid,
        "title": outline["title"],
        "create_time": t0,
        "update_time": last_t,
        "current_node": current,
        "mapping": mapping,
    }]

    units = {label: unit_id(cid, labels[label]) for label in order}
    active = [label for label, _ in outline["turns"]]
    gold = {
        "schema": gold_schema.SCHEMA,
        "fixture": fid,
        "name": outline["name"],
        "conversation_id": cid,
        "labels": units,
        "active_path": [units[label] for label in active],
        **_translate(outline["gold"], units, fid),
    }
    texts = {units[label]: m["message"]["content"]["parts"][0]
             for label, m in ((lb, mapping[labels[lb]]) for lb in order)}
    roles = {units[label]: role_of(label) for label in order}
    gold_schema.validate(gold, texts=texts, roles=roles)

    readme = _readme(outline, units, active)
    return conversation, gold, readme


def _translate(value, units: dict, fid: str, key: str = ""):
    """Replace label strings with unit ids, recursively; dict keys too when they are labels."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            nk = units[k] if _LABEL_RE.match(k) and k in units else k
            if _LABEL_RE.match(k) and k not in units:
                raise ValueError(f"{fid}: gold names unknown label {k}")
            out[nk] = _translate(v, units, fid, k)
        return out
    if isinstance(value, list):
        return [_translate(v, units, fid, key) for v in value]
    if isinstance(value, str) and _LABEL_RE.match(value):
        if value not in units:
            raise ValueError(f"{fid}: gold names unknown label {value}")
        return units[value]
    return value


def _readme(outline: dict, units: dict, active: list) -> str:
    lines = [f"# {outline['id']}: {outline['name']}", "",
             "Generated by `python3 -m benchmark.fixturegen` from "
             f"`benchmark/outlines/{outline['id']}.json`. Edit the outline, not this directory.", "",
             "**Synthetic text only.** Written from scratch for this repo; no real conversation, "
             "export, title or quote is used.", ""]
    lines += [f"- {para}" for para in outline["readme"]]
    lines += ["", "## Turns", "", "| label | branch | text |", "|---|---|---|"]
    text = {label: t for label, t in outline["turns"]}
    text.update({a["label"]: a["text"] for a in outline.get("alternates", [])})
    for label in units:
        branch = "active" if label in active else "alternate"
        lines.append(f"| {label} | {branch} | {text[label].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def outline_ids(outlines: Path = OUTLINES) -> list[str]:
    return sorted(p.stem for p in outlines.glob("*.json"))


def render(fixture_id: str, outlines: Path = OUTLINES) -> dict[str, str]:
    """Return {filename: content} for one fixture directory."""
    conversation, gold, readme = build(load_outline(fixture_id, outlines))
    return {"conversation.json": _dump(conversation), "gold.json": _dump(gold), "README.md": readme}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--check", action="store_true", help="fail if committed fixtures differ from their outlines")
    ap.add_argument("ids", nargs="*", help="fixture ids (default: every outline)")
    args = ap.parse_args(argv)
    stale = []
    for fid in args.ids or outline_ids():
        for name, content in render(fid).items():
            path = FIXTURES / fid / name
            if args.check:
                if not path.exists() or path.read_text(encoding="utf-8") != content:
                    stale.append(str(path.relative_to(ROOT)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    if stale:
        print("stale fixtures (run python3 -m benchmark.fixturegen):\n  " + "\n  ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
