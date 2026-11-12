# Open questions for Zach (2026-11-10)

For handoff HO-ANA-20261110-9Q72. **None of these is decided.** Each lists what is known and what the options are; the choice is Zach's.

## Q1. Retention default and supervised tuning

- **Known [Zach]:** "Running 60% actually ended up pretty optimal, as retention must also be weighted vs precision." He suggested ROC-AUC, F-score and message-level features (entropy, length, words per sentence) for a "supervised" tuning.
- **Context [thread]:** the 2026-08 run that misbehaved kept 100% of anchors with no expansion, so the 60% finding came from a different configuration than the one diagnosed.
- **Questions:**
  - Keep 60% as the starting default for anchor retention per objective?
  - Should tuning be supervised against the synthetic fixtures' gold anchors first, and against labelled real data only later (which would need your labelling and your export)?
  - Is retention per objective, or per round?

## Q2. When to use a stronger model for synthesis

- **Known:** the design keeps judgement in three places: distillation, placement and the integrate narrative. The validators do not depend on model strength.
- **Options:**
  - (a) one model for everything;
  - (b) a stronger model only for placement and adjudication;
  - (c) a stronger model only for the final integrate narrative;
  - (d) decide from the layer-by-layer scores, where a layer that fails its fixtures gets the stronger model.
- **Question:** which, and is there a cost ceiling per run?

## Q3. Visuals

- **Known [Zach]:** "I would also love some visuals, such as clustering … for the corpus and for the target topic and artifacts".
- **Questions:**
  - Which view first: the whole corpus, the segments for one target, or the claim ledger over revisions?
  - Should visuals wait until retrieval passes its fixtures? The design note assumes yes.

## Q4. Adjudicating the starting state (raised by this design)

- **Known [thread]:** "bad claim + excellent preservation = durable bad claim". The design adds an automatic adjudication gate: the quote guard plus an adversarial terminology pass.
- **Question:** should your adjudication of the round-0 ledger be required, sampled (for example every Nth run), or skipped when the automatic gate passes?
