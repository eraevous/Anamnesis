# Design note: segment-level retrieval and adjudicated claim recurrence

- Status: **proposed** (draft PR). Nothing here is decided. Zach decides; Kate reviews first.
- Author: `claude:anamnesis#wake-20261110-19` (scheduled run), for handoff HO-ANA-20261110-9Q72.
- Starting point: the coordinator's brief of Zach's ChatGPT thread "Anamnesis search string" (2026-08-25 to 08-27), kept in the private coordination records at `kairos/research/anamnesis/2026-08-25-search-string-thread-brief.md` (named by path only; not checkable from here). This run did not read the raw capture.
- Companion files: [benchmark fixture plan](benchmark-fixture-plan.md), [open questions for Zach](open-questions-2026-11-10.md).

## How claims in this note are labelled

| label | meaning |
|---|---|
| **[Zach]** | Zach's own words, quoted in the brief |
| **[thread]** | ChatGPT's evaluation in that thread, or Codex's diagnosis that Zach relayed; not a decision |
| **[code]** | read from the rescued reconstruction code (`anamnesis-spike-rescue@7f7aea2`) in this run |
| **[check]** | the 2026-11-09 round-trip check on the rescued offline core (synthetic fixture) |
| **[proposed]** | this note's design; provisional until Zach accepts it |

## 1. What problem this design is for

**[Zach]** "The entire purpose of the project is very much to handle meandering conversations like those, as pure retrieval and search would be highly contaminated, non-chunked semantic search would get swamped, and the passages are otherwise extremely hard to find."

The failure of the first version was not one bug. Each layer let the next one treat a guess as a fact [thread]:

1. Search returned unbounded, role-blind evidence (100% of 120 anchors, no adjacent turns, one compound query).
2. Reconstruction labelled conflicting claims `EXPLICIT`, conflated named layers and invented chronology from evidence numbering.
3. Recurrence re-ran reconstruction on a union of evidence, so claims had no identity to carry.
4. Claim-delta recurrence, once added, faithfully preserved a bad baseline: 13 claims in, 13 out, 0 revisions, with an instruction echo as the top claim.

**[proposed]** The design principle that follows: **every layer has its own object, its own invariant and its own test, and no layer may promote what the layer below gave it.** "Bad claim + excellent preservation = durable bad claim" [thread] is the failure this note is built to stop.

## 2. The objects

| object | what it is | identity | produced by |
|---|---|---|---|
| message unit | one source message, verbatim, with role and branch status | `message_unit_id` (exists [code]: `MessageUnit` in `core/corpus/message_units.py`) | offline core (stage 1, kept as is) |
| anchor | one unit that matched one retrieval objective | `(objective_id, message_unit_id)` | anchor discovery |
| segment | a locally coherent island inside a possibly incoherent conversation [thread] | `seg:<conversation_id>:<branch_tip>:<first_unit>..<last_unit>` | expansion + merging |
| segment context | the units added around a segment so it can be read, each marked as context, not evidence | belongs to its segment | context recovery |
| distillate | what the segment contributes, as per-speaker statements with verbatim quote spans | `dist:<segment_id>@<n>` | distillation |
| placement | the segment's relation to the target | one of six classes (§5) | epistemic placement |
| evidence link | a quote span in a unit, with stance and authority (§4) | `ev:<message_unit_id>:<char_start>-<char_end>` | distillation |
| claim | one atomic assertion about the target | immutable `claim_id@revision` | claim integration |

**[proposed]** A conversation is never a retrieval object. It is only the container a segment's units come from.

## 3. The pipeline

```
objectives → anchors → adjacent-turn expansion → segment merging → context recovery
          → distillation → epistemic placement → claim integration (claim-delta)
```

This is the order the thread converged on [thread]. The stages and their invariants [proposed]:

### 3.1 Diagnostic retrieval objectives (replaces the one compound query)

Each retrieval round is a set of objectives, not one query. Every objective names what it is for and what it is linked to:

| objective kind | linked to | asks |
|---|---|---|
| `seed` | the target phrase (round 0 only) | where is this topic discussed at all |
| `verify_claim` | `claim_id@rev` | is there source evidence for this exact claim |
| `find_contradiction` | `claim_id@rev` | is there evidence against it |
| `resolve_term` | a term gap (e.g. a layer name) | how did the user define or use this term |
| `recover_rejection` | `claim_id@rev` or a segment placed `rejection` | why was this rejected, and by whom |
| `find_temporal` | `claim_id@rev` or a chronology gap | when was this said, relative to what |
| `investigate_open_loop` | a gap | was this ever resolved |

