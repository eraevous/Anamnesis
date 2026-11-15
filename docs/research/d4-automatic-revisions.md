# D4: when, whether and which memory revisions may apply automatically

- **Status: evidence packet, for Zach to decide.** The recommendation in §4 is a proposal. Until D4 is answered, **every revision is proposed only**, the Auditor's mechanical ones included.
- For handoff HO-KAI-20261115-936P (private coordination records; named by id only). Written by `claude:anamnesis#wake-20261115-17` on 2026-11-15.
- **The question.** **[Zach]** "I think we need to research that because I don't have a clear answer on when, whether, and which revisions should apply automatically."
- **Method.** A research subagent ran a web search on 2026-11-15 and fetched most of the sources itself; secondary sources are flagged. Nobody has re-checked the citations since, so spot-check them before quoting any outside this repository. No personal material was used.

## 1. What other systems do

### LLM memory products and libraries

All of them write automatically. The user gets control only after the fact.

| system | how it revises | review before applying? | after-the-fact control |
|---|---|---|---|
| Mem0 ([paper](https://www.emergentmind.com/papers/2504.19413)) | An LLM chooses ADD / UPDATE / DELETE / NOOP against similar memories | no | API only |
| Zep / Graphiti ([blog](https://www.getzep.com/blog/beyond-static-knowledge-graphs/)) | An LLM finds conflicting edges and sets `expired_at`. Bi-temporal: `valid_at`/`invalid_at` for real-world time, `created_at`/`expired_at` for database time. Nothing is deleted | no | history stays queryable |
| A-MEM ([paper](https://arxiv.org/pdf/2502.12110)) | A new note makes an LLM rewrite neighbouring notes in place | no | none: originals are replaced |
| Letta / MemGPT ([blog](https://www.letta.com/blog/memory-blocks)) | The agent edits memory blocks through tools; "sleep-time" agents rewrite blocks in the background | no | read-only blocks can be changed only by the developer |
| LangMem ([docs](https://langchain-ai.github.io/langmem/background_quickstart)) | The agent writes in conversation, or a background manager writes after it | no | none described |
| ChatGPT ([OpenAI, Apr 2025](https://openai.com/index/memory-and-new-controls-for-chatgpt/)) | Saved memories plus automatic chat-history insights; a "Memory updated" notice appears | no | view and delete saved memories. A secondary source ([TechRadar, Oct 2025](https://www.techradar.com/ai-platforms-assistants/chatgpt/chatgpt-is-smarter-now-that-its-learned-to-forget-a-huge-memory-upgrade-is-coming)) reports re-ranking instead of erasing, plus rollback to earlier versions |
| Claude ([help center](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context)) | Memory is saved as topics during chats | no | view, edit and delete; incognito chats write nothing |

No primary source for ChatGPT "Dreaming" was found. The [design note](../design/living-memory-and-memory-network.md) §1 already labels it [reported].

### Knowledge bases that use bots

These communities approve *a kind of edit*, not each edit.

- **English Wikipedia** ([BRFA](https://www.wikipedia.org/wiki/Wikipedia:BRFA)):
  - The Bot Approvals Group approves each bot task.
  - It first grants a limited trial (25, 50, 100 or 500 edits, or a set number of days) and reviews the result.
  - Contested tasks need community consensus before the request is filed. Uncontroversial tasks can be fast-tracked.
- **Wikidata** ([bot requests](https://www.wikidata.org/wiki/Wikidata:Requests_for_permissions/Bot)):
  - Each task needs its own request and a test run, for example 50 to 250 edits.
  - The bot flag comes only after consensus.
  - An [analysis of 683 requests](https://opensym.org/blog/2020/02/26/approving-automation-analyzing-requests-for-permissions-of-bots-in-wikidata/) found that some stalled because people could not agree on how the bot should work.
- **OpenStreetMap** ([automated-edits code](https://wiki.openstreetmap.org/wiki/Automated_Edits_code_of_conduct)):
  - Before a bot runs: document the algorithm, use a dedicated account, tag every changeset `mechanical=yes`, start small and keep backups for reverts.
  - Any wider scope needs new approval. "Blanket approval for vague 'fixing'" is not possible.
  - Exceptions: obvious typos, reverting vandalism, fixing your own work.
  - The code warns that reverts are hard and can damage nearby edits.

## 2. Risks the evidence points to

1. **Poisoning arrives through ordinary content.**
   - In [Rehberger's SpAIware](https://thehackernews.com/2024/09/chatgpt-macos-flaw-couldve-enabled-long.html) (2024), instructions hidden in a document were written into ChatGPT memory and persisted across later chats.
   - [MINJA](https://arxiv.org/abs/2503.03704) plants memories with ordinary queries alone.
   - With [AgentPoison](https://neurips.cc/virtual/2024/poster/94715), poisoning under 0.1% of memory gave at least 80% attack success, with almost no visible loss in normal performance.
2. **The model's own confidence is not a safe gate.** In [arXiv 2601.05504](https://arxiv.org/html/2601.05504v1), gating on trust scores failed both ways:
   - with one model, all 23 candidates were rejected;
   - with another, 54 poisoned entries were accepted at full trust.

   The authors call the score "a confidence filter, not a security filter".
3. **Memory feeds sycophancy.**
   - In [Jain et al., CHI 2026](https://arxiv.org/abs/2509.12517), user memory profiles caused the largest rise in agreement sycophancy.
   - [PersistBench](https://arxiv.org/html/2602.01146) reports a median of 97.8% memory-induced sycophancy failures across 18 models.

   Automatically strengthening claims from the same conversations the model is shaping would risk a self-reinforcing loop.
4. **Human review is not free.** Automation bias leads to missed errors and wrongly accepted suggestions, in experts as well as novices; training does not prevent it, and divided attention makes it worse ([Parasuraman & Manzey 2010](https://trid.trb.org/View/1085175)). A queue of every revision invites rubber-stamping.
5. **In-place rewrites lose the audit trail.** A-MEM is the counter-example. Anamnesis's append-only ledger (R-MEM-2) already prevents this.

## 3. What follows for Anamnesis

- **"Whether" isn't all-or-nothing.** The products auto-apply everything. Wikipedia, Wikidata and OpenStreetMap auto-apply nothing until a *kind* of edit has passed a reviewed trial. The second model fits Anamnesis's purpose (provenance and Zach's authority) better.
- **"Which" should be decided by edit type and the source of the input, not by model confidence.** Mechanical checks that never read meaning (a quote that no longer matches its source, a broken locator, a date that has passed) are the bot-policy "obvious typo" class. Revisions that interpret meaning (ADD, STRENGTHEN, SUPERSEDE from new conversation) are the class the poisoning and sycophancy results warn about.
- **"When" is after a trial, per type, with a way back.** That means a tagged identity per job, a run record, and a one-step revert. Anamnesis gets the revert for free from the append-only ledger.

## 4. Recommendation (proposal; Zach decides)

Use a **graduated, per-revision-type ladder**. Each step requires Zach's explicit approval for that type, and a type can move up only after a reviewed trial.

| stage | what happens | entry condition |
|---|---|---|
| 0. proposed (now) | Everything lands as `proposed`; Zach or a sampled review accepts it | default for every type |
| 1. shadow | The job runs and records what it *would* apply; nothing changes; agreement with Zach's decisions is measured | a type is nominated |
| 2. trial | A small batch (for example 25 to 50 revisions) is applied, tagged `mechanical=yes` and the job id, and Zach reviews all of it | shadow agreement high, zero harmful errors |
| 3. auto with audit | That type applies on its own; each run writes a run record; Zach sees a daily digest and reviews a random sample (say 1 in 10) | trial passed; Zach's yes for this type |
| demote | Any harmful error returns the type to stage 1 | automatic |

The types that could ever reach stage 3 come from the first slice below; the rest stay proposed. Within the first slice, the order from lowest to highest risk:

1. **Broken-locator flag:** the flag only; no claim changes.
2. **Quote-guard `WEAKEN`:** the cited span no longer matches its source unit.
3. **Time-scope `lapsed`:** a plan's date passed with no evidence. It is never marked `done`.

**Stays proposed indefinitely, until a new decision:**
- `ADD`, `STRENGTHEN` and `SUPERSEDE` from conversation;
- any promotion to `core`;
- anything on the people or preferences shelves;
- anything whose evidence comes from a pasted block or an imported draft;
- anything a model proposed from text it also generated.

The guards hold regardless of stage:
- Gate on the source of the input and on mechanical checks, never on a model's self-reported confidence.
- One identity per job.
- Rollback is a query, never a restore (R-MEM-2).
- Keep the review queue small by sampling, so that review stays real.

## 5. What would change this recommendation

- If shadow runs show the mechanical types disagree with Zach often, they stay at stage 0 or 1.
- If review volume at stage 0 overwhelms him before any type reaches stage 3, sampling has to start earlier. That is a review-design question, not a reason to auto-apply meaning-changing revisions.
- If a future evaluation (design note §5, "network value") shows the Curator's proposals are accepted almost always over a long trial, stage 1 could be opened for `ADD` on low-risk shelves. That would be a new decision.

## 6. Decision for Zach

**D4:** adopt the ladder in §4, with the first slice limited to the three mechanical types. Answer Yes, No or Adjust. Until he answers, nothing applies automatically.

QAT: a stress-test was applied by hand (the installed skill was not invoked in this scheduled run). Concern taken: "approve by type" could hide a type that is mechanical in name but reads meaning. Answered by limiting the first slice to checks with no model in the loop. Concern rejected: "auto-apply everything with rollback, like the products do". The poisoning and sycophancy evidence, and Anamnesis's provenance purpose, argue against it.
