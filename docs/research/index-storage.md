# Where an instance's index and vectors live (HO-ANA-20261118-6H0J item 3)

Author: claude:anamnesis#wake-20261119-05
Reviewer: none yet (Codex on the PR; Kate not asked yet)
model=claude-opus-5-5 (configured; serving model may differ)
Status: **recommendation, not a decision.** Zach chooses (DEC-252: "any use of CogScaff needs to be quick. Whether that means the indices and embedded vectors in GitHub or hosted somewhere else is another question").

## What was measured

`python3 -m benchmark.storage_bench` on synthetic conversations (invented words from a fixed seed; nothing real). Raw output: [`index-storage-bench-2026-11-19.json`](index-storage-bench-2026-11-19.json). One run, a 2-CPU cloud container, Python 3 + its bundled SQLite; read the figures as orders of magnitude.

| | 500 conversations (10k units, 9 MB export) | 5,000 conversations (100k units, 92 MB export) | label |
|---|---|---|---|
| Parse export | 0.19 s | 2.8 s | measured |
| Build FTS5 index | 0.18 s | 2.5 s | measured |
| **Rebuild from sources (parse + build)** | **0.38 s** | **5.3 s** | measured |
| Index file | 12.6 MB | 126 MB | measured |
| Index, zlib (≈ what git stores per version) | 4.8 MB | 50 MB | measured |
| Cold query (new process: import, open, one query) | 37 ms | 43 ms | measured |
| Warm query (open index, median) | 0.8 ms | 6.3 ms | measured |
| Hosted fetch of the compressed index, 50 / 200 Mbit/s + 0.3 s round trips | 1.1 / 0.5 s | 8.3 / 2.3 s | model |
| Vectors, 384-dim float32 | 15 MB | 154 MB | model |
| Vectors, 1536-dim float32 | 61 MB | 614 MB | model |

Caveats: random invented words compress worse than real prose, so real indexes will compress better than this. "Cold" does not drop the OS page cache. Most of the cold figure is Python start-up and import, not SQLite.

## The options

| Option | Fresh clone cost | Speed once present | Git history | Fits |
|---|---|---|---|---|
| **A. In git, plain** (`InRepoStore`, tracked) | download every index version ever committed (≈50 MB per rebuild at 100k units) | fast | grows by the compressed index on every rebuild, forever; GitHub warns at 50 MB and refuses files over 100 MB | small, rarely rebuilt indexes only |
| **B. In git via LFS** (`InRepoStore`, LFS-tracked) | the current index only | fast | pointer files only | needs LFS on every clone; LFS storage and bandwidth quotas |
| **C. Local cache rebuilt from sources, keyed by digest** (`CacheStore`, shipped) | nothing extra; first use rebuilds (0.4 s at 10k units, 5 s at 100k) | fast | nothing | FTS index: rebuild is cheap and offline |
| **D. Hosted** (S3 or similar; not built, no spending authorized) | fetch the current index on first use (model: 1–8 s) | fast once cached locally | nothing | big artifacts that are expensive to rebuild |
| **E. Hybrid** | C for what is cheap to rebuild, B or D for what isn't | fast | pointers only | both indexes and vectors |

## The deciding fact: rebuild cost differs by artifact

- **The FTS5 index is cheap to rebuild.** Seconds at 100k units, offline, from the sources alone. Storing it anywhere costs more than rebuilding it. This holds only if the *sources* are on the machine; an instance whose sources stay out of git (the `init` layout ignores `sources/`) has nothing to rebuild from on a fresh clone.
- **Vectors are expensive to rebuild.** A provider embedder means network calls, money, and Zach's material going to a provider (outside this handoff's grant to run). They are also the largest artifact (up to ~600 MB at 100k units, 1536-dim). The tracked `vector/mosaic.index` in CogScaff is this case.

## Recommendation (for Zach to accept, change or reject)

**E, hybrid:**
1. FTS index: **C**, a local cache keyed by sources digest. Shipped in this PR as the default-ready backend; nothing is committed.
2. Vectors: stored **content-addressed by (unit content hash, embedder model id)** so a re-export re-embeds only changed units, and kept in **B (LFS)** while they fit comfortably in your LFS quota, moving to **D (hosted)** if they outgrow it. Either way a local copy is the read path, so queries stay at local speed.
3. Sources: decide whether CogScaff's sources are in its git (then C works on any clone) or fetched separately (then C needs that fetch first, and the "quick" requirement is met only after it).

**What changes:** CogScaff would stop tracking `vector/mosaic.index` in plain git (if B or D) and would not track an FTS index at all. **What doesn't:** the engine's index format, the instance layout from #15, and the read path. **Can it wait:** yes until item 4 (ingest and ask) needs a default; until then the cache backend is enough for the demo and tests.

## What is not here

- No hosted backend code and no AWS use: creating hosted storage is outside the grant. The interface (`fetch`, `destination`) is what one would implement.
- No vector store yet: the vectors figures are arithmetic, not measurements. Building the content-addressed vector store belongs with item 4 once the choice above is made.
- No real data was read. CogScaff was not touched.