- The rescue already has `RetrievalObjective` with `target_claim_ids` and a `RecurrencePolicy` [code]. This design keeps that type and adds the `kind` and the gap link.
- Invariant: every anchor carries the `objective_id` that found it, so a later layer can tell "found because we looked for support" from "found because we looked for contradiction".

### 3.2 Anchor discovery

- Lexical (FTS5) over **message units, including alternates**, as the reconciliation already assigns to stage 4 [check]. Semantic search may be added later as a second anchor source with the same output shape.
- Invariant: every anchor carries `message_unit_id`, `author_role`, `branch_status` and `conversation_id`. A hit that resolves only to a conversation is a defect.
- Retention (keep the top fraction of anchors per objective) is a parameter, not a constant. Its default is an open question for Zach (see the open-questions file).

### 3.3 Adjacent-turn expansion

- Expand each anchor by `before`/`after` units **along its own branch path** (the parent chain to the anchor, then the active children after it). Never step into a sibling branch.
- Default is non-zero; the 2026-08 run used `before=0, after=0` and lost every boundary [thread]. Starting default proposed: `before=2, after=2`. Tuning belongs to the evaluation in §7, not to this note.
- Invariant: an expanded unit records which anchor pulled it in and at what distance.

### 3.4 Segment merging

- Two expanded ranges merge **only if they overlap or touch on the same branch path**.
- They never merge because they share a conversation id, a title or a project [thread].
- So several segments from one conversation are legitimate when they are distinct islands. This is the property the hockey fixture tests.

### 3.5 Context recovery

- Add the minimum surrounding units needed to read the segment (the question a reply answers, a definition the segment relies on), up to a cap.
- Invariant: context units are marked `context`. They can be shown to a reader or a model, but they cannot be cited as support unless a later objective retrieves them as anchors in their own right.

### 3.6 Distillation

- Output is per speaker: each statement names its unit and quotes a span from **one** unit. A quote never crosses a unit boundary, so it never blurs speakers.
- Invariant: no distillate statement without a quote span; no chronology except from unit timestamps and branch order. Evidence numbering is never a time order (it was in 2026-08 [thread]).

### 3.7 Epistemic placement

Each segment gets one placement relative to the target [thread]:

| placement | meaning |
|---|---|
| `core` | states or develops the target idea itself |
| `ancestor` | an earlier idea the target grew from |
| `application` | the target applied to something else |
| `conflict` | evidence against, or a competing version |
| `rejection` | the idea, or a draft of it, explicitly turned down |
| `adjacency` | nearby but not the target |

- The rescue's `ClaimScope` (`CURRENT_CORE`, `ANCESTOR`, `APPLICATION`, `ADJACENT_IDEA`, `IMPORTED_PROPOSAL`, `GENERATED_EXTENSION`, `UNCLEAR`) [code] is close but mixes two axes: placement and authority. **[proposed]** Split them: placement stays on the segment; `IMPORTED_PROPOSAL` and `GENERATED_EXTENSION` move to the evidence's `artifact_class` (§4), where they can block canon.
- Placement is a judgement. It is recorded with its rationale and the model or person that made it, and it is evaluated against gold labels (§7).

### 3.8 Claim integration: claim-delta recurrence, adjudicated

See §5.

## 4. Evidence carries stance and authority

Every evidence link has these fields [thread → proposed shape]:

| field | values | why |
|---|---|---|
| `speaker` | `user`, `assistant`, `tool`, `system` (from `author_role` [code]) | who said it |
| `artifact_class` | `user_statement`, `assistant_reply`, `imported_draft` (text pasted from another model or person), `instruction` (prompt or setup text addressed to a model), `generated_summary`, `code_or_log`, `unclassified` (the safe default for pasted or quoted blocks, rule 6) | what kind of text it is, independent of who pasted it |
| `local_stance` | `asserts`, `proposes`, `questions`, `rejects`, `quotes`, `instructs` | what the span does where it sits |
| `acceptance_status` | `accepted` (with the unit where the user accepts), `rejected` (with the unit), `unreviewed`, `superseded` | what the user did with it |
| `canonical_eligibility` | `eligible`, or `ineligible:<reason>` | whether it may ground a claim placed `core` |

