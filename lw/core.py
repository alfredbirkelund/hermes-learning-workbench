"""Learning Workbench domain core: identities, transitions, invariants, persistence.

The model proposes (study start, route, exercises, assessments, reference revisions); the learner acts
(drafts, submissions, hints, reveal, skip, retry, contests, route edits, export, archive, deletion).
Every function is called either by an agent tool (actor "agent"), by a learner door (actor "learner":
the Desktop pane's plugin routes or a user-typed /study command) or by a host hook (actor "host").
The actor is fixed by the caller's door and is never read from input.

Exercise states:  awaiting_answer -> awaiting_check -> checked
                  awaiting_answer -> skipped
                  checked (latest exercise, verdict not correct) -> awaiting_answer   (learner retry)
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

# ---------------------------------------------------------------------------------------------
# limits (keep the study small and the payloads bounded)
# ---------------------------------------------------------------------------------------------
MAX_GOAL = 500
MAX_MATERIAL = 60_000
MAX_TITLE = 200
MAX_STEP_TITLE = 120
MAX_STEP_WHY = 300
MAX_NOTE = 500
MAX_PROMPT = 6_000
MAX_LESSON = 3_000
MAX_REFERENCE = 6_000
MAX_RUBRIC = 3_000
MAX_HINT = 1_500
MAX_HINTS = 3
MAX_ANSWER = 12_000
MAX_REASONING = 12_000
MAX_EXPLANATION = 2_000
MAX_GAP = 500
MAX_QUOTE = 300
MAX_CONTEST = 4_000
MAX_REASON = 500
MAX_STEPS = 7
MIN_STEPS = 2
MAX_DIAGNOSTICS = 3
DELETE_TOKEN_MINUTES = 5

EXERCISE_KINDS = ("diagnostic", "check", "practice")
REASONING_MODES = ("optional", "requested")
VERDICTS = ("correct", "partially_correct", "incorrect", "cannot_judge")
OPEN_STATES = ("awaiting_answer", "awaiting_check")
HELP_CLASSES = ("unaided", "after_feedback", "hinted", "after_chat", "after_reveal")
ROUTE_OPS = ("rename", "skip", "restore")


class LwError(Exception):
    """A refused operation. ``code`` is stable; ``message`` says what to do next."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        out = {"ok": False, "error": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return prefix + "_" + "".join(secrets.choice(alphabet) for _ in range(10))


def _clean(value: Any, field: str, limit: int, *, required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise LwError("invalid_input", f"{field} must be text.", field=field)
    text = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    if required and not text:
        raise LwError("invalid_input", f"{field} is empty.", field=field)
    if len(text) > limit:
        raise LwError("invalid_input", f"{field} is too long ({len(text)} characters, limit {limit}).",
                      field=field, limit=limit)
    return text


def _int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise LwError("invalid_input", f"{field} must be an integer.", field=field)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise LwError("invalid_input", f"{field} must be an integer.", field=field) from None


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _event(conn, study_id: str, actor: str, kind: str, exercise_id: Optional[str] = None, **detail) -> None:
    conn.execute(
        "INSERT INTO events(study_id, created_at, actor, kind, exercise_id, detail_json) VALUES (?,?,?,?,?,?)",
        (study_id, now(), actor, kind, exercise_id, json.dumps(detail, ensure_ascii=False, sort_keys=True)))


def _bump(conn, study_id: str, actor: str) -> int:
    conn.execute("UPDATE studies SET revision = revision + 1, updated_at = ? WHERE id = ?", (now(), study_id))
    rev = conn.execute("SELECT revision FROM studies WHERE id = ?", (study_id,)).fetchone()["revision"]
    if actor == "learner":
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (f"learner_rev:{study_id}", str(rev)))
    return rev


def _learner_revision(conn, study_id: str) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"learner_rev:{study_id}",)).fetchone()
    return int(row["value"]) if row else 0


def _norm(text: str) -> str:
    """Whitespace-collapsed, case-folded text for quote verification."""
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text).strip().casefold()


# ---------------------------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------------------------

def _study(conn, study_id: Any) -> sqlite3.Row:
    if not isinstance(study_id, str) or not study_id:
        raise LwError("invalid_input", "study_id is required.", field="study_id")
    row = conn.execute("SELECT * FROM studies WHERE id = ?", (study_id,)).fetchone()
    if row is None:
        raise LwError("unknown_study", f"No study {study_id!r} in this profile.", study_id=study_id)
    return row


def active_study_for_session(conn, session_id: str) -> Optional[sqlite3.Row]:
    if not session_id:
        return None
    return conn.execute(
        "SELECT * FROM studies WHERE hermes_session_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
        (session_id,)).fetchone()


def _exercise(conn, study_id: str, exercise_id: Any) -> sqlite3.Row:
    if not isinstance(exercise_id, str) or not exercise_id:
        raise LwError("invalid_input", "exercise_id is required.", field="exercise_id")
    row = conn.execute("SELECT * FROM exercises WHERE id = ? AND study_id = ?", (exercise_id, study_id)).fetchone()
    if row is None:
        raise LwError("unknown_exercise", f"No exercise {exercise_id!r} in this study.", exercise_id=exercise_id)
    return row


def _open_exercise(conn, study_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM exercises WHERE study_id = ? AND state IN ('awaiting_answer','awaiting_check') "
        "ORDER BY sequence DESC LIMIT 1", (study_id,)).fetchone()


def _latest_exercise(conn, study_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM exercises WHERE study_id = ? ORDER BY sequence DESC LIMIT 1",
                        (study_id,)).fetchone()


def _latest_reference(conn, exercise_id: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM exercise_references WHERE exercise_id = ? ORDER BY revision DESC LIMIT 1",
        (exercise_id,)).fetchone()


def _latest_attempt(conn, exercise_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM attempts WHERE exercise_id = ? ORDER BY number DESC LIMIT 1",
                        (exercise_id,)).fetchone()


def _latest_assessment(conn, attempt_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM assessments WHERE attempt_id = ? ORDER BY revision DESC LIMIT 1",
                        (attempt_id,)).fetchone()


def _open_contest(conn, attempt_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT c.* FROM contests c WHERE c.attempt_id = ? AND NOT EXISTS "
        "(SELECT 1 FROM assessments s WHERE s.responds_to_contest_id = c.id) ORDER BY c.created_at DESC LIMIT 1",
        (attempt_id,)).fetchone()


