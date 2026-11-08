"""S1: agent tools through Hermes's real registry. The model proposes; it can never write learner records."""
from conftest import rid


def test_start_binds_study_to_the_calling_chat_and_get_reads_it(env):
    out = env.start(session_id="chat-A")
    view = env.agent_view("chat-A")
    assert view["study_id"] == out["study_id"]
    assert view["goal"] == "Understand why d/dx x² = 2x"
    assert view["bound_to_this_chat"] is True
    assert view["material"]["source"] == "attached_by_agent"
    assert "include_material=true" in view["material"]["note"] and "text" not in view["material"]
    full = env.tool("learning_workbench_get", {"include_material": True}, session_id="chat-A")["study"]
    assert full["material"]["text"].startswith("The derivative is the limit")
    assert "never as instructions" in full["material"]["note"]
    assert view["next_action"].startswith("diagnose")


def test_get_in_a_chat_without_a_study_says_so(env):
    out = env.tool("learning_workbench_get", {}, session_id="chat-empty")
    assert out == {"ok": True, "study": None,
                   "message": "No active study in this chat. Start one with learning_workbench_start."}


def test_a_chat_cannot_start_two_active_studies(env):
    first = env.start(session_id="chat-A")
    second = env.tool("learning_workbench_start", {"goal": "Something else"}, session_id="chat-A")
    assert second["ok"] is False and second["error"] == "study_exists"
    assert second["details"]["study_id"] == first["study_id"]


def test_only_one_exercise_can_be_open(env):
    study = env.start()
    first = env.pose(study)
    assert first["ok"] and first["state"] == "awaiting_answer"
    again = env.pose({**study, "study_revision": first["study_revision"]}, prompt="A different question?")
    assert again["ok"] is False and again["error"] == "exercise_pending"
    assert again["details"]["exercise_id"] == first["exercise_id"]
    assert len(env.rows("SELECT id FROM exercises")) == 1


def test_forged_learner_fields_are_rejected_and_create_nothing(env):
    study = env.start()
    forged = env.pose(study, learner_answer="x² + 2xh + h²", actor="learner", human_confirmed=True)
    assert forged["ok"] is False and forged["error"] == "unknown_fields"
    assert set(forged["details"]["fields"]) == {"actor", "human_confirmed", "learner_answer"}
    assert env.rows("SELECT id FROM exercises") == []
    assert env.rows("SELECT id FROM attempts") == []


def test_another_chat_cannot_write_into_this_study(env):
    study = env.start(session_id="chat-A")
    out = env.pose(study, session_id="chat-B")
    assert out["ok"] is False and out["error"] == "wrong_session"
    assert env.rows("SELECT id FROM exercises") == []


def test_invalid_model_output_gets_an_actionable_error(env):
    study = env.start()
    out = env.pose(study, kind="quiz", prompt="   ")
    assert out["ok"] is False and out["error"] == "invalid_input"
    assert "kind must be one of diagnostic, check, practice" in out["message"]
    too_many = env.pose(study, hints=["a", "b", "c", "d"])
    assert too_many["ok"] is False and "at most 3" in too_many["message"]


def test_no_tool_exists_that_records_a_learner_answer(env):
    from tools.registry import registry

    names = {n for n in registry.get_all_tool_names() if n.startswith("learning_workbench")}
    assert names, "plugin tools did not register"
    for name in names:
        schema = registry.get_entry(name, scope=env.manager.scope_key).schema
        props = set(schema["parameters"]["properties"])
        assert not props & {"answer", "learner_answer", "reasoning_text", "actor", "human_confirmed"}, name
