# Testing Learning Workbench

What was run, what it showed, and what was not tested. Everything runs in an isolated Hermes home
(`scripts/testenv.sh`); `scripts/lw-isolation-check` proves the isolation with a negative control.

## Layers

| Layer | Command | What is real | What is stubbed |
|---|---|---|---|
| Deterministic tests | `scripts/verify.sh` | Hermes `PluginManager`, tool registry, hook dispatch, `/study` dispatch, FastAPI router, SQLite | the event transport to Desktop |
| Mutation check | `scripts/mutation-check.py` | each invariant broken in a scratch copy; the named test must fail | — |
| Pane harness | `scripts/pane-harness.sh` | `desktop/plugin.js` rendered with React 19 in jsdom, against the real plugin backend over HTTP | the Desktop host (`ctx.rest`, `request`, composer) |
| Live backend | `tests/e2e/live.py` | `hermes serve` (the backend Hermes Desktop starts), chat over its WebSocket JSON-RPC, learner actions over the plugin's REST routes with the session token, a real model | the Desktop window itself |
| Host lifecycle | `scripts/lifecycle-check.sh` | `hermes plugins validate / install / update / doctor / disable / enable / remove` in a fresh home | — |

## Results (version 0.2.0)

- **Deterministic:** 44 passed. Covers the learning loop, help labels, quote verification, frozen
  keys, one open exercise, drafts and conflicts, duplicate and parallel submits, stale writes,
  contests, route edits, delete, compaction rebinding, lessons, chat help, the schema-1 → 2
  migration on a real store written by the previous release (`tests/fixtures/store-v1.sql`), and
  refusal of a store from a newer version.
- **Mutation:** 17/17 invariants caught (the test suite fails when any one is broken).
- **Pane harness:** 18/18 steps, including double-click guards, autosave, a two-window draft
  conflict, chat switching with a late response, Hermes unreachable after a submit, the lesson and
  the chat-help notice.
- **Lifecycle:** see `.test-artifacts/lifecycle.txt` from the last run (not committed). The check
  installs the previous release from a git source, writes learner data with it, moves the source
  to the candidate and runs `hermes plugins update`, then checks the data and the migration.

## Live sessions (gpt-6.1-sol through Hermes 0.23.0)

Two full study sessions on "why is the derivative of x² equal to 2x", using
`tests/fixtures/derivative-notes.md`. The learner side was scripted; the tutor was the real model.

**Run 1 (previous release)** found one product problem: two of four checks were posed without
teaching the step first. Fix: lessons. A step's first check must carry one, and the pane shows it
above the question.

**Run 2** (lessons in place) went through diagnosis, a three-step route and nine exercises:

- All three lessons taught the idea on a different case than the check asked about.
- Help labels matched what happened: after hints, after feedback, after reveal, unaided.
- A sign error got `partially_correct`, quoting the learner's own wrong reasoning, with the gap
  named ("h is end − start, not its absolute value").
- After a revealed solution, the tutor asked for a fresh problem before crediting the step.
- **Found:** asked in chat, the tutor solved the open exercise in full, and the plugin could not
  see it. Fix: the chat-help reminder and label. After the fix, the same request got a pointer to
  the recorded reveal, and a conceptual question got an explanation on a different example.
- A draft survived a restart of `hermes serve`, and the chat resumed afterwards.
- A contest got a revised check (revision 2) with the first verdict kept. The pane's own contest
  message was not counted as chat help for the exercise that was open at the time.
- The real store from run 1, written by the previous release, was migrated in place by the new
  version: all attempts and checks intact.

## Not tested

- **The Hermes Desktop window itself.** The pane's logic is tested in jsdom against the real
  backend; it has not been clicked through by hand in the packaged Desktop yet.
- macOS and Windows; remote or OAuth gateways; messaging platforms beyond `/study` in the CLI.
- Other models than gpt-6.1-sol for the live sessions.
