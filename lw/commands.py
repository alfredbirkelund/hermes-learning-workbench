"""/study: the text door for learners on surfaces without the Desktop pane (CLI, TUI, messaging).

A slash command is typed by the user, so it is a learner door like the pane. It reuses the same core;
it is a fallback, not a second full interface.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from typing import Any, Callable, Optional

from . import core, db, export, tools

logger = logging.getLogger(__name__)

USAGE = (
    "Usage: /study [status] | answer <text> (add a line 'Reasoning: ...' for your reasoning) | hint | reveal | "
    "skip | retry | contest <why> | export [N] [with-material] | finish | delete [N]\n"
    "N is a finished study's number from /study when no study is active."
)
STORAGE_BUSY = "Not saved: the Workbench storage is busy or unavailable. Nothing changed; try again in a moment."
HELP_LABEL = {"unaided": "unaided", "after_feedback": "after feedback", "hinted": "after hints",
              "after_chat": "after chat help", "after_reveal": "after reveal"}


def _split_answer(text: str) -> tuple[str, str]:
    match = re.search(r"(?im)^\s*reasoning\s*:", text)
    if not match:
        return text.strip(), ""
    return text[:match.start()].strip(), text[match.end():].strip()


def _status(view: dict) -> str:
    lines = [f"Study: {view['goal']}"]
    route = view.get("route")
    if route:
        lines.append("Route:")
        for i, step in enumerate(route["steps"], 1):
            ev = step["evidence"]
            mark = " (skipped)" if step["status"] == "skipped" else ""
            lines.append(f"  {i}. {step['title']}{mark} [{ev['correct_unaided']} unaided correct, "
                         f"{ev['correct_with_help']} with help]")
    open_id = view.get("open_exercise_id")
    if open_id:
        ex = next(e for e in view["exercises"] if e["exercise_id"] == open_id)
        if ex.get("lesson"):
            lines.append(f"Lesson ({ex['step_title'] or 'this step'}):")
            lines += [f"  {line}" if line else "" for line in ex["lesson"].splitlines()]
        lines.append(f"Exercise {ex['sequence']} ({ex['kind']}): {ex['prompt']}")
        if ex["hints"]:
            lines += [f"  Hint {i}: {h}" for i, h in enumerate(ex["hints"], 1)]
        if ex["state"] == "awaiting_answer":
            lines.append(f"Answer with /study answer <text>. Hints left: {ex['hints_total'] - ex['hints_shown']}.")
        else:
            lines.append("Your answer is waiting for the tutor's check. Ask Hermes to check it.")
    elif view["exercises"]:
        ex = view["exercises"][-1]
        last = ex["attempts"][-1] if ex["attempts"] else None
        if last and last["assessments"]:
            s = last["assessments"][-1]
            lines.append(f"Last check (exercise {ex['sequence']}, {HELP_LABEL.get(last['help_class'], '')}): "
                         f"{s['verdict']}. {s['explanation']}")
        lines.append("No open exercise. Ask Hermes for the next step.")
    else:
        lines.append("No exercise yet. Ask Hermes to diagnose where to start.")
    return "\n".join(lines)


def _confirm(ask: Optional[Callable], question: str, yes: str) -> Optional[bool]:
    """True/False from the host picker, or None when nothing can ask (then require the text form)."""
    if ask is None:
        return None
    try:
        picked = ask(question, [{"value": "yes", "label": yes}, {"value": "no", "label": "Cancel"}])
    except Exception:
        logger.debug("learning-workbench: ask_choice unavailable", exc_info=True)
        return None
    if picked is None:
        return None
    return picked == "yes"


def _finished_in_chat(conn, session_id: str) -> list[dict]:
    """This chat's finished studies, newest first; their position is the N in `/study export N`."""
    rows = [s for s in core.list_studies(conn) if s["hermes_session_id"] == session_id and s["status"] != "active"]
    return sorted(rows, key=lambda s: s["created_at"], reverse=True)


