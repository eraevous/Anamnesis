"""The Embedder interface (R-MEM-6), promoted from experiments/embedding-probe into the engine.

Reviewer guide:
- `model_id` is stored on every vector row, so a re-embed with another model is a new index.
- Provider and local implementations (OpenAI, WordLlama) stay in the probe: they need third-party
  packages or a key, and the engine imports the standard library only.
- `HashingEmbedder` is a deterministic, dependency-free stand-in for tests and the demo instance.
  It is NOT semantic: it hashes words into buckets, so it only finds shared words, like FTS5.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List, Protocol, Sequence, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    model_id: str
    dim: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


class HashingEmbedder:
    def __init__(self, dim: int = 64) -> None:
        self.dim = dim
        self.model_id = f"anamnesis:hashing-v1-{dim}"

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for word in re.findall(r"\w+", text.lower()):
                h = int.from_bytes(hashlib.sha256(word.encode("utf-8")).digest()[:4], "big")
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out
