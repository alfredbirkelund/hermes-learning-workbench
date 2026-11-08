"""Host lifecycle seams: hooks, slash command, registration and the agent's inability to act as the learner."""
from conftest import PLUGIN


def _command(env):
    from hermes_cli.plugin_command_dispatch import invoke_plugin_command

    entry = env.manager._plugin_commands["study"]

    def run(text, session_id="chat-A"):
        return invoke_plugin_command(entry["handler"], text, session_id=session_id, session_key="k",
                                     platform="cli", source=None)
    return run


def test_plugin_registers_its_tools_hook_and_command(env):
    loaded = env.manager._plugins[PLUGIN]
    assert sorted(env.tool_names()) == sorted([
        "learning_workbench_start", "learning_workbench_get", "learning_workbench_propose_plan",
        "learning_workbench_propose_exercise", "learning_workbench_assess", "learning_workbench_revise_reference"])
    assert "post_compaction" in loaded.hooks_registered
    assert "study" in env.manager._plugin_commands


def test_compaction_that_rotates_the_session_keeps_the_study_attached(env, events):
    study = env.start(session_id="chat-A")
    env.manager.invoke_hook("post_compaction", session_id="chat-A2", old_session_id="chat-A", kind="summarize",
                            reason="manual", before_tokens=1000, after_tokens=200, messages_removed=10)
    view = env.agent_view("chat-A2")
    assert view["study_id"] == study["study_id"] and view["bound_to_this_chat"]
    assert env.tool("learning_workbench_get", {}, session_id="chat-A")["study"] is None
    # in-place compaction (no rotation) changes nothing
    env.manager.invoke_hook("post_compaction", session_id="chat-A2", old_session_id="", kind="summarize",
                            reason="pre_api", before_tokens=None, after_tokens=None, messages_removed=None)
    assert env.agent_view("chat-A2")["study_id"] == study["study_id"]
    kinds = [r["kind"] for r in env.rows("SELECT kind FROM events ORDER BY seq")]
    assert kinds.count("study_rebound") == 1


def test_study_command_is_a_working_text_door(env):
    run = _command(env)
    assert "No active study" in run("")
    study = env.start()
    env.pose(study)
    status = run("status")
    assert "Exercise 1 (diagnostic): Expand (x+h)²." in status
    hint = run("hint")
    assert hint == "Hint 1 of 2: Multiply (x+h)(x+h)."
    saved = run("answer x² + 2xh + h²\nReasoning: FOIL gives two xh terms")
    assert saved.startswith("Saved as attempt 1 on exercise 1 (after hints)")
    attempt = env.agent_view()["exercises"][0]["attempts"][0]
    assert attempt["answer"] == "x² + 2xh + h²" and attempt["reasoning"] == "FOIL gives two xh terms"
    assert attempt["channel"] == "command"
    reveal = run("reveal")  # no interactive picker in this test host: asks for the explicit text form
    assert reveal in ("To reveal the solution, type: /study reveal confirm", "Not revealed.")
    assert run("export").startswith("Learning record written to ")
    assert run("bogus").startswith("Usage: /study")


def test_agent_cannot_reach_learner_actions_through_any_tool(env):
    """Every learner action lives behind the plugin routes or /study; no agent tool accepts learner fields."""
    from tools.registry import registry

    learner_words = {"answer", "learner_answer", "attempt_text", "actor", "human_confirmed", "confirm",
                     "confirm_token", "client_request_id", "seen", "draft"}
    for name in env.tool_names():
        schema = registry.get_entry(name, scope=env.manager.scope_key).schema
        props = set(schema["parameters"]["properties"])
        assert not props & learner_words, (name, props & learner_words)
        assert schema["parameters"].get("additionalProperties") is False, name
    study = env.start()
    ex = env.pose(study)
    forged = env.tool("learning_workbench_assess",
                      {"study_id": study["study_id"], "attempt_id": "at_fake", "verdict": "correct",
                       "evidence_quote": "x", "explanation": "y", "answer": "forged learner text"})
    assert forged["ok"] is False and forged["error"] == "unknown_fields"
    assert env.rows("SELECT id FROM attempts") == []
    assert env.learner_view(study["study_id"])["exercises"][0]["exercise_id"] == ex["exercise_id"]