def _pick_finished(rest: str, finished: list[dict]) -> tuple[Optional[dict], str, Optional[str]]:
    """`N [more]` -> (finished study N, more, None); an N out of range -> (None, rest, message)."""
    match = re.match(r"(\d+)(?:\s+(.*))?$", rest, re.S)
    if not match:
        return None, rest, None
    n = int(match.group(1))
    if not 1 <= n <= len(finished):
        return None, rest, (f"There is no finished study {n} in this chat. /study lists them." if finished
                            else "This chat has no finished studies.")
    return finished[n - 1], (match.group(2) or "").strip(), None


def _no_active_study(finished: list[dict]) -> str:
    text = ("No active study in this chat. Tell Hermes what you want to learn (and paste or point to the material); "
            "it will start one.")
    if not finished:
        return text
    lines = [text, "Finished in this chat:"]
    lines += [f"  {i}. {s['goal']} (started {s['created_at'][:10]})" for i, s in enumerate(finished, 1)]
    lines.append("Export one with /study export <number>, or delete it with /study delete <number>.")
    return "\n".join(lines)


def _export(conn, sid: str, rest: str, plugin_version: str) -> str:
    out = export.export_study(conn, sid, include_material=rest.lower() == "with-material",
                              plugin_version=plugin_version)
    return f"Learning record written to {out['path']}"


def _delete(conn, sid: str, rest: str, ask: Optional[Callable], again: str) -> str:
    """Two steps: a request that shows what goes, then a confirmation (host picker or a typed token)."""
    if rest.lower().startswith("confirm "):
        token = rest.split(None, 1)[1].strip()
    else:
        req = core.request_delete(conn, study_id=sid)
        ok = _confirm(ask, f"Delete the study '{req['goal']}' with {req['attempts']} attempt(s) and its exports? "
                           "This cannot be undone.", "Delete permanently")
        if ok is None:
            return (f"This deletes the study '{req['goal']}' with {req['attempts']} attempt(s) and its exports. "
                    f"To confirm within 5 minutes, type: {again} confirm {req['confirm_token']}")
        if not ok:
            return "Not deleted."
        token = req["confirm_token"]
    out = core.confirm_delete(conn, study_id=sid, token=token)
    files = export.delete_exports(sid)
    text = f"Deleted the study and its records ({out['attempts']} attempt(s))."
    if files["left"]:
        text += (f" {len(files['left'])} exported file(s) could not be removed; delete them yourself: "
                 + ", ".join(files["left"]))
    return text


