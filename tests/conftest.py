"""Test harness: load the plugin through Hermes's real PluginManager in a throwaway home.

Public seams only (see docs/SCOPE.md):
  S1  agent tools   -> Hermes tool registry dispatch, with the session_id Hermes injects
  S2  learner door  -> the plugin's FastAPI router (dashboard/plugin_api.py) through TestClient
  S3  saved state   -> read back through a fresh connection / fresh router
Run with:  scripts/verify.sh   (or PYTHONPATH=<pytest>:<hermes-agent> python -m pytest tests)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN = "learning-workbench"

# Safety net: Hermes's process bootstrap re-executes the interpreter (os.execv) when it is imported
# under a data root whose runtime differs, which silently replaces the pytest process. Nothing in these
# tests may import it; with this entry an accidental import fails loudly instead.
sys.modules["hermes_bootstrap"] = None  # type: ignore[assignment]


@pytest.fixture
def events(monkeypatch):
    """Stand-in for the gateway transport behind hermes_cli.plugin_events.broadcast_plugin_event.

    The real tui_gateway.server pulls in the whole transport stack (and the bootstrap above); the
    sanctioned door (broadcast_plugin_event) is still exercised end to end, only delivery is captured.
    """
    import types

    sent: list[tuple[str, dict]] = []
    stub = types.ModuleType("tui_gateway.server")
    stub._broadcast_global_event = lambda name, payload: sent.append((name, payload))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tui_gateway.server", stub)
    return sent


def _load_router_module():
    """Import dashboard/plugin_api.py the way Hermes's dashboard does (by file path)."""
    name = f"lw_test_plugin_api_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(name, REPO / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Env:
    def __init__(self, home: Path, manager):
        self.home = home
        self.manager = manager
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        self.api_module = _load_router_module()
        app.include_router(self.api_module.router, prefix=f"/api/plugins/{PLUGIN}")
        self.client = TestClient(app)

    # S1 -------------------------------------------------------------------------------------
    def tool(self, name: str, args: dict, session_id: str = "chat-A") -> dict:
        from tools.registry import registry

        raw = registry.dispatch(name, args, scope=self.manager.scope_key, session_id=session_id,
                                task_id=f"task-{session_id}")
        return json.loads(raw) if isinstance(raw, str) else raw

    def tool_names(self) -> set[str]:
        from tools.registry import registry

        return {n for n in registry.get_all_tool_names() if n.startswith("learning_workbench")}

    # S2 -------------------------------------------------------------------------------------
    def api(self, method: str, path: str, body: dict | None = None):
        resp = self.client.request(method, f"/api/plugins/{PLUGIN}{path}", json=body)
        return resp.status_code, resp.json()

    # S3 -------------------------------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.home / "plugin-data" / PLUGIN / "data.db"

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    # helpers ------------------------------------------------------------------------------------
    def start(self, session_id: str = "chat-A", **kw) -> dict:
        args = {"goal": "Understand why d/dx x² = 2x",
                "material_text": "The derivative is the limit of (f(x+h) − f(x))/h as h → 0."}
        args.update(kw)
        out = self.tool("learning_workbench_start", args, session_id=session_id)
        assert out["ok"], out
        return out

    def pose(self, study: dict, session_id: str = "chat-A", **kw) -> dict:
        args = {"study_id": study["study_id"], "study_revision": study["study_revision"], "kind": "diagnostic",
                "prompt": "Expand (x+h)².", "reference_answer": "x² + 2xh + h²",
                "rubric": "All three terms, including the cross term 2xh.",
                "hints": ["Multiply (x+h)(x+h).", "Use FOIL: first, outer, inner, last."]}
        args.update(kw)
        out = self.tool("learning_workbench_propose_exercise", args, session_id=session_id)
        return out

    def learner_view(self, study_id: str) -> dict:
        code, body = self.api("GET", f"/studies/{study_id}")
        assert code == 200, body
        return body["study"]

    def agent_view(self, session_id: str = "chat-A") -> dict:
        out = self.tool("learning_workbench_get", {}, session_id=session_id)
        assert out["ok"], out
        return out["study"]

    def rev(self, session_id: str = "chat-A") -> int:
        return self.agent_view(session_id)["study_revision"]

    def plan(self, study_id: str, steps: list | None = None, session_id: str = "chat-A") -> dict:
        steps = steps or [{"title": "Expand (x+h)²", "why": "The difference quotient needs it."},
                          {"title": "Take the limit h → 0", "why": "The derivative is that limit."}]
        out = self.tool("learning_workbench_propose_plan",
                        {"study_id": study_id, "study_revision": self.rev(session_id), "steps": steps},
                        session_id=session_id)
        assert out["ok"], out
        return out

    def pose_step(self, study_id: str, step_id: str, kind: str = "check", session_id: str = "chat-A", **kw) -> dict:
        args = {"study_id": study_id, "study_revision": self.rev(session_id), "kind": kind, "step_id": step_id,
                "prompt": "Compute lim_{h→0} (2xh + h²)/h.", "reference_answer": "2x",
                "rubric": "Divides by h first, then lets h → 0; answer 2x.", "hints": ["Cancel h first."]}
        args.update(kw)
        out = self.tool("learning_workbench_propose_exercise", args, session_id=session_id)
        assert out["ok"], out
        return out

    def submit(self, study_id: str, exercise_id: str, answer: str, reasoning: str = "", request_id: str | None = None):
        return self.api("POST", f"/studies/{study_id}/exercises/{exercise_id}/attempts",
                        {"client_request_id": request_id or rid(), "answer": answer, "reasoning": reasoning})

    def assess(self, study_id: str, attempt_id: str, verdict: str, quote: str, session_id: str = "chat-A", **kw):
        args = {"study_id": study_id, "attempt_id": attempt_id, "verdict": verdict, "evidence_quote": quote,
                "explanation": kw.pop("explanation", "Checked against the rubric.")}
        args.update(kw)
        return self.tool("learning_workbench_assess", args, session_id=session_id)

    def fresh_process(self, code: str) -> dict:
        """Run ``code`` in a NEW Python process against the same Hermes home; it must print one JSON object."""
        import subprocess

        runtime = os.environ.get("LW_HERMES_RUNTIME", str(Path.home() / ".hermes" / "hermes-agent"))
        prelude = (
            "import sys, json, importlib.util\n"
            "sys.modules['hermes_bootstrap'] = None\n"
            f"spec = importlib.util.spec_from_file_location('lw_fresh_api', {str(REPO / 'dashboard' / 'plugin_api.py')!r})\n"
            "api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)\n")
        env = {**os.environ, "HERMES_HOME": str(self.home), "HOME": str(self.home.parent / "os-home"),
               "PYTHONPATH": os.pathsep.join([runtime, os.environ.get("PYTHONPATH", "")])}
        proc = subprocess.run([sys.executable, "-c", prelude + code], env=env, capture_output=True, text=True,
                              timeout=60)
        assert proc.returncode == 0, proc.stderr[-3000:]
        return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture
def env(tmp_path, monkeypatch, events):
    home = tmp_path / "hermes-home"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / PLUGIN).symlink_to(REPO, target_is_directory=True)
    (home / "config.yaml").write_text(f"plugins:\n  enabled:\n    - {PLUGIN}\n", encoding="utf-8")
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "os-home"))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled))
    monkeypatch.setenv("HERMES_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.delenv("HERMES_DATA_DIR_SUFFIX", raising=False)
    from hermes_cli.plugins import PluginManager

    manager = PluginManager()
    manager.discover_and_load()
    loaded = manager._plugins[PLUGIN]
    assert loaded.error is None, loaded.error
    assert loaded.enabled
    return Env(home, manager)


def rid() -> str:
    return uuid.uuid4().hex
