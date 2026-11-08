"""Readable Markdown learning record for one study (the learner's export)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from . import core, db

HELP_LABEL = {"unaided": "unaided", "after_feedback": "retry after feedback", "hinted": "after hints",
              "after_chat": "after help in chat while the exercise was open",
              "after_reveal": "after the solution was revealed"}
VERDICT_LABEL = {"correct": "correct", "partially_correct": "partly correct", "incorrect": "not yet correct",
                 "cannot_judge": "could not judge"}


def _fence(text: str) -> str:
    """Verbatim block: a fence longer than any backtick run inside, so learner text renders exactly."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def render(view: dict, *, material_text: str = "", plugin_version: str = "") -> str:
    lines: list[str] = []
    add = lines.append
    add(f"# Learning record: {_one_line(view['goal'])}")
    add("")
    add(f"Exported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from Learning Workbench "
        f"{plugin_version}. Study `{view['study_id']}`, started {view['created_at'][:10]}, {view['status']}.")
    add("")
    add("Checks are the tutor model's proposals, not grades or certifications. Learner answers are verbatim.")
    add("")
    m = view["material"]
    origin = f", from {m['origin']}" if m.get("origin") else ""
    add(f"Material: {m['title']} ({m['source'].replace('_', ' ')}, {m['chars']} characters{origin}, "
        f"sha256 {m['sha256'][:12]}).")
    add("")
    route = view.get("route")
    if route:
        add(f"## Route (revision {route['plan_revision']})")
        add("")
        for i, step in enumerate(route["steps"], 1):
            ev = step["evidence"]
            status = " (skipped by the learner)" if step["status"] == "skipped" else ""
            add(f"{i}. **{_one_line(step['title'])}**{status}: {_one_line(step['why'])}")
            add(f"   Evidence: {ev['attempts']} attempt(s), {ev['correct_unaided']} correct unaided "
                f"({ev['independent_practice_correct']} on fresh practice), {ev['correct_with_help']} correct with help.")
        for step in route["removed_steps"]:
            add(f"- Removed from the route later: **{_one_line(step['title'])}** (its attempts are kept below)")
        add("")
    add("## Exercises")
    add("")
    if not view["exercises"]:
        add("No exercises yet.")
        add("")
    for ex in view["exercises"]:
        step = f" · {ex['step_title']}" if ex.get("step_title") else ""
        add(f"### Exercise {ex['sequence']} · {ex['kind']}{step}")
        add("")
        if ex.get("lesson"):
            add("Lesson:")
            add("")
            add(_fence(ex["lesson"]))
            add("")
            add("Question:")
            add("")
        add(_fence(ex["prompt"]))
        add("")
        help_bits = [f"hints shown: {ex['hints_shown']} of {ex['hints_total']}",
                     "solution revealed" if ex["solution_revealed"] else "solution not revealed",
                     f"state: {ex['state'].replace('_', ' ')}"]
        add("; ".join(help_bits).capitalize() + ".")
        add("")
        for a in ex["attempts"]:
            add(f"#### Attempt {a['number']} · {HELP_LABEL.get(a['help_class'], a['help_class'])} · "
                f"{a['created_at'][:16].replace('T', ' ')} UTC")
            add("")
            add("Answer:")
            add("")
            add(_fence(a["answer"]))
            add("")
            if a["reasoning"]:
                add("Reasoning:")
                add("")
                add(_fence(a["reasoning"]))
                add("")
            contests = {c["contest_id"]: c for c in a["contests"]}
            for s in a["assessments"]:
                if s["responds_to_contest_id"] and s["responds_to_contest_id"] in contests:
                    c = contests[s["responds_to_contest_id"]]
                    add("Learner contested the check:")
                    add("")
                    add(_fence(c["text"]))
                    add("")
                label = "Check" if s["revision"] == 1 else f"Revised check (revision {s['revision']})"
                add(f"{label}: **{VERDICT_LABEL.get(s['verdict'], s['verdict'])}**")
                add("")
                add(f"Quoted from the answer: “{_one_line(s['evidence_quote'])}”")
                add("")
                add(_one_line(s["explanation"]))
                if s["gap"]:
                    add("")
                    add(f"Gap: {_one_line(s['gap'])}")
                add("")
            for c in a["contests"]:
                if not c["answered"]:
                    add("Learner contested the check (no response yet):")
                    add("")
                    add(_fence(c["text"]))
                    add("")
        if ex.get("reference_answer") and ex["solution_revealed"]:
            add("Revealed solution:")
            add("")
            add(_fence(ex["reference_answer"]))
            add("")
    if material_text:
        add("## Material")
        add("")
        add(_fence(material_text))
        add("")
    return "\n".join(lines).rstrip() + "\n"


def export_study(conn, study_id: str, *, include_material: bool = False, plugin_version: str = "") -> dict:
    view = core.study_view(conn, study_id, audience="learner")
    text = view["material"].pop("text", "") if include_material else ""
    markdown = render(view, material_text=text, plugin_version=plugin_version)
    out_dir = exports_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{study_id}-{stamp}.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(markdown, encoding="utf-8")
    tmp.replace(path)
    with core.write_tx(conn):
        core._event(conn, study_id, "learner", "exported", include_material=bool(include_material), file=path.name)
    return {"study_id": study_id, "path": str(path), "markdown": markdown, "chars": len(markdown)}


def exports_dir() -> Path:
    path = db.data_dir() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def delete_exports(study_id: str) -> int:
    removed = 0
    for path in exports_dir().glob(f"{study_id}-*.md"):
        path.unlink(missing_ok=True)
        removed += 1
    return removed