def _latest_plan(conn, study_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM plan_revisions WHERE study_id = ? ORDER BY revision DESC LIMIT 1",
                        (study_id,)).fetchone()


def _count(conn, sql: str, args: tuple) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


def _require_active(study: sqlite3.Row) -> None:
    if study["status"] != "active":
        raise LwError("study_closed", "This study is archived; start a new one.", study_id=study["id"])


def _require_agent_binding(study: sqlite3.Row, session_id: str) -> None:
    """Agent writes must come from the chat the study is bound to."""
    _require_active(study)
    if not session_id:
        raise LwError("no_session", "This tool needs a Hermes chat session.")
    if study["hermes_session_id"] != session_id:
        raise LwError("wrong_session",
                      "This study belongs to another chat. Open that chat to continue it, or start a new study here.",
                      study_id=study["id"])


def _require_fresh(conn, study: sqlite3.Row, study_revision: Any) -> None:
    try:
        seen = _int(study_revision, "study_revision")
    except LwError:
        raise LwError("invalid_input", "study_revision must be the integer from your latest learning_workbench result.",
                      field="study_revision") from None
    current = int(study["revision"])
    if seen > current:
        raise LwError("invalid_input", f"study_revision {seen} is newer than the study ({current}).",
                      study_revision=current)
    if seen < _learner_revision(conn, study["id"]):
        raise LwError("stale_study",
                      "The learner changed the study after your last read. Call learning_workbench_get and decide again.",
                      study_revision=current)


def _help_class(conn, exercise_id: str) -> tuple[str, int, bool, bool]:
    hints_seen = _count(conn, "SELECT COUNT(*) FROM events WHERE exercise_id = ? AND kind = 'hint_shown'", (exercise_id,))
    solution_seen = _count(conn, "SELECT COUNT(*) FROM events WHERE exercise_id = ? AND kind = 'solution_revealed'",
                           (exercise_id,)) > 0
    chat_seen = _count(conn, "SELECT COUNT(*) FROM events WHERE exercise_id = ? AND kind = 'chat_while_open'",
                       (exercise_id,)) > 0
    feedback_seen = _count(conn, "SELECT COUNT(*) FROM assessments s JOIN attempts a ON a.id = s.attempt_id "
                                 "WHERE a.exercise_id = ?", (exercise_id,)) > 0
    if solution_seen:
        cls = "after_reveal"
    elif chat_seen:  # chat help is unbounded (it may have included the answer), so it outranks a frozen hint
        cls = "after_chat"
    elif hints_seen:
        cls = "hinted"
    elif feedback_seen:
        cls = "after_feedback"
    else:
        cls = "unaided"
    return cls, hints_seen, solution_seen, feedback_seen


def _open_for_chat(conn, session_id: str) -> Optional[tuple[str, sqlite3.Row]]:
    study = active_study_for_session(conn, session_id)
    if study is None:
        return None
    ex = conn.execute("SELECT id, sequence, kind, prompt FROM exercises WHERE study_id = ? AND state = 'awaiting_answer' "
                      "ORDER BY sequence DESC LIMIT 1", (study["id"],)).fetchone()
    return (study["id"], ex) if ex is not None else None


def _chat_info(study_id: str, ex: sqlite3.Row) -> dict:
    return {"study_id": study_id, "exercise_id": ex["id"], "sequence": ex["sequence"], "kind": ex["kind"],
            "prompt": ex["prompt"]}


def open_exercise_for_chat(conn, *, session_id: str) -> Optional[dict]:
    """The exercise in this chat waiting for the learner's own answer, if any (read-only)."""
    found = _open_for_chat(conn, session_id)
    return _chat_info(*found) if found else None


def note_chat_turn(conn, *, session_id: str) -> Optional[dict]:
    """A learner chat turn is starting while this chat's study has an exercise waiting for their own answer.

    Records it, so the next attempt counts as made after chat help (whatever the tutor replies, the plugin cannot
    see it), and returns what the agent must be reminded of this turn. No study revision bump: nothing the agent
    may write depends on it, and a bump would make its next write fail as stale for no reason."""
    if _open_for_chat(conn, session_id) is None:  # cheap read first; most turns have no open exercise
        return None
    with write_tx(conn):
        found = _open_for_chat(conn, session_id)
        if found is None:
            return None
        study_id, ex = found
        _event(conn, study_id, "learner", "chat_while_open", ex["id"])
    return _chat_info(study_id, ex)


# ---------------------------------------------------------------------------------------------
# studies
# ---------------------------------------------------------------------------------------------

def start_study(conn, *, actor: str, session_id: str, goal: Any, material_text: Any = "",
                material_title: Any = "", material_origin: Any = "") -> dict:
    goal_s = _clean(goal, "goal", MAX_GOAL)
    text = _clean(material_text, "material_text", MAX_MATERIAL, required=False)
    title = _clean(material_title, "material_title", MAX_TITLE, required=False)
    if not title:
        title = ("Pasted text" if actor == "learner" else "Material") if text else "No material"
    origin = _clean(material_origin, "material_origin", MAX_TITLE, required=False)
    if not session_id:
        raise LwError("no_session", "A study must be started inside a Hermes chat.")
    source = ("pasted_by_learner" if actor == "learner" else "attached_by_agent") if text else "none"
    with write_tx(conn):
        existing = active_study_for_session(conn, session_id)
        if existing is not None:
            raise LwError("study_exists",
                          "This chat already has an active study. Continue it (learning_workbench_get) or finish it "
                          "in the Workbench first.", study_id=existing["id"])
        sid = new_id("st")
        ts = now()
        conn.execute(
            "INSERT INTO studies(id, created_at, updated_at, revision, status, goal, hermes_session_id, created_by, "
            "material_title, material_text, material_source, material_origin, material_sha256) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, ts, ts, 1, "active", goal_s, session_id, actor, title, text, source, origin,
             hashlib.sha256(text.encode("utf-8")).hexdigest()))
        _event(conn, sid, actor, "study_started", goal=goal_s, material_chars=len(text), material_source=source)
        if actor == "learner":
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (f"learner_rev:{sid}", "1"))
    return {"study_id": sid, "study_revision": 1}


def archive_study(conn, *, study_id: str) -> dict:
    with write_tx(conn):
        study = _study(conn, study_id)
        if study["status"] == "archived":
            return {"study_id": study_id, "status": "archived", "study_revision": study["revision"], "duplicate": True}
        conn.execute("UPDATE studies SET status = 'archived' WHERE id = ?", (study_id,))
        conn.execute("DELETE FROM drafts WHERE study_id = ?", (study_id,))
        _event(conn, study_id, "learner", "study_archived")
        rev = _bump(conn, study_id, "learner")
    return {"study_id": study_id, "status": "archived", "study_revision": rev, "duplicate": False}


