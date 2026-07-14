-- Sprint 1.8 — Persistence for Tasks, Workflows, and Execution History.
-- Adds tables for task lifecycle, workflow DAGs, and execution history.

-- ============================================================
-- Tasks table
-- ============================================================
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    priority        TEXT    NOT NULL DEFAULT 'normal'
                    CHECK(priority IN ('low', 'normal', 'high', 'critical')),
    state           TEXT    NOT NULL DEFAULT 'pending'
                    CHECK(state IN ('pending', 'ready', 'running', 'waiting',
                                    'completed', 'failed', 'cancelled')),
    progress        INTEGER NOT NULL DEFAULT 0
                    CHECK(progress >= 0 AND progress <= 100),
    agent_name      TEXT    NOT NULL DEFAULT '',
    tools_used      TEXT    NOT NULL DEFAULT '[]',
    created_at      TEXT    NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    result          TEXT,
    errors          TEXT    NOT NULL DEFAULT '[]',
    metadata        TEXT    NOT NULL DEFAULT '{}',
    parent_id       TEXT,
    workflow_id     TEXT,

    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_state
    ON tasks (state);

CREATE INDEX IF NOT EXISTS idx_tasks_workflow_id
    ON tasks (workflow_id);

CREATE INDEX IF NOT EXISTS idx_tasks_agent_name
    ON tasks (agent_name);

CREATE INDEX IF NOT EXISTS idx_tasks_created_at
    ON tasks (created_at);

-- ============================================================
-- Workflows table
-- ============================================================
CREATE TABLE IF NOT EXISTS workflows (
    id              TEXT PRIMARY KEY,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    state           TEXT    NOT NULL DEFAULT 'pending'
                    CHECK(state IN ('pending', 'running', 'completed',
                                    'failed', 'cancelled')),
    created_at      TEXT    NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    result          TEXT,
    errors          TEXT    NOT NULL DEFAULT '[]',
    metadata        TEXT    NOT NULL DEFAULT '{}',
    dag_definition  TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_workflows_state
    ON workflows (state);

CREATE INDEX IF NOT EXISTS idx_workflows_created_at
    ON workflows (created_at);

-- ============================================================
-- Execution history table (audit trail for every execution step)
-- ============================================================
CREATE TABLE IF NOT EXISTS execution_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT,
    workflow_id     TEXT,
    event_type      TEXT    NOT NULL,
    agent_name      TEXT    NOT NULL DEFAULT '',
    tool_name       TEXT    NOT NULL DEFAULT '',
    duration_ms     INTEGER,
    status          TEXT    NOT NULL DEFAULT ''
                    CHECK(status IN ('', 'success', 'failed', 'timeout',
                                     'cancelled', 'denied')),
    error           TEXT,
    result          TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    metadata        TEXT    NOT NULL DEFAULT '{}',

    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_history_task_id
    ON execution_history (task_id);

CREATE INDEX IF NOT EXISTS idx_execution_history_workflow_id
    ON execution_history (workflow_id);

CREATE INDEX IF NOT EXISTS idx_execution_history_event_type
    ON execution_history (event_type);

CREATE INDEX IF NOT EXISTS idx_execution_history_created_at
    ON execution_history (created_at);

-- ============================================================
-- Scheduler jobs table (for Sprint 1.8 — Phase 3)
-- ============================================================
CREATE TABLE IF NOT EXISTS scheduler_jobs (
    id              TEXT PRIMARY KEY,
    name            TEXT    NOT NULL,
    task_name       TEXT    NOT NULL DEFAULT '',
    description     TEXT    NOT NULL DEFAULT '',
    priority        TEXT    NOT NULL DEFAULT 'normal'
                    CHECK(priority IN ('low', 'normal', 'high', 'critical')),
    state           TEXT    NOT NULL DEFAULT 'pending'
                    CHECK(state IN ('pending', 'scheduled', 'running',
                                    'completed', 'failed', 'cancelled',
                                    'paused')),
    schedule_type   TEXT    NOT NULL DEFAULT 'once'
                    CHECK(schedule_type IN ('once', 'interval', 'cron')),
    run_at          TEXT,
    interval_seconds INTEGER,
    cron_expression TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 3,
    last_run_at     TEXT,
    next_run_at     TEXT,
    created_at      TEXT    NOT NULL,
    completed_at    TEXT,
    error           TEXT,
    metadata        TEXT    NOT NULL DEFAULT '{}',
    task_data       TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_state
    ON scheduler_jobs (state);

CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_next_run_at
    ON scheduler_jobs (next_run_at);

CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_schedule_type
    ON scheduler_jobs (schedule_type);