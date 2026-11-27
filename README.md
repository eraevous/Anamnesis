# Anamnesis

Anamnesis is an early research project on **long-term memory for AI assistants**: recovering,
connecting and reconsidering what was worked out across long chat histories and documents. What
was said, by whom, what was decided, and what was only inferred—with the provenance kept.

It is for people building or evaluating memory systems who need continuity across long, meandering
conversations without turning an assistant's suggestion into the user's position.

**Status: research stage.** This repository holds synthetic benchmark fixtures and their validator, an
offline engine (standard library only) that imports a ChatGPT export and answers questions by quoting
and citing the messages it found, a command line around that engine, and a local embedding experiment.
Claim extraction, revisable claims, the memory network and the narrative layer are still design work.

## Try the evidence before the memory

Start with [the buried branch](fixtures/F-00/README.md): a conversation about a garden irrigation timer
that detours into how work finds its way through a team. The user accepts part of a suggestion and
rejects the rest. A later turn returns to the idea, and an alternate reply proposes something else.
Those distinctions are the point of the project.

To check the committed fixtures and try the engine yourself you need only Git and Python 3.10 or newer;
nothing to install:

```sh
git clone https://github.com/eraevous/Anamnesis.git
cd Anamnesis
python3 -m benchmark.fixturegen --check
python3 -m unittest discover -s tests -v
python3 -m anamnesis init /tmp/anamnesis-demo --demo
python3 -m anamnesis ask /tmp/anamnesis-demo "what did I call emergent routing?"
```

These commands use only the standard library, need no API key or personal chat export, and make no
network requests. `fixturegen --check` exits silently on success; the tests print their results. The
demo instance is built from the synthetic fixtures, and `ask` answers with quoted user messages, each
naming its source message id. The fixture checks validate structure, consistency and specific text
properties; they do **not** score retrieval or establish that the expected answers are correct.

## What is here

- **Five synthetic benchmark fixtures**, generated from readable [outlines](benchmark/outlines). Each
  has a ChatGPT-export-shaped `conversation.json`, a `gold.json` of expected answers and evidence labels,
  and a readable transcript. F-00 and [F-00b](fixtures/F-00b/README.md) cover buried ideas with and
  without shared query words. [F-10](fixtures/F-10/README.md), [F-11](fixtures/F-11/README.md) and
  [F-12](fixtures/F-12/README.md) specify message-level citations, alternate-branch retrieval and
  quotes that stay within one speaker's message.
- **A deterministic generator and gold validator** in [benchmark](benchmark). The validator checks
  references, segment boundaries, verbatim evidence quotes and evidence eligibility rules. For
  example, an unaccepted assistant suggestion cannot support a core claim in the expected answers.
  The [tests](tests/test_fixtures.py) exercise these rules; they do not implement claim extraction.
  To change a fixture, edit its outline rather than the generated files: the generator's write mode
  regenerates them, and `--check` reports drift without rewriting.
- **The engine** in [anamnesis](anamnesis), described below: export parsing, rolling Cells, a
  message-level full-text index and an extractive, cited `ask`.
- **Instances and a command line**: `anamnesis init`, `check`, `ingest` and `ask`, described below.
- **An isolated embedding probe** comparing SQLite FTS5 lexical retrieval, local WordLlama embeddings
  and hybrid ranking on eight invented conversations and sixteen queries. Read the
  [experiment and its recorded results](experiments/embedding-probe/README.md) separately: it needs
  additional dependencies, and its small dataset does not establish quality at scale. An OpenAI
  embedder is defined in the probe; its entry point builds a request payload but never sends it.
- **A Docket comparator** in [docket](docket/README.md): a small tool that checks whether a reply was
  filed once, unchanged, as the original, run against synthetic cases.

## Engine (offline core)

`anamnesis/` is the engine: standard library only, offline, and independent of any memory instance
(`anamnesis init` creates one, see Instances below).

- `units.py`: parses a ChatGPT export (ZIP, folder, sharded folder or `conversations.json`) into
  `anamnesis.message-unit.v1` units, one per node, every branch kept and labelled `active`,
  `alternate` or `unknown`. Ids are `chatgpt-message:<key>:<message id>`, where the key is the
  export's conversation id normalised (`normalize_key(id)[:48]`: lowercase, `-` and punctuation to `_`).
- `cells.py`: rolling Cells, overlapping pairs of consecutive turns on one branch path, with direction,
  platform and model.
- `index.py`: a message-level SQLite FTS5 index; hits name their unit and its branch.
- `embedder.py`: the `Embedder` interface (`model_id`, `dim`, `embed`) and a non-semantic hashing stand-in.
- `answer.py`: `ingest` exports into an index and `ask` a question. The answer is extractive: each line
  quotes one unit and names its id, speaker, conversation and branch; the active path is searched first,
  alternates only when it has no hit (labelled as not the chosen reply); no hit means no answer.

