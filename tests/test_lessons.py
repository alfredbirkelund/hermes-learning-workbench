"""Lessons: a step's first check carries the explanation the learner reads above the question; schema 1 -> 2."""
import sqlite3
from pathlib import Path

from test_lifecycle import _command

FIXTURES = Path(__file__).parent / "fixtures"
LESSON = ("The difference quotient (f(x+h) − f(x))/h is the slope between two points on the curve.\n\n"
          "For f(x) = x², expand (x+h)² = x² + 2xh + h², subtract x², then divide every term by h.")


def _check(env, sid, step_id, session_id="chat-A", **kw):
    args = {"study_id": sid, "study_revision": env.rev(session_id), "kind": "check", "step_id": step_id,
            "prompt": "Simplify (2xh + h²)/h.", "reference_answer": "2x + h", "rubric": "Divide each term by h."}
    args.update(kw)
    return env.tool("learning_workbench_propose_exercise", args, session_id=session_id)


def test_a_steps_first_check_needs_a_lesson_and_a_refusal_stores_nothing(env):
    sid = env.start()["study_id"]
    a = env.plan(sid)["steps"][0]["step_id"]
    before = env.learner_view(sid)
    out = _check(env, sid, a)
    assert out["ok"] is False and out["error"] == "lesson_required" and "Expand (x+h)²" in out["message"]
    for blank in ("", "   \n "):
        assert _check(env, sid, a, lesson=blank)["error"] == "lesson_required"
    after = env.learner_view(sid)
    assert after["exercises"] == [] and after["study_revision"] == before["study_revision"]
    assert env.rows("SELECT COUNT(*) AS n FROM exercises")[0]["n"] == 0

    out = _check(env, sid, a, lesson=LESSON)
    assert out["ok"], out
    assert env.learner_view(sid)["exercises"][0]["lesson"] == LESSON  # verbatim, paragraph break kept
    assert env.agent_view()["exercises"][0]["lesson"] == LESSON

    code, body = env.submit(sid, out["exercise_id"], "2x + h")
    assert code == 200, body
    assert env.assess(sid, body["attempt_id"], "correct", "2x + h")["ok"]
    again = _check(env, sid, a, prompt="Simplify (6xh + 3h²)/h.")  # the step was taught: a later check may skip it
    assert again["ok"], again
    assert env.learner_view(sid)["exercises"][1]["lesson"] == ""


def test_only_a_check_carries_a_lesson_and_it_is_size_limited(env):
    study = env.start()
    out = env.pose(study, lesson=LESSON)  # diagnostic
    assert out["ok"] is False and out["error"] == "invalid_input" and out["details"]["field"] == "lesson"
    sid = study["study_id"]
    b = env.plan(sid)["steps"][1]["step_id"]
    out = env.tool("learning_workbench_propose_exercise",
                   {"study_id": sid, "study_revision": env.rev(), "kind": "practice", "step_id": b,
                    "prompt": "Find the derivative of 3x² from first principles.", "reference_answer": "6x",
                    "rubric": "Quotient 6x + 3h, limit 6x.", "lesson": LESSON})
    assert out["ok"] is False and out["details"]["field"] == "lesson"
    out = _check(env, sid, b, lesson="x" * 3001)
    assert out["ok"] is False and out["error"] == "invalid_input" and out["details"]["field"] == "lesson"
    assert env.learner_view(sid)["exercises"] == []


def test_the_lesson_reaches_the_study_command_and_the_export(env):
    sid = env.start()["study_id"]
    a = env.plan(sid)["steps"][0]["step_id"]
    assert _check(env, sid, a, lesson=LESSON)["ok"]
    shown = _command(env)("")
    assert "Lesson (Expand (x+h)²):" in shown
    assert "\n  The difference quotient" in shown and "\n  For f(x) = x², expand" in shown  # indented, paragraphs kept
    assert shown.index("Lesson") < shown.index("Simplify (2xh + h²)/h.")
    code, body = env.api("POST", f"/studies/{sid}/export", {})
    assert code == 200, body
    md = body["markdown"]
    assert "Lesson:" in md and "then divide every term by h." in md
    assert md.index("Lesson:") < md.index("Question:") < md.index("Simplify (2xh + h²)/h.")


def _load_v1_store(env):
    path = env.db_path
    assert not path.exists(), "the store must not be open yet"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path)
    raw.executescript((FIXTURES / "store-v1.sql").read_text(encoding="utf-8"))
    raw.close()
    return raw


def test_a_v1_store_is_upgraded_in_place_and_keeps_every_record(env):
    _load_v1_store(env)
    before = {t: env.rows(f"SELECT * FROM {t} ORDER BY rowid")
              for t in ("studies", "exercises", "attempts", "assessments", "events")}
    assert env.rows("SELECT value FROM meta WHERE key = 'schema_version'")[0]["value"] == "1"

    study = env.agent_view(session_id="chat-v1")  # first use opens the store, which upgrades it
    assert env.rows("SELECT value FROM meta WHERE key = 'schema_version'")[0]["value"] == "2"
    for table, rows in before.items():
        now = env.rows(f"SELECT * FROM {table} ORDER BY rowid")
        if table == "exercises":
            assert [r.pop("lesson") for r in now] == [""]
        assert now == rows, table  # nothing rewritten
    attempt = study["exercises"][0]["attempts"][0]
    assert attempt["answer"] == "x² + 2xh + h²" and attempt["help_class"] == "hinted"
    assert attempt["assessments"][0]["verdict"] == "correct"

    # the step already had a check under v1, so it counts as taught; the untouched step still needs a lesson
    a, b = (s["step_id"] for s in study["route"]["steps"])
    assert _check(env, study["study_id"], a, session_id="chat-v1", prompt="Expand (x+2h)².")["ok"]
    sid = study["study_id"]
    env.api("POST", f"/studies/{sid}/exercises/{env.agent_view(session_id='chat-v1')['open_exercise_id']}/skip")
    assert _check(env, sid, b, session_id="chat-v1")["error"] == "lesson_required"


def test_a_store_from_a_newer_version_is_refused_untouched_with_a_clear_message(env):
    _load_v1_store(env)
    raw = sqlite3.connect(env.db_path)
    raw.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    raw.commit()
    raw.close()
    schema = env.rows("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")
    out = env.tool("learning_workbench_get", {}, session_id="chat-v1")
    assert out["ok"] is False and out["error"] == "store_too_new" and "Update the plugin" in out["message"]
    code, body = env.api("GET", "/session-study?session_id=chat-v1")
    assert code == 409 and body["error"] == "store_too_new" and "newer version" in body["message"]
    assert "Update the plugin" in _command(env)("", session_id="chat-v1")
    assert env.rows("SELECT value FROM meta WHERE key = 'schema_version'")[0]["value"] == "99"
    assert env.rows("SELECT type, name, sql FROM sqlite_master ORDER BY type, name") == schema  # nothing created
