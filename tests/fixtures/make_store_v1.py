"""Regenerate tests/fixtures/store-v1.sql: a real schema-1 store written by the v1 code (commit 0c563e0).

The migration test loads this dump and opens it with the current code, so the upgrade is tested against data
the old release actually wrote, not a hand-made imitation.

  git archive 0c563e0 lw | tar -x -C "$TMPDIR/lw-v1" && python tests/fixtures/make_store_v1.py "$TMPDIR/lw-v1"
"""
import sys
import tempfile
from pathlib import Path

v1_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(v1_root))
from lw import core, db  # noqa: E402  (the v1 package)

assert db.SCHEMA_VERSION == 1, f"expected the v1 code, got schema {db.SCHEMA_VERSION}"
path = Path(tempfile.mkdtemp()) / "data.db"
conn = db.connect(path)
s = core.start_study(conn, actor="agent", session_id="chat-v1", goal="Understand why d/dx x² = 2x",
                     material_text="The derivative is the limit of (f(x+h) − f(x))/h as h → 0.",
                     material_title="notes.md", material_origin="chat")
sid = s["study_id"]
rev = lambda: core.study_view(conn, sid, audience="agent")["study_revision"]  # noqa: E731
plan = core.propose_plan(conn, session_id="chat-v1", study_id=sid, study_revision=rev(),
                         steps=[{"title": "Expand (x+h)²", "why": "The difference quotient needs it."},
                                {"title": "Take the limit h → 0", "why": "The derivative is that limit."}])
steps = core.study_view(conn, sid, audience="agent")["route"]["steps"]
ex = core.propose_exercise(conn, session_id="chat-v1", study_id=sid, study_revision=rev(), kind="check",
                           step_id=steps[0]["step_id"], prompt="Expand (x+h)².", reference_answer="x² + 2xh + h²",
                           rubric="All three terms, including 2xh.", hints=["Multiply (x+h)(x+h)."])
core.show_hint(conn, study_id=sid, exercise_id=ex["exercise_id"], seen=0)
at = core.submit_attempt(conn, study_id=sid, exercise_id=ex["exercise_id"], client_request_id="v1-req-0001",
                         answer="x² + 2xh + h²", reasoning="FOIL: x·x, x·h twice, h·h.", channel="desktop")
core.assess(conn, session_id="chat-v1", study_id=sid, attempt_id=at["attempt_id"], verdict="correct",
            explanation="All three terms, with the cross term counted twice.", evidence_quote="x² + 2xh + h²")
conn.close()

import sqlite3  # noqa: E402

dump = "\n".join(sqlite3.connect(path).iterdump())
out = Path(__file__).with_name("store-v1.sql")
out.write_text("-- Schema-1 store written by Learning Workbench commit 0c563e0 (see make_store_v1.py).\n" + dump + "\n",
               encoding="utf-8")
print(f"wrote {out} ({len(dump)} chars); study {sid}, exercise {ex['exercise_id']}, attempt {at['attempt_id']}")