def rebind_session(conn, *, old_session_id: str, new_session_id: str) -> Optional[str]:
    """Follow a chat whose session id rotated (context compaction). Returns the rebound study id."""
    if not old_session_id or not new_session_id or old_session_id == new_session_id:
        return None
    with write_tx(conn):
        study = active_study_for_session(conn, old_session_id)
        if study is None or active_study_for_session(conn, new_session_id) is not None:
            return None
        conn.execute("UPDATE studies SET hermes_session_id = ? WHERE id = ?", (new_session_id, study["id"]))
        _event(conn, study["id"], "host", "study_rebound", from_session=old_session_id, to_session=new_session_id)
        _bump(conn, study["id"], "host")
    return study["id"]


# ---------------------------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------------------------

def propose_plan(conn, *, session_id: str, study_id: Any, study_revision: Any, steps: Any, note: Any = "") -> dict:
    if not isinstance(steps, list) or not (MIN_STEPS <= len(steps) <= MAX_STEPS):
        raise LwError("invalid_input", f"steps must be a list of {MIN_STEPS}-{MAX_STEPS} steps, smallest useful route first.",
                      field="steps")
    note_s = _clean(note, "note", MAX_NOTE, required=False)
    cleaned = []
    for i, item in enumerate(steps):
        if not isinstance(item, dict):
            raise LwError("invalid_input", f"steps[{i}] must be an object with title and why.", field=f"steps[{i}]")
        extra = set(item) - {"title", "why", "step_id"}
        if extra:
            raise LwError("invalid_input", f"steps[{i}] has unknown field(s): {', '.join(sorted(extra))}.",
                          field=f"steps[{i}]")
        cleaned.append({"title": _clean(item.get("title"), f"steps[{i}].title", MAX_STEP_TITLE),
                        "why": _clean(item.get("why"), f"steps[{i}].why", MAX_STEP_WHY),
                        "step_id": item.get("step_id")})
    with write_tx(conn):
        study = _study(conn, study_id)
        _require_agent_binding(study, session_id)
        _require_fresh(conn, study, study_revision)
        latest = _latest_plan(conn, study_id)
        previous = {it["step_id"]: it for it in json.loads(latest["items_json"])} if latest else {}
        seen_ids: set[str] = set()
        items = []
        ts = now()
        for i, step in enumerate(cleaned):
            sid = step["step_id"]
            if sid:
                if not isinstance(sid, str) or conn.execute("SELECT 1 FROM steps WHERE id = ? AND study_id = ?",
                                                            (sid, study_id)).fetchone() is None:
                    raise LwError("unknown_step", f"steps[{i}].step_id {sid!r} is not a step of this study. Omit "
                                  "step_id for a new step.", step_id=sid)
                if sid in seen_ids:
                    raise LwError("invalid_input", f"step {sid} appears twice.", step_id=sid)
                status = previous.get(sid, {}).get("status", "active")
            else:
                sid = new_id("sp")
                conn.execute("INSERT INTO steps(id, study_id, created_at, created_by, title, why) VALUES (?,?,?,?,?,?)",
                             (sid, study_id, ts, "agent", step["title"], step["why"]))
                status = "active"
            seen_ids.add(sid)
            items.append({"step_id": sid, "title": step["title"], "why": step["why"], "status": status})
        revision = (latest["revision"] + 1) if latest else 1
        conn.execute("INSERT INTO plan_revisions(study_id, revision, created_at, actor, note, items_json) "
                     "VALUES (?,?,?,?,?,?)", (study_id, revision, ts, "agent", note_s,
                                              json.dumps(items, ensure_ascii=False)))
        removed = sorted(set(previous) - seen_ids)
        _event(conn, study_id, "agent", "plan_proposed", plan_revision=revision, steps=len(items), removed=removed)
        rev = _bump(conn, study_id, "agent")
    return {"plan_revision": revision, "steps": items, "removed_step_ids": removed, "study_revision": rev}


def edit_route(conn, *, study_id: str, base_plan_revision: Any, op: Any, step_id: Any, title: Any = None) -> dict:
    if op not in ROUTE_OPS:
        raise LwError("invalid_input", f"op must be one of {', '.join(ROUTE_OPS)}.", field="op")
    base = _int(base_plan_revision, "base_plan_revision")
    new_title = _clean(title, "title", MAX_STEP_TITLE) if op == "rename" else None
    with write_tx(conn):
        study = _study(conn, study_id)
        _require_active(study)
        latest = _latest_plan(conn, study_id)
        if latest is None:
            raise LwError("no_route", "There is no route to edit yet.")
        if latest["revision"] != base:
            raise LwError("stale_route", "The route changed since you loaded it. Reload and try again.",
                          plan_revision=latest["revision"])
        items = json.loads(latest["items_json"])
        item = next((it for it in items if it["step_id"] == step_id), None)
        if item is None:
            raise LwError("unknown_step", f"No step {step_id!r} in the current route.", step_id=step_id)
        if op == "rename":
            if item["title"] == new_title:
                return {"plan_revision": latest["revision"], "study_revision": study["revision"], "duplicate": True}
            item["title"] = new_title
        elif op == "skip":
            item["status"] = "skipped"
        else:
            item["status"] = "active"
        revision = latest["revision"] + 1
        conn.execute("INSERT INTO plan_revisions(study_id, revision, created_at, actor, note, items_json) "
                     "VALUES (?,?,?,?,?,?)", (study_id, revision, now(), "learner", f"{op} {step_id}",
                                              json.dumps(items, ensure_ascii=False)))
        _event(conn, study_id, "learner", "route_edited", op=op, step_id=step_id, plan_revision=revision)
        rev = _bump(conn, study_id, "learner")
    return {"plan_revision": revision, "study_revision": rev, "duplicate": False}


# ---------------------------------------------------------------------------------------------
# exercises (agent side)
# ---------------------------------------------------------------------------------------------

