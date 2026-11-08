# Learning Workbench for Hermes

Study with Hermes the way a good tutor works: find out what you already know, agree a short route,
and answer one exercise at a time in your own words. Learning Workbench adds a pane to Hermes
Desktop and six tools for the agent. The plugin keeps the record honest. Your answers are stored
exactly as you wrote them. Hints, revealed solutions and the tutor's verdicts stay on record. All
of it survives restarts, context compaction and a closed pane.

```
goal + material → diagnose (≤ 3 questions) → route (2–7 steps, yours to rename/skip)
                         ↓
       teach in chat → exercise in the pane → your answer + reasoning → tutor's check
             ↑                                                         │
             └──────────── repair · hint · retry · reveal ─────────────┘
                         ↓
       fresh practice without help → evidence you can export
```

## Why a plugin and not a prompt

A tutoring skill can tell the model to wait for your answer. It cannot make the record trustworthy.
A skill cannot:

- **Freeze the answer key before you answer.** The reference answer, rubric and up to three hints
  are stored when the exercise is posed. They stay hidden until you take a hint or confirm a reveal.
  A changed key becomes a numbered revision with a reason.
- **Keep your words yours.** Only you can submit an attempt: from the pane, or with `/study answer`
  in a text chat. No agent tool takes answer text, so the model cannot write an answer for you.
  Forged fields such as `actor` or `human_confirmed` are refused.
- **Make verdicts cite you.** A check must quote your attempt word for word. The plugin verifies
  the quote and rejects a verdict that quotes nothing you wrote.
- **Label help honestly.** Every attempt carries how it was made: unaided, after feedback, after
  hints, or after the solution was revealed. The label is fixed when you submit, so a hinted answer
  never counts as unaided later.
- **Keep one exercise open.** While an exercise waits for you, the tutor cannot replace it or pose
  another one.
- **Survive the session.** Drafts autosave. Closing the pane, restarting Hermes or a context
  compaction that rotates the session id brings back the same exercise, draft and attempts.
- **Let you disagree.** Contest a verdict. The tutor answers with a new revision, and the earlier
  verdict stays on record.

What it is not: no spaced repetition or flashcards (use Anki), no mastery scores or
certificates, no course marketplace, no extra model calls. Verdicts come from your configured
model. They are proposals, not grades.

## Install

```bash
hermes plugins install <owner>/hermes-learning-workbench --enable
```

Then turn the pane on in Hermes Desktop under **Capabilities → Plugins → Learning Workbench**.
The Desktop half of a unified plugin is opt-in. Requires Hermes `>= 0.23.0`. No Python
dependencies, no accounts, no network calls of its own.

## Quick start

1. Open a chat and ask: *"Teach me why the derivative of x² is 2x. Use this:"* and paste your text,
   or point Hermes at a file (it reads files with its own tools).
   You can also start in the pane: write a goal, paste material, click **Start study**.
2. Hermes asks one to three short diagnostic questions in the pane. Answer there; add your
   reasoning if you like (`Ctrl+Enter` submits).
3. Hermes proposes a short route. Rename or skip steps in the pane; it is your plan.
4. Hermes teaches a step in chat, then poses a check. You answer, and Hermes checks it, quoting
   your words. Not there yet? Read the explanation, then **Try again**, take a **Hint**, or
   **Reveal solution…**.
5. After a correct answer that needed help, Hermes poses a fresh practice problem so you can show
   it without help.
6. **Export record** writes a Markdown file of everything: route, prompts, your answers word for
   word, help received, every verdict and contest.

Without Desktop (CLI, TUI, messaging): `/study` shows the current exercise; use `/study answer
<text>` (add a line `Reasoning: …`), `/study hint`, `/study reveal`, `/study skip`, `/study retry`,
`/study contest <why>`, `/study export`, `/study finish`, `/study delete`. This is a fallback, not
a full second interface. (`/learn` is a Hermes built-in, hence `/study`.)

## Privacy and data

- **Stored locally** in the active profile at `<HERMES_HOME>/plugin-data/learning-workbench/data.db`
  (SQLite). Exports go to `…/learning-workbench/exports/`.
- **Sent to your model provider:** whatever the tutor reads, through Hermes's normal model access.
  That covers the material, when the tutor asks for it, and your submitted answers, reasoning and
  contests. Drafts never leave your machine.
  Local storage does not mean offline processing.
- **Retention:** records stay until you delete them. Updating, disabling or uninstalling the plugin
  keeps them; reinstalling shows them again. **Delete…** in the pane, or `/study delete`, removes
  one study and its exports after a confirmation step; other studies are untouched.
- Material and transcripts are treated as untrusted text. They are shown as plain text and never run
  as HTML or code.

## How it works

```
 Hermes agent ──tools──▶ ┌──────────── Learning Workbench ────────────┐
  (proposes)             │ core: ids, transitions, versions, checks   │──▶ SQLite (profile)
 Desktop pane ──ctx.rest▶│ learner routes (stamped "learner")         │
  (learner acts)         │ /study command (stamped "learner")         │
                         └─────────────────────────────────────────────┘
```

- **Agent tools** (`learning_workbench_start`, `_get`, `_propose_plan`, `_propose_exercise`,
  `_assess`, `_revise_reference`) can propose and read. None of them can submit, hint, reveal or
  confirm anything for you.
- **Learner door:** the pane calls the plugin's own routes through the Desktop SDK's
  `ctx.rest` (scoped by Hermes to this plugin and the active profile), and `/study` is typed by
  you. Submitting starts a normal chat turn through the same `prompt.submit` call the Desktop
  composer uses, queued so it never interrupts a running turn.
- **Hook:** `post_compaction` keeps a study attached to its chat when compaction rotates the
  session id. It acts only on chats that have a study.
- No core patches, no system-prompt injection, no background processes.

## Threat model and support boundaries

This is an attribution boundary, not a sandbox. Within Hermes, the model cannot create or change
learner records through the plugin. Any program running as your user can still edit the SQLite
file directly, and that includes Hermes's own terminal tool. Hidden solutions are a learning aid,
not exam security. They are in the local database.

Tested on: Hermes Agent 0.23.0 (source `2bd378ed1b8d`) and the packaged Hermes Desktop (build
`2237be35`, Linux/Wayland), in-process plugin mode. Not tested: macOS and Windows Desktop builds,
`plugins.isolation: host`, remote/OAuth gateways (the pane polls, so it should work without the
event socket), messaging platforms beyond `/study` in the CLI. See
[docs/TESTING.md](docs/TESTING.md) for what was run and what was not.

## Development

```bash
scripts/verify.sh            # JS syntax check + deterministic tests (real plugin loader, registry, router)
scripts/mutation-check.py    # breaks 12 invariants one at a time; each must be caught
scripts/pane-harness.sh      # real plugin.js in React 19/jsdom against the real backend (host stubbed)
scripts/lifecycle-check.sh   # validate, install from git, doctor, disable/enable, reinstall, uninstall
```

All test processes run in an isolated Hermes home (`scripts/testenv.sh`,
`scripts/lw-isolation-check`).

## Credits and license

MIT. Inspired by [LearnHarness](https://github.com/amosblomqvist/learnharness) by Amos Blomqvist
(AGPL-3.0): its diagnose → plan → teach → practice loop with persistent learner reasoning. No
LearnHarness code, prompts or assets are used.
