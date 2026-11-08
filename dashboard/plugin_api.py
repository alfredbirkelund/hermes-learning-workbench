"""Learning Workbench learner routes, mounted by Hermes at /api/plugins/learning-workbench/.

These routes are the learner's door. Desktop calls them through ``ctx.rest`` (host-authenticated, scoped to
this plugin and the active profile). Every write here is stamped as the learner's; no request field can claim
another actor, and agent tools cannot reach these functions.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def _load_core_package():
    """Load the plugin's ``lw`` package under a private module name (this file is imported by path)."""
    name = "learning_workbench_lw"
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    pkg_dir = Path(__file__).resolve().parent.parent / "lw"
    spec = importlib.util.spec_from_file_location(name, pkg_dir / "__init__.py",
                                                  submodule_search_locations=[str(pkg_dir)])
    if spec is None or spec.loader is None:
        raise ImportError("learning-workbench: cannot load core package")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_pkg = _load_core_package()
core = importlib.import_module(_pkg.__name__ + ".core")
db = importlib.import_module(_pkg.__name__ + ".db")
export = importlib.import_module(_pkg.__name__ + ".export")
tools = importlib.import_module(_pkg.__name__ + ".tools")

router = APIRouter()

_STATUS = {
    "invalid_input": 400, "no_session": 400, "unknown_study": 404, "unknown_exercise": 404, "unknown_step": 404,
    "unknown_assessment": 404,
}


def _version() -> str:
    try:
        text = (Path(__file__).resolve().parent.parent / "plugin.yaml").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _run(fn, *, study_id: str | None = None, notify: bool = False):
    conn = None
    try:
        conn = db.connect()
        out = fn(conn)
        if notify and study_id and not (isinstance(out, dict) and out.get("duplicate")):
            tools.broadcast(study_id)
        return {"ok": True, **out}
    except core.LwError as exc:
        return JSONResponse(status_code=_STATUS.get(exc.code, 409), content=exc.to_dict())
    except sqlite3.OperationalError as exc:  # locked or unwritable store: nothing was committed
        logger.warning("learning-workbench: storage unavailable: %s", exc)
        return JSONResponse(status_code=503, content={
            "ok": False, "error": "storage_unavailable",
            "message": "Not saved: the Workbench storage is busy or unavailable. Your text is still here; try again."})
    finally:
        if conn is not None:
            conn.close()


def _body(body: Any) -> dict:
    return body if isinstance(body, dict) else {}


@router.get("/health")
def health() -> dict:
    return {"ok": True, "plugin": db.PLUGIN_NAME, "version": _version()}


@router.get("/session-study")
def session_study(session_id: str = "") -> Any:
    def op(conn):
        row = core.active_study_for_session(conn, session_id)
        earlier = [s for s in core.list_studies(conn)
                   if session_id and s["hermes_session_id"] == session_id and s["status"] != "active"]
        if row is None:
            return {"study": None, "earlier": earlier}
        return {"study": core.study_view(conn, row["id"], audience="learner"), "earlier": earlier}
    return _run(op)


@router.post("/studies")
def start_study(body: dict = Body(...)) -> Any:
    b = _body(body)

    def op(conn):
        out = core.start_study(conn, actor="learner", session_id=str(b.get("session_id") or ""),
                               goal=b.get("goal"), material_text=b.get("material_text", ""),
                               material_title=b.get("material_title", ""))
        tools.broadcast(out["study_id"])
        return {**out, "study": core.study_view(conn, out["study_id"], audience="learner")}
    return _run(op)


@router.get("/studies/{study_id}")
def get_study(study_id: str) -> Any:
    return _run(lambda conn: {"study": core.study_view(conn, study_id, audience="learner")})


@router.put("/studies/{study_id}/exercises/{exercise_id}/draft")
def put_draft(study_id: str, exercise_id: str, body: dict = Body(...)) -> Any:
    b = _body(body)
    return _run(lambda conn: core.save_draft(conn, study_id=study_id, exercise_id=exercise_id,
                                             base_revision=b.get("base_revision"), answer=b.get("answer"),
                                             reasoning=b.get("reasoning")))


