"""Message units: one record per node of a ChatGPT export, every branch kept.

Ported from eraevous/anamnesis-spike-rescue `src/core/corpus/message_units.py` (WIP 7f7aea2), with the
two helpers it borrowed from the provider-coupled parser (`_content_text`, now `content_text`;
`normalize_filename`, now `normalize_key`)
inlined, so this module imports the standard library only (HO-ANA-20261118-6H0J item 1).

Reviewer guide:
- `message_unit_id` is `chatgpt-message:<conversation key>:<message id>`. The conversation key is
  NOT the raw export id: it is `normalize_key(id)[:48]` (`conversation_key`). This is the
  "normalised-key finding" of HO-ANA-20261116-CFNM. An id such as `conv_synth_rt` is unchanged by
  it; one such as `Abc-123` becomes `abc_123`. Ids stay stable across exports either way, because
  the key depends on the export's conversation id only, never on position, unless the id is missing.
- Every node with a message becomes a unit, active or not. `branch_status` is `active` when the node
  lies on the path from `current_node` to the root, `alternate` otherwise, and `unknown` when the
  export names no `current_node`.
- Behaviour is unchanged from the rescue except where noted in `conversation_key`.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List



MESSAGE_UNIT_SCHEMA_VERSION = "anamnesis.message-unit.v1"
MESSAGE_UNIT_MANIFEST_SCHEMA_VERSION = "anamnesis.message-unit-manifest.v1"


class MessageUnitKind(str, Enum):
    USER_PROMPT = "user_prompt"
    ASSISTANT_RESPONSE = "assistant_response"
    SYSTEM_INSTRUCTION = "system_instruction"
    TOOL_OUTPUT = "tool_output"
    OTHER_MESSAGE = "other_message"


class BranchStatus(str, Enum):
    ACTIVE = "active"
    ALTERNATE = "alternate"
    UNKNOWN = "unknown"


_ROLE_KINDS = {
    "user": MessageUnitKind.USER_PROMPT,
    "assistant": MessageUnitKind.ASSISTANT_RESPONSE,
    "system": MessageUnitKind.SYSTEM_INSTRUCTION,
    "developer": MessageUnitKind.SYSTEM_INSTRUCTION,
    "tool": MessageUnitKind.TOOL_OUTPUT,
}


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unit_id(conversation_id: str, message_id: str) -> str:
    return f"chatgpt-message:{conversation_id}:{message_id}"


# Optional fields, in the order `to_dict` writes them; a None value is left out of the dict.
_UNIT_OPTIONAL = (
    "external_conversation_id", "parent_node_id", "active_branch_index", "transcript_index",
    "transcript_start", "transcript_end", "author_name", "text", "content_hash", "content_type",
    "create_time", "update_time", "model_slug", "model_slug_source", "default_model_slug",
    "default_model_slug_source", "project_id", "project_id_source", "project_name",
    "message_type", "message_source", "request_id", "turn_exchange_id", "status", "end_turn",
)
_UNIT_OPTIONAL_INTS = ("active_branch_index", "transcript_index", "transcript_start", "transcript_end")
_BUNDLE_OPTIONAL = (
    "external_conversation_id", "current_node_id", "create_time", "update_time",
    "default_model_slug", "project_id", "project_id_source", "project_name", "memory_scope",
    "conversation_template_id",
)


@dataclass(frozen=True)
class MessageUnit:
    message_unit_id: str
    conversation_id: str
    message_id: str
    node_id: str
    author_role: str
    unit_type: MessageUnitKind
    branch_status: BranchStatus
    source_index: int
    child_node_ids: List[str] = field(default_factory=list)
    source_type: str = "chatgpt_message"
    external_conversation_id: str | None = None
    parent_node_id: str | None = None
    active_branch_index: int | None = None
    transcript_index: int | None = None
    transcript_start: int | None = None
    transcript_end: int | None = None
    author_name: str | None = None
    text: str | None = None
    content_hash: str | None = None
    content_type: str | None = None
    create_time: str | int | float | None = None
    update_time: str | int | float | None = None
    model_slug: str | None = None
    model_slug_source: str | None = None
    default_model_slug: str | None = None
    default_model_slug_source: str | None = None
    project_id: str | None = None
    project_id_source: str | None = None
    project_name: str | None = None
    message_type: str | None = None
    message_source: str | None = None
    request_id: str | None = None
    turn_exchange_id: str | None = None
    status: str | None = None
    end_turn: bool | None = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": MESSAGE_UNIT_SCHEMA_VERSION,
            "message_unit_id": self.message_unit_id,
            "source_type": self.source_type,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "node_id": self.node_id,
            "author_role": self.author_role,
            "unit_type": self.unit_type.value,
            "branch_status": self.branch_status.value,
            "source_index": self.source_index,
            "child_node_ids": list(self.child_node_ids),
        }
        payload.update(
            {key: value for key in _UNIT_OPTIONAL if (value := getattr(self, key)) is not None}
        )
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MessageUnit":
        schema_version = payload.get("schema_version")
        if schema_version != MESSAGE_UNIT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported message-unit schema: {schema_version}")
        optional = {key: payload.get(key) for key in _UNIT_OPTIONAL}
        for key in _UNIT_OPTIONAL_INTS:
            if optional[key] is not None:
                optional[key] = int(optional[key])
        return cls(
            message_unit_id=str(payload["message_unit_id"]),
            source_type=str(payload.get("source_type") or "chatgpt_message"),
            conversation_id=str(payload["conversation_id"]),
            message_id=str(payload["message_id"]),
            node_id=str(payload["node_id"]),
            child_node_ids=[str(value) for value in payload.get("child_node_ids", [])],
            author_role=str(payload["author_role"]),
            unit_type=MessageUnitKind(str(payload["unit_type"])),
            branch_status=BranchStatus(str(payload["branch_status"])),
            source_index=int(payload["source_index"]),
            **optional,
        )


@dataclass(frozen=True)
class ConversationMessageBundle:
    conversation_id: str
    title: str
    messages: List[MessageUnit]
    external_conversation_id: str | None = None
    current_node_id: str | None = None
    create_time: str | int | float | None = None
    update_time: str | int | float | None = None
    default_model_slug: str | None = None
    project_id: str | None = None
    project_id_source: str | None = None
    project_name: str | None = None
    memory_scope: str | None = None
    conversation_template_id: str | None = None
    # node id -> parent node id for nodes that carry no message (they become no unit). Kept so a
    # branch path can be walked through them (anamnesis addition; omitted from to_dict when empty).
    skipped_node_parents: Dict[str, str | None] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schema_version": MESSAGE_UNIT_SCHEMA_VERSION,
            "conversation_id": self.conversation_id,
            "title": self.title,
            "messages": [message.to_dict() for message in self.messages],
        }
        payload.update(
            {key: value for key in _BUNDLE_OPTIONAL if (value := getattr(self, key)) is not None}
        )
        if self.skipped_node_parents:
            payload["skipped_node_parents"] = dict(self.skipped_node_parents)
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ConversationMessageBundle":
        if payload.get("schema_version") != MESSAGE_UNIT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported message-unit schema: {payload.get('schema_version')}"
            )
        return cls(
            conversation_id=str(payload["conversation_id"]),
            title=str(payload["title"]),
            skipped_node_parents=dict(payload.get("skipped_node_parents") or {}),
            messages=[MessageUnit.from_dict(item) for item in payload["messages"]],
            **{key: payload.get(key) for key in _BUNDLE_OPTIONAL},
        )


def _active_branch(mapping: Dict[str, Any], current_node: Any) -> Dict[str, int]:
    if current_node in (None, ""):
        return {}
    node_id = str(current_node)
    reverse_path: List[str] = []
    seen: set[str] = set()
    while node_id and node_id not in seen:
        seen.add(node_id)
        reverse_path.append(node_id)
        node = mapping.get(node_id)
        if not isinstance(node, dict) or node.get("parent") in (None, ""):
            break
        node_id = str(node["parent"])
    return {value: index for index, value in enumerate(reversed(reverse_path))}


def _project_fields(conversation: Dict[str, Any]) -> tuple[str | None, str | None]:
    explicit = _optional_string(conversation.get("project_id"))
    if explicit:
        return explicit, "conversation.project_id"
    template_id = _optional_string(conversation.get("conversation_template_id"))
    if (
        conversation.get("memory_scope") == "project_enabled"
        and template_id
        and template_id.startswith("g-p-")
    ):
        return template_id, "conversation.conversation_template_id"
    return None, None


def _transcript_spans(
    mapping: Dict[str, Any], current_node: str | None, active_indexes: Dict[str, int]
) -> Dict[str, tuple[int, int, int]]:
    if current_node is not None:
        ordered_ids = [
            node_id
            for node_id, _ in sorted(active_indexes.items(), key=lambda item: item[1])
        ]
    else:
        sortable: List[tuple[str, Dict[str, Any]]] = [
            (str(key), node)
            for key, node in mapping.items()
            if isinstance(node, dict)
        ]
        sortable.sort(
            key=lambda item: (
                (item[1].get("message") or {}).get("create_time") is None,
                (item[1].get("message") or {}).get("create_time") or 0,
            )
        )
        ordered_ids = [key for key, _ in sortable]

    spans: Dict[str, tuple[int, int, int]] = {}
    cursor = 0
    transcript_index = 0
    for node_id in ordered_ids:
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author = message.get("author") or {}
        content = message.get("content") or {}
        if not isinstance(author, dict) or not isinstance(content, dict):
            continue
        role = str(author.get("role") or "unknown").lower()
        if role == "system":
            continue
        text = content_text(content).strip()
        if not text:
            continue
        if transcript_index:
            cursor += 1
        prefix = f"{role.upper()}: "
        start = cursor + len(prefix)
        end = start + len(text)
        spans[node_id] = (transcript_index, start, end)
        cursor = end
        transcript_index += 1
    return spans


def build_conversation_message_bundle(
    conversation: Dict[str, Any], conversation_id: str
) -> ConversationMessageBundle:
    mapping = conversation.get("mapping") or {}
    if not isinstance(mapping, dict):
        mapping = {}
    current_node = _optional_string(conversation.get("current_node"))
    active_indexes = _active_branch(mapping, current_node)
    transcript_spans = _transcript_spans(mapping, current_node, active_indexes)
    active_known = current_node is not None
    external_id = _optional_string(
        conversation.get("id") or conversation.get("conversation_id")
    )
    project_id, project_source = _project_fields(conversation)
    project_name = _optional_string(conversation.get("project_name"))
    conversation_default_model = _optional_string(
        conversation.get("default_model_slug")
    )
    messages: List[MessageUnit] = []
    skipped_parents: Dict[str, str | None] = {}

    for source_index, (mapping_key, node_value) in enumerate(mapping.items()):
        if not isinstance(node_value, dict):
            continue
        message = node_value.get("message")
        if not isinstance(message, dict):
            skipped_parents[str(mapping_key)] = _optional_string(node_value.get("parent"))
            continue
        node_id = str(mapping_key)
        message_id = _optional_string(message.get("id")) or node_id
        author = message.get("author") or {}
        metadata = message.get("metadata") or {}
        content = message.get("content") or {}
        if not isinstance(author, dict):
            author = {}
        if not isinstance(metadata, dict):
            metadata = {}
        if not isinstance(content, dict):
            content = {}
        role = str(author.get("role") or "unknown").lower()
        text = content_text(content).strip() or None
        observed_model = _optional_string(metadata.get("model_slug"))
        message_default_model = _optional_string(metadata.get("default_model_slug"))
        default_model = message_default_model or conversation_default_model
        message_project = _optional_string(metadata.get("gizmo_id"))
        if not (
            conversation.get("memory_scope") == "project_enabled"
            and message_project
            and message_project.startswith("g-p-")
        ):
            message_project = None
        effective_project = project_id or message_project
        effective_project_source = project_source or (
            "message.metadata.gizmo_id" if message_project else None
        )
        if active_known:
            branch_status = (
                BranchStatus.ACTIVE
                if node_id in active_indexes
                else BranchStatus.ALTERNATE
            )
        else:
            branch_status = BranchStatus.UNKNOWN
        transcript_span = transcript_spans.get(node_id)

        messages.append(
            MessageUnit(
                message_unit_id=_unit_id(conversation_id, message_id),
                conversation_id=conversation_id,
                external_conversation_id=external_id,
                message_id=message_id,
                node_id=node_id,
                parent_node_id=_optional_string(node_value.get("parent")),
                child_node_ids=[
                    str(value) for value in (node_value.get("children") or [])
                ],
                author_role=role,
                author_name=_optional_string(author.get("name")),
                unit_type=_ROLE_KINDS.get(role, MessageUnitKind.OTHER_MESSAGE),
                branch_status=branch_status,
                source_index=source_index,
                active_branch_index=active_indexes.get(node_id),
                transcript_index=(transcript_span[0] if transcript_span else None),
                transcript_start=(transcript_span[1] if transcript_span else None),
                transcript_end=(transcript_span[2] if transcript_span else None),
                text=text,
                content_hash=_hash_text(text) if text is not None else None,
                content_type=_optional_string(content.get("content_type")),
                create_time=message.get("create_time"),
                update_time=message.get("update_time"),
                model_slug=observed_model,
                model_slug_source=(
                    "message.metadata.model_slug" if observed_model else None
                ),
                default_model_slug=default_model,
                default_model_slug_source=(
                    "message.metadata.default_model_slug"
                    if message_default_model
                    else (
                        "conversation.default_model_slug"
                        if conversation_default_model
                        else None
                    )
                ),
                project_id=effective_project,
                project_id_source=effective_project_source,
                project_name=project_name,
                message_type=_optional_string(metadata.get("message_type")),
                message_source=_optional_string(metadata.get("message_source")),
                request_id=_optional_string(metadata.get("request_id")),
                turn_exchange_id=_optional_string(metadata.get("turn_exchange_id")),
                status=_optional_string(message.get("status")),
                end_turn=(
                    bool(message["end_turn"])
                    if message.get("end_turn") is not None
                    else None
                ),
            )
        )

    return ConversationMessageBundle(
        conversation_id=conversation_id,
        external_conversation_id=external_id,
        title=str(conversation.get("title") or conversation_id),
        current_node_id=current_node,
        create_time=conversation.get("create_time"),
        update_time=conversation.get("update_time"),
        default_model_slug=conversation_default_model,
        project_id=project_id,
        project_id_source=project_source,
        project_name=project_name,
        memory_scope=_optional_string(conversation.get("memory_scope")),
        conversation_template_id=_optional_string(
            conversation.get("conversation_template_id")
        ),
        messages=messages,
        skipped_node_parents=skipped_parents,
    )


def bundle_hash(bundle: ConversationMessageBundle) -> str:
    encoded = json.dumps(
        bundle.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _hash_text(encoded)


def active_message_fingerprints(bundle: ConversationMessageBundle) -> List[str]:
    active = sorted(
        (
            message
            for message in bundle.messages
            if message.branch_status is BranchStatus.ACTIVE
        ),
        key=lambda message: message.active_branch_index or 0,
    )
    return [
        f"{message.message_unit_id}:{message.content_hash or '-'}" for message in active
    ]


def summarize_bundle(bundle: ConversationMessageBundle) -> Dict[str, Any]:
    role_counts = Counter(message.author_role for message in bundle.messages)
    kind_counts = Counter(message.unit_type.value for message in bundle.messages)
    models = {m.model_slug for m in bundle.messages if m.model_slug}
    default_models = {m.default_model_slug for m in bundle.messages if m.default_model_slug}
    projects = {m.project_id for m in bundle.messages if m.project_id}
    payload: Dict[str, Any] = {
        "message_count": len(bundle.messages),
        "active_message_count": sum(
            message.branch_status is BranchStatus.ACTIVE
            for message in bundle.messages
        ),
        "role_counts": dict(sorted(role_counts.items())),
        "unit_type_counts": dict(sorted(kind_counts.items())),
        "model_slugs": sorted(models),
        "default_model_slugs": sorted(default_models),
        "project_ids": sorted(projects),
    }
    for key, value in (
        ("default_model_slug", bundle.default_model_slug),
        ("project_id", bundle.project_id),
        ("project_id_source", bundle.project_id_source),
        ("project_name", bundle.project_name),
    ):
        if value is not None:
            payload[key] = value
    return payload


def content_text(content: Dict[str, Any]) -> str:
    """Readable text from the known ChatGPT content shapes (from the rescue's openai_export)."""
    parts = content.get("parts") or []
    text_parts: List[str] = []
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
        elif isinstance(part, dict):
            for key in ("text", "content", "caption"):
                value = part.get(key)
                if isinstance(value, str):
                    text_parts.append(value)
                    break
    direct_text = content.get("text")
    if isinstance(direct_text, str):
        text_parts.append(direct_text)
    return "\n".join(p for p in text_parts if p.strip())



def normalize_key(name: str) -> str:
    """Lowercase, path-safe slug (the rescue's `normalize_filename`, unchanged)."""
    sanitized = re.sub(r"[\\/:*?\"<>|]", "_", name)
    sanitized = sanitized.replace(" ", "_").replace("-", "_")
    sanitized = re.sub(r"[^\w]+", "_", sanitized)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized.lower()


def conversation_key(conversation: Dict[str, Any], index: int) -> str:
    """The key unit ids are built from (the rescue's `_conversation_key`, unchanged).

    With an export id: `normalize_key(id)[:48]`, stable across exports. Without one it falls back to
    position and title, which is NOT stable across exports; such units are only as stable as the export.
    """
    stable_id = conversation.get("id") or conversation.get("conversation_id")
    if stable_id:
        return normalize_key(str(stable_id))[:48]
    title = normalize_key(str(conversation.get("title") or f"conversation_{index}"))[:32]
    return f"{index:04d}_{title}"


def load_conversations(export_path: Path) -> List[Dict[str, Any]]:
    """Conversations from an export ZIP, an unpacked export folder or a bare conversations.json.

    Sharded exports (`conversations-*.json`) are read in name order.
    """
    export_path = Path(export_path)
    if export_path.is_file() and export_path.suffix == ".json":
        return _as_list(json.loads(export_path.read_text(encoding="utf-8")))
    if export_path.is_dir():
        shards = sorted(export_path.glob("conversations-*.json"))
        if shards:
            out: List[Dict[str, Any]] = []
            for shard in shards:
                out.extend(_as_list(json.loads(shard.read_text(encoding="utf-8"))))
            return out
        return _as_list(json.loads((export_path / "conversations.json").read_text(encoding="utf-8")))
    with zipfile.ZipFile(export_path) as zf:
        names = zf.namelist()
        shards = sorted(n for n in names if Path(n).name.startswith("conversations-") and n.endswith(".json"))
        if shards:
            out = []
            for name in shards:
                with zf.open(name) as f:
                    out.extend(_as_list(json.load(f)))
            return out
        name = next((n for n in names if Path(n).name == "conversations.json"), None)
        if name is None:
            raise FileNotFoundError(f"no conversations.json in {export_path}")
        with zf.open(name) as f:
            return _as_list(json.load(f))


def _as_list(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("conversations file must hold a JSON list")
    return [item for item in payload if isinstance(item, dict)]


def bundles_from_export(export_path: Path) -> List[ConversationMessageBundle]:
    """Parse an export into one bundle per conversation, in export order."""
    return [
        build_conversation_message_bundle(convo, conversation_key(convo, index))
        for index, convo in enumerate(load_conversations(export_path))
    ]