Rules [proposed]:

1. Only `user_statement` evidence, or other evidence with `acceptance_status: accepted` and a cited acceptance unit, is `eligible` for a `core` claim.
2. `imported_draft` is `ineligible:unaccepted_import` until accepted. This is what stops rejected external drafts becoming canon [thread].
3. `instruction` text is never eligible and never becomes a claim on its own. This is the instruction-echo guard: the top claim in 2026-08 was the run's own prompt echoed back [thread].
4. `generated_summary`, including any earlier output of this pipeline, is never evidence (§5, rule 3).

Two levels, so the rules don't over-reach: evidence is **admissible** when it is a quote span in a source unit and its class is not `instruction` or `generated_summary`; admissible evidence can support claims of any placement. **Canonical eligibility** is the stricter test (rule 1), and it is required only for claims placed `core`.
5. An assistant reply the user never responded to is `unreviewed`; it can support `adjacency` or `application`, not `core`.
6. **Classification is itself a judgement, so its default is the safe one** (added after the stress test). Text inside a user message is not automatically `user_statement`: a long block, a quoted block, or a block the user introduces as someone else's starts as `artifact_class: unclassified`, which is `ineligible:unclassified` until it is classified with a recorded rationale. Otherwise a misclassified paste would get the same free pass to canon that 2026-08 gave it.
7. A `user_statement` is eligible, not automatically current: a later user statement that contradicts it gives `MARK_CONFLICTING` (or `SUPERSEDE` when the user says so), ordered by timestamp (§5 rule 9).

## 5. Claim-delta recurrence, adjudicated

The rescue already has the right vocabulary [code]: `ClaimRevision` with `claim_id`, `revision`, `previous_revision_id`, `evidence_refs`, `lifecycle`, and the seven `RevisionOperation`s. The 2026-08 failure was not the vocabulary. It was that nothing enforced it [thread]. So this section is about enforcement.

### Rules [proposed]

1. **Atomic claims first.** Before any recurrence, compound claims are split until each states one thing. A claim citing evidence for only part of its text is split, not kept whole.
2. **Immutable `claim_id@revision`.** A revision is a new record pointing to its predecessor. Nothing is edited in place.
3. **Prior state is visible, never evidence.** The model sees the current ledger in a separate, labelled field. The validator rejects any revision whose `evidence_refs` cite a claim, a distillate or any generated text rather than an evidence link into a source unit. (Rule taken from the thread: "Prior state may guide preservation … but only source evidence may justify claims.")
4. **Absence never means deletion.** A claim the model does not mention in a round is carried forward with an implicit `PRESERVE` that is *logged as implicit*. No operation deletes. A claim leaves the active set only through `SUPERSEDE` (with the superseding claim) or `WEAKEN` to `UNRESOLVED` (with the evidence or guard result that weakened it).
5. **The seven operations** and what each must carry:

| operation | must cite |
|---|---|
| `PRESERVE` | nothing new (explicit or implicit) |
| `STRENGTHEN` | at least one new admissible evidence link (eligible if the claim is `core`) |
| `WEAKEN` | the evidence, guard result or adjudication that weakened it |
| `MARK_CONFLICTING` | the conflicting evidence link |
| `SUPERSEDE` | the new claim id and the evidence for it |
| `ADD` | admissible evidence for the new claim (eligible if the claim is `core`) |
| `LEAVE_UNRESOLVED` | the objective that failed to settle it |

6. **The quote guard downgrades; it does not only report.** For every evidence link on a claim: the quoted span must occur verbatim (after whitespace normalisation) in the cited unit, and must clear an overlap threshold with the claim text. A failure writes a `WEAKEN` revision automatically, with reason `quote_guard`: `EXPLICIT → INFERRED`, and `INFERRED → UNRESOLVED` if no link survives. In 2026-08 the guard flagged 7 citations and downgraded 0 claims [thread]. A guard that reports without writing a revision fails its test.
7. **Adjudicated starting state.** Round 0 claims do not enter recurrence as `EXPLICIT` on the model's say-so. They pass an adjudication gate first:
   - the quote guard (rule 6);
   - an **adversarial terminology pass**: every named term the claims depend on (layer names, tier names, project names) is resolved by a `resolve_term` objective against `user_statement` evidence; a claim whose term resolution conflicts with the user's own usage is `MARK_CONFLICTING`. This is the check that would have caught the FHC layer conflation [thread];
   - optionally, Zach's adjudication of the round-0 ledger. Whether that is required, sampled or skipped is an open question.
   Claims that fail the gate enter as `UNRESOLVED`, not `EXPLICIT`.
