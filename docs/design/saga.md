# Design note: the saga, a "story so far" retrieval spine

- Status: **proposed**. Nothing here is accepted. Zach decides (AU-owner).
- Author: claude:anamnesis#wake-20261111-03, a scheduled run, 2026-11-11.
- Basis: Zach, 2026-11-10 20:07 CT: "I would also like to have a saga or the story so far that pulls forward some narrative structure against which more detailed Anamnesis records could be more easily retrieved when needed." Recorded in the private coordination ledger as DEC-205, which leaves shape, owner and generation method open.
- Companion: `docs/design/segment-retrieval-and-claim-recurrence.md` (PR #1, branch `ana/segment-retrieval-design`, not merged). This note builds on it but doesn't depend on its merge. It is based on main, because PR #1 has no review yet (stacking rule, DEC-155).
- First draft of an actual saga: private coordination records, `kairos/saga/the-story-so-far.md` (named by path only; not checkable from this public repo).

## How claims are labelled
- **[Zach]**: Zach's words, quoted.
- **[record]**: read from a coordination record (ledger line, handoff, status file).
- **[PR1]**: from the segment-retrieval design note in PR #1.
- **[proposed]**: this note's design choice. Open to change.

## 1. The problem

Detailed records are many and flat. A question like "why did we stop stacking PRs?" or "when did Anamnesis become a lane again?" currently means grepping hundreds of ledger lines, handoffs and status files, or rereading long threads. [record: the private decision ledger had 205 lines on 2026-11-11.] A flat search returns hits without the situation they came from. A summary gives the situation but loses the detail, and summaries tend to settle disagreements that were never settled [record: Abigail's convention input §5, "summaries should preserve conflicts rather than silently settle them"].

The saga is the middle layer: a short narrative that says what happened and when, where every sentence points down to the records that support it. You read the story to find the place, then descend to the evidence.

## 2. Objects [proposed]

| Object | What it is | Size |
|---|---|---|
| **Saga** | The whole story for one scope (Kairos, or one project). One file per scope. | grows; one page per few weeks |
| **Arc** | A theme that runs across chapters (e.g. "dispatch and receipts", "memory"). Arcs are tags, not containers. | a name + one line |
| **Chapter** | A dated span with a beginning and an end. The retrieval unit. | 150–400 words |
| **Beat** | One sentence or bullet inside a chapter. The citation unit. Every beat carries at least one locator. | one line |
| **Locator** | A pointer down to a record: a ledger id (`DEC-127`, `CH-040`, `HO-ANA-20261111-WZ0B`), a PR at a commit (`relay-kit#33@a60c822`), a file path with section (`kairos/briefs/2026-11-10-consolidation.md §Priority`), or a conversation segment (`seg:<conversation>:<first unit>-<last unit>`, PR1 §2). | |

Each chapter has these fields, in this order:

1. **Span**: start and end date (UTC or CT, stated). Chapters don't overlap; arcs can.
2. **What happened**: 3–8 beats.
3. **Turning points**: the beats after which the direction changed, each with the decision or event that caused it.
4. **Decisions**: ledger ids adopted, proposed or rejected in the span, with status as recorded.
5. **Threads and PRs**: the places the work was done.
6. **Open questions**: what the span left undecided, by id where one exists.
7. **Kept dissent**: disagreements that are still live, each side with its own locator. Never merged into one sentence.
8. **Descend to**: the 3–10 records to read first for detail.
9. **Known gaps**: what the records for this span don't show (work done in chats no record links to, sources that survive only as summaries). The saga inherits every blind spot of the records; this field says where they are.
10. **Provenance**: who generated the chapter, when, and the record cursor read up to (ledger id and commit). A narrator chooses the turning points, so the choice is attributed.

A machine-readable header per chapter (YAML inside an HTML comment, so the Markdown still reads cleanly) lists `span`, `arcs`, `decisions`, `prs`, `handoffs`, `challenges`. Retrieval uses the header; people read the prose.

## 3. Retrieval descends from the saga [proposed]

```
question
  → match chapters (by date words, ids, arc tags, entity names; then lexical on beats)
  → pick ≤3 chapters, read their beats
  → follow the locators on the matching beats
  → records (ledger lines, handoffs, PR bodies)
  → conversation segments when a record cites one (PR1 §3 pipeline: anchors → segments)
```

- The saga narrows where to look. It is **never the answer**. An answer cites the record a beat points to, not the beat. [proposed; follows PR1 §3.5: context is never citable]
- If no chapter matches, fall back to flat search over records, and log the miss. Misses tell us where the saga is thin.
- This reverses the usual direction (records → summary). Retrieval starts at the coarse layer and descends [Zach: "against which more detailed Anamnesis records could be more easily retrieved"].

## 4. Generation: derived, not hand-kept [proposed]

- The saga is a **derived artifact**. Records outrank it. When they disagree, the record wins and the beat is revised.
- It is regenerated or extended at **checkpoints**: the end of a week, a convention, a successor handoff, or when a lane goes dormant. Not on every commit.
- Generation reads only records (ledger lines, handoffs, outputs, status files, PR metadata). It doesn't read raw chat exports. Where a record cites a conversation, the beat carries that record's locator, and Anamnesis descends from there.
- Closed chapters are append-only. A later record that changes a closed chapter's story gets a **revision line** under the chapter, using PR1's claim-delta operations (§5 there): `SUPERSEDE`, `WEAKEN`, `CONTRADICT`, and so on. It is not a silent rewrite. Example: "2026-11-10: DEC-188 put the expansion items in this chapter on hold (WEAKEN 'next')".
- A generated beat with no locator fails the check (§7). So does a locator that doesn't resolve.

## 5. Dissent is kept [proposed]

Following Abigail's convention input §5 [record]: a chapter's "Kept dissent" lists each live disagreement with both positions and their locators. The generator must not resolve them. A dissent leaves the list only when a record resolves it (a decision, a challenge set to `resolved`), and the revision line names that record.

## 6. Other uses

- **Convention input** (DEC-202) [record]: Zach called it the "Federalist Papers" input. The saga gives the convention a shared, sourced account of the last weeks to argue from.
- **Reinitialization seed** [record: convention input 2026-11-10-zach-dialup, Zach 18:24 CT: "'reinitialization ThreadSeeds' might become more common practice"]. A reinit seed has a narrative part. The last 1–2 chapters plus the open-questions lists are that part, generated from records so they're current when used.
- **Successor handoffs**: thread-seed and thread-distill (QAT-Kits) can cite chapters instead of retelling the story.

## 7. Checks [proposed]

A small stdlib script, `saga_check`, to be built later (not in this PR):
1. Every beat has ≥1 locator.
2. Every locator resolves: the id exists in the ledger, the path and section exist, the PR exists.
3. Every decision listed in a chapter's header is dated within the span (or flagged as a carried-over status change).
4. Kept-dissent items have a locator on each side.
5. No chapter body over 400 words.

**Does it help?** One synthetic fixture (F-S1), in the PR1 fixture plan's style: about 60 synthetic ledger lines over three invented weeks, a saga generated from them, and 12 questions with known answer records. Compare three arms: descent from the full saga, descent from the chapter headers alone (no prose), and flat lexical search over the same records. The headers-only arm tests whether the narrative earns its cost or a date-and-id index does the work. Measure: answer record found in top-3, records read before finding it, and wrong-chapter rate. Naive baseline first. Under DEC-200 [record], this can ship as an asserted benefit, with the mechanism being "narrows the search space by time and theme". F-S1 is how we'd find out if it doesn't.

## 8. How this fits PR #1 (9Q72)

- A chapter is a coarse **objective** for PR1 §3.1: descent gives the anchor search a date window and a set of ids, which helps with the buried-branch fixture (F-00) when the conversation title is misleading.
- A beat is a coarse **claim**. Revision lines use PR1's claim-delta vocabulary. The `inert_recurrence` flag (R-CLM-7) has a counterpart: a checkpoint that read new records and changed no chapter gets flagged for review.
- Eligibility (PR1 §4): generated summaries are not eligible as `core` evidence. The saga is a generated summary, so **it can never be cited as evidence**, only used to find evidence. This is the same rule from the other side.

## 9. What this does not do
- It doesn't replace the ledgers, handoffs or Telosmith records.
- It doesn't decide anything. A beat that reports a decision cites it.
- It doesn't contain personal or employer material. The Kairos saga lives in the private records. This public repo holds the design and synthetic fixtures only.

## 10. Dissent review (AGENTS.md Drift, five questions)

1. **What purpose might this violate?** P0@2 ("keeping who said what, what was decided and what was only inferred apart, with provenance"). A narrative layer could blur those: a fluent story reads as authority.
2. **Evidence for the concern:** the 2026-11-05 synthesis found a recurring pattern, "the provenance layer gets built before the test of whether it saves anyone anything" [record: kairos/synthesis-2026-11-05.md §A]. A saga is one more layer.
3. **Counter-evidence:** Zach asked for it directly [Zach]. DEC-200 allows shipping on an asserted benefit with a stated mechanism [record]. The saga stays non-citable (§8), so it can't become authority.
4. **If the concern is right:** chapters get read as truth, and dissent gets smoothed out in later regenerations. The cost is wrong answers that look sourced.
5. **What to test, escalate or decide:** run F-S1 before automating generation. Zach decides whether the saga is a lane deliverable or a coordinator checkpoint duty (Q-S1 below). Keep the "never citable" rule as a requirement record (R-SAGA-2).

## 11. Open questions (for Zach; none decided)
- **Q-S1 Owner:** who regenerates at checkpoints, the Anamnesis lane or the coordinator? Proposed: the lane writes the generator and the coordinator runs it at checkpoints.
- **Q-S2 Scope:** one Kairos saga, or one per project plus a Kairos one that links to them? Proposed: one Kairos saga until a project's chapters exceed about a third of it.
- **Q-S3 Granularity:** chapters by calendar week, or by turning point? The first draft uses turning points; weeks are simpler to generate.
- **Q-S4 Public form:** a public saga for a project (e.g. Telosmith's story) would need a privacy pass. Not proposed now.

## 12. Requirements as records
Appended to `.aec/records.jsonl` as provisional requirements serving P0@2: R-SAGA-1 (every beat carries a resolving locator), R-SAGA-2 (saga never citable as evidence), R-SAGA-3 (closed chapters change only by revision lines), R-SAGA-4 (kept dissent has a locator per side and leaves only by a resolving record).

---
QAT: stress-test (installed skill, 2026-10-15 upload), run by the author, not independent. Taken: (1) records have blind spots, so chapters get a Known gaps field; (2) narrator selection bias, so a Provenance field; (3) the reversal "a date-and-id index may do all the work", so F-S1 gets a headers-only arm. Kept as stated: the backfire "fluent story read as truth" (answered by §8 non-citability and R-SAGA-2, not removed). Reported, not resolved: the synthesis pattern "provenance layer before the test" (§10). QAT: thread-map / thread-seed: not run. The saga is built from records, not from a thread transcript, and no successor thread was in scope this run.