def propose_exercise(conn, *, session_id: str, study_id: Any, study_revision: Any, kind: Any, prompt: Any,
                     reference_answer: Any, rubric: Any, hints: Any = None, step_id: Any = None,
                     reasoning: Any = "optional", lesson: Any = None) -> dict:
    if kind not in EXERCISE_KINDS:
        raise LwError("invalid_input", f"kind must be one of {', '.join(EXERCISE_KINDS)}.", field="kind")
    if reasoning is None:
        reasoning = "optional"
    if reasoning not in REASONING_MODES:
        raise LwError("invalid_input", "reasoning must be 'optional' or 'requested'.", field="reasoning")
    prompt_s = _clean(prompt, "prompt", MAX_PROMPT)
    ref_s = _clean(reference_answer, "reference_answer", MAX_REFERENCE)
    rubric_s = _clean(rubric, "rubric", MAX_RUBRIC)
    if hints is None:
        hints = []
    if not isinstance(hints, list) or len(hints) > MAX_HINTS:
        raise LwError("invalid_input", f"hints must be a list of at most {MAX_HINTS} strings.", field="hints")
    hint_list = [_clean(h, f"hints[{i}]", MAX_HINT) for i, h in enumerate(hints)]
    lesson_s = _clean(lesson, "lesson", MAX_LESSON, required=False)
    if lesson_s and kind != "check":
        raise LwError("invalid_input",
                      "Only a check carries a lesson. A diagnostic finds the starting point and practice shows what the "
                      "learner can do without new teaching; omit lesson.", field="lesson")
    with write_tx(conn):
        study = _study(conn, study_id)
        _require_agent_binding(study, session_id)
        _require_fresh(conn, study, study_revision)
        open_ex = _open_exercise(conn, study_id)
        if open_ex is not None:
            waiting = ("the learner's answer" if open_ex["state"] == "awaiting_answer"
                       else "your check of the submitted attempt (learning_workbench_assess)")
            raise LwError("exercise_pending",
                          f"Exercise {open_ex['sequence']} ({open_ex['id']}) is still waiting for {waiting}. "
                          "One exercise at a time: do not pose another and do not reveal its answer.",
                          exercise_id=open_ex["id"], state=open_ex["state"])
        plan = _latest_plan(conn, study_id)
        step_title = ""
        if kind == "diagnostic":
            if plan is not None:
                raise LwError("invalid_input", "The route exists; use kind 'check' or 'practice' with a step_id.",
                              field="kind")
            used = _count(conn, "SELECT COUNT(*) FROM exercises WHERE study_id = ? AND kind = 'diagnostic'", (study_id,))
            if used >= MAX_DIAGNOSTICS:
                raise LwError("diagnosis_complete",
                              f"Diagnosis is capped at {MAX_DIAGNOSTICS} questions. Propose the route now "
                              "(learning_workbench_propose_plan).", used=used)
            if step_id:
                raise LwError("invalid_input", "Diagnostic questions come before the route; omit step_id.",
                              field="step_id")
        else:
            if plan is None:
                raise LwError("no_route", "Propose the route first (learning_workbench_propose_plan), then pose "
                              "checks and practice for its steps.")
            items = {it["step_id"]: it for it in json.loads(plan["items_json"])}
            if not step_id:
                raise LwError("invalid_input", "Checks and practice belong to a route step: pass step_id.",
                              field="step_id")
            if step_id not in items:
                raise LwError("unknown_step", f"No step {step_id!r} in the current route.", step_id=step_id)
            if items[step_id]["status"] == "skipped":
                raise LwError("step_skipped", "The learner skipped this step. Pick another step or ask them in chat.",
                              step_id=step_id)
            step_title = items[step_id]["title"]
            if kind == "check" and not lesson_s:
                checked_before = conn.execute("SELECT 1 FROM exercises WHERE study_id = ? AND step_id = ? "
                                              "AND kind = 'check' LIMIT 1", (study_id, step_id)).fetchone()
                if checked_before is None:
                    raise LwError("lesson_required",
                                  f"Teach step '{step_title}' before checking it: pass `lesson`, the explanation the "
                                  "learner reads in the pane right above the question (a few short paragraphs or a "
                                  "worked example, plain text). Teach the idea; do not solve this exercise.",
                                  step_id=step_id)
        seq = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 AS n FROM exercises WHERE study_id = ?",
                           (study_id,)).fetchone()["n"]
        eid = new_id("ex")
        ts = now()
        conn.execute(
            "INSERT INTO exercises(id, study_id, sequence, step_id, step_title, kind, created_at, created_by, prompt, "
            "lesson, reasoning_mode, hints_json, state, state_changed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, study_id, seq, step_id or None, step_title, kind, ts, "agent", prompt_s, lesson_s, reasoning,
             json.dumps(hint_list, ensure_ascii=False), "awaiting_answer", ts))
        conn.execute(
            "INSERT INTO exercise_references(exercise_id, revision, created_at, reference_answer, rubric, reason) "
            "VALUES (?,?,?,?,?,?)", (eid, 1, ts, ref_s, rubric_s, "initial"))
        _event(conn, study_id, "agent", "exercise_proposed", eid, exercise_kind=kind, sequence=seq,
               hints=len(hint_list), lesson_chars=len(lesson_s))
        rev = _bump(conn, study_id, "agent")
    return {"exercise_id": eid, "sequence": seq, "state": "awaiting_answer", "study_revision": rev}


def revise_reference(conn, *, session_id: str, study_id: Any, exercise_id: Any, reference_answer: Any,
                     rubric: Any, reason: Any) -> dict:
    ref_s = _clean(reference_answer, "reference_answer", MAX_REFERENCE)
    rubric_s = _clean(rubric, "rubric", MAX_RUBRIC)
    reason_s = _clean(reason, "reason", MAX_REASON)
    with write_tx(conn):
        study = _study(conn, study_id)
        _require_agent_binding(study, session_id)
        ex = _exercise(conn, study_id, exercise_id)
        current = _latest_reference(conn, ex["id"])
        if current["reference_answer"] == ref_s and current["rubric"] == rubric_s:
            raise LwError("no_change", "The reference answer and rubric are unchanged.")
        revision = current["revision"] + 1
        conn.execute("INSERT INTO exercise_references(exercise_id, revision, created_at, reference_answer, rubric, "
                     "reason) VALUES (?,?,?,?,?,?)", (ex["id"], revision, now(), ref_s, rubric_s, reason_s))
        _event(conn, study_id, "agent", "reference_revised", ex["id"], reference_revision=revision, reason=reason_s)
        rev = _bump(conn, study_id, "agent")
    return {"exercise_id": ex["id"], "reference_revision": revision, "study_revision": rev}


