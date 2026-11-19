"""Anamnesis engine: offline core (message units, rolling Cells, FTS5 index, Embedder interface) and memory instances.

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
from .answer import Answer, Citation, IngestReport, ask, ingest
from .instance import INSTANCE_LAYOUT, INSTANCE_SCHEMA_VERSION, InstanceError, init_instance, load_manifest, validate_manifest

__all__ = [
    "MESSAGE_UNIT_SCHEMA_VERSION", "BranchStatus", "ConversationMessageBundle", "MessageUnit",
    "MessageUnitKind", "bundles_from_export", "conversation_key", "load_conversations", "normalize_key",
    "Cell", "cells_for_bundle", "Hit", "MessageIndex", "fts5_available", "Embedder", "HashingEmbedder",
    "Answer", "Citation", "IngestReport", "ask", "ingest",
    "INSTANCE_LAYOUT", "INSTANCE_SCHEMA_VERSION", "InstanceError", "init_instance", "load_manifest", "validate_manifest",
]
