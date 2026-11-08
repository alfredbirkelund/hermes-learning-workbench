"""Live backend driver: a real `hermes serve` in the isolated test home, driven the way Hermes Desktop's main
process drives its local backend. Chat turns go over the WebSocket JSON-RPC (/api/ws, prompt.submit), learner
actions over the plugin's REST routes with the X-Hermes-Session-Token header. Only the renderer is absent.

Run through scripts/lw-env so it lands in the isolated home:
  scripts/lw-env python tests/e2e/live.py serve-start | serve-stop | status
  scripts/lw-env python tests/e2e/live.py say [--new] <text>      one learner chat turn (prints tools + reply)
  scripts/lw-env python tests/e2e/live.py view                    learner view of this chat's study
  scripts/lw-env python tests/e2e/live.py key                     hidden reference + rubric of the open/latest exercise (evaluator only)
  scripts/lw-env python tests/e2e/live.py answer <answer> [reasoning]   submit on the open exercise, then ask for the check
  scripts/lw-env python tests/e2e/live.py hint | reveal | retry | skip
  scripts/lw-env python tests/e2e/live.py contest <text>          contest the latest check, then ask the tutor to look again
State: .test-artifacts/live/state.json. Transcript: .test-artifacts/live/transcript.jsonl.
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIVE = REPO / ".test-artifacts" / "live"
STATE = LIVE / "state.json"
TRANSCRIPT = LIVE / "transcript.jsonl"
PLUGIN = "learning-workbench"
HOME = Path(os.environ["HERMES_HOME"])
TURN_TIMEOUT = float(os.environ.get("LW_TURN_TIMEOUT", "420"))


def load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(state: dict) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def log(kind: str, **data) -> None:
    LIVE.mkdir(parents=True, exist_ok=True)
    with TRANSCRIPT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.strftime("%H:%M:%S"), "kind": kind, **data}, ensure_ascii=False) + "\n")


# --- HTTP (learner door) ---------------------------------------------------------------------------------

def http(method: str, path: str, body: dict | None = None, state: dict | None = None):
    state = state or load()
    req = urllib.request.Request(f"http://127.0.0.1:{state['port']}/api/plugins/{PLUGIN}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Hermes-Session-Token": state["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        text = exc.read().decode()
        try:
            return exc.code, json.loads(text)
        except ValueError:
            return exc.code, {"raw": text[:500]}


# --- WebSocket JSON-RPC (chat turns) ---------------------------------------------------------------------

class Gateway:
    def __init__(self, state: dict):
        from websockets.sync.client import connect

        self.ws = connect(f"ws://127.0.0.1:{state['port']}/api/ws?token={state['token']}", max_size=None,
                          open_timeout=30)
        self.next_id = 1
        self.events: list[dict] = []
        self._wait_event(lambda e: e.get("type") == "gateway.ready", 30)

    def close(self) -> None:
        self.ws.close()

    def _frames(self, timeout: float):
        raw = self.ws.recv(timeout=timeout)
        for line in str(raw).splitlines():
            if line.strip():
                yield json.loads(line)

    def _wait_event(self, pred, timeout: float) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            for frame in self._frames(max(0.5, end - time.time())):
                if frame.get("method") == "event":
                    ev = frame.get("params") or {}
                    self.events.append(ev)
                    if pred(ev):
                        return ev
        raise TimeoutError("event wait timed out")

    def rpc(self, method: str, params: dict, timeout: float = 60) -> dict:
        rid = self.next_id
        self.next_id += 1
        self.ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        end = time.time() + timeout
        while time.time() < end:
            for frame in self._frames(max(0.5, end - time.time())):
                if frame.get("id") == rid:
                    if "error" in frame:
                        raise RuntimeError(f"{method}: {frame['error']}")
                    return frame.get("result") or {}
                if frame.get("method") == "event":
                    self.events.append(frame.get("params") or {})
        raise TimeoutError(method)


def open_session(gw: Gateway, state: dict, new: bool) -> str:
    if new or not state.get("stored"):
        res = gw.rpc("session.create", {})
        state["stored"] = res.get("stored_session_id")
        save(state)
        log("session", stored=state["stored"])
        return res["session_id"]
    res = gw.rpc("session.resume", {"session_id": state["stored"]})
    return res["session_id"]


def run_turn(text: str, new: bool = False) -> None:
    state = load()
    gw = Gateway(state)
    try:
        sid = open_session(gw, state, new)
        start = len(gw.events)
        log("learner_message", text=text)
        res = gw.rpc("prompt.submit", {"session_id": sid, "text": text, "queued": True})
        if res.get("status") not in (None, "started", "accepted", "ok"):
            print(f"[prompt.submit status: {res.get('status')}]")
        ev = gw._wait_event(lambda e: e.get("type") in ("turn.end", "turn.error") and e.get("session_id") in (sid, None),
                            TURN_TIMEOUT)
        tools, replies = [], []
        for e in gw.events[start:]:
            p = e.get("payload") or {}
            if e.get("type") == "tool.start":
                tools.append(p.get("name") or p.get("tool") or "?")
            elif e.get("type") == "message.complete" and p.get("text"):
                replies.append(p["text"])
        log("tutor_turn", tools=tools, reply="\n".join(replies), end=ev.get("type"))
        print(f"tools: {', '.join(tools) or '(none)'}")
        print("tutor:", "\n".join(replies).strip()[:3000] or "(no text)")
        if ev.get("type") == "turn.error":
            print("TURN ERROR:", json.dumps(ev.get("payload"))[:500])
    finally:
        gw.close()


# --- learner view --------------------------------------------------------------------------------------

def study_view(state: dict) -> dict | None:
    code, body = http("GET", f"/session-study?session_id={state.get('stored', '')}", state=state)
    if code != 200:
        raise SystemExit(f"session-study failed: {code} {body}")
    return body.get("study")


def current(study: dict) -> dict | None:
    eid = study.get("open_exercise_id") or study.get("latest_exercise_id")
    return next((e for e in study["exercises"] if e["exercise_id"] == eid), None)


def show(study: dict | None) -> None:
    if not study:
        print("(no study in this chat)")
        return
    print(f"goal: {study['goal']}  [rev {study['study_revision']}, created by {study['created_by']}]")
    if study.get("route"):
        for i, s in enumerate(study["route"]["steps"], 1):
            ev = s["evidence"]
            print(f"  {i}. {s['title']} ({s['status']}) — {ev['correct_unaided']} unaided ✓, {ev['correct_with_help']} helped, "
                  f"{ev['attempts']} attempts")
    else:
        print(f"  (no route; diagnostics {study['diagnostics']['used']}/{study['diagnostics']['cap']})")
    ex = current(study)
    if ex:
        print(f"exercise {ex['sequence']} · {ex['kind']} · {ex['state']} · hints {ex['hints_shown']}/{ex['hints_total']}"
              f"{' · solution revealed' if ex['solution_revealed'] else ''}")
        print("  prompt:", ex["prompt"])
        for h in ex["hints"]:
            print("  hint:", h)
        for a in ex["attempts"]:
            print(f"  attempt {a['number']} ({a['help_class']}): {a['answer']!r}" + (f" | reasoning: {a['reasoning']!r}" if a["reasoning"] else ""))
            for s in a["assessments"]:
                print(f"    check r{s['revision']}: {s['verdict']} | quote {s['evidence_quote']!r} | {s['explanation']}" +
                      (f" | gap: {s['gap']}" if s["gap"] else ""))
            for c in a["contests"]:
                print(f"    contest ({'answered' if c['answered'] else 'pending'}): {c['text']!r}")
    if study.get("draft"):
        d = study["draft"]
        if d["answer"] or d["reasoning"]:
            print(f"draft r{d['revision']}: {d['answer']!r}")


def key() -> None:
    """Evaluator-only: the frozen reference + rubric (read-only from the store)."""
    state = load()
    study = study_view(state)
    ex = current(study) if study else None
    if not ex:
        print("(no exercise)")
        return
    conn = sqlite3.connect(f"file:{HOME / 'plugin-data' / PLUGIN / 'data.db'}?mode=ro", uri=True)
    for rev, ref, rubric, reason in conn.execute(
            "SELECT revision, reference_answer, rubric, reason FROM exercise_references WHERE exercise_id = ? "
            "ORDER BY revision", (ex["exercise_id"],)):
        print(f"reference r{rev} ({reason}): {ref}\n  rubric: {rubric}")
    hints = json.loads(conn.execute("SELECT hints_json FROM exercises WHERE id = ?", (ex["exercise_id"],)).fetchone()[0])
    print("frozen hints:", hints)


def learner_action(action: str, *args: str) -> None:
    state = load()
    study = study_view(state)
    if not study:
        raise SystemExit("no study in this chat")
    ex = current(study)
    sid = study["study_id"]
    if action == "answer":
        answer, reasoning = args[0], (args[1] if len(args) > 1 else "")
        code, body = http("POST", f"/studies/{sid}/exercises/{study['open_exercise_id']}/attempts",
                          {"client_request_id": uuid.uuid4().hex, "answer": answer, "reasoning": reasoning}, state)
        print("submit:", code, json.dumps(body, ensure_ascii=False)[:300])
        log("learner_submit", answer=answer, reasoning=reasoning, code=code, receipt=body)
        if code == 200 and not body.get("duplicate"):
            run_turn(f"[Learning Workbench] I submitted attempt {body['number']} for exercise "
                     f"{body['exercise_sequence']}. Please check it.")
        return
    if action == "contest":
        last = next((a for e in reversed(study["exercises"]) for a in reversed(e["attempts"]) if a["assessments"]), None)
        if not last:
            raise SystemExit("nothing to contest")
        code, body = http("POST", f"/studies/{sid}/assessments/{last['assessments'][-1]['assessment_id']}/contest",
                          {"client_request_id": uuid.uuid4().hex, "text": args[0]}, state)
        print("contest:", code, json.dumps(body)[:300])
        log("learner_contest", text=args[0], code=code)
        if code == 200:
            run_turn(f"[Learning Workbench] I contested your check of attempt {last['number']}. Please look again.")
        return
    path = {"hint": "hint", "reveal": "reveal", "retry": "retry", "skip": "skip"}[action]
    if ex is None:
        raise SystemExit("no exercise")
    body = {"seen": ex["hints_shown"]} if action == "hint" else ({"confirm": True} if action == "reveal" else {})
    code, out = http("POST", f"/studies/{sid}/exercises/{ex['exercise_id']}/{path}", body, state)
    print(f"{action}:", code, json.dumps(out, ensure_ascii=False)[:400])
    log(f"learner_{action}", code=code)


# --- serve lifecycle -----------------------------------------------------------------------------------

def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve_start() -> None:
    state = load()
    if state.get("pid") and Path(f"/proc/{state['pid']}").exists():
        print(f"already running: pid {state['pid']} port {state['port']}")
        return
    port, token = free_port(), secrets.token_urlsafe(24)
    LIVE.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HERMES_DASHBOARD_SESSION_TOKEN": token}
    logf = open(LIVE / "serve.log", "ab")
    proc = subprocess.Popen([sys.executable, "-m", "hermes_cli.main", "serve", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True, cwd=str(LIVE))
    state.update(port=port, token=token, pid=proc.pid)
    save(state)
    end = time.time() + 180
    while time.time() < end:
        if proc.poll() is not None:
            raise SystemExit(f"hermes serve exited ({proc.returncode}); see {LIVE / 'serve.log'}")
        try:
            code, body = http("GET", "/health", state=state)
            if code == 200:
                print(f"hermes serve up: pid {proc.pid} port {port}; plugin health {body}")
                return
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit("hermes serve did not become healthy in 180 s")


def serve_stop() -> None:
    state = load()
    pid = state.get("pid")
    if not pid or not Path(f"/proc/{pid}").exists():
        print("not running")
        return
    environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    if f"HERMES_HOME={HOME}".encode() not in environ:
        raise SystemExit(f"refusing: pid {pid} is not this test home's serve process")
    os.killpg(pid, signal.SIGTERM)
    for _ in range(60):
        if not Path(f"/proc/{pid}").exists():
            break
        time.sleep(0.5)
    print(f"stopped pid {pid}" if not Path(f"/proc/{pid}").exists() else f"pid {pid} still running")
    state.pop("pid", None)
    save(state)


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "serve-start":
        serve_start()
    elif cmd == "serve-stop":
        serve_stop()
    elif cmd == "status":
        print(json.dumps({k: v for k, v in load().items() if k != "token"}))
    elif cmd == "say":
        new = bool(args and args[0] == "--new")
        run_turn(" ".join(args[1:] if new else args), new=new)
        show(study_view(load()))
    elif cmd == "view":
        show(study_view(load()))
    elif cmd == "key":
        key()
    elif cmd in ("answer", "contest", "hint", "reveal", "retry", "skip"):
        learner_action(cmd, *args)
        if cmd in ("answer", "contest"):
            show(study_view(load()))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
