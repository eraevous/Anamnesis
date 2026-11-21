# Design note: living memory, a memory network, and the first full run

- Status: **direction adopted (D1), design only.** Zach answered D1–D5 on 2026-11-15 (§7). Nothing is built. The R-MEM requirements become committed only when Zach merges the PR that records them; D4 is open and under research.
- Revised by `claude:anamnesis#wake-20261115-17` for handoff HO-KAI-20261115-936P: §2 (rolling Cells, family), §3 (nothing applies automatically until D4), §4.5 (embeddings), §7 (answers). Two of the readings applied here are the orchestrator's (coordinator's) and are **unconfirmed** (R1, R2 in §7).
- Author: `claude:anamnesis#wake-2026-11-15-05`, a scheduled run, for handoff HO-KAI-20261115-7MV6 (private coordination records; named by id only).
- Basis: two messages from Zach (2026-11-14, quoted in §0), and his confirmation of the coordinator's reading that this is design input, not a build request.
- Builds on, and does not replace: [segment retrieval and claim recurrence](segment-retrieval-and-claim-recurrence.md) (the claim ledger, evidence classes, quote guard), [the saga](saga.md) (narrative layer that descends to records), [benchmark fixtures](benchmark-fixture-plan.md).
- **Synthetic examples only.** No export, conversation or personal record was read for this note. The real run in §4 is not authorized; it is a separate decision for Zach (§7).

## How claims are labelled

| label | meaning |
|---|---|
| **[Zach]** | Zach's words, quoted |
| **[reported]** | what public reporting says about a third-party product; not checked by this run |
| **[seed]** | Zach's vocabulary in Cognitive-Scaffolding `core_seed.yml` v1.0 |
| **[existing]** | already proposed in this repo's other design notes |
| **[proposed]** | this note's design; provisional |
| **[unconfirmed reading]** | an interpretation of Zach's words that he hasn't confirmed |

## 0. What Zach asked

- **[Zach]** "This is what we aspire to with Anamnesis, along with structured and multifaceted information storage, retrieval, and recombination", said of a summary of ChatGPT's "Dreaming V3".
- **[Zach]** "I think would also posit some form of network, large or small, that circulates, reads, evaluates, discusses, and other processes on the Anamnesis memory, which I imagine would start like a full Cognitive Scaffolding run on the most recent OpenAI export, complete with semantic partitioning, a multi-indexed vector database, and some other operations on the embedded memories, both combined and separate from machine continuations. Possibly on a thread, film, other dimensions organization, too. With some manner of ability to retrieve a tree, or even "strands" woven with copy/paste or other inter-thread or inter-family cell."

Five questions follow from these: what to take from Dreaming V3 (§1), which units to build on (§2), what the network is (§3), what the first run looks like (§4), how we'd know it works (§5). §6 covers what this means for Zach, §7 what he decides.

## 1. Dreaming V3: what to take, where to differ

### What it does [reported]

- After chats, a background process synthesizes a living profile of the user, asynchronously.
- Facts age: "you're going to X" later becomes "you went to X".
- The user can review the summary, edit it, or switch back to saved memories.
- OpenAI reports internal-eval figures (factual recall, preference adherence) and about 5× less retrieval compute than retrieval-augmented generation. Those are its own evals on its own data; they don't transfer to Anamnesis (§5).
- Reporting notes the design limits the audit trail: the user sees the synthesis, not how each sentence got there.

### What to take [proposed]

| Dreaming V3 idea | Anamnesis form |
|---|---|
| Background synthesis after each session | A **curator** job (§3) that reads new Cells and proposes claim revisions. It never writes a profile directly. |
| A living profile | **Frames** [seed]: bounded, generated memory objects rendered *from* the claim ledger at a stated revision. A profile is one Frame per kind of memory, not one blob. |
| Facts that age | Every claim carries a **time scope** (`valid_from`, `valid_until`, `tense: planned / ongoing / done / lapsed`). Aging is a `SUPERSEDE` revision that cites the later evidence, not a rewrite. A plan whose date passes with no evidence becomes `lapsed` via `WEAKEN`, never silently `done`. |
| Editable summary | Zach edits a Frame; the edit is recorded as an **owner revision** on the claims behind that sentence (highest authority, cites the edit itself as evidence). The Frame is then re-rendered. |
| Cheaper retrieval by reading the synthesis first | Read the Frame or saga chapter first, then descend to evidence through its locators [existing: saga §3]. Same saving, but every sentence still resolves to a source. |