def assess(conn, *, session_id: str, study_id: Any, attempt_id: Any, verdict: Any, explanation: Any,
           evidence_quote: Any, gap: Any = "", responds_to_contest_id: Any = None) -> dict:
    if verdict not in VERDICTS:
        raise LwError("invalid_input", f"verdict must be one of {', '.join(VERDICTS)}.", field="verdict")
    explanation_s = _clean(explanation, "explanation", MAX_EXPLANATION)
    quote_s = _clean(evidence_quote, "evidence_quote", MAX_QUOTE).strip("\"'“”‘’ ")
    if not quote_s:
        raise LwError("invalid_input", "evidence_quote is empty.", field="evidence_quote")
    gap_s = _clean(gap, "gap", MAX_GAP, required=False)
    with write_tx(conn):
        study = _study(conn, study_id)
        _require_agent_binding(study, session_id)
        if not isinstance(attempt_id, str) or not attempt_id:
            raise LwError("invalid_input", "attempt_id is required.", field="attempt_id")
        attempt = conn.execute("SELECT * FROM attempts WHERE id = ? AND study_id = ?", (attempt_id, study_id)).fetchone()
        if attempt is None:
            raise LwError("unknown_attempt", f"No attempt {attempt_id!r} in this study.", attempt_id=attempt_id)
        ex = _exercise(conn, study_id, attempt["exercise_id"])
        haystacks = [_norm(attempt["answer"]), _norm(attempt["reasoning"])]
        needle = _norm(quote_s)
        if not any(needle in h for h in haystacks if h):
            raise LwError("quote_not_found",
                          "evidence_quote must be copied verbatim from the learner's answer or reasoning for this "
                          "attempt. Quote the words your verdict rests on.", attempt_id=attempt_id)
        latest = _latest_assessment(conn, attempt_id)
        contest_id = None
        if responds_to_contest_id:
            contest = _open_contest(conn, attempt_id)
            if contest is None or contest["id"] != responds_to_contest_id:
                raise LwError("unknown_contest", "No open learner contest with that id on this attempt.",
                              contest_id=responds_to_contest_id)
            contest_id = contest["id"]
        else:
            if latest is not None:
                raise LwError("already_assessed",
                              "This attempt already has a check. A new verdict is only recorded in response to a learner "
                              "contest (responds_to_contest_id).", attempt_id=attempt_id)
            newest = _latest_attempt(conn, ex["id"])
            if newest["id"] != attempt_id or ex["state"] != "awaiting_check":
                raise LwError("stale_attempt",
                              "That attempt is no longer the one waiting for a check. Call learning_workbench_get.",
                              attempt_id=attempt_id, exercise_state=ex["state"])
        revision = (latest["revision"] + 1) if latest else 1
        ref = _latest_reference(conn, ex["id"])
        aid = new_id("as")
        conn.execute(
            "INSERT INTO assessments(id, study_id, attempt_id, revision, created_at, verdict, explanation, evidence_quote, "
            "gap, reference_revision, responds_to_contest_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (aid, study_id, attempt_id, revision, now(), verdict, explanation_s, quote_s, gap_s, ref["revision"],
             contest_id))
        if contest_id is None:
            conn.execute("UPDATE exercises SET state = 'checked', state_changed_at = ? WHERE id = ?", (now(), ex["id"]))
        _event(conn, study_id, "agent", "assessment_proposed", ex["id"], assessment_id=aid, attempt_id=attempt_id,
               verdict=verdict, revision=revision, responds_to_contest_id=contest_id)
        rev = _bump(conn, study_id, "agent")
    return {"assessment_id": aid, "attempt_id": attempt_id, "exercise_id": ex["id"], "revision": revision,
            "verdict": verdict, "help_class": attempt["help_class"], "exercise_kind": ex["kind"],
            "study_revision": rev}


# ---------------------------------------------------------------------------------------------
# exercises (learner side)
# ---------------------------------------------------------------------------------------------

def save_draft(conn, *, study_id: str, exercise_id: str, base_revision: Any, answer: Any, reasoning: Any) -> dict:
    answer_s = _clean(answer, "answer", MAX_ANSWER, required=False)
    reasoning_s = _clean(reasoning, "reasoning", MAX_REASONING, required=False)
    base = _int(base_revision, "base_revision")
    with write_tx(conn):
        _require_active(_study(conn, study_id))
        ex = _exercise(conn, study_id, exercise_id)
        if ex["state"] != "awaiting_answer":
            raise LwError("exercise_not_open", "This exercise is not waiting for an answer any more.",
                          state=ex["state"])
        row = conn.execute("SELECT * FROM drafts WHERE exercise_id = ?", (exercise_id,)).fetchone()
        current = row["revision"] if row else 0
        if base != current:
            raise LwError("draft_conflict",
                          "This draft changed in another window. Reload it before saving again.",
                          revision=current, answer=row["answer"] if row else "",
                          reasoning=row["reasoning"] if row else "")
        new_rev = current + 1
        conn.execute(
            "INSERT INTO drafts(exercise_id, study_id, revision, answer, reasoning, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(exercise_id) DO UPDATE SET revision=excluded.revision, answer=excluded.answer, "
            "reasoning=excluded.reasoning, updated_at=excluded.updated_at",
            (exercise_id, study_id, new_rev, answer_s, reasoning_s, now()))
    return {"draft_revision": new_rev}


