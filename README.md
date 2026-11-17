# Anamnesis

Anamnesis is an early research project for **AI conversation memory and retrieval**: recovering,
connecting and reconsidering knowledge accumulated in conversations and documents. What was said,
by whom, what was decided, and what was only inferred—with the provenance kept.

It is for people building or evaluating memory systems who need continuity across long, meandering
conversations without turning an assistant's suggestion into the user's position. Today this repository
contains synthetic benchmark fixtures, validation tools and a local embedding experiment. The full
memory system is still design work; there is no end-to-end chat-export import or search application here.

## Try the evidence before the memory

Start with [the buried branch](fixtures/F-00/README.md): a conversation about a garden irrigation timer
that detours into how work finds its way through a team. The user accepts part of a suggestion and
rejects the rest. A later turn returns to the idea, and an alternate reply proposes something else.
Those distinctions are the point of the project.

To check the committed fixtures locally, use Git and Python 3.10 or newer:

```sh
git clone https://github.com/eraevous/Anamnesis.git
cd Anamnesis
python3 -m benchmark.fixturegen --check
python3 -m unittest discover -s tests -v
```

The Python commands use only the standard library, need no API key or personal chat export, and make
no network requests. The first exits silently on success; the second prints the test results. These
checks validate fixture structure, consistency and specific text properties. They do **not** run a
retrieval system or establish that the expected answers are correct.

## What is here

- **Five synthetic fixtures**, generated from readable [outlines](benchmark/outlines). Each has a
  ChatGPT-export-shaped `conversation.json`, a `gold.json` of expected answers and evidence labels,
  and a readable transcript. F-00 and [F-00b](fixtures/F-00b/README.md) cover buried ideas with and
  without shared query words. [F-10](fixtures/F-10/README.md), [F-11](fixtures/F-11/README.md) and
  [F-12](fixtures/F-12/README.md) specify message-level citations, alternate-branch retrieval and
  quotes that stay within one speaker's message.
- **A deterministic generator and gold validator** in [benchmark](benchmark). The validator checks
  references, segment boundaries, verbatim evidence quotes and evidence eligibility rules. For
  example, an unaccepted assistant suggestion cannot support a core claim in the expected answers.
  The [tests](tests/test_fixtures.py) exercise these rules; they do not implement claim extraction.
- **An isolated embedding probe** comparing SQLite FTS5 lexical retrieval, local WordLlama embeddings
  and hybrid ranking on eight invented conversations and sixteen queries. Read the
  [experiment and its recorded results](experiments/embedding-probe/README.md) separately: it needs
  additional dependencies, and its small dataset does not establish quality at scale. An OpenAI
  embedder is defined in the probe but is not called by its entry point.

To change a benchmark fixture, edit its outline rather than the generated files. The generator's
write mode regenerates the fixtures; the `--check` mode above reports drift without rewriting them.

## The direction, and the unfinished parts

The aim is knowledge that can be revisited, not just remembered: recover a passage in its branch,
keep human statements distinct from machine continuations, and let later evidence challenge earlier
claims. A useful memory should preserve a rejection or an unresolved disagreement as carefully as
an accepted idea.

The design notes describe segment retrieval, revisable claims, a memory network and a cited
“story so far.” Those are not shipped capabilities. The offline-core port, benchmark retrieval
scoring and naive baseline are still absent from this repository. Gold labels need human review;
passing the fixture tests is not evidence that a memory system can recover or interpret real chats.

## Follow the design

- [Segment retrieval and claim recurrence](docs/design/segment-retrieval-and-claim-recurrence.md):
  finding bounded passages and revising claims against source evidence.
- [Benchmark fixture plan](docs/design/benchmark-fixture-plan.md): implemented fixtures, proposed
  regressions and the boundary between validation and retrieval scoring.
- [Living memory and a memory network](docs/design/living-memory-and-memory-network.md): the broader
  direction, including overlapping message pairs called *Cells* and proposed memory revision work.
- [The saga](docs/design/saga.md): a proposed narrative layer whose citations lead back to records.
- [Purpose and status records](.aec/records.jsonl), with their [format explained here](.aec/README.md):
  the repository's versioned reasoning about what it is for. The longer-term idea remains knowledge
  versioned like code, with its sources and changes visible.
