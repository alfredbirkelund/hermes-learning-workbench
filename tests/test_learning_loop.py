"""The learning loop end to end through both doors (S1 agent tools, S2 learner routes, S3 saved state)."""
from conftest import rid


def _open(env, study_id):
    view = env.learner_view(study_id)
    return next(e for e in view["exercises"] if e["exercise_id"] == view["open_exercise_id"])


def test_full_loop_gap_repair_hint_retry_and_independent_practice(env, events):
    study = env.start()
    sid = study["study_id"]
    # diagnose
    diag = env.pose(study)
    code, rec = env.submit(sid, diag["exercise_id"], "x² + h²", reasoning="square each term")
    assert code == 200 and rec["help_class"] == "unaided" and rec["number"] == 1
    agent = env.agent_view()
    attempt = agent["exercises"][0]["attempts"][0]
    assert attempt["answer"] == "x² + h²" and attempt["reasoning"] == "square each term"
    assert agent["next_action"].startswith("assess")
    out = env.assess(sid, rec["attempt_id"], "incorrect", "x² + h²", gap="Missing the cross term 2xh.")
    assert out["ok"] and out["revision"] == 1
    # route
    plan = env.plan(sid)
    step_a = plan["steps"][0]["step_id"]
    view = env.agent_view()
    assert view["route"]["steps"][0]["title"] == "Expand (x+h)²"
    assert view["next_action"].startswith("teach_next") and step_a in view["next_action"]
    # check with a hint, wrong answer, retry
    check = env.pose_step(sid, step_a, prompt="Expand (x+h)².", reference_answer="x² + 2xh + h²",
                          rubric="All three terms.", hints=["Write it as (x+h)(x+h)."])
    code, hint = env.api("POST", f"/studies/{sid}/exercises/{check['exercise_id']}/hint", {"seen": 0})
    assert code == 200 and hint["hints"] == ["Write it as (x+h)(x+h)."]
    code, rec = env.submit(sid, check["exercise_id"], "x² + xh + h²")
    assert rec["help_class"] == "hinted"
    out = env.assess(sid, rec["attempt_id"], "partially_correct", "xh", gap="The cross term appears twice: 2xh.")
    assert out["ok"]
    assert env.agent_view()["next_action"].startswith("repair")
    code, _ = env.api("POST", f"/studies/{sid}/exercises/{check['exercise_id']}/retry", {})
    assert code == 200
    reopened = env.learner_view(sid)
    assert reopened["draft"]["answer"] == "x² + xh + h²"  # retry starts from the last answer
    code, rec2 = env.submit(sid, check["exercise_id"], "x² + 2xh + h²", reasoning="xh + hx = 2xh")
    assert rec2["number"] == 2 and rec2["help_class"] == "hinted"  # the hint still counts
    env.assess(sid, rec2["attempt_id"], "correct", "2xh")
    assert env.agent_view()["next_action"].startswith("independent_practice")
    # fresh practice without help
    practice = env.pose_step(sid, step_a, kind="practice", prompt="Expand (a+3)².",
                             reference_answer="a² + 6a + 9", rubric="All three terms.", hints=[])
    code, rec3 = env.submit(sid, practice["exercise_id"], "a² + 6a + 9")
    assert rec3["help_class"] == "unaided"
    env.assess(sid, rec3["attempt_id"], "correct", "a² + 6a + 9")
    ev = env.agent_view()["route"]["steps"][0]["evidence"]
    assert ev == {"attempts": 3, "correct_unaided": 1, "correct_with_help": 1, "independent_practice_correct": 1}
    assert any(name == "plugin.learning-workbench.study.changed" for name, _ in events)


def test_verdict_must_quote_the_learner_and_nothing_is_recorded_otherwise(env):
    study = env.start()
    sid = study["study_id"]
    ex = env.pose(study)
    _, rec = env.submit(sid, ex["exercise_id"], "x squared plus two x h plus h squared")
    out = env.assess(sid, rec["attempt_id"], "correct", "x² + 2xh + h²")
    assert out["ok"] is False and out["error"] == "quote_not_found"
    assert env.rows("SELECT id FROM assessments") == []
    assert env.learner_view(sid)["exercises"][0]["state"] == "awaiting_check"
    ok = env.assess(sid, rec["attempt_id"], "correct", "Two X H  plus")  # whitespace and case tolerant
    assert ok["ok"] and ok["verdict"] == "correct"