def submit_attempt(conn, *, study_id: str, exercise_id: str, client_request_id: Any, answer: Any,
                   reasoning: Any, channel: str) -> dict:
    """Record one learner attempt exactly once per client_request_id."""
    if not isinstance(client_request_id, str) or not (8 <= len(client_request_id) <= 80):
        raise LwError("invalid_input", "client_request_id must be a unique string of 8-80 characters.")
    answer_s = _clean(answer, "answer", MAX_ANSWER)
    reasoning_s = _clean(reasoning, "reasoning", MAX_REASONING, required=False)
    with write_tx(conn):
        dup = conn.execute("SELECT * FROM attempts WHERE study_id = ? AND client_request_id = ?",
                           (study_id, client_request_id)).fetchone()
        if dup is not None:
            if dup["exercise_id"] != exercise_id or dup["answer"] != answer_s or dup["reasoning"] != reasoning_s:
                raise LwError("request_id_reused", "That request id was already used for a different submission.")
            return _attempt_receipt(conn, dup, duplicate=True)
        study = _study(conn, study_id)
        _require_active(study)
        ex = _exercise(conn, study_id, exercise_id)
        if ex["state"] != "awaiting_answer":
            raise LwError("exercise_not_open",
                          "This exercise is not waiting for an answer (it was answered, skipped or replaced). "
                          "Reload the workbench.", state=ex["state"])
        help_class, hints_seen, solution_seen, feedback_seen = _help_class(conn, exercise_id)
        number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 AS n FROM attempts WHERE exercise_id = ?",
                              (exercise_id,)).fetchone()["n"]
        ref = _latest_reference(conn, exercise_id)
        aid = new_id("at")
        conn.execute(
            "INSERT INTO attempts(id, study_id, exercise_id, number, created_at, channel, answer, reasoning, help_class, "
            "hints_seen, solution_seen, feedback_seen, reference_revision, client_request_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, study_id, exercise_id, number, now(), channel, answer_s, reasoning_s, help_class, hints_seen,
             int(solution_seen), int(feedback_seen), ref["revision"], client_request_id))
        conn.execute("UPDATE exercises SET state = 'awaiting_check', state_changed_at = ? WHERE id = ?",
                     (now(), exercise_id))
        conn.execute("DELETE FROM drafts WHERE exercise_id = ?", (exercise_id,))
        _event(conn, study_id, "learner", "attempt_submitted", exercise_id, attempt_id=aid, number=number,
               help_class=help_class, channel=channel)
        _bump(conn, study_id, "learner")
        row = conn.execute("SELECT * FROM attempts WHERE id = ?", (aid,)).fetchone()
        return _attempt_receipt(conn, row, duplicate=False)


def _attempt_receipt(conn, row: sqlite3.Row, *, duplicate: bool) -> dict:
    rev = conn.execute("SELECT revision FROM studies WHERE id = ?", (row["study_id"],)).fetchone()["revision"]
    seq = conn.execute("SELECT sequence FROM exercises WHERE id = ?", (row["exercise_id"],)).fetchone()["sequence"]
    return {"attempt_id": row["id"], "exercise_id": row["exercise_id"], "exercise_sequence": seq,
            "number": row["number"], "help_class": row["help_class"], "duplicate": duplicate, "study_revision": rev}


def show_hint(conn, *, study_id: str, exercise_id: str, seen: Any) -> dict:
    """Reveal the next frozen hint. ``seen`` is how many hints the learner's screen showed (double-click guard)."""
    seen_n = _int(seen, "seen")
    with write_tx(conn):
        _require_active(_study(conn, study_id))
        ex = _exercise(conn, study_id, exercise_id)
        hints = json.loads(ex["hints_json"])
        shown = _count(conn, "SELECT COUNT(*) FROM events WHERE exercise_id = ? AND kind = 'hint_shown'", (exercise_id,))
        if seen_n < shown:  # an earlier click already revealed it
            return {"hints": hints[:shown], "hints_shown": shown, "hints_total": len(hints), "duplicate": True}
        if ex["state"] != "awaiting_answer":
            raise LwError("exercise_not_open", "Hints are available while the exercise waits for your answer.",
                          state=ex["state"])
        if shown >= len(hints):
            raise LwError("no_more_hints", "No more hints for this exercise. You can reveal the solution or ask in chat.")
        _event(conn, study_id, "learner", "hint_shown", exercise_id, index=shown)
        _bump(conn, study_id, "learner")
    return {"hints": hints[:shown + 1], "hints_shown": shown + 1, "hints_total": len(hints), "duplicate": False}


def reveal_solution(conn, *, study_id: str, exercise_id: str) -> dict:
    with write_tx(conn):
        _require_active(_study(conn, study_id))
        ex = _exercise(conn, study_id, exercise_id)
        already = _count(conn, "SELECT COUNT(*) FROM events WHERE exercise_id = ? AND kind = 'solution_revealed'",
                         (exercise_id,)) > 0
        if not already:
            if ex["state"] not in ("awaiting_answer", "checked", "skipped"):
                raise LwError("exercise_not_open", "Wait for the tutor's check before revealing the solution.",
                              state=ex["state"])
            ref = _latest_reference(conn, exercise_id)
            _event(conn, study_id, "learner", "solution_revealed", exercise_id, reference_revision=ref["revision"])
            _bump(conn, study_id, "learner")
        ref = _latest_reference(conn, exercise_id)
    return {"exercise_id": exercise_id, "reference_answer": ref["reference_answer"],
            "reference_revision": ref["revision"], "duplicate": already}


def skip_exercise(conn, *, study_id: str, exercise_id: str) -> dict:
    with write_tx(conn):
        _require_active(_study(conn, study_id))
        ex = _exercise(conn, study_id, exercise_id)
        if ex["state"] == "skipped":
            return {"exercise_id": exercise_id, "state": "skipped", "duplicate": True}
        if ex["state"] != "awaiting_answer":
            raise LwError("exercise_not_open", "Only an exercise waiting for your answer can be skipped.",
                          state=ex["state"])
        conn.execute("UPDATE exercises SET state = 'skipped', state_changed_at = ? WHERE id = ?", (now(), exercise_id))
        conn.execute("DELETE FROM drafts WHERE exercise_id = ?", (exercise_id,))
        _event(conn, study_id, "learner", "exercise_skipped", exercise_id)
        _bump(conn, study_id, "learner")
    return {"exercise_id": exercise_id, "state": "skipped", "duplicate": False}


def retry_exercise(conn, *, study_id: str, exercise_id: str) -> dict:
    with write_tx(conn):
        _require_active(_study(conn, study_id))
        ex = _exercise(conn, study_id, exercise_id)
        if ex["state"] == "awaiting_answer":
            return {"exercise_id": exercise_id, "state": "awaiting_answer", "duplicate": True}
        if ex["state"] != "checked":
            raise LwError("exercise_not_open", "You can try again after the tutor has checked your answer.",
                          state=ex["state"])
        latest_ex = _latest_exercise(conn, study_id)
        if latest_ex["id"] != exercise_id:
            raise LwError("superseded", "The tutor has posed a newer exercise; answer that one.",
                          exercise_id=latest_ex["id"])
        last = _latest_attempt(conn, exercise_id)
        verdict = _latest_assessment(conn, last["id"])
        if verdict is not None and verdict["verdict"] == "correct":
            raise LwError("already_correct", "This answer was checked as correct. Ask the tutor for a fresh problem.")
        conn.execute("UPDATE exercises SET state = 'awaiting_answer', state_changed_at = ? WHERE id = ?",
                     (now(), exercise_id))
        conn.execute(
            "INSERT INTO drafts(exercise_id, study_id, revision, answer, reasoning, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(exercise_id) DO UPDATE SET revision=drafts.revision + 1, answer=excluded.answer, "
            "reasoning=excluded.reasoning, updated_at=excluded.updated_at",
            (exercise_id, study_id, 1, last["answer"], last["reasoning"], now()))
        _event(conn, study_id, "learner", "retry_started", exercise_id, after_attempt=last["id"])
        _bump(conn, study_id, "learner")
    return {"exercise_id": exercise_id, "state": "awaiting_answer", "duplicate": False}


