"""Learning Workbench for Hermes Agent: registration entry point."""
from __future__ import annotations

import logging

from .lw import commands, core, db, tools

logger = logging.getLogger(__name__)


def _on_post_compaction(session_id: str = "", old_session_id: str = "", **_: object) -> None:
    """Keep a study attached to its chat when compaction rotates the session id."""
    if not old_session_id or old_session_id == session_id:
        return
    conn = None
    try:
        if not db.store_exists():  # no store, no study to follow; never create one for people who never study
            return
        conn = db.connect()
        study_id = core.rebind_session(conn, old_session_id=old_session_id, new_session_id=session_id)
        if study_id:
            tools.broadcast(study_id)
    except Exception:
        logger.exception("learning-workbench: could not follow the compacted session")
    finally:
        if conn is not None:
            conn.close()


def register(ctx) -> None:
    tools.register(ctx)
    ctx.register_hook("post_compaction", _on_post_compaction)
    commands.register(ctx)
