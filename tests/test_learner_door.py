"""S2: the learner's door (plugin routes): idempotency, rapid clicks, two windows, failures, export, delete."""
import sqlite3
import sys
import threading

from conftest import PLUGIN, rid


def _setup_open_exercise(env):
    study = env.start()
    ex = env.pose(study)
    return study["study_id"], ex["exercise_id"]


def test_repeated_submit_with_one_request_id_creates_one_attempt(env):
    sid, eid = _setup_open_exercise(env)
    req = rid()
    first = env.submit(sid, eid, "x² + 2xh + h²", request_id=req)
    second = env.submit(sid, eid, "x² + 2xh + h²", request_id=req)
    assert first[0] == second[0] == 200
    assert first[1]["attempt_id"] == second[1]["attempt_id"] and second[1]["duplicate"] is True
    assert len(env.rows("SELECT id FROM attempts")) == 1
    reused = env.submit(sid, eid, "something else", request_id=req)
    assert reused[0] == 409 and reused[1]["error"] == "request_id_reused"


def test_rapid_parallel_clicks_record_exactly_one_attempt(env):
    sid, eid = _setup_open_exercise(env)
    api = env.api_module
    req = rid()
    results, errors = [], []
    barrier = threading.Barrier(8)

    def click(request_id):
        barrier.wait()
        try:
            results.append(api.post_attempt(sid, eid, {"client_request_id": request_id, "answer": "x² + 2xh + h²"}))
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    # four clicks of the same button press, four from a second window with their own request ids
    threads = [threading.Thread(target=click, args=(req,)) for _ in range(4)]
    threads += [threading.Thread(target=click, args=(rid(),)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert len(env.rows("SELECT id FROM attempts")) == 1
    oks = [r for r in results if isinstance(r, dict) and r.get("ok")]
    refused = [r for r in results if not isinstance(r, dict)]
    assert len({r["attempt_id"] for r in oks}) == 1
    assert all(r.status_code == 409 for r in refused)


def test_two_windows_editing_one_draft_conflict_instead_of_overwriting(env):
    sid, eid = _setup_open_exercise(env)
    path = f"/studies/{sid}/exercises/{eid}/draft"
    code, a = env.api("PUT", path, {"base_revision": 0, "answer": "window A", "reasoning": ""})
    assert code == 200 and a["draft_revision"] == 1
    code, b = env.api("PUT", path, {"base_revision": 0, "answer": "window B", "reasoning": ""})
    assert code == 409 and b["error"] == "draft_conflict" and b["details"]["answer"] == "window A"
    code, b2 = env.api("PUT", path, {"base_revision": 1, "answer": "window B after reload", "reasoning": "why"})
    assert code == 200 and b2["draft_revision"] == 2
    draft = env.learner_view(sid)["draft"]
    assert (draft["answer"], draft["reasoning"], draft["revision"]) == ("window B after reload", "why", 2)


def test_draft_and_pending_exercise_survive_a_restart(env):
    sid, eid = _setup_open_exercise(env)
    env.api("PUT", f"/studies/{sid}/exercises/{eid}/draft",
            {"base_revision": 0, "answer": "half-finished answer", "reasoning": "line 1\nline 2"})
    fresh = env.fresh_process("print(json.dumps(api.session_study('chat-A')))")
    study = fresh["study"]
    assert study["open_exercise_id"] == eid
    assert study["draft"]["answer"] == "half-finished answer" and study["draft"]["reasoning"] == "line 1\nline 2"
    assert "reference_answer" not in study["exercises"][0]


def test_drafts_never_reach_the_agent(env):
    sid, eid = _setup_open_exercise(env)
    env.api("PUT", f"/studies/{sid}/exercises/{eid}/draft",
            {"base_revision": 0, "answer": "PRIVATE DRAFT TEXT", "reasoning": ""})
    raw = env.tool("learning_workbench_get", {"include_material": True})
    assert "PRIVATE DRAFT TEXT" not in str(raw)


def test_double_clicked_hint_reveals_one_hint(env):
    sid, eid = _setup_open_exercise(env)
    path = f"/studies/{sid}/exercises/{eid}/hint"
    one = env.api("POST", path, {"seen": 0})
    two = env.api("POST", path, {"seen": 0})
    assert one[1]["hints_shown"] == two[1]["hints_shown"] == 1 and two[1]["duplicate"] is True
    assert len(env.rows("SELECT seq FROM events WHERE kind = 'hint_shown'")) == 1
    env.api("POST", path, {"seen": 1})
    code, body = env.api("POST", path, {"seen": 2})
    assert code == 409 and body["error"] == "no_more_hints"


def test_a_locked_store_refuses_honestly_and_the_retry_records_once(env):
    sid, eid = _setup_open_exercise(env)
    blocker = sqlite3.connect(env.db_path, timeout=1)
    blocker.isolation_level = None
    blocker.execute("BEGIN IMMEDIATE")
    req = rid()
    try:
        code, body = env.submit(sid, eid, "x² + 2xh + h²", request_id=req)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert code == 503 and body["error"] == "storage_unavailable"
    assert env.rows("SELECT id FROM attempts") == []
    code, body = env.submit(sid, eid, "x² + 2xh + h²", request_id=req)
    assert code == 200 and len(env.rows("SELECT id FROM attempts")) == 1


def test_an_interrupted_assessment_leaves_no_partial_record(env, monkeypatch):
    sid, eid = _setup_open_exercise(env)
    _, rec = env.submit(sid, eid, "x² + 2xh + h²")
    plugin_module = env.manager._plugins[PLUGIN].module
    core_mod = sys.modules[plugin_module.__name__ + ".lw.core"]
    real_event = core_mod._event

    def crash(conn, study_id, actor, kind, *a, **kw):
        if kind == "assessment_proposed":
            raise RuntimeError("simulated crash mid-assessment")
        return real_event(conn, study_id, actor, kind, *a, **kw)

    monkeypatch.setattr(core_mod, "_event", crash)
    out = env.assess(sid, rec["attempt_id"], "correct", "2xh")
    assert out["ok"] is False and out["error"] == "internal_error"
    assert env.rows("SELECT id FROM assessments") == []
    assert env.learner_view(sid)["exercises"][0]["state"] == "awaiting_check"
    monkeypatch.setattr(core_mod, "_event", real_event)
    assert env.assess(sid, rec["attempt_id"], "correct", "2xh")["ok"]


def test_learner_can_start_a_study_from_the_pane(env):
    code, body = env.api("POST", "/studies", {"session_id": "chat-P", "goal": "Read a balance sheet",
                                              "material_text": "Assets = Liabilities + Equity."})
    assert code == 200 and body["study"]["material"]["source"] == "pasted_by_learner"
    view = env.agent_view("chat-P")
    assert view["created_by"] == "learner"
    assert "include_material=true" in view["next_action"]
    code, body = env.api("POST", "/studies", {"session_id": "chat-P", "goal": "Another"})
    assert code == 409 and body["error"] == "study_exists"
    code, body = env.api("POST", "/studies", {"session_id": "chat-Q", "goal": "   "})
    assert code == 400 and body["error"] == "invalid_input"
    code, body = env.api("POST", "/studies", {"goal": "No chat"})
    assert code == 400 and body["error"] == "no_session"


def test_session_switch_reads_only_that_chats_study(env):
    a = env.start(session_id="chat-A", goal="Study A")
    b = env.start(session_id="chat-B", goal="Study B")
    code, body = env.api("GET", "/session-study?session_id=chat-A")
    assert body["study"]["study_id"] == a["study_id"]
    code, body = env.api("GET", "/session-study?session_id=chat-B")
    assert body["study"]["study_id"] == b["study_id"]
    code, body = env.api("GET", "/session-study?session_id=chat-none")
    assert body["study"] is None
    # an action aimed at study A with study B's exercise id is refused
    ex_b = env.pose(b, session_id="chat-B")
    code, body = env.submit(a["study_id"], ex_b["exercise_id"], "answer")
    assert code == 404 and body["error"] == "unknown_exercise"


def test_export_is_readable_and_keeps_learner_text_verbatim(env):
    sid, eid = _setup_open_exercise(env)
    tricky = "x² + 2xh + h²\n```\n<script>alert(1)</script> **not bold**"
    _, rec = env.submit(sid, eid, tricky, reasoning="FOIL")
    env.assess(sid, rec["attempt_id"], "correct", "x² + 2xh + h²")
    code, out = env.api("POST", f"/studies/{sid}/export", {})
    assert code == 200
    md = out["markdown"]
    assert "````text\n" + tricky + "\n````" in md  # fence longer than the learner's backticks
    assert "not grades or certifications" in md
    assert "## Material" not in md
    path = env.home / "plugin-data" / PLUGIN / "exports"
    files = list(path.glob(f"{sid}-*.md"))
    assert len(files) == 1 and files[0].read_text(encoding="utf-8") == md
    code, with_material = env.api("POST", f"/studies/{sid}/export", {"include_material": True})
    assert "## Material" in with_material["markdown"] and "The derivative is the limit" in with_material["markdown"]


def test_delete_needs_two_steps_and_touches_only_the_selected_study(env):
    a = env.start(session_id="chat-A", goal="Delete me")
    b = env.start(session_id="chat-B", goal="Keep me")
    ex = env.pose(a)
    env.submit(a["study_id"], ex["exercise_id"], "x² + 2xh + h²")
    env.api("POST", f"/studies/{a['study_id']}/export", {})
    env.api("POST", f"/studies/{b['study_id']}/export", {})
    code, body = env.api("POST", f"/studies/{a['study_id']}/delete", {"confirm_token": "guess"})
    assert code == 409 and body["error"] == "delete_not_confirmed"
    code, req = env.api("POST", f"/studies/{a['study_id']}/delete-request", {})
    assert code == 200 and req["attempts"] == 1 and req["exports"] == 1
    code, done = env.api("POST", f"/studies/{a['study_id']}/delete", {"confirm_token": req["confirm_token"]})
    assert code == 200 and done["deleted"] is True and done["exports_removed"] == 1
    for table in ("studies", "exercises", "attempts", "events", "drafts", "exercise_references"):
        col = "id" if table == "studies" else "study_id"
        if table == "exercise_references":
            assert env.rows("SELECT COUNT(*) AS n FROM exercise_references")[0]["n"] == 0
            continue
        assert env.rows(f"SELECT COUNT(*) AS n FROM {table} WHERE {col} = ?", (a["study_id"],))[0]["n"] == 0
    assert env.learner_view(b["study_id"])["goal"] == "Keep me"
    exports = env.home / "plugin-data" / PLUGIN / "exports"
    assert [p.name.split("-")[0] for p in exports.glob("*.md")] == [b["study_id"]]


def test_finished_study_frees_the_chat_and_stays_listed(env):
    a = env.start(session_id="chat-A", goal="First")
    code, _ = env.api("POST", f"/studies/{a['study_id']}/archive", {})
    assert code == 200
    out = env.tool("learning_workbench_propose_exercise",
                   {"study_id": a["study_id"], "study_revision": 99, "kind": "diagnostic", "prompt": "q",
                    "reference_answer": "a", "rubric": "r"})
    assert out["ok"] is False and out["error"] == "study_closed"
    env.start(session_id="chat-A", goal="Second")
    code, body = env.api("GET", "/session-study?session_id=chat-A")
    assert body["study"]["goal"] == "Second" and [s["goal"] for s in body["earlier"]] == ["First"]