def contest(conn, *, study_id: str, assessment_id: Any, text: Any, client_request_id: Any) -> dict:
    if not isinstance(client_request_id, str) or not (8 <= len(client_request_id) <= 80):
        raise LwError("invalid_input", "client_request_id must be a unique string of 8-80 characters.")
    text_s = _clean(text, "text", MAX_CONTEST)
    with write_tx(conn):
        dup = conn.execute("SELECT * FROM contests WHERE study_id = ? AND client_request_id = ?",
                           (study_id, client_request_id)).fetchone()
        if dup is not None:
            if dup["assessment_id"] != assessment_id or dup["text"] != text_s:
                raise LwError("request_id_reused", "That request id was already used for a different contest.")
            return {"contest_id": dup["id"], "attempt_id": dup["attempt_id"], "duplicate": True}
        _require_active(_study(conn, study_id))
        row = conn.execute("SELECT * FROM assessments WHERE id = ? AND study_id = ?", (assessment_id, study_id)).fetchone()
        if row is None:
            raise LwError("unknown_assessment", "No such check in this study.", assessment_id=assessment_id)
        latest = _latest_assessment(conn, row["attempt_id"])
        if latest["id"] != row["id"]:
            raise LwError("stale_assessment", "That check has been revised; contest the latest one.",
                          assessment_id=latest["id"])
        if _open_contest(conn, row["attempt_id"]) is not None:
            raise LwError("contest_pending", "Your earlier contest on this attempt is still waiting for the tutor.")
        cid = new_id("ct")
        conn.execute("INSERT INTO contests(id, study_id, assessment_id, attempt_id, created_at, text, client_request_id) "
                     "VALUES (?,?,?,?,?,?,?)", (cid, study_id, row["id"], row["attempt_id"], now(), text_s,
                                                client_request_id))
        attempt = conn.execute("SELECT exercise_id FROM attempts WHERE id = ?", (row["attempt_id"],)).fetchone()
        _event(conn, study_id, "learner", "assessment_contested", attempt["exercise_id"], contest_id=cid,
               assessment_id=row["id"])
        _bump(conn, study_id, "learner")
    return {"contest_id": cid, "attempt_id": row["attempt_id"], "duplicate": False}


# ---------------------------------------------------------------------------------------------
# deletion
# ---------------------------------------------------------------------------------------------

def _study_counts(conn, study_id: str) -> dict:
    return {"exercises": _count(conn, "SELECT COUNT(*) FROM exercises WHERE study_id = ?", (study_id,)),
            "attempts": _count(conn, "SELECT COUNT(*) FROM attempts WHERE study_id = ?", (study_id,)),
            "checks": _count(conn, "SELECT COUNT(*) FROM assessments WHERE study_id = ?", (study_id,))}


def request_delete(conn, *, study_id: str) -> dict:
    token = secrets.token_urlsafe(18)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=DELETE_TOKEN_MINUTES)).isoformat()
    with write_tx(conn):
        study = _study(conn, study_id)
        conn.execute("INSERT OR REPLACE INTO delete_tokens(study_id, token, expires_at) VALUES (?,?,?)",
                     (study_id, hashlib.sha256(token.encode()).hexdigest(), expires))
        counts = _study_counts(conn, study_id)
    return {"study_id": study_id, "goal": study["goal"], "confirm_token": token, "expires_at": expires, **counts}


def confirm_delete(conn, *, study_id: str, token: Any) -> dict:
    if not isinstance(token, str) or not token:
        raise LwError("invalid_input", "Deletion needs the confirmation token from the first step.")
    with write_tx(conn):
        _study(conn, study_id)
        row = conn.execute("SELECT * FROM delete_tokens WHERE study_id = ?", (study_id,)).fetchone()
        if row is None or not secrets.compare_digest(row["token"], hashlib.sha256(token.encode()).hexdigest()):
            raise LwError("delete_not_confirmed", "Confirmation did not match. Start the deletion again.")
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            conn.execute("DELETE FROM delete_tokens WHERE study_id = ?", (study_id,))
            raise LwError("delete_expired", "The confirmation expired. Start the deletion again.")
        counts = _study_counts(conn, study_id)
        conn.execute("DELETE FROM studies WHERE id = ?", (study_id,))
        conn.execute("DELETE FROM meta WHERE key = ?", (f"learner_rev:{study_id}",))
    return {"study_id": study_id, "deleted": True, **counts}


# ---------------------------------------------------------------------------------------------
# read models
# ---------------------------------------------------------------------------------------------

def _rows(conn, sql: str, args: tuple) -> list[sqlite3.Row]:
    return list(conn.execute(sql, args).fetchall())


def _route(conn, study_id: str, exercises: list[dict]) -> Optional[dict]:
    plan = _latest_plan(conn, study_id)
    if plan is None:
        return None
    items = json.loads(plan["items_json"])
    current_ids = {it["step_id"] for it in items}
    steps_out = []
    for it in items:
        steps_out.append({**it, "evidence": _evidence([e for e in exercises if e["step_id"] == it["step_id"]])})
    removed = []
    for row in _rows(conn, "SELECT * FROM steps WHERE study_id = ? ORDER BY created_at", (study_id,)):
        if row["id"] in current_ids:
            continue
        linked = [e for e in exercises if e["step_id"] == row["id"]]
        if linked:  # removed from the route, but its evidence stays visible
            removed.append({"step_id": row["id"], "title": row["title"], "why": row["why"], "status": "removed",
                            "evidence": _evidence(linked)})
    return {"plan_revision": plan["revision"], "revised_by": plan["actor"], "steps": steps_out,
            "removed_steps": removed}