```python
from anamnesis import bundles_from_export, MessageIndex
with MessageIndex("index.sqlite") as ix:
    ix.add(bundles_from_export("fixtures/F-11/conversation.json"))
    for hit in ix.search("zephyrine backup agreed"):
        print(hit.message_unit_id, hit.branch_status, hit.snippet)
```

```python
from anamnesis import MessageIndex, ask, ingest
with MessageIndex() as ix:
    ingest(ix, ["fixtures/F-00/conversation.json"])
    print(ask(ix, "what did I call emergent routing?").render())
```

The engine's tests use synthetic data only.

## Instances

An instance is a folder one person owns: their sources, what the engine derives from them, and the
rules for both. The engine never depends on one.

```bash
python3 -m anamnesis init ~/memory           # new instance, or add the missing parts in place
python3 -m anamnesis init ~/demo --demo      # a demo instance from the synthetic fixtures
python3 -m anamnesis check ~/memory          # validate the manifest
python3 -m anamnesis ingest ~/memory ~/Downloads/chatgpt-export.zip   # import an export, build the index
python3 -m anamnesis ask ~/memory "what did we decide about backups?"  # quoted, cited answer
```

`pip install .` from a checkout installs the same commands as `anamnesis ...` (no dependencies).
`ingest` copies each export into `sources/imported/` (named by its SHA-256, so exports that share
the name `conversations.json` never collide), lists it in the manifest and rebuilds the index from
every listed source; `ask` rebuilds first if a source changed. `ask` exits 3 when nothing in the
instance matches, and then gives no answer. The index lives where the manifest's optional `index`
says: `{"backend": "cache"}` (default, `.anamnesis/index/`, git-ignored) or
`{"backend": "in-repo", "path": "index/messages.sqlite"}`; which an instance should use is still open
([index storage](docs/research/index-storage.md)). `tests/test_cold.py` is the cold test: a fresh HOME
and the package alone, `init ~/memory`, ingest a synthetic export, ask, cite
(`ANAMNESIS_COLD_INSTALL=1` makes it a real `pip install` into a new venv).

| Path | Holds |
|---|---|
| `anamnesis.instance.json` | the manifest: sources, embedder, shelves, privacy |
| `sources/` | raw exports (git-ignored by default) |
| `claims/ledger.jsonl` | the claim ledger, append-only |
| `rendered/frames/`, `rendered/films/`, `rendered/saga/` | rendered Frames, Films and the saga |
| `library/` | standalone artifacts the owner curates (an existing one is kept as is) |
| `runs/` | one record per engine run |
| `.anamnesis/` | local caches such as the FTS5 index (git-ignored; where indexes live is still open) |

`init` never overwrites, moves or deletes a file, so an existing repository can adopt the layout in
place. The manifest's `privacy` block (visibility, `sealed`, `opaque`, `profile_only`,
`agent_access`) records proposed features and is not enforced yet. `--demo` needs a repository
checkout, because the fixtures are not packaged yet.

## The direction, and the unfinished parts

The aim is knowledge that can be revisited, not just remembered: recover a passage in its branch,
keep human statements distinct from machine continuations, and let later evidence challenge earlier
claims. A useful memory should preserve a rejection or an unresolved disagreement as carefully as
an accepted idea.

What ships today is retrieval and quotation: `ask` finds messages and quotes them; it does not
extract, summarise or revise claims. The design notes describe segment retrieval, revisable claims,
a memory network and a cited "story so far"; those are not shipped capabilities. `init` creates an empty
claim ledger and folders for rendered output, but nothing writes claims or renders anything yet. Benchmark retrieval
scoring and a naive baseline are still absent. Gold labels need human review, and passing the
fixture tests is not evidence that a memory system can recover or interpret real chats.

## Follow the design

- [Segment retrieval and claim recurrence](docs/design/segment-retrieval-and-claim-recurrence.md):
  finding bounded passages and revising claims against source evidence.
- [Benchmark fixture plan](docs/design/benchmark-fixture-plan.md): implemented fixtures, proposed
  regressions and the boundary between validation and retrieval scoring.
- [Living memory and a memory network](docs/design/living-memory-and-memory-network.md): the broader
  direction, including the overlapping message pairs called *Cells* and proposed memory revision work.
- [The saga](docs/design/saga.md): a proposed narrative layer whose citations lead back to records.
- [Open questions](docs/design/open-questions-2026-11-10.md) and
  [index storage](docs/research/index-storage.md): what is still undecided.
- [Purpose and status records](.aec/records.jsonl), with their [format explained here](.aec/README.md)
  (provisional until Zach commits them): the repository's versioned reasoning about what it is for.
  The longer-term idea remains knowledge versioned like code, with its sources and changes visible.
