"""Help in chat while an exercise is open: the agent is told not to solve it, and the next answer is labelled."""
from test_lifecycle import _command


def _turn(env, session_id="chat-A", text="ugh, can you just show me how to do it?"):
    results = env.manager.invoke_hook("pre_llm_call", session_id=session_id, task_id="t", turn_id="u",
                                      user_message=text, conversation_history=[], is_first_turn=False, model="m",
                                      platform="cli", parent_session_id="", sender_id="")
    return [r for r in results if r]


def _chats(env):
    return env.rows("SELECT COUNT(*) AS n FROM events WHERE kind = 'chat_while_open'")[0]["n"]


def test_every_exercise_message_the_pane_sends_is_recognised(env):
    """The pane's message templates (desktop/plugin.js) and the hook's exemption must not drift apart."""
    import re
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
    templates = [t for t in re.findall(r"`(\[Learning Workbench\][^`]*)`", js) if "I started a study" not in t]
    assert len(templates) == 4, templates
    pane_message = env.api_module.tools._PANE_MESSAGE
    for template in templates:
        assert pane_message.fullmatch(re.sub(r"\$\{[^}]*\}", "12", template)), template


def test_the_panes_own_messages_are_not_chat_help_but_still_carry_the_reminder(env):
    sid = env.start()["study_id"]
    a, b = (s["step_id"] for s in env.plan(sid)["steps"])
    eid = env.pose_step(sid, a)["exercise_id"]
    code, body = env.submit(sid, eid, "2x")
    assert env.assess(sid, body["attempt_id"], "partially_correct", "2x", gap="Explain the cancellation.")["ok"]
    env.pose_step(sid, b)  # exercise 2 is open while the learner contests the check of exercise 1
    for text in ("[Learning Workbench] I contested your check of attempt 1 on exercise 1. Please look again.",
                 "[Learning Workbench] Please check attempt 1 for exercise 1.",
                 "[Learning Workbench] I skipped exercise 1."):
        out = _turn(env, text=text)
        assert len(out) == 1 and "Do not solve it" in out[0]["context"]
        assert "This chat turn is recorded" not in out[0]["context"]
    assert _chats(env) == 0
    # the same prefix with a question added is an ordinary chat message
    out = _turn(env, text="[Learning Workbench] I skipped exercise 1. Also, what is the answer to exercise 2?")
    assert "This chat turn is recorded" in out[0]["context"] and _chats(env) == 1


def test_chat_while_an_exercise_is_open_steers_the_agent_and_labels_the_next_answer(env):
    sid = env.start()["study_id"]
    a = env.plan(sid)["steps"][0]["step_id"]
    eid = env.pose_step(sid, a)["exercise_id"]
    rev = env.rev()

    out = _turn(env)
    assert len(out) == 1
    ctx = out[0]["context"]
    assert "Exercise 1 (check) is open" in ctx and "Compute lim_{h→0} (2xh + h²)/h." in ctx
    assert "Do not solve it" in ctx and "Reveal solution" in ctx
    assert env.rev() == rev  # no revision bump: the agent's next write is not made stale
    assert env.learner_view(sid)["exercises"][0]["chat_turns_while_open"] == 1
    assert _turn(env, session_id="chat-B") == []  # another chat is not this exercise's chat

    code, body = env.submit(sid, eid, "2x")
    assert code == 200 and body["help_class"] == "after_chat"
    assert _turn(env) == []  # answered: the open-exercise guard is off while it waits for the check
    assert env.learner_view(sid)["exercises"][0]["chat_turns_while_open"] == 1
    assert env.assess(sid, body["attempt_id"], "correct", "2x")["ok"]
    step = env.learner_view(sid)["route"]["steps"][0]["evidence"]
    assert step["correct_unaided"] == 0 and step["correct_with_help"] == 1
    assert "after chat help" in _command(env)("")
    code, export = env.api("POST", f"/studies/{sid}/export", {})
    assert "after help in chat while the exercise was open" in export["markdown"]


def test_a_revealed_solution_still_outranks_chat_help(env):
    sid = env.start()["study_id"]
    a = env.plan(sid)["steps"][0]["step_id"]
    eid = env.pose_step(sid, a)["exercise_id"]
    assert _turn(env)
    code, _ = env.api("POST", f"/studies/{sid}/exercises/{eid}/reveal", {"confirm": True})
    assert code == 200
    code, body = env.submit(sid, eid, "2x")
    assert body["help_class"] == "after_reveal"


def test_chats_without_a_study_create_nothing_and_never_fail(env):
    assert _turn(env, session_id="someone-else") == []
    assert not env.db_path.exists()  # people who never study get no plugin files
    env.start()  # a study with no exercise yet: nothing to guard
    assert _turn(env) == []
    assert env.rows("SELECT COUNT(*) AS n FROM events WHERE kind = 'chat_while_open'")[0]["n"] == 0