def _evidence(exercises: list[dict]) -> dict:
    """Counts only. One correct answer is evidence, not mastery."""
    out = {"attempts": 0, "correct_unaided": 0, "correct_with_help": 0, "independent_practice_correct": 0}
    for ex in exercises:
        for a in ex["attempts"]:
            out["attempts"] += 1
            final = a["assessments"][-1]["verdict"] if a["assessments"] else None
            if final != "correct":
                continue
            if a["help_class"] == "unaided":
                out["correct_unaided"] += 1
                if ex["kind"] == "practice":
                    out["independent_practice_correct"] += 1
            else:
                out["correct_with_help"] += 1
    return out


def study_view(conn, study_id: str, *, audience: str) -> dict:
    """Serialized study. ``audience`` is 'agent' (no drafts) or 'learner' (no hidden solutions/hints)."""
    study = _study(conn, study_id)
    exercises = _rows(conn, "SELECT * FROM exercises WHERE study_id = ? ORDER BY sequence", (study_id,))
    attempts = _rows(conn, "SELECT * FROM attempts WHERE study_id = ? ORDER BY exercise_id, number", (study_id,))
    assessments = _rows(conn, "SELECT * FROM assessments WHERE study_id = ? ORDER BY attempt_id, revision", (study_id,))
    contests = _rows(conn, "SELECT * FROM contests WHERE study_id = ? ORDER BY created_at", (study_id,))
    events = _rows(conn, "SELECT * FROM events WHERE study_id = ? "
                         "AND kind IN ('hint_shown','solution_revealed','chat_while_open') ORDER BY seq", (study_id,))
    answered = {s["responds_to_contest_id"] for s in assessments if s["responds_to_contest_id"]}
    by_attempt: dict[str, list[dict]] = {}
    for s in assessments:
        by_attempt.setdefault(s["attempt_id"], []).append({
            "assessment_id": s["id"], "revision": s["revision"], "created_at": s["created_at"],
            "verdict": s["verdict"], "explanation": s["explanation"], "evidence_quote": s["evidence_quote"],
            "gap": s["gap"], "reference_revision": s["reference_revision"],
            "responds_to_contest_id": s["responds_to_contest_id"]})
    contests_by_attempt: dict[str, list[dict]] = {}
    for c in contests:
        contests_by_attempt.setdefault(c["attempt_id"], []).append({
            "contest_id": c["id"], "assessment_id": c["assessment_id"], "created_at": c["created_at"],
            "text": c["text"], "answered": c["id"] in answered})
    out_ex = []
    for ex in exercises:
        hints = json.loads(ex["hints_json"])
        shown = sum(1 for e in events if e["exercise_id"] == ex["id"] and e["kind"] == "hint_shown")
        revealed = any(e["exercise_id"] == ex["id"] and e["kind"] == "solution_revealed" for e in events)
        chats = sum(1 for e in events if e["exercise_id"] == ex["id"] and e["kind"] == "chat_while_open")
        ref = _latest_reference(conn, ex["id"])
        item = {
            "exercise_id": ex["id"], "sequence": ex["sequence"], "kind": ex["kind"], "step_id": ex["step_id"],
            "step_title": ex["step_title"], "prompt": ex["prompt"], "lesson": ex["lesson"],
            "reasoning": ex["reasoning_mode"],
            "state": ex["state"], "created_at": ex["created_at"], "hints_total": len(hints),
            "hints_shown": shown, "solution_revealed": revealed, "chat_turns_while_open": chats,
            "reference_revision": ref["revision"],
            "attempts": [],
        }
        if audience == "agent":
            item["hints"] = hints
            item["reference_answer"] = ref["reference_answer"]
            item["rubric"] = ref["rubric"]
        else:
            item["hints"] = hints[:shown]
            if revealed:
                item["reference_answer"] = ref["reference_answer"]
        for a in attempts:
            if a["exercise_id"] != ex["id"]:
                continue
            item["attempts"].append({
                "attempt_id": a["id"], "number": a["number"], "created_at": a["created_at"],
                "answer": a["answer"], "reasoning": a["reasoning"], "help_class": a["help_class"],
                "hints_seen": a["hints_seen"], "solution_seen": bool(a["solution_seen"]),
                "channel": a["channel"], "reference_revision": a["reference_revision"],
                "assessments": by_attempt.get(a["id"], []), "contests": contests_by_attempt.get(a["id"], []),
            })
        last = item["attempts"][-1] if item["attempts"] else None
        item["outcome"] = (last["assessments"][-1]["verdict"] if last and last["assessments"] else None)
        out_ex.append(item)
    open_ex = next((e for e in out_ex if e["state"] in OPEN_STATES), None)
    pending_contests = [c for e in out_ex for a in e["attempts"] for c in a["contests"] if not c["answered"]]
    view = {
        "study_id": study["id"], "study_revision": study["revision"], "status": study["status"],
        "goal": study["goal"], "hermes_session_id": study["hermes_session_id"],
        "created_at": study["created_at"], "updated_at": study["updated_at"], "created_by": study["created_by"],
        "material": {"title": study["material_title"], "source": study["material_source"],
                     "origin": study["material_origin"], "chars": len(study["material_text"]),
                     "sha256": study["material_sha256"]},
        "route": _route(conn, study_id, out_ex),
        "diagnostics": {"used": sum(1 for e in out_ex if e["kind"] == "diagnostic"), "cap": MAX_DIAGNOSTICS},
        "exercises": out_ex,
        "open_exercise_id": open_ex["exercise_id"] if open_ex else None,
        "pending_contests": [c["contest_id"] for c in pending_contests],
    }
    if audience == "learner":
        view["material"]["text"] = study["material_text"]
        latest = out_ex[-1] if out_ex else None
        view["latest_exercise_id"] = latest["exercise_id"] if latest else None
        if open_ex and open_ex["state"] == "awaiting_answer":
            d = conn.execute("SELECT * FROM drafts WHERE exercise_id = ?", (open_ex["exercise_id"],)).fetchone()
            view["draft"] = ({"exercise_id": open_ex["exercise_id"], "revision": d["revision"], "answer": d["answer"],
                              "reasoning": d["reasoning"], "updated_at": d["updated_at"]} if d else
                             {"exercise_id": open_ex["exercise_id"], "revision": 0, "answer": "", "reasoning": "",
                              "updated_at": None})
    return view


def list_studies(conn) -> list[dict]:
    return [{"study_id": r["id"], "goal": r["goal"], "status": r["status"], "created_at": r["created_at"],
             "updated_at": r["updated_at"], "hermes_session_id": r["hermes_session_id"]}
            for r in _rows(conn, "SELECT * FROM studies ORDER BY updated_at DESC", ())]