@router.post("/studies/{study_id}/exercises/{exercise_id}/attempts")
def post_attempt(study_id: str, exercise_id: str, body: dict = Body(...)) -> Any:
    b = _body(body)
    return _run(lambda conn: core.submit_attempt(conn, study_id=study_id, exercise_id=exercise_id,
                                                 client_request_id=b.get("client_request_id"), answer=b.get("answer"),
                                                 reasoning=b.get("reasoning"), channel="desktop"),
                study_id=study_id, notify=True)


@router.post("/studies/{study_id}/exercises/{exercise_id}/hint")
def post_hint(study_id: str, exercise_id: str, body: dict = Body(...)) -> Any:
    b = _body(body)
    return _run(lambda conn: core.show_hint(conn, study_id=study_id, exercise_id=exercise_id, seen=b.get("seen")),
                study_id=study_id, notify=True)


@router.post("/studies/{study_id}/exercises/{exercise_id}/reveal")
def post_reveal(study_id: str, exercise_id: str, body: dict = Body(...)) -> Any:
    if _body(body).get("confirm") is not True:
        return JSONResponse(status_code=400, content={
            "ok": False, "error": "confirmation_required",
            "message": "Revealing the solution needs an explicit confirmation."})
    return _run(lambda conn: core.reveal_solution(conn, study_id=study_id, exercise_id=exercise_id),
                study_id=study_id, notify=True)


@router.post("/studies/{study_id}/exercises/{exercise_id}/skip")
def post_skip(study_id: str, exercise_id: str) -> Any:
    return _run(lambda conn: core.skip_exercise(conn, study_id=study_id, exercise_id=exercise_id),
                study_id=study_id, notify=True)


@router.post("/studies/{study_id}/exercises/{exercise_id}/retry")
def post_retry(study_id: str, exercise_id: str) -> Any:
    return _run(lambda conn: core.retry_exercise(conn, study_id=study_id, exercise_id=exercise_id),
                study_id=study_id, notify=True)


@router.post("/studies/{study_id}/assessments/{assessment_id}/contest")
def post_contest(study_id: str, assessment_id: str, body: dict = Body(...)) -> Any:
    b = _body(body)
    return _run(lambda conn: core.contest(conn, study_id=study_id, assessment_id=assessment_id, text=b.get("text"),
                                          client_request_id=b.get("client_request_id")),
                study_id=study_id, notify=True)


@router.post("/studies/{study_id}/route")
def post_route(study_id: str, body: dict = Body(...)) -> Any:
    b = _body(body)
    return _run(lambda conn: core.edit_route(conn, study_id=study_id, base_plan_revision=b.get("base_plan_revision"),
                                             op=b.get("op"), step_id=b.get("step_id"), title=b.get("title")),
                study_id=study_id, notify=True)


@router.post("/studies/{study_id}/export")
def post_export(study_id: str, body: dict = Body(default={})) -> Any:
    b = _body(body)
    return _run(lambda conn: export.export_study(conn, study_id, include_material=b.get("include_material") is True,
                                                 plugin_version=_version()))


@router.post("/studies/{study_id}/archive")
def post_archive(study_id: str) -> Any:
    return _run(lambda conn: core.archive_study(conn, study_id=study_id), study_id=study_id, notify=True)


@router.post("/studies/{study_id}/delete-request")
def post_delete_request(study_id: str) -> Any:
    def op(conn):
        out = core.request_delete(conn, study_id=study_id)
        out["exports"] = len(list(export.exports_dir().glob(f"{study_id}-*.md")))
        return out
    return _run(op)


@router.post("/studies/{study_id}/delete")
def post_delete(study_id: str, body: dict = Body(...)) -> Any:
    b = _body(body)

    def op(conn):
        out = core.confirm_delete(conn, study_id=study_id, token=b.get("confirm_token"))
        out["exports_removed"] = export.delete_exports(study_id)
        tools.broadcast(study_id)
        return out
    return _run(op)
