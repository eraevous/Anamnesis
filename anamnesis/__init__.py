"""Anamnesis engine: offline core (message units, rolling Cells, FTS5 index, Embedder interface).

Standard library only; importing it never imports a provider SDK (tests/test_engine_core.py checks).
Ported from eraevous/anamnesis-spike-rescue for HO-ANA-20261118-6H0J item 1.
"""

from .units import (
    MESSAGE_UNIT_SCHEMA_VERSION,
    BranchStatus,
    ConversationMessageBundle,
    MessageUnit,
    MessageUnitKind,
    bundles_from_export,
    conversation_key,
    load_conversations,
    normalize_key,
)
from .cells import Cell, cells_for_bundle
from .index import Hit, MessageIndex, fts5_available
from .embedder import Embedder, HashingEmbedder
from .storage import CacheStore, IndexStore, InRepoStore, open_index, sources_digest

__all__ = [
    "MESSAGE_UNIT_SCHEMA_VERSION", "BranchStatus", "ConversationMessageBundle", "MessageUnit",
    "MessageUnitKind", "bundles_from_export", "conversation_key", "load_conversations", "normalize_key",
    "Cell", "cells_for_bundle", "Hit", "MessageIndex", "fts5_available", "Embedder", "HashingEmbedder",
    "CacheStore", "IndexStore", "InRepoStore", "open_index", "sources_digest",
]