8. **Inert recurrence is flagged.** If a round retrieved new eligible evidence and produced zero non-`PRESERVE` revisions, the run reports `inert_recurrence` for review. 13 in, 13 out, 0 revisions [thread] is the case this catches. It is a flag, not an error: sometimes nothing should change.
9. **Chronology comes only from sources.** A claim about order or time cites unit timestamps or branch order. Evidence ids carry no order.

### The recurrence loop

```
ledger@n ──► gaps and weak claims ──► objectives (§3.1) ──► §3.2–3.7 ──► candidate revisions
                                                                              │
ledger@n+1 ◄── validator (rules 3–6, 9) ◄── quote guard ◄────────────────────┘
```

**[Zach]** asked for "recurrent" and "integrate" stages, with integrate run on a recurrence result, and a pipeline of "search phrase -> initial synthesis -> first reconstruction -> full recurrence pipeline". **[proposed]** Under this design, "integrate" is the narrative synthesis written *from* a ledger revision (timeline, construction narrative, extensions), and it is a `generated_summary`: readable, portable, never fed back as evidence. His three reconstruction outputs (evidence packet, concatenated artifact of referenced material, standalone synthesis) map onto: the evidence links with their segments, the segments rendered verbatim per speaker, and the integrate output.

## 6. How the three stage-4 gaps map onto this design

The 2026-11-09 round-trip check passed 21/24 on the rescued offline core; the three failures were all retrieval granularity [check]:

| gap [check] | where it lands | acceptance test |
|---|---|---|
| 1. A hit cites a conversation, not a message | §3.2 anchors are units | every hit carries `message_unit_id`, `author_role`, `branch_status`; a conversation-only hit fails |
| 2. Alternate-only text cannot be found | §3.2 indexes all units, including alternates; §3.3 expands along the alternate's own branch | a word that exists only in a regenerated-away reply returns ≥1 anchor labelled `alternate`; its segment never includes units from the sibling branch |
| 3. A snippet spans several speakers | §3.6 quotes never cross a unit; segments render per unit with the speaker on each | no evidence link spans two units; a rendered segment labels each unit's speaker from source metadata |

These are the same segment-boundary problem the thread names: a hit has no boundary (gap 1), a branch is a boundary search can't see (gap 2), and a snippet ignores the speaker boundary (gap 3).

## 7. Evaluate each layer separately

The thread's point [thread]: an end-to-end score hides which layer failed. Each layer gets its own metric against gold labels on the synthetic fixtures ([plan](benchmark-fixture-plan.md)):

| layer | metric (proposed) |
|---|---|
| anchor retrieval | recall@k of gold anchor units per objective; precision at the retention cut |
| boundary recovery | per gold island, overlap (IoU over unit ids) of the recovered segment |
| adjacency yield | share of gold-relevant units reached only through expansion, not as anchors |
| segment distinctness | gold islands recovered as separate segments; merges across non-overlapping ranges (target 0) |
| distillation | statements with a correct speaker and a verbatim quote span; quotes crossing units (target 0) |
| epistemic placement | confusion matrix over the six placements |
| claim use | share of `core` claims grounded only in eligible evidence; instruction-derived claims (target 0); invented chronology (target 0) |
| recurrence | guard-triggered downgrades actually written; inert-recurrence flag fires on the preserved-bad-baseline fixture; false-downgrade rate on the good-baseline fixture (F-06) |
| yield | share of claims reaching `core` (so safe defaults that leave everything `UNRESOLVED` are visible) |

## 8. What this does not change

- **The rescued offline core stays the single parser** (pipeline reconciliation, HO-MEM-20261107-3TNX). This design consumes its message units; it does not add a second parser or inventory.
- Stage 4 (FTS5, message level, branch aware) is §3.2 here, not a new subsystem.
- Cognitive-Scaffolding and the rescue repo are unchanged by this PR.
- No model provider, no embedding call and no implementation in this PR. The rescue's reconstruction code is read, not ported.