def test_hinted_and_revealed_answers_never_become_unaided_after_reload(env):
    study = env.start()
    sid = study["study_id"]
    ex = env.pose(study)
    eid = ex["exercise_id"]
    env.api("POST", f"/studies/{sid}/exercises/{eid}/hint", {"seen": 0})
    _, rec = env.submit(sid, eid, "x² + 2xh + h²")
    env.assess(sid, rec["attempt_id"], "incorrect", "x²")  # wrong verdict so a retry is possible
    code, body = env.api("POST", f"/studies/{sid}/exercises/{eid}/reveal", {"confirm": True})
    assert code == 200 and body["reference_answer"] == "x² + 2xh + h²"
    env.api("POST", f"/studies/{sid}/exercises/{eid}/retry", {})
    _, rec2 = env.submit(sid, eid, "x² + 2xh + h²")
    env.assess(sid, rec2["attempt_id"], "correct", "2xh")
    # reload in a separate process
    fresh = env.fresh_process(f"print(json.dumps(api.get_study({sid!r})))")
    attempts = fresh["study"]["exercises"][0]["attempts"]
    assert [a["help_class"] for a in attempts] == ["hinted", "after_reveal"]
    assert [a["solution_seen"] for a in attempts] == [False, True]


def test_solution_stays_hidden_until_an_explicit_reveal(env):
    study = env.start()
    sid = study["study_id"]
    ex = env.pose(study)
    learner = env.learner_view(sid)["exercises"][0]
    assert "reference_answer" not in learner and learner["hints"] == [] and learner["hints_total"] == 2
    assert "rubric" not in learner
    code, body = env.api("POST", f"/studies/{sid}/exercises/{ex['exercise_id']}/reveal", {})
    assert code == 400 and body["error"] == "confirmation_required"
    assert "reference_answer" not in env.learner_view(sid)["exercises"][0]


def test_plan_rewrite_and_learner_edits_never_orphan_attempts(env):
    study = env.start()
    sid = study["study_id"]
    plan = env.plan(sid)
    a, b = plan["steps"][0]["step_id"], plan["steps"][1]["step_id"]
    ex = env.pose_step(sid, a, prompt="Expand (x+1)².", reference_answer="x² + 2x + 1", rubric="three terms")
    _, rec = env.submit(sid, ex["exercise_id"], "x² + 2x + 1")
    env.assess(sid, rec["attempt_id"], "correct", "x² + 2x + 1")
    # the agent drops step A and adds a new one
    revised = env.plan(sid, steps=[{"title": "Take the limit h → 0", "why": "core idea", "step_id": b},
                                   {"title": "Power rule", "why": "generalise"}])
    assert revised["removed_step_ids"] == [a]
    view = env.agent_view()
    removed = view["route"]["removed_steps"]
    assert [s["step_id"] for s in removed] == [a] and removed[0]["evidence"]["correct_unaided"] == 1
    assert view["exercises"][0]["step_title"] == "Expand (x+h)²"  # meaning frozen at creation
    # the learner renames step B; exercises keep the title they were posed under
    ex_b = env.pose_step(sid, b)
    plan_rev = env.learner_view(sid)["route"]["plan_revision"]
    code, out = env.api("POST", f"/studies/{sid}/route",
                        {"base_plan_revision": plan_rev, "op": "rename", "step_id": b, "title": "Limits, slowly"})
    assert code == 200
    view = env.learner_view(sid)
    assert view["route"]["steps"][0]["title"] == "Limits, slowly"
    assert view["exercises"][1]["exercise_id"] == ex_b["exercise_id"]
    assert view["exercises"][1]["step_title"] == "Take the limit h → 0"
    assert env.rows("SELECT COUNT(*) AS n FROM attempts")[0]["n"] == 1


