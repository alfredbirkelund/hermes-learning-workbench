"""Test-only HTTP server for the pane harness: the plugin's REAL router plus an /__agent door for test steps.

Run: python tests/e2e/pane_server.py <hermes_home>   -> prints "PORT <n>" once listening.
The /__agent routes exist only in this harness; they call the plugin's real agent tool handlers with a chosen
session id, the way Hermes's tool registry would. Gateway delivery of plugin events is captured, not sent.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main() -> None:
    home = Path(sys.argv[1]).resolve()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    sys.modules["hermes_bootstrap"] = None  # never let the process bootstrap re-exec this server
    events: list = []
    stub = types.ModuleType("tui_gateway.server")
    stub._broadcast_global_event = lambda name, payload: events.append((name, payload))
    sys.modules["tui_gateway.server"] = stub

    spec = importlib.util.spec_from_file_location("lw_harness_api", REPO / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    tools = api.tools

    from fastapi import Body, FastAPI
    import uvicorn

    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/learning-workbench")
    handlers = {schema["name"]: tools._tool(schema, fn) for schema, fn in tools.TOOLS}

    @app.post("/__agent/{tool}")
    def agent(tool: str, body: dict = Body(...)):
        raw = handlers[tool](body.get("args") or {}, session_id=body.get("session_id", ""))
        return json.loads(raw)

    @app.get("/__events")
    def get_events():
        return {"events": events}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    print(f"PORT {port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