def handle(raw_args: str, *, session_id: str = "", ask: Optional[Callable] = None,
           plugin_version: str = "") -> str:
    raw = (raw_args or "").strip()
    verb, _, rest = raw.partition(" ")
    verb = verb.lower() or "status"
    rest = rest.strip()
    if verb in ("help", "-h", "--help"):
        return USAGE
    if not session_id:
        return "Learning Workbench needs a chat session. Send a message first, then try /study again."
    try:
        conn = db.connect()
    except db.StoreTooNew as exc:
        return str(exc)
    except sqlite3.OperationalError:
        return STORAGE_BUSY
    sid = None
    changed = False
    try:
        finished = _finished_in_chat(conn, session_id)
        if verb in ("export", "delete"):
            picked, more, problem = _pick_finished(rest, finished)
            if problem:
                return problem
            if picked is not None:
                n = finished.index(picked) + 1
                if verb == "export":
                    return _export(conn, picked["study_id"], more, plugin_version)
                return _delete(conn, picked["study_id"], more, ask, again=f"/study delete {n}")
        row = core.active_study_for_session(conn, session_id)
        if row is None:
            return _no_active_study(finished)
        sid = row["id"]
        view = core.study_view(conn, sid, audience="learner")
        open_id = view.get("open_exercise_id")
        latest_id = view.get("latest_exercise_id")
        if verb == "status":
            return _status(view)
        if verb == "answer":
            if not open_id:
                return "There is no open exercise to answer."
            answer, reasoning = _split_answer(rest)
            if not answer:
                return "Write your answer after /study answer."
            out = core.submit_attempt(conn, study_id=sid, exercise_id=open_id,
                                      client_request_id=f"cmd-{uuid.uuid4().hex}", answer=answer,
                                      reasoning=reasoning, channel="command")
            changed = True
            return (f"Saved as attempt {out['number']} on exercise {out['exercise_sequence']} "
                    f"({HELP_LABEL[out['help_class']]}). Ask Hermes to check it, for example: \"check my answer\".")
        if verb == "hint":
            if not open_id:
                return "There is no open exercise."
            ex = next(e for e in view["exercises"] if e["exercise_id"] == open_id)
            out = core.show_hint(conn, study_id=sid, exercise_id=open_id, seen=ex["hints_shown"])
            changed = not out["duplicate"]
            return f"Hint {out['hints_shown']} of {out['hints_total']}: {out['hints'][-1]}"
        if verb == "reveal":
            target = open_id or latest_id
            if not target:
                return "There is no exercise to reveal."
            if rest.lower() != "confirm":
                ok = _confirm(ask, "Reveal the solution? Your next attempt on this exercise will count as "
                                   "after-reveal.", "Reveal the solution")
                if ok is None:
                    return "To reveal the solution, type: /study reveal confirm"
                if not ok:
                    return "Not revealed."
            out = core.reveal_solution(conn, study_id=sid, exercise_id=target)
            changed = not out["duplicate"]
            return f"Solution (reference revision {out['reference_revision']}):\n{out['reference_answer']}"
        if verb == "skip":
            if not open_id:
                return "There is no open exercise to skip."
            core.skip_exercise(conn, study_id=sid, exercise_id=open_id)
            changed = True
            return "Skipped. Tell Hermes why, or ask for an easier step."
        if verb == "retry":
            if not latest_id:
                return "There is no exercise to retry."
            core.retry_exercise(conn, study_id=sid, exercise_id=latest_id)
            changed = True
            return "The exercise is open again. Answer with /study answer <text>."
        if verb == "contest":
            if not rest:
                return "Say why the check is wrong: /study contest <why>."
            target = next((a for e in reversed(view["exercises"]) for a in reversed(e["attempts"])
                           if a["assessments"]), None)
            if target is None:
                return "There is no check to contest yet."
            core.contest(conn, study_id=sid, assessment_id=target["assessments"][-1]["assessment_id"], text=rest,
                         client_request_id=f"cmd-{uuid.uuid4().hex}")
            changed = True
            return "Contest recorded; the earlier verdict stays on record. Ask Hermes to look at it again."
        if verb == "export":
            return _export(conn, sid, rest, plugin_version)
        if verb == "finish":
            ok = _confirm(ask, "Finish this study? You can still export or delete it, and this chat can start a "
                               "new one.", "Finish the study") if rest.lower() != "confirm" else True
            if ok is None:
                return "To finish the study, type: /study finish confirm"
            if not ok:
                return "Kept open."
            core.archive_study(conn, study_id=sid)
            changed = True
            n = next(i for i, s in enumerate(_finished_in_chat(conn, session_id), 1) if s["study_id"] == sid)
            return (f"Study finished. It is number {n} in this chat's finished list: /study export {n} writes its "
                    f"record and /study delete {n} removes it.")
        if verb == "delete":
            return _delete(conn, sid, rest, ask, again="/study delete")
        return USAGE
    except core.LwError as exc:
        return exc.message
    except sqlite3.OperationalError:
        logger.warning("learning-workbench: /study found the store busy or unavailable", exc_info=True)
        return STORAGE_BUSY
    except OSError as exc:
        logger.warning("learning-workbench: /study could not write or remove an export", exc_info=True)
        return f"Could not write or remove the export file ({exc.strerror or exc}). Your records are unchanged."
    finally:
        try:
            if changed and sid:
                tools.broadcast(sid)
        finally:
            conn.close()


def register(ctx) -> None:
    version = getattr(getattr(ctx, "manifest", None), "version", "") or ""
    ask = getattr(ctx, "ask_choice", None)

    def study(raw_args: str, *, session_id: str = "", **_: Any) -> str:
        return handle(raw_args, session_id=session_id, ask=ask, plugin_version=version)

    ctx.register_command("study", study, description="Learning Workbench: status, answer, hint, reveal, export",
                         args_hint="[status|answer|hint|reveal|skip|retry|contest|export|finish|delete]")