### Where Anamnesis deliberately differs [proposed]

1. **Provenance for every synthesized fact.** Each sentence in a Frame points to `claim_id@rev`, and each claim to evidence links (quote spans in source units) with speaker, source, time and what it replaced [existing: §4–5 of the segment note]. A Frame sentence without a resolvable locator is a defect, not a style choice.
2. **Reversible history, no overwrite.** Claims are append-only revisions. Any Frame can be re-rendered at any past ledger revision ("what did the memory say on 1 November?"). Switching back is a query, not a restore.
3. **Several kinds of memory, not one profile.** Separate claim scopes (shelves), each with its own Frame and its own aging rules. In standard terms the shelves hold semantic memory (claims about people, projects and decisions) and some procedural memory (preferences, agents' rules); the source units and Cells they cite are the episodic memory:

| shelf | holds | ages by |
|---|---|---|
| people | who someone is to Zach, roles, how to work with them | role changes; explicit statements |
| projects | goals, state, scope, what's parked | status changes in records |
| decisions | what was decided, by whom, superseded by what | ledger supersession only |
| agents (lanes) | what each lane (an agent) is for, its rules, who it pairs with | lane-rule changes |
| preferences | how Zach wants work done | his explicit restatement only |

4. **Generated text is never evidence** [existing]. A Frame, a saga chapter or an agent's discussion can guide where to look; only quote spans in source units justify a claim. This is the guard against the "durable bad claim" failure.
5. **Zach's words and machine continuations stay distinguishable** at every layer (§4.3). Dreaming V3 synthesizes from both without a visible line between them [reported, inferred from the limited audit trail].

## 2. Units: Zach's vocabulary as the data model

Design the store around the units Zach already defined [seed] rather than new ones:

| unit [seed] | definition [seed] | storage form [proposed] |
|---|---|---|
| **Cell** | "An atomic interaction—one input (prompt) and one output (response)." | **Rolling Cell** (D2): `cell:<call_unit_id>→<response_unit_id>`, keyed by the two stable message-unit ids (positions `i`, `i+1` are kept for traversal only, since alternate replies share them), any two consecutive messages on one branch path, sliding by one, with `direction` (`human→model` or `model→human`), `platform` and `model`. Built over two **message units** (one message each [existing]). |
| **Thread** | "A linear sequence of Cells forming a coherent dialogue or process." | one root-to-tip branch path through Cells |
| **Fork / Branch / Node** | a new Thread from a Cell or Frame; a named fork; the forking Cell | from the export's `mapping`: alternates and edits already carry `branch_status` [existing]. A Node is a Cell with >1 child. |
| **Frame** | "A bounded, context-rich memory object, composed of selected Cells and metadata." | the rendered memory objects of §1 (and saga chapters), always stamped with the ledger revision they came from |
| **Film** | "A stylized or annotated version of a Thread, often organized thematically or narrative[ly]." | an ordered list of Cell or segment references with annotations, stored separately from the sources. A **strand** is a Film whose Cells come from more than one Thread. |

**D2, answered: rolling Cells.** **[Zach]** "these should be rolling cells, i.e. call response pairs that do overlap. So most every call will also be a response sort of thing, like a chain. That way we can look at more easily what passed through from often what passes from the initiating text and then what comes out of that, whether it's the human processing it or the robot processing it."

How this note applies it (the coordinator's reading R1, **[unconfirmed reading]**):

- A Cell is the pair `(m[i], m[i+1])` along one branch path, sliding by one. Every message except a Thread's first and last is the response in one Cell and the call in the next, so a Thread of *n* messages has *n−1* Cells.
- Each Cell records its `direction`: `human→model` (a model transforms Zach's text) or `model→human` (Zach transforms a model's text). A chain of Cells shows what passed from the initiating text into each reply, and who transformed it.
- At a fork, the call message starts one Cell per child, so the Fork tree is a tree of Cells. Because the Cell id is the pair of message-unit ids, each alternate reply is its own Cell and a citation names the exact reply, as the branch-specific evidence contract requires [existing: segment note §3].
- **The Cell is the evidence atom; quote spans stay inside one half.** A claim cites a Cell and a span in its call or its response, never a span across both. That keeps Zach's words separable from the reply [existing: gap 3 of the round-trip check] and keeps the quote guard per message.
- Indexes (§4.2) can key on either: the Cell index embeds the pair; the user-only and reply-only indexes embed single messages. The synthetic probe (§4.5) builds rolling Cells and checks the *n−1* count and both directions.

**D5, confirmed: family means model family** (Claude, ChatGPT, others). **[Zach]** "4, yes". Every Cell records `platform` and `model` (when the export has it), so cross-family links are a filter, not a new structure.

## 3. The memory network [proposed]

What Zach describes is a set of agents that circulate, read, evaluate and discuss the memory. The design question is what they may change. The answer here: **they propose; validators and Zach dispose.** The network is jobs inside the Anamnesis lane, not new Kairos lanes.

### Roles (start with two, add only when a test shows the need)

| role | reads | may write | cadence |
|---|---|---|---|
| **Curator** (first) | new Cells since its last run; the current ledger as a labelled, non-evidence field | candidate revisions (`ADD`, `STRENGTHEN`, `SUPERSEDE` with evidence) | after each ingest |
| **Auditor** (first) | claims and their evidence; clocks | mechanical revisions only: quote-guard `WEAKEN`, `lapsed` time scopes, broken-locator flags | daily, cheap, no model needed for most checks |
| Challenger (later) | claims placed `core`; Frames | `find_contradiction` objectives and `MARK_CONFLICTING` proposals; a written challenge | weekly, or on any Frame Zach is about to rely on |
| Librarian (later) | ledger, Films, saga | re-rendered Frames, Films and strands (all `generated_summary`) | after accepted revisions |
| Steward (later) | everything that leaves the local store | blocks; never edits content | every export or share |

The Challenger is the natural seat for the other model family (the role of ChatGPT (Kate), the cross-family reviewer, in Kairos), so that the agent checking a claim isn't the one that proposed it.

### What no role may do

- delete anything; promote a claim to `core` without eligible evidence; edit a source unit; cite a Frame, Film, saga chapter or another agent's discussion as evidence; send source text to a provider not approved for it (§4.5).

### How the network's own work is recorded

- Each job run writes a **run record**: role, model, inputs (`ledger@n`, Cell range), outputs (revision ids), validator results, cost.
- Discussions between roles are kept as artifacts of class `agent_discussion` (a kind of `generated_summary`): readable, linkable from a revision's rationale, never evidence.
- **Auto-applied: nothing, until D4 is answered.** **[Zach]** "I think we need to research that because I don't have a clear answer on when, whether, and which revisions should apply automatically." Every revision, the Auditor's mechanical ones included, lands as `proposed` until a review rule accepts it.
  - **This hold overrides the automatic application in the other notes.** Segment note §5 rule 6 (R-CLM-4) still requires the quote guard to *write* a `WEAKEN` revision rather than only report; on the memory ledger that revision is written as `proposed` and takes effect when accepted. Benchmark fixture F-05 is read the same way: it passes when each wrong claim has a `WEAKEN` or `MARK_CONFLICTING` revision recorded within one round, and fails if the guard only reports. Whether a synthetic test run may apply its own proposals in a throwaway ledger is part of D4 (**[proposed]**, not decided). The evidence packet for that decision is [D4: automatic revisions](../research/d4-automatic-revisions.md); its recommendation is a proposal, not adopted.
- A network that runs and changes nothing on new evidence raises the `inert_recurrence` flag [existing].

## 4. The first run: a full Cognitive-Scaffolding pass (design, not authorized)

This is the shape of the run Zach imagines over his most recent ChatGPT export. It runs **only after** D1, D3 and the pending public-port decision (§7). Every stage is local; the corpus and every index live outside every repository.

### 4.1 Pipeline

```
export ─► inventory (aggregate counts only) ─► offline core parse (all mapping nodes, branch_status, stable ids)
       ─► Cells + Threads + Fork tree ─► semantic partitioning (segments inside Threads)
       ─► indexes (4.2) ─► embedding operations (4.3) ─► links: copy/paste + cross-thread (4.4)
       ─► round-0 claims per shelf (adjudicated, segment note §5) ─► Frames, Films, saga chapters
```

- **Inventory first.** A read-only, user-run command that prints counts (conversations, messages, branches, date range, total characters) and a cost estimate for each later stage. Nothing else runs until Zach has seen those numbers.
- **Semantic partitioning** = the segment design [existing §3]: islands of coherent topic inside meandering Threads, found by boundaries along one branch path, never across siblings.

### 4.2 Indexes (the "multi-indexed" store)

| index | key | for |
|---|---|---|
| lexical (SQLite FTS5) | message unit, including alternates | exact phrases, names, rare terms [existing] |
| vector: **user turns only** | Zach's half of each Cell | what *he* said and how his ideas cluster |
| vector: **replies only** | the model's half | what machines proposed |
| vector: **Cell** | both halves together | "where did this exchange happen" |
| vector: **segment** | a segment's units | topic-level retrieval, the saga's descent target |
| metadata | thread, time, branch, platform, model, project tag, shelf | filtering, ordering, Films |

Each vector index stores the embedding model id and version per row, so a re-embed is a new index, not an overwrite.

### 4.3 Operations on the embeddings, combined and separate

- **Combined vs separated clusters.** Cluster the Cell index and the user-only index; where they disagree, a model's replies are pulling the apparent topic away from what Zach was asking. That gap is itself a finding ("machine drift").
- **Origin of an idea.** For a term or concept, find its earliest occurrence in the user-only index and in the replies-only index. If the reply came first and Zach adopted it later, the claim's evidence must say so (acceptance, not authorship).
- **Near-duplicates across Threads.** Exact and near-exact repeats (hashed shingles first, embeddings second) mark copy/paste between Threads, including from one model family to another. A pasted block starts as `unclassified` [existing rule 6] until its source Cell is found.

### 4.4 Retrieval shapes: tree and strand

- **Tree:** for a query, return the Fork tree of each matching Thread, pruned to the paths that reach a hit, with alternates labelled. This is what "retrieve a tree" asks for, and it is the only honest way to show a regenerated or edited exchange.
- **Strand:** a chain of Cells linked across Threads (and families) by copy/paste, explicit back-reference or high similarity, ordered by time. Each link records its kind and confidence; a similarity-only link is shown as weaker than a copy/paste link. A strand is saved as a Film, so it can be annotated and revisited.

### 4.5 Privacy and provider questions (must be answered before the real run)

- **Where embeddings are computed (D3, answered).** **[Zach]** "test local, like let's see how well that works. I also am okay and authorizing using OpenAI's embeddings as per the original cognitive scaffolding with pretty much full retention."
  - Both sit behind one `Embedder` interface (`model_id`, `dim`, `embed(texts)`); every vector row stores `model_id`, so switching models is a new index.
  - "Full retention" is read as: Kairos keeps the full text, the vectors and the provenance on its own side, as Cognitive-Scaffolding did. It does not ask the provider to retain anything (coordinator's reading R2, **[unconfirmed reading]**).
  - The local test so far: [embedding probe](../../experiments/embedding-probe/README.md). It is a small synthetic test; the stronger local models need a run where model downloads are allowed.
  - The OpenAI authorization covers embedding calls. The first full run on Zach's export is still a separate go.
- Where the store lives and how it is deleted.
- Whether any Frame may be shown to another agent or model, and which shelves (people especially) stay local-only.
- These follow the existing rule: synthetic fixtures in repositories, real material only where Zach puts it.

## 5. Evaluation: our own tests

OpenAI's figures are its internal evals on its users' data; they say nothing about Anamnesis. We measure on two sets: the synthetic fixtures (always, in CI) and, once authorized, Kairos's own coordination records, which have built-in ground truth (decision ids, supersession links, timestamps).

| test | question form | pass means | gold from |
|---|---|---|---|
| **Recall** | "What did we decide about X, and when?" | correct answer **and** a locator that resolves and supports it | decision-ledger lines |
| **Staleness** | ask about something later superseded (synthetic example: rule A adopted on day 1, replaced by rule B on day 10) | answers B, says it replaced A, cites both | supersession links |
| **Preference adherence** | a task where a stated preference applies (synthetic: "never stack PRs") | output follows it; a checklist grader scores it | the preferences shelf |
| **Attribution** | "Who proposed X, and who accepted it?" | separates the person's words from model proposals correctly | evidence `speaker` + `acceptance_status` |
| **Reversibility** | render a Frame at `ledger@n`, apply revisions, roll back | identical output at `@n` | the ledger itself |
| **Provenance precision** | sample Frame sentences | every locator resolves; the quoted span supports the sentence | quote guard |
| **Network value** | run with and without the Curator and Challenger | revisions they add are accepted at a useful rate, false downgrades stay low | review outcomes |

Baselines to beat: FTS alone; plain vector retrieval over Cells; Frames without descent. Retrieval cost is measured too, so the "cheaper than RAG" claim is checked here rather than borrowed.

## 6. Ramifications for Zach

- **What changes:** Anamnesis gets one clear product shape: shelves of claims with provenance, Frames rendered from them, a small network that proposes changes, and retrieval by tree and strand. The existing segment, recurrence and saga designs become the parts it is built from, not separate efforts.
- **What it costs:**
  - Building: the offline core port first (already pending), then indexes, then two network roles. Each is a reviewable PR with synthetic tests.
  - Running: embedding the whole export once, plus small per-ingest runs. The figure is unknown until the inventory prints the corpus size; the inventory is the first thing to build for that reason.
  - His time: reviewing proposed revisions. Sampling keeps this bounded, but early on he would see most of them.
- **What can wait:** Challenger, Librarian and Steward roles; visuals; the people shelf (the most sensitive); cross-family strands beyond ChatGPT until other exports exist.
- **What depends on it:** the saga's generation [existing]; Today and other surfaces that would read Frames; any future "what did I decide about X" question answered with sources.
- **What doesn't change:** nothing personal enters a repository; no provider is called on his material before the separate go for the first full run; no claim reaches `core` without his words or his acceptance.

## 7. Decisions for Zach

Answered 2026-11-15 11:03 CT on the decision sheet (verbatim record in the private coordination records, `kairos/decisions/answers-2026-11-15.md`).

| id | question | answer | status |
|---|---|---|---|
| D1 | Adopt this direction (Dreaming-style synthesis with provenance, reversible history and shelves; a propose-only network)? | "D1, yes." | **adopted**; R-MEM records committed on merge of this revision |
| D2 | Is the evidence atom the message unit, or the Cell? | "these should be rolling cells, i.e. call response pairs that do overlap …" | **rolling Cells** (§2); reading R1 unconfirmed |
| D3 | Embeddings: local model or which provider, under what retention? | "test local … I also am okay and authorizing using OpenAI's embeddings as per the original cognitive scaffolding with pretty much full retention." | **local first, OpenAI authorized**, one interface (§4.5); reading R2 unconfirmed |
| D4 | Which revisions may apply automatically? | "I think we need to research that …" | **open**; nothing applies automatically meanwhile; [evidence packet](../research/d4-automatic-revisions.md) |
| D5 | Does "family" mean model family? | "4, yes" | **confirmed** |
| (existing) | Public port of the offline core + FTS5 to Cognitive-Scaffolding (pending since 2026-11-07). | | still pending; blocks the first build step |

Readings for Zach to confirm:
- **R1 (D2):** a Cell is `(m[i], m[i+1])`, sliding by one, with direction recorded. Yes / No / Adjust.
- **R2 (D3):** "full retention" means Kairos keeps full text, vectors and provenance on its side; it says nothing about OpenAI's own retention. Yes / No / Adjust.

## 8. Dissent review (AGENTS.md Drift: this proposes a new subsystem)

1. **Which purpose might this violate?** Anamnesis's purpose is recovering knowledge with provenance kept. A network that writes to memory could turn interpretation into adoption without anyone deciding.
2. **Evidence for the concern:** the 2026-08 pipeline did exactly this: preserved bad claims faithfully [existing]. Background synthesis is the same pattern at larger scale, and runs unwatched.
3. **Counter-evidence:** the design forbids generated text as evidence, makes the network propose-only, and (until D4 is answered) lets nothing apply automatically. The ledger is append-only, so a bad run can be rolled back.
4. **If the concern is right:** Frames would drift from Zach's words while still looking sourced, because locators would point to evidence that supports a weaker reading. The provenance-precision and attribution tests (§5) are built to catch this; the Challenger exists for it.
5. **What to test or decide:** run the Curator alone against fixtures F-00 and F-06 before any real data; measure false `core` promotions (target 0) and accepted-revision rate. D1 and D4 are Zach's.

QAT: stress-test applied by hand (the installed skill was not invoked in this scheduled run). Concerns taken: (a) network scope creep, answered by starting with two roles; (b) aging can silently mark plans as done, answered by `lapsed`; (c) copy/paste detection can mistake a quoted block for the user's own statement, answered by the `unclassified` default. Concern rejected: "drop the vector store, FTS is enough": the user-only vs reply-only separation Zach asked for needs embeddings, and FTS stays as the exact-match path.
