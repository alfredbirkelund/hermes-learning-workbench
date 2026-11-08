# Learning Workbench: approved scope

Approved by Alfred on 2026-11-08 ("build the plugin | decide on all the items, do what you think is best").
This file is the review baseline for product and spec review.

## Decisions taken under that delegation

| Item | Decision | Reason |
|---|---|---|
| Scope and test seams | As in the checkpoint below | Approved |
| Live model access in the isolated test home | `with-secrets ANTHROPIC_API_KEY`; a cheaper Anthropic model for spike and iteration, the configured `claude-opus-5-5` for the formal live evaluation and the demo journey | No token stores are read or copied; the formal evaluation uses the model the user actually runs |
| Desktop testing | A separate packaged Desktop instance (own `HERMES_HOME`, own `HERMES_DESKTOP_USER_DATA_DIR`, DevTools on a discovered free port) | Tests what users have installed; no change to the shared Hermes checkout |
| License | MIT; README credits LearnHarness as the inspiration; no LearnHarness code, prompts or assets are copied | Matches the Hermes ecosystem and avoids AGPL obligations |
| Bundled skill | None. Guidance lives in tool descriptions and tool results | A skill must not substitute for runtime mechanics; avoids an extra permission |
| Publication | Not authorised. No public repo, catalog PR, X post or form submission without a separate message from Alfred | Brief rule |

## User journey (one study per chat, one active exercise at a time)

1. START: in the pane, a goal plus pasted text; or in chat ("teach me X from this"), where Hermes reads files with its own tools and calls the start tool.
2. DIAGNOSE: one to three short questions. The runtime caps diagnosis at three questions before a route exists.
3. ROUTE: two to seven steps, each with a reason. The learner can rename, skip or restore steps; every edit is a new attributed revision.
4. TEACH: the tutor explains in chat, then poses a check whose reference answer, rubric and up to three hints are frozen in advance.
5. ANSWER: answer plus optional reasoning; the draft autosaves; Submit records exactly once and starts a normal chat turn.
6. CHECK: the verdict must quote the learner's actual words; the runtime verifies the quote. A gap leads to repair in chat, then retry, hint or reveal.
7. PRACTICE: a fresh problem without help; a correct answer counts as unaided evidence (counts, never percentages).
8. RESUME: close the pane, restart, reopen; the same exercise, draft and attempts return.
9. OWN IT: contest a verdict (the old one is kept), export Markdown, delete one study after a two-step confirmation.

## Keep / cut

Keep: study bound to its chat with material provenance; route list whose old steps keep their attempts; draft autosave and idempotent submit; attempt help class (unaided, hinted, retry after feedback, after reveal); verdict revisions and learner contests; export; delete one study; `/study` text fallback without UI parity.

Cut or defer in v1: scheduling, spaced repetition, flashcards; graph canvas and drag-and-drop planning; knowledge graph and cross-lesson retrieval; a file-import UI; on-demand generated hints; LaTeX rendering inside the pane; extra model calls; mastery scores or badges; voice; code execution; a bundled skill.

## Architecture

One installable folder with two doors and one store:

- Agent tools (stamped `agent`): start, get, propose_plan, propose_exercise, assess, revise_reference.
- Learner actions (pane routes via host-authenticated `ctx.rest`, or user-typed `/study`; stamped `learner`): submit, draft, hint, reveal, skip, retry, contest, edit route, export, delete.
- Core (plain Python + sqlite3): the runtime owns ids, transitions, versions and persistence in `<HERMES_HOME>/plugin-data/learning-workbench/data.db`. Drafts never reach the model.
- Pane: Desktop SDK only. It starts the tutor's turn through the same `prompt.submit` RPC that the Desktop composer uses (`host.composer.submit` when a newer Desktop has it).

## Test seams

- S1 agent tools through the real Hermes plugin loader and tool registry, including forged attribution.
- S2 learner routes through the plugin API router: duplicates, rapid clicks, two windows, stale revisions.
- S3 saved state read back by a fresh process.
- S4 host lifecycle through the real CLI in the isolated home.
- S5 rendered interactions in the isolated Desktop, driven over DevTools, screenshots inspected.
- S6 live model evaluation, reported separately.

## Spike gate (first, at most 90 minutes)

Pass only if, in the real isolated Desktop and confirmed by reading the store: an agent-created exercise appears in the pane; one Submit yields exactly one attempt on the right exercise and chat and starts the agent turn; the agent cannot replace the pending exercise; closing the pane and restarting Desktop and the backend restore the same exercise and draft.

## Threat model summary

Attribution boundary, not a sandbox. Agent tools cannot create or alter learner records. Learner actions arrive only through the Desktop plugin API or user-typed slash commands. Any same-user local process, including Hermes's own terminal tool, can still edit the SQLite file directly.