## 9. Requirements as records

The rules above are appended to `.aec/records.jsonl` as `provisional` requirement records (`R-SEG-*`, `R-EV-*`, `R-CLM-*`, `R-EVAL-*`), each `serves: P0@2`, so the Telosmith records→spec derivation (HO-AEC-20261110-DW5N) can read them later. They are proposals; none is committed.

## 10. Dissent review (AGENTS.md Drift, five questions)

1. **Which purpose might this violate?** P0@2 ("keeping who said what, what was decided and what was only inferred apart, with provenance"). Risk: an elaborate pipeline whose judgement layers (placement, eligibility) themselves become a new place where inference is promoted.
2. **Evidence for the concern.** The 2026-08 version had a claim-delta vocabulary too, and it failed because nothing enforced it. More vocabulary is not more safety.
3. **Counter-evidence.** This design puts the enforcement in validators that are checkable on synthetic fixtures (verbatim quotes, unit-bounded spans, eligibility rules, guard writes a revision), not in prompts. The judgement layers record who judged and are evaluated against gold labels.
4. **If the concern is right.** The rebuild repeats 2026-08 with better names. The fixtures would show it: the preserved-bad-baseline and instruction-echo fixtures would pass through.
5. **What to test or decide.** Build the fixtures and the validators before any model-driven stage. If the validators cannot be made to fail the five regression fixtures, stop and revisit the design.

## 11. Stress test (QAT)

Run by the note's own author in the same run, so it is not an independent review; Kate's review is the independent one (DEC-038).

- **Core idea:** enforce honesty in validators at every layer, so no layer can promote what the layer below gave it.
- **Assumptions.** *Necessary:* message units are faithful (shown by the round-trip check, on one synthetic fixture). *Unverified:* that `artifact_class` can be classified reliably; that a lexical overlap threshold separates bad citations from legitimate paraphrase; that lexical anchors can find a branch whose title gives no clue. *Value judgement:* false `core` claims are worse than missing ones.
- **Concerns and what was done:**
  1. *Classification moves the promotion problem instead of removing it.* A pasted draft misclassified as `user_statement` goes straight to canon. **Taken:** §4 rule 6 (unclassified by default, ineligible until classified with a rationale).
  2. *The hockey analogue may be too easy.* In F-00 the buried branch shares words with the query, so lexical search finds it. The real case is hard because it doesn't. **Taken:** fixture F-00b, where the branch shares no surface terms with the seed query; lexical-only retrieval is expected to fail it, which makes it the test for any semantic or term-expansion anchor source.
  3. *The quote guard could over-correct.* An overlap threshold punishes correct claims that paraphrase or abstract, and the ledger decays toward `UNRESOLVED`. The reverse of a preserved bad baseline is an eroded good one. **Taken:** fixture F-06 (a good baseline with legitimately paraphrasing citations must survive recurrence) and a false-downgrade rate in §7.
  4. *Circularity.* The same author wrote the design and the fixtures, so the fixtures may fit the design. **Taken:** the fixture plan asks for at least one regression fixture written blind, by Kate or a fresh-context agent, from the failure description alone.
  5. *Overfitting to known failures.* Every rule answers a 2026-08 failure. New failure kinds (tool output, multi-conversation contradiction, very long single turns) are not covered. **Partly taken:** listed as a gap in the fixture plan; not designed for here.
  6. *Low yield.* Safe defaults may leave early ledgers mostly `UNRESOLVED`, which is honest but may not be useful to Zach. **Not taken as a change:** reported as a metric (share of claims reaching `core`) so the trade-off is visible, and left to Zach (open question Q4).
- **Strongest counterargument:** Zach's test is "does it find the passage I remember". Synthetic fixtures can pass while real retrieval fails. Answer: true, and only his export can settle it (#44 acceptance A); the fixtures prove the validators, not usefulness.
- **The uncomfortable tweak:** the first build is a deliberately naive baseline that must *fail* every regression fixture before any validator is written, so the fixtures are shown to discriminate.

QAT: stress-test (installed skill `anthropic-skills:stress-test`, 2026-10-15 upload; run by the author on this note): concerns 1–4 taken, 5 partly taken, 6 reported rather than changed.
