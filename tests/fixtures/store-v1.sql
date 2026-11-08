-- Schema-1 store written by Learning Workbench commit 0c563e0 (see make_store_v1.py).
BEGIN TRANSACTION;
CREATE TABLE assessments (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    verdict TEXT NOT NULL,
    explanation TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    gap TEXT NOT NULL,
    reference_revision INTEGER NOT NULL,
    responds_to_contest_id TEXT,
    UNIQUE (attempt_id, revision)
);
INSERT INTO "assessments" VALUES('as_9zij8uvezp','st_3ep8zt6pn4','at_j5nv2w56pg',1,'2026-11-08T15:08:43.354Z','correct','All three terms, with the cross term counted twice.','x² + 2xh + h²','',1,NULL);
CREATE TABLE attempts (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    exercise_id TEXT NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
    number INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    channel TEXT NOT NULL,
    answer TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    help_class TEXT NOT NULL,
    hints_seen INTEGER NOT NULL,
    solution_seen INTEGER NOT NULL,
    feedback_seen INTEGER NOT NULL,
    reference_revision INTEGER NOT NULL,
    client_request_id TEXT NOT NULL,
    UNIQUE (exercise_id, number),
    UNIQUE (study_id, client_request_id)
);
INSERT INTO "attempts" VALUES('at_j5nv2w56pg','st_3ep8zt6pn4','ex_5yc5maw8bq',1,'2026-11-08T15:08:43.353Z','desktop','x² + 2xh + h²','FOIL: x·x, x·h twice, h·h.','hinted',1,0,0,1,'v1-req-0001');
CREATE TABLE contests (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    assessment_id TEXT NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    attempt_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    UNIQUE (study_id, client_request_id)
);
CREATE TABLE delete_tokens (
    study_id TEXT PRIMARY KEY REFERENCES studies(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE drafts (
    exercise_id TEXT PRIMARY KEY REFERENCES exercises(id) ON DELETE CASCADE,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    answer TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    exercise_id TEXT,
    detail_json TEXT NOT NULL
);
INSERT INTO "events" VALUES(1,'st_3ep8zt6pn4','2026-11-08T15:08:43.350Z','agent','study_started',NULL,'{"goal": "Understand why d/dx x² = 2x", "material_chars": 58, "material_source": "attached_by_agent"}');
INSERT INTO "events" VALUES(2,'st_3ep8zt6pn4','2026-11-08T15:08:43.350Z','agent','plan_proposed',NULL,'{"plan_revision": 1, "removed": [], "steps": 2}');
INSERT INTO "events" VALUES(3,'st_3ep8zt6pn4','2026-11-08T15:08:43.351Z','agent','exercise_proposed','ex_5yc5maw8bq','{"exercise_kind": "check", "hints": 1, "sequence": 1}');
INSERT INTO "events" VALUES(4,'st_3ep8zt6pn4','2026-11-08T15:08:43.352Z','learner','hint_shown','ex_5yc5maw8bq','{"index": 0}');
INSERT INTO "events" VALUES(5,'st_3ep8zt6pn4','2026-11-08T15:08:43.353Z','learner','attempt_submitted','ex_5yc5maw8bq','{"attempt_id": "at_j5nv2w56pg", "channel": "desktop", "help_class": "hinted", "number": 1}');
INSERT INTO "events" VALUES(6,'st_3ep8zt6pn4','2026-11-08T15:08:43.354Z','agent','assessment_proposed','ex_5yc5maw8bq','{"assessment_id": "as_9zij8uvezp", "attempt_id": "at_j5nv2w56pg", "responds_to_contest_id": null, "revision": 1, "verdict": "correct"}');
CREATE TABLE exercise_references (
    exercise_id TEXT NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    rubric TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (exercise_id, revision)
);
INSERT INTO "exercise_references" VALUES('ex_5yc5maw8bq',1,'2026-11-08T15:08:43.351Z','x² + 2xh + h²','All three terms, including 2xh.','initial');
CREATE TABLE exercises (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    step_id TEXT,
    step_title TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    prompt TEXT NOT NULL,
    reasoning_mode TEXT NOT NULL,
    hints_json TEXT NOT NULL,
    state TEXT NOT NULL,
    state_changed_at TEXT NOT NULL,
    UNIQUE (study_id, sequence)
);
INSERT INTO "exercises" VALUES('ex_5yc5maw8bq','st_3ep8zt6pn4',1,'sp_ikxveuh5vy','Expand (x+h)²','check','2026-11-08T15:08:43.351Z','agent','Expand (x+h)².','optional','["Multiply (x+h)(x+h)."]','checked','2026-11-08T15:08:43.354Z');
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT INTO "meta" VALUES('schema_version','1');
INSERT INTO "meta" VALUES('learner_rev:st_3ep8zt6pn4','5');
CREATE TABLE plan_revisions (
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL,
    items_json TEXT NOT NULL,
    PRIMARY KEY (study_id, revision)
);
INSERT INTO "plan_revisions" VALUES('st_3ep8zt6pn4',1,'2026-11-08T15:08:43.350Z','agent','','[{"step_id": "sp_ikxveuh5vy", "title": "Expand (x+h)²", "why": "The difference quotient needs it.", "status": "active"}, {"step_id": "sp_nupvc49bqk", "title": "Take the limit h → 0", "why": "The derivative is that limit.", "status": "active"}]');
CREATE TABLE steps (
    id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    title TEXT NOT NULL,
    why TEXT NOT NULL
);
INSERT INTO "steps" VALUES('sp_ikxveuh5vy','st_3ep8zt6pn4','2026-11-08T15:08:43.350Z','agent','Expand (x+h)²','The difference quotient needs it.');
INSERT INTO "steps" VALUES('sp_nupvc49bqk','st_3ep8zt6pn4','2026-11-08T15:08:43.350Z','agent','Take the limit h → 0','The derivative is that limit.');
CREATE TABLE studies (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    goal TEXT NOT NULL,
    hermes_session_id TEXT,
    created_by TEXT NOT NULL,
    material_title TEXT NOT NULL,
    material_text TEXT NOT NULL,
    material_source TEXT NOT NULL,
    material_origin TEXT NOT NULL,
    material_sha256 TEXT NOT NULL
);
INSERT INTO "studies" VALUES('st_3ep8zt6pn4','2026-11-08T15:08:43.350Z','2026-11-08T15:08:43.354Z',6,'active','Understand why d/dx x² = 2x','chat-v1','agent','notes.md','The derivative is the limit of (f(x+h) − f(x))/h as h → 0.','attached_by_agent','chat','e801b4b08289ee50e1bb4fe82a4add0b6081981e4435a49c6625cc07d94a8c49');
CREATE INDEX studies_session ON studies(hermes_session_id, status);
CREATE INDEX exercises_study ON exercises(study_id, state);
CREATE INDEX events_study ON events(study_id, seq);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('events',6);
COMMIT;
