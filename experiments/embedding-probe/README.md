# Local embedding probe (D3)

- For handoff HO-KAI-20261115-936P (private coordination records; named by id only). Run by `claude:anamnesis#wake-20261115-17`, 2026-11-15.
- **Synthetic data only.** `fixture.json` holds 8 invented conversations (32 messages, two platforms) and 16 queries with gold answers. No export, corpus or personal record was read.
- **An experiment, not project code.** The embedding packages are installed in a throwaway virtualenv and are not added to the repository (see the docstring in `probe.py`).

## What it tests

1. **Rolling Cells (D2, reading R1).** It builds `(m[i], m[i+1])` pairs, sliding by one. It checks two things: each conversation of *n* messages gives *n−1* Cells (32 messages, 8 conversations, 24 Cells), and both directions occur (`human->model`, `model->human`). Every Cell also carries `platform` and `model` (D5).
2. **One embedder interface (D3).** `Embedder` has `model_id`, `dim` and `embed(texts)`. Two implementations exist:
   - `LocalWordLlama` is the local one. It is the only one the probe runs.
   - `OpenAIEmbedder` has the same shape. It is never called here, because there is no key and no spending.
3. **Retrieval quality.** Ranking happens at conversation level and is scored with recall@1, recall@3 and MRR. It compares:
   - lexical: SQLite FTS5 BM25 over Cells, standing in for the offline core's FTS5 path;
   - dense, over each of the four §4.2 indexes (Cell, user-only, reply-only, all messages);
   - hybrid: reciprocal-rank fusion of the lexical and dense Cell rankings.

## Which local model, and why that one

This sandbox can't reach Hugging Face, so the usual local sentence-embedding models (MiniLM, BGE, nomic) couldn't be downloaded. The probe uses **WordLlama** (`l2_supercat`, 256 dimensions) because its weights ship inside the pip wheel.

WordLlama is a *static* model: it averages token embeddings taken from an LLM, with no attention at inference time. That makes it very fast and small, but weaker than a sentence-transformer. Treat these numbers as a floor for "local", not as the local option's ceiling.

Loader note: WordLlama 0.4.0 looks for its bundled tokenizer under `tokenizer/`, but the wheel ships it under `tokenizers/`. Passing `cache_dir=<package dir>` with `disable_download=True` makes it load fully offline.

## Results (2026-11-15)

| method | recall@1 | recall@3 | MRR |
|---|---|---|---|
| lexical FTS5 (Cell) | 0.875 | 0.938 | 0.896 |
| dense (Cell) | 0.938 | 1.000 | 0.958 |
| dense (user-only) | 0.875 | 0.938 | 0.922 |
| dense (reply-only) | 0.812 | 1.000 | 0.896 |
| dense (all messages) | 0.938 | 1.000 | 0.969 |
| hybrid RRF (FTS5 + dense Cell) | 0.938 | 1.000 | 0.958 |

Speed: 56 units embedded in about 7 ms on a CPU, roughly 8,000 units a second. At that rate, cost and time are not the constraint for a local static model, even on a whole export.

## What this does and doesn't show

- **Shows:** the full pipeline works locally with no network. That covers rolling Cells, the four indexes, the one interface, `model_id` per vector, and lexical and hybrid retrieval.
- **Shows:** dense retrieval found a paraphrase that lexical retrieval missed completely. For "Portugal holiday rescheduled", FTS5 returned nothing and the dense index found the Lisbon conversation.
- **Shows:** neither method found the copy/paste query, "text copied between conversations about stacking". Both ranked the *source* thread or another thread first. Detecting a paste needs the near-duplicate pass in §4.3 (hashed shingles), not embeddings. That pass was already the plan, and this result supports it.
- **Doesn't show:** quality at scale. Eight conversations with distinct topics is close to the ceiling for every method; 16 queries can't separate methods that differ by one hit. The differences in the table are one or two queries each and are **not significant**.
- **Doesn't show:** how a real local sentence-embedding model or OpenAI's embeddings compare.

## Next step proposed (not authorized here)

Rerun the same probe on a larger synthetic or public set, at least a few hundred conversations with near-topic distractors. Compare three embedders on it:

- WordLlama;
- one sentence-transformer;
- OpenAI `text-embedding-3-small`. This costs cents on synthetic text, but needs a key and Zach's go on where it runs.

The run needs an environment that can download models, such as Zach's machine or a runner with Hugging Face allowed. The probe already takes any `Embedder`.
