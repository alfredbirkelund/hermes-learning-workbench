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

- **Deterministic:** 53 passed. Covers the learning loop, every help label (unaided, after
  feedback, after hints, after chat help, after reveal), quote verification, frozen keys, one open
  exercise, drafts and conflicts, duplicate and parallel submits, stale writes, contests, route
  edits, finishing a study and reaching it again by number, delete (including export files that
  cannot be removed), two exports in the same second, a busy store, compaction rebinding, lessons,
  chat help, the schema-1 → 2 migration on a real store written by the previous release
  (`tests/fixtures/store-v1.sql`), and refusal of a store from a newer version.
- **Mutation:** 22/22 invariants caught (the test suite fails when any one is broken).
- **Pane harness:** 19/19 steps, including double-click guards, autosave, a two-window draft
  conflict, chat switching with a late response, Hermes unreachable after a submit, the lesson,
  the chat-help notice, and finishing a study, then exporting and deleting it from the finished
  list.
- **Lifecycle:** written to `.test-artifacts/lifecycle.txt` (not committed). The check installs the
  previous release from a git source, writes learner data with it, moves the source to the
  candidate and runs `hermes plugins update`, then checks the data and the migration, then
  disable/enable, a forced reinstall, uninstall (data kept) and reinstall (same records back).

## Live sessions (gpt-6.1-sol through Hermes 0.23.0)

Two full study sessions on "why is the derivative of x² equal to 2x", using
`tests/fixtures/derivative-notes.md`. The learner side was scripted; the tutor was the real model.

**Run 1 (previous release)** found one product problem: two of four checks were posed without
teaching the step first. Fix: lessons. A step's first check must carry one, and the pane shows it
above the question.

**Run 2** (lessons in place) went through diagnosis, a three-step route and nine exercises:

- No lesson solved its check. Two lessons worked a different case (any start and step; x = 2 for a
  check at x = 3); the third stated the check's own expression and taught the tools (expanding a
  square, factoring, when cancelling h is allowed) without doing the simplification.
- Help labels matched what happened: after hints, after reveal, after chat help, unaided. No
  attempt in the live runs was *after feedback* (a retry after a check without a hint or reveal);
  that label is covered by the deterministic tests.
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

## Independent review

Three fresh reviewers (standards, product/spec, security/reliability) read the whole plugin at
commit `05d1b37` and reported reproducible findings. Fixed since:

- **Finished studies were unreachable** (spec, major): after *Finish*, no button or command reached
  the study, though the pane promised it stayed exportable. Now the pane lists them under
  *Finished in this chat* with export and delete, and `/study` lists them by number for
  `/study export N` and `/study delete N`.
- **Compaction created a store for people who never studied** (reliability): the
  `post_compaction` hook now checks first, like the per-turn hook.
- Error handling: a busy store and export-file failures now get the plugin's own messages in
  `/study` and the routes; a delete whose export files cannot be removed still deletes and names
  the files left behind; two exports in the same second no longer share a file name.
- Two overstated claims in this file were corrected (lessons, *after feedback*).
- Pane timers now use the SDK's tracked `ctx.setTimeout`.

Judgement calls not changed: label maps kept per door (they are worded differently on purpose; a
test now checks that every door labels every help class), the six state guards in `lw/core.py`,
and the size of `lw/core.py`.

## Hermes Desktop (v0.23.0, by hand)

Hermes Desktop built from the `v0.23.0` tag, isolated home and app data, gpt-6.1-sol, a 1920×1080
window. (The Desktop package on the test machine is v0.21.1, which predates unified plugin panes: it
does not copy the package's desktop half into `desktop-plugins/`, so there is no pane; `/study`
still works there.)

- The installed package appeared under **Capabilities → Plugins** as *Off*; one switch turned on
  both halves, and the pane opened in the right sidebar.
- A chat request with pasted notes started a study. The pane showed the diagnosis, then the route,
  a lesson and a check. A wrong claim got *Partly correct*, quoting the sentence and naming the gap;
  **Try again** plus a hint led to *Correct*, and the tutor then posed a fresh practice problem.
- Asked in chat for the answer, the tutor pointed to **Reveal solution** and gave a nudge; the pane
  noted the chat help.
- With a draft typed, the Desktop and its gateway were stopped and started again: same chat, same
  exercise, the unsent draft and the notice were back.
- Export and the reveal dialog (the host's `ConfirmDialog`) rendered as intended.

Found and fixed here: the route list pushed the lesson and question below the fold (the route now
folds to one line while an exercise is open, and a new exercise scrolls into view); "Sent to Hermes."
stayed after the reply; the export notice showed a long path (now the file name, path on hover).

## Not tested

- macOS and Windows; packaged Desktop releases other than the v0.23.0 build above; remote or
  OAuth gateways; messaging platforms beyond `/study` in the CLI.
- Other models than gpt-6.1-sol for the live sessions.
