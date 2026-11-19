# Anamnesis

Anamnesis is about recovering, connecting and reconsidering the knowledge that has accumulated in
conversations and documents: what was said, by whom, what was decided, and what was only inferred, with the
provenance kept. It is a placeholder for now. The first experiment's protocol is
`experiments/anamnesis-001/protocol.md` in the private coordination records (relay-kit-private; named here
by path only), and the direction it feeds is the Knowledge Engine idea in the Kairos intake: repositories
operated by agents in which knowledge is versioned like code. Purpose and status are in
`.aec/records.jsonl` (provisional until Zach commits them).

## Engine (offline core)

`anamnesis/` is the engine: standard library only, offline, and independent of any memory instance
(Zach's own memory repository is one instance; `anamnesis init` creates one, see Instances below).

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

Ported from the private rescue repository (anamnesis-spike-rescue) with synthetic tests only:
`python3 -m unittest discover -s tests`.

## Instances

An instance is a folder one person owns: their sources, what the engine derives from them, and the
rules for both. The engine never depends on one.

```bash
python3 -m anamnesis init ~/memory           # new instance, or add the missing parts in place
python3 -m anamnesis init ~/demo --demo      # a demo instance from the synthetic fixtures
python3 -m anamnesis check ~/memory          # validate the manifest
```

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
