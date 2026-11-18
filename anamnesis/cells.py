"""Rolling Cells: the evidence atom (R-MEM-4, R-MEM-5).

A Cell is two consecutive messages (m[i], m[i+1]) on one branch path, sliding by one, so Cells
overlap: (u1, a2), (a2, u3), (u3, a4), ... Every branch is walked, so an edited or regenerated
alternate gets its own Cells; a Cell is `active` only if both halves are active.

Reviewer guide:
- "Consecutive" skips nodes with no text and system/developer instructions (they are not turns of
  the conversation); the pair is the nearest texted ancestor and the node. This is the shape the
  embedding probe used (experiments/embedding-probe `rolling_cells`), extended to all branches.
- `direction` is `human->model`, `model->human` or `other` (tool output, unknown roles).
- `platform` is the source platform; `model` is the model-side half's `model_slug`, else its
  `default_model_slug`, else None (R-MEM-5: recorded when the source has it).
- A Cell holds unit ids, not text copies, so a quote span always resolves inside one half.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .units import BranchStatus, ConversationMessageBundle, MessageUnit

CELL_SCHEMA_VERSION = "anamnesis.cell.v1"
_SKIP_ROLES = {"system", "developer"}
_HUMAN = {"user"}
_MODEL = {"assistant"}


@dataclass(frozen=True)
class Cell:
    cell_id: str
    conversation_id: str
    first: str  # message_unit_id of m[i]
    second: str  # message_unit_id of m[i+1]
    direction: str
    branch_status: str
    platform: str
    model: str | None

    def to_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {"schema_version": CELL_SCHEMA_VERSION, **self.__dict__}
        return {k: v for k, v in out.items() if v is not None}


def _direction(a: MessageUnit, b: MessageUnit) -> str:
    if a.author_role in _HUMAN and b.author_role in _MODEL:
        return "human->model"
    if a.author_role in _MODEL and b.author_role in _HUMAN:
        return "model->human"
    return "other"


def _branch(a: MessageUnit, b: MessageUnit) -> str:
    statuses = {a.branch_status, b.branch_status}
    if statuses == {BranchStatus.ACTIVE}:
        return BranchStatus.ACTIVE.value
    if BranchStatus.UNKNOWN in statuses:
        return BranchStatus.UNKNOWN.value
    return BranchStatus.ALTERNATE.value


def cells_for_bundle(bundle: ConversationMessageBundle, platform: str = "chatgpt") -> List[Cell]:
    by_node = {u.node_id: u for u in bundle.messages}
    turn = {u.node_id for u in bundle.messages if u.text and u.author_role not in _SKIP_ROLES}

    def texted_parent(node_id: str) -> MessageUnit | None:
        seen = {node_id}
        parent = by_node[node_id].parent_node_id
        while parent is not None and parent not in seen:
            seen.add(parent)
            if parent in turn:
                return by_node[parent]
            unit = by_node.get(parent)
            if unit is None:  # parent node has no message (e.g. the export's root)
                return None
            parent = unit.parent_node_id
        return None

    cells: List[Cell] = []
    for unit in sorted(bundle.messages, key=lambda u: u.source_index):
        if unit.node_id not in turn:
            continue
        prev = texted_parent(unit.node_id)
        if prev is None:
            continue
        model_half = unit if unit.author_role in _MODEL else prev if prev.author_role in _MODEL else None
        model = (model_half.model_slug or model_half.default_model_slug) if model_half else None
        cells.append(
            Cell(
                cell_id=f"cell:{prev.message_unit_id}|{unit.message_unit_id}",
                conversation_id=bundle.conversation_id,
                first=prev.message_unit_id,
                second=unit.message_unit_id,
                direction=_direction(prev, unit),
                branch_status=_branch(prev, unit),
                platform=platform,
                model=model,
            )
        )
    return cells
