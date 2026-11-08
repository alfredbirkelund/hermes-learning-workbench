# Catalog submission draft: Learning Workbench

Status: SUBMITTED. PR https://github.com/NousResearch/hermes-agent/pull/151417 pins
`7724a7cbea573889025ed750cd9102c4ec3de926`, validated with
`hermes plugins validate --install-deps` on a clean clone at that commit (16/16 checks, security
scan safe). Any further change to this repository needs a SHA-bump PR (catalog rule 4).

## plugin-catalog/learning-workbench.yaml

```yaml
name: learning-workbench
title: Learning Workbench
repo: https://github.com/alfredbirkelund/hermes-learning-workbench
sha: 7724a7cbea573889025ed750cd9102c4ec3de926
version: "0.2.0"
description: "Study with Hermes in a Desktop pane: a short diagnosis, a route of 2–7 steps you can rename or skip, then one lesson and exercise at a time, answered in your own words. The plugin keeps the record honest: answer keys and hints are frozen before you answer, only you can submit an attempt (the agent's six tools propose and read), every check must quote your words, and each attempt is labelled unaided, after feedback, after hints, after help in chat or after a revealed solution. Drafts and attempts survive restarts and compaction, a contest keeps the earlier verdict, and you can export a Markdown record. /study covers the CLI and messaging. Disclosure — studies are stored locally in $HERMES_HOME/plugin-data/learning-workbench/ (SQLite) until you delete them, including after uninstall; while an exercise is open in a chat, the pre_llm_call hook adds a short reminder with that exercise's question to the turn's user message and records that the turn happened (not its text); no network calls of its own, no credentials, no shell commands, no background processes, no telemetry."
maintainer: alfredbirkelund
tier: community
category: desktop
requires_hermes: ">=0.23.0"
docs_url: https://github.com/alfredbirkelund/hermes-learning-workbench#readme
readme: true
platforms: []
screenshots:
  - https://raw.githubusercontent.com/alfredbirkelund/hermes-learning-workbench/7724a7cbea573889025ed750cd9102c4ec3de926/docs/screenshots/goal-material-route.png
  - https://raw.githubusercontent.com/alfredbirkelund/hermes-learning-workbench/7724a7cbea573889025ed750cd9102c4ec3de926/docs/screenshots/lesson-above-the-check.png
  - https://raw.githubusercontent.com/alfredbirkelund/hermes-learning-workbench/7724a7cbea573889025ed750cd9102c4ec3de926/docs/screenshots/verdict-quotes-the-learner.png
  - https://raw.githubusercontent.com/alfredbirkelund/hermes-learning-workbench/7724a7cbea573889025ed750cd9102c4ec3de926/docs/screenshots/answer-in-your-own-words.png
known_issues:
  - "The Desktop pane is opt-in: after installing, turn it on under Capabilities → Plugins → Learning Workbench."
  - "Verdicts come from your configured model. They are proposals, not grades; contest any you disagree with."
capabilities:
  provides_tools:
    - learning_workbench_start
    - learning_workbench_get
    - learning_workbench_propose_plan
    - learning_workbench_propose_exercise
    - learning_workbench_assess
    - learning_workbench_revise_reference
  provides_hooks:
    - post_compaction
    - pre_llm_call
  provides_middleware: []
  requires_env: []
```

## PR description

**Learning Workbench** turns Hermes into a tutor whose record you can trust. You bring a goal and
material; Hermes diagnoses where to start, proposes a short route, and teaches one step at a time
in a Desktop pane. You answer each exercise in your own words. The plugin, not the model, decides
what counts as your work:

- answer keys, rubrics and hints are frozen before you answer and hidden until you take a hint or
  confirm a reveal;
- only the learner can submit an attempt (pane routes or `/study`); the agent's tools can only
  propose and read;
- a check must quote the attempt word for word, and the quote is verified;
- every attempt is labelled by the help it had: unaided, after feedback, after hints, after help
  in chat, or after a revealed solution;
- a route step's first check must carry a lesson, so nothing is tested before it is taught.

Hermes surfaces used: six tools (`ctx.register_tool`), `pre_llm_call` and `post_compaction` hooks,
a `/study` slash command, dashboard plugin routes (`dashboard/manifest.json` + `plugin_api.py`),
plugin storage (`plugin-data/learning-workbench/`) and a Desktop pane (`desktop/plugin.js`, plugin
SDK only: `ctx.rest`, `host.request`, `host.composer` when present, plugin events).

Disclosures (rule 13):
- Stores studies locally in `$HERMES_HOME/plugin-data/learning-workbench/data.db` (SQLite) and
  exports in `…/exports/`. Kept after update, disable and uninstall; removed per study by the
  learner's two-step delete.
- While an exercise is open in a chat, `pre_llm_call` adds a short reminder (including that
  exercise's question) to the turn's user message, never the system prompt, and records that a
  chat turn happened (not its content). Chats without an open exercise are untouched, and nothing
  is created for people who never use the Workbench.
- No network calls of its own, no credentials, no shell commands, no background processes, no
  telemetry. Verdicts use the user's configured model through Hermes.

Validation at the pinned commit: `hermes plugins validate --install-deps` on a clean clone → 16/16 checks,
security scan **safe**, desktop surface and no-core-override pass. Tests: `scripts/verify.sh` (53 tests), `scripts/mutation-check.py` (22/22),
`scripts/pane-harness.sh` (19 steps), `scripts/lifecycle-check.sh` (install, update from the
previous release with data migration, disable/enable, reinstall, uninstall keeps data). Live
sessions with a real model are summarised in `docs/TESTING.md`.

Screenshots: four, pinned to the sha (goal/route, lesson above the check, a verdict quoting the learner, an exercise awaiting an answer).