def test_learner_route_edit_makes_stale_agent_writes_fail(env):
    study = env.start()
    sid = study["study_id"]
    plan = env.plan(sid)
    stale_rev = env.rev()
    b = plan["steps"][1]["step_id"]
    plan_rev = env.learner_view(sid)["route"]["plan_revision"]
    code, _ = env.api("POST", f"/studies/{sid}/route", {"base_plan_revision": plan_rev, "op": "skip", "step_id": b})
    assert code == 200
    out = env.tool("learning_workbench_propose_exercise",
                   {"study_id": sid, "study_revision": stale_rev, "kind": "check", "step_id": b, "prompt": "q",
                    "reference_answer": "a", "rubric": "r"})
    assert out["ok"] is False and out["error"] == "stale_study"
    out = env.tool("learning_workbench_propose_exercise",
                   {"study_id": sid, "study_revision": env.rev(), "kind": "check", "step_id": b, "prompt": "q",
                    "reference_answer": "a", "rubric": "r"})
    assert out["ok"] is False and out["error"] == "step_skipped"
    # a second window editing from the old route revision is refused, not merged
    code, body = env.api("POST", f"/studies/{sid}/route", {"base_plan_revision": plan_rev, "op": "restore", "step_id": b})
    assert code == 409 and body["error"] == "stale_route"


def test_diagnosis_is_capped_before_a_route(env):
    study = env.start()
    sid = study["study_id"]
    for i in range(3):
        ex = env.pose({"study_id": sid, "study_revision": env.rev()}, prompt=f"Question {i}?")
        assert ex["ok"], ex
        _, rec = env.submit(sid, ex["exercise_id"], f"answer {i}")
        env.assess(sid, rec["attempt_id"], "correct", f"answer {i}")
    out = env.pose({"study_id": sid, "study_revision": env.rev()}, prompt="A fourth?")
    assert out["ok"] is False and out["error"] == "diagnosis_complete"
    assert env.agent_view()["next_action"].startswith("plan:")


def test_contest_keeps_the_earlier_verdict(env):
    study = env.start()
    sid = study["study_id"]
    ex = env.pose(study)
    _, rec = env.submit(sid, ex["exercise_id"], "(x+h)² = x² + 2hx + h²")
    first = env.assess(sid, rec["attempt_id"], "incorrect", "2hx", explanation="Expected 2xh.")
    again = env.assess(sid, rec["attempt_id"], "correct", "2hx")
    assert again["ok"] is False and again["error"] == "already_assessed"
    code, contest = env.api("POST", f"/studies/{sid}/assessments/{first['assessment_id']}/contest",
                            {"client_request_id": rid(), "text": "2hx and 2xh are the same term."})
    assert code == 200
    view = env.agent_view()
    assert view["pending_contests"] == [contest["contest_id"]]
    assert view["next_action"].startswith("respond_to_contest")
    second = env.assess(sid, rec["attempt_id"], "correct", "2hx", explanation="Multiplication commutes.",
                        responds_to_contest_id=contest["contest_id"])
    assert second["ok"] and second["revision"] == 2
    checks = env.learner_view(sid)["exercises"][0]["attempts"][0]["assessments"]
    assert [(c["revision"], c["verdict"]) for c in checks] == [(1, "incorrect"), (2, "correct")]
    assert env.agent_view()["pending_contests"] == []


def test_reference_revisions_are_versioned_and_attempts_keep_theirs(env):
    study = env.start()
    sid = study["study_id"]
    ex = env.pose(study)
    _, rec = env.submit(sid, ex["exercise_id"], "x² + 2xh + h²")
    out = env.tool("learning_workbench_revise_reference",
                   {"study_id": sid, "exercise_id": ex["exercise_id"], "reference_answer": "x² + 2hx + h²",
                    "rubric": "Accept 2xh or 2hx.", "reason": "The rubric was too narrow."}, session_id="chat-A")
    assert out["ok"] and out["reference_revision"] == 2
    view = env.agent_view()
    assert view["exercises"][0]["reference_revision"] == 2
    assert view["exercises"][0]["attempts"][0]["reference_revision"] == 1
    checked = env.assess(sid, rec["attempt_id"], "correct", "2xh")
    assert env.rows("SELECT reference_revision FROM assessments WHERE id = ?",
                    (checked["assessment_id"],))[0]["reference_revision"] == 2
